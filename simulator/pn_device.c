/* pn_device.c — Profinet IO Device (slave) implementation.
 * Three background threads:
 *   pkt_thread   — DCP server + cyclic RX (raw Ethernet via Npcap)
 *   rpc_thread   — RPC/CM UDP server on port 34964
 *   cyclic_thread— periodic input-frame TX to controller
 */

#include "pn_device.h"
#include "../src/utils.h"
#include "../src/frame_io.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

/* ─── Profinet EtherType and FrameIDs ────────────────────────────────────── */
#define ETH_PN              0x8892u
#define FID_DCP_IDENTIFY    0xFEFEu  /* DCP Identify Request multicast */
#define FID_DCP_IDENTIFY_R  0xFEFFu  /* DCP Identify Response */
#define FID_DCP_SET         0xFEFDu  /* DCP Set/Get unicast request */
#define FID_DCP_SET_R       0xFEFCu  /* DCP Set/Get response */

/* RPC port */
#define RPC_PORT 34964

/* ─── Logging helper ─────────────────────────────────────────────────────── */
static void dev_log(DevState *dev, const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    pn_log("%s", buf);

    if (dev->notify_hwnd) {
        char *heap = strdup(buf);
        if (heap)
            PostMessage(dev->notify_hwnd, WM_DEV_LOG, 0, (LPARAM)(intptr_t)heap);
    }
}

/* ─── PROFIdrive auto-simulation ─────────────────────────────────────────── */
static void compute_profidrive(DevState *dev)
{
    int on = (dev->stw1 & 0x0001) != 0;
    int en = (dev->stw1 & 0x0008) != 0;
    int sp = (dev->stw1 & 0x0040) != 0;

    if (on && en && sp) {
        dev->zsw1 = 0x0F37;
        dev->nist  = dev->nsoll;
    } else if (on) {
        dev->zsw1 = 0x0F31;
        dev->nist  = 0;
    } else {
        dev->zsw1 = 0x0F21;
        dev->nist  = 0;
    }
}

/* ─── DCP helpers ────────────────────────────────────────────────────────── */

static void dcp_send_identify_response(DevState *dev, const uint8_t *req_buf)
{
    /* req_buf[6..11] = source MAC of the controller (our dst) */
    uint8_t frame[512] = {0};
    int pos = 0;

    /* Ethernet header */
    memcpy(frame + pos, req_buf + 6, 6); pos += 6;  /* dst */
    memcpy(frame + pos, dev->local_mac, 6); pos += 6; /* src */
    frame[pos++] = 0x88; frame[pos++] = 0x92;        /* EtherType */

    /* FrameID = 0xFEFF */
    frame[pos++] = 0xFE; frame[pos++] = 0xFF;

    /* DCP header: ServiceID=0x05, ServiceType=0x01 (response) */
    frame[pos++] = 0x05; /* ServiceID */
    frame[pos++] = 0x01; /* ServiceType */
    /* XID (4 bytes) */
    memcpy(frame + pos, req_buf + 18, 4); pos += 4;
    /* ResponseDelay = 0x0000 */
    frame[pos++] = 0x00; frame[pos++] = 0x00;
    /* DataLength placeholder (2 bytes) */
    int data_len_off = pos;
    frame[pos++] = 0x00; frame[pos++] = 0x00;

    int data_start = pos;

    /* Block: NameOfStation (option=0x02, sub=0x02) */
    uint16_t name_len = (uint16_t)strlen(dev->station_name);
    frame[pos++] = 0x02; /* option */
    frame[pos++] = 0x02; /* sub */
    pn_put_u16be(frame, pos, name_len + 2); pos += 2; /* block_len incl. 2B DCPBlockError */
    frame[pos++] = 0x00; frame[pos++] = 0x00;          /* BlockQualifier / DCPBlockError */
    memcpy(frame + pos, dev->station_name, name_len); pos += name_len;
    if (name_len & 1) frame[pos++] = 0x00;            /* pad to even */

    /* Block: IP (option=0x01, sub=0x02) */
    frame[pos++] = 0x01;
    frame[pos++] = 0x02;
    pn_put_u16be(frame, pos, 14); pos += 2; /* block_len = 2(flags)+4+4+4 */
    frame[pos++] = 0x00; frame[pos++] = 0x00; /* flags */
    memcpy(frame + pos, &dev->ip,      4); pos += 4;
    memcpy(frame + pos, &dev->subnet,  4); pos += 4;
    memcpy(frame + pos, &dev->gateway, 4); pos += 4;

    /* Block: DeviceID (option=0x02, sub=0x07) */
    frame[pos++] = 0x02;
    frame[pos++] = 0x07;
    pn_put_u16be(frame, pos, 6); pos += 2; /* 2B reserved + 2B vendor_id + 2B device_id */
    frame[pos++] = 0x00; frame[pos++] = 0x00; /* reserved */
    pn_put_u16be(frame, pos, dev->vendor_id); pos += 2;
    pn_put_u16be(frame, pos, dev->device_id); pos += 2;

    /* Fill DataLength */
    pn_put_u16be(frame, data_len_off, (uint16_t)(pos - data_start));

    /* Pad to min Ethernet payload */
    while (pos < 60) frame[pos++] = 0x00;

    frameio_send(dev->pcap, frame, pos);
    InterlockedIncrement(&dev->frames_tx);
}

static void dcp_handle_identify(DevState *dev, const uint8_t *buf, int len)
{
    if (len < 26) return;

    uint8_t service_id   = buf[14];
    uint8_t service_type = buf[15];
    if (service_id != 0x05 || service_type != 0x00) return;

    /* Parse blocks starting at buf[26] to decide if we should respond */
    int pos = 26;
    int data_len = (int)pn_get_u16be(buf, 22); /* DCP DataLength field at offset 22 */
    int end      = 26 + data_len;
    if (end > len) end = len;

    int respond = 0;

    while (pos + 4 <= end) {
        uint8_t  opt     = buf[pos];
        uint8_t  sub     = buf[pos + 1];
        uint16_t blk_len = pn_get_u16be(buf, pos + 2);
        pos += 4;

        if (opt == 0xFF && sub == 0xFF) {
            /* AllStations — always respond */
            respond = 1;
            break;
        }
        if (opt == 0x02 && sub == 0x02) {
            /* NameOfStation filter */
            uint16_t name_len = blk_len > 2 ? blk_len - 2 : 0; /* skip 2B BlockQualifier */
            int data_off = pos + 2; /* skip BlockQualifier */
            if (data_off + name_len <= end) {
                if (name_len == (uint16_t)strlen(dev->station_name) &&
                    memcmp(buf + data_off, dev->station_name, name_len) == 0) {
                    respond = 1;
                }
            }
            /* Also respond if station name is empty (unset device) */
            if (dev->station_name[0] == '\0') respond = 1;
        }
        /* advance, pad to even */
        pos += (int)blk_len;
        if (blk_len & 1) pos++;
    }

    if (respond)
        dcp_send_identify_response(dev, buf);
}

static void dcp_handle_set(DevState *dev, const uint8_t *buf, int len)
{
    if (len < 26) return;

    /* Must be addressed to our MAC */
    if (memcmp(buf, dev->local_mac, 6) != 0) return;

    uint8_t service_id   = buf[14];
    uint8_t service_type = buf[15];
    /* ServiceID 0x04 = Set, ServiceType 0x00 = request */
    if (service_id != 0x04 || service_type != 0x00) return;

    uint16_t data_len = pn_get_u16be(buf, 22);
    int pos = 26;
    int end = 26 + (int)data_len;
    if (end > len) end = len;

    while (pos + 4 <= end) {
        uint8_t  opt     = buf[pos];
        uint8_t  sub     = buf[pos + 1];
        uint16_t blk_len = pn_get_u16be(buf, pos + 2);
        pos += 4;

        if (opt == 0x01 && sub == 0x02 && blk_len >= 14) {
            /* IP block: 2B flags + 4B ip + 4B subnet + 4B gateway */
            uint32_t new_ip, new_sub, new_gw;
            memcpy(&new_ip,  buf + pos + 2, 4);
            memcpy(&new_sub, buf + pos + 6, 4);
            memcpy(&new_gw,  buf + pos + 10, 4);
            dev_set_ip(dev, new_ip, new_sub, new_gw);
            dev_log(dev, "DCP SetIP: %u.%u.%u.%u",
                    (new_ip)&0xFF, (new_ip>>8)&0xFF,
                    (new_ip>>16)&0xFF, (new_ip>>24)&0xFF);
            PostMessage(dev->notify_hwnd, WM_DEV_DCP_SETIP, 0, 0);
        } else if (opt == 0x02 && sub == 0x02 && blk_len >= 2) {
            /* NameOfStation block: 2B BlockQualifier + name */
            uint16_t name_len = blk_len - 2;
            if (name_len > 239) name_len = 239;
            EnterCriticalSection(&dev->io_lock);
            memcpy(dev->station_name, buf + pos + 2, name_len);
            dev->station_name[name_len] = '\0';
            LeaveCriticalSection(&dev->io_lock);
            dev_log(dev, "DCP SetName: \"%s\"", dev->station_name);
            PostMessage(dev->notify_hwnd, WM_DEV_DCP_SETNAME, 0, 0);
        }

        pos += (int)blk_len;
        if (blk_len & 1) pos++;
    }

    /* Send Set Response (FrameID 0xFEFC) */
    uint8_t resp[60] = {0};
    memcpy(resp,     buf + 6, 6);  /* dst = controller src */
    memcpy(resp + 6, dev->local_mac, 6);
    resp[12] = 0x88; resp[13] = 0x92;
    resp[14] = 0xFE; resp[15] = 0xFC;  /* FrameID */
    resp[16] = 0x04; /* ServiceID = Set */
    resp[17] = 0x01; /* ServiceType = response */
    memcpy(resp + 18, buf + 18, 4);    /* XID echo */
    resp[22] = 0x00; resp[23] = 0x00;  /* ResponseDelay */
    /* DataLength = 4 (one result block header is 4 bytes) */
    resp[24] = 0x00; resp[25] = 0x04;
    /* Result block: option=0xFF sub=0xFF len=0x0003 result=0 reserved=0 pad=0 */
    resp[26] = 0xFF; resp[27] = 0xFF;
    resp[28] = 0x00; resp[29] = 0x03;
    resp[30] = 0x00; resp[31] = 0x00; resp[32] = 0x00;

    frameio_send(dev->pcap, resp, 60);
    InterlockedIncrement(&dev->frames_tx);
}

/* ─── Cyclic RX handler ───────────────────────────────────────────────────── */
static void cyclic_handle_rx(DevState *dev, const uint8_t *buf, int len)
{
    /* Ethernet 14B + FrameID 2B + IOPS 1B + data starts at buf[17] */
    if (len < 22) return;
    uint8_t iops = buf[16];
    if (!(iops & 0x80)) return; /* IOPS not GOOD */

    uint16_t stw1  = (uint16_t)(buf[17] | (buf[18] << 8)); /* little-endian */
    uint16_t nsoll = (uint16_t)(buf[19] | (buf[20] << 8));

    EnterCriticalSection(&dev->io_lock);
    dev->stw1  = stw1;
    dev->nsoll = nsoll;
    if (dev->auto_sim) compute_profidrive(dev);
    LeaveCriticalSection(&dev->io_lock);

    InterlockedIncrement(&dev->frames_rx);
    PostMessage(dev->notify_hwnd, WM_DEV_IO_UPDATE, 0, 0);
}

/* ─── Packet receive thread (DCP + cyclic RX) ────────────────────────────── */
static DWORD WINAPI dev_pkt_thread(LPVOID param)
{
    DevState *dev = (DevState *)param;
    uint8_t buf[2048];

    dev_log(dev, "Packet thread started");

    while (WaitForSingleObject(dev->stop_event, 0) == WAIT_TIMEOUT) {
        int n = frameio_recv(dev->pcap, buf, sizeof(buf), 10);
        if (n < 16) continue;

        /* EtherType must be 0x8892 */
        if (buf[12] != 0x88 || buf[13] != 0x92) continue;

        uint16_t fid = pn_get_u16be(buf, 14);

        if (fid == FID_DCP_IDENTIFY) {
            dcp_handle_identify(dev, buf, n);
        } else if (fid == FID_DCP_SET) {
            dcp_handle_set(dev, buf, n);
        } else if (dev->state == DEV_CYCLIC && fid == dev->output_frame_id) {
            cyclic_handle_rx(dev, buf, n);
        }
    }

    dev_log(dev, "Packet thread stopped");
    return 0;
}

/* ─── RPC helpers ────────────────────────────────────────────────────────── */

/* Object UUID for Profinet IO: DEA00001-6C97-11D1-8271-00A02442DF7D */
static const uint8_t PN_OBJ_UUID[16] = {
    0x00, 0x00, 0xa0, 0xde, 0x97, 0x6c, 0xd1, 0x11,
    0x82, 0x71, 0x00, 0xa0, 0x24, 0x42, 0xdf, 0x7d
};

/* Write DCE/RPC response header into buf[0..23].
 * callid: echoed from request (4 bytes).
 * opnum:  operation number.
 * Returns 24 (header size). */
static int rpc_write_hdr(uint8_t *buf, const uint8_t callid[4], uint16_t opnum,
                         uint8_t pkt_type)
{
    memset(buf, 0, 24);
    buf[0]  = 4;          /* version */
    buf[1]  = 0;          /* minor version */
    buf[2]  = pkt_type;   /* 0=request, 2=response */
    buf[3]  = 0x22;       /* flags: last frag + frag */
    buf[4]  = 0x10;       /* data representation: LE, ASCII, IEEE */
    buf[5]  = 0x00;
    buf[6]  = 0x00;
    buf[7]  = 0x00;
    /* frag_length at [8..9] — filled later */
    /* auth_length at [10..11] = 0 */
    memcpy(buf + 12, callid, 4); /* call_id */
    /* alloc_hint at [16..19] = 0 */
    /* context_id at [20..21] = 0 */
    pn_put_u16le(buf, 22, opnum);
    return 24;
}

static void rpc_put_frag_len(uint8_t *buf, int total_len)
{
    buf[8] = (uint8_t)(total_len);
    buf[9] = (uint8_t)(total_len >> 8);
}

/* ─── RPC ConnectRequest handler ─────────────────────────────────────────── */
static void rpc_handle_connect(DevState *dev, const uint8_t *buf, int len,
                               const struct sockaddr_in *from)
{
    /* Payload starts at offset 56 (24 RPC hdr + 16 ObjUUID + 16 IfaceUUID) */
    const uint8_t *payload = buf + 56;
    int plen = len - 56;
    if (plen < 4) return;

    /* Reset AR state */
    memset(dev->ar_uuid,   0, sizeof(dev->ar_uuid));
    dev->session_key   = 0;
    dev->output_frame_id = 0;
    dev->input_frame_id  = 0;
    dev->iocr_ref_out  = 0;
    dev->iocr_ref_in   = 0;
    dev->send_clock_factor = 128;
    dev->data_length   = 4;

    int pos = 0;
    while (pos + 6 <= plen) {
        uint16_t blk_type = pn_get_u16be(payload, pos);
        uint16_t blk_len  = pn_get_u16be(payload, pos + 2);
        if (blk_len == 0) break; /* malformed — prevent infinite loop */

        int data_start = pos + 4; /* block data starts after type(2)+len(2) */
        int blk_end    = data_start + (int)blk_len;
        if (blk_end > plen) break; /* block extends past packet end */

        if (blk_type == 0x0101 && blk_len >= 28) {
            /* ARBlockReq: ver(2) pad(2) ARType(2) ARUUID(16) SessionKey(2) CtrlMAC(6) ... */
            memcpy(dev->ar_uuid, payload + data_start + 4, 16);
            dev->session_key = pn_get_u16be(payload, data_start + 20);
            memcpy(dev->ctrl_mac, payload + data_start + 22, 6);
        } else if (blk_type == 0x0102 && blk_len >= 14) {
            /* IOCRBlockReq: ver(2) IOCRType(2) IOCRRef(2) RT_Class(2) DataLength(2) FrameID(2) ClockFactor(2)... */
            uint16_t iocr_type = pn_get_u16be(payload, data_start + 2);
            uint16_t iocr_ref  = pn_get_u16be(payload, data_start + 4);
            uint16_t data_len  = pn_get_u16be(payload, data_start + 8);
            uint16_t frame_id  = pn_get_u16be(payload, data_start + 10);
            uint16_t clock_f   = (blk_len >= 14) ?
                                 pn_get_u16be(payload, data_start + 12) : 128;

            if (iocr_type == 1) { /* Output (controller→device) */
                dev->output_frame_id   = frame_id;
                dev->iocr_ref_out      = iocr_ref;
                dev->data_length       = data_len;
                dev->send_clock_factor = clock_f ? clock_f : 128;
            } else if (iocr_type == 2) { /* Input (device→controller) */
                dev->input_frame_id = frame_id;
                dev->iocr_ref_in    = iocr_ref;
            }
        }

        pos = blk_end;
        if (blk_end & 1) pos++; /* pad to even */
    }

    dev_log(dev, "RPC Connect: AR_UUID set, ctrl_mac=%02X:%02X:%02X:%02X:%02X:%02X",
            dev->ctrl_mac[0], dev->ctrl_mac[1], dev->ctrl_mac[2],
            dev->ctrl_mac[3], dev->ctrl_mac[4], dev->ctrl_mac[5]);
    dev_log(dev, "  OutputFrameID=0x%04X InputFrameID=0x%04X ClockFactor=%u",
            dev->output_frame_id, dev->input_frame_id, dev->send_clock_factor);

    /* Build ConnectResponse */
    uint8_t resp[512];
    int rpos = 0;

    /* RPC header (24B) — fill later, need length first */
    rpos = rpc_write_hdr(resp, buf + 12, 0, 2);

    /* ObjUUID (16B) */
    memcpy(resp + rpos, PN_OBJ_UUID, 16); rpos += 16;

    /* Return code 4B = 0 */
    memset(resp + rpos, 0, 4); rpos += 4;

    /* AR block response 0x8101 */
    int blk_start = rpos;
    pn_put_u16be(resp, rpos, 0x8101); rpos += 2; /* type */
    int blk_len_off = rpos; rpos += 2;            /* len placeholder */
    resp[rpos++] = 0x01; resp[rpos++] = 0x00;    /* version */
    pn_put_u16be(resp, rpos, 0x0006); rpos += 2;  /* ARType = IOAR */
    memcpy(resp + rpos, dev->ar_uuid, 16); rpos += 16;
    pn_put_u16be(resp, rpos, dev->session_key); rpos += 2;
    memcpy(resp + rpos, dev->local_mac, 6); rpos += 6;
    pn_put_u16be(resp, rpos, 0x8894); rpos += 2;  /* UDP port */
    pn_put_u16be(resp, rpos, 0); rpos += 2;        /* name_len */
    pn_put_u16be(resp, blk_len_off, (uint16_t)(rpos - blk_start - 4));

    /* IOCRBlockRes for output IOCR (type 1) 0x8102 */
    blk_start = rpos;
    pn_put_u16be(resp, rpos, 0x8102); rpos += 2;
    blk_len_off = rpos; rpos += 2;
    resp[rpos++] = 0x01; resp[rpos++] = 0x00;    /* version */
    pn_put_u16be(resp, rpos, 0x0001); rpos += 2;  /* IOCRType = output */
    pn_put_u16be(resp, rpos, dev->iocr_ref_out); rpos += 2;
    pn_put_u16be(resp, rpos, dev->output_frame_id); rpos += 2;
    pn_put_u16be(resp, blk_len_off, (uint16_t)(rpos - blk_start - 4));

    /* IOCRBlockRes for input IOCR (type 2) */
    blk_start = rpos;
    pn_put_u16be(resp, rpos, 0x8102); rpos += 2;
    blk_len_off = rpos; rpos += 2;
    resp[rpos++] = 0x01; resp[rpos++] = 0x00;
    pn_put_u16be(resp, rpos, 0x0002); rpos += 2;  /* IOCRType = input */
    pn_put_u16be(resp, rpos, dev->iocr_ref_in); rpos += 2;
    pn_put_u16be(resp, rpos, dev->input_frame_id); rpos += 2;
    pn_put_u16be(resp, blk_len_off, (uint16_t)(rpos - blk_start - 4));

    /* AlarmCRBlockRes 0x8103 */
    blk_start = rpos;
    pn_put_u16be(resp, rpos, 0x8103); rpos += 2;
    blk_len_off = rpos; rpos += 2;
    resp[rpos++] = 0x01; resp[rpos++] = 0x00;
    pn_put_u16be(resp, rpos, 0x0001); rpos += 2;  /* AlarmCRType */
    pn_put_u16be(resp, rpos, 0x0001); rpos += 2;  /* LocalAlarmRef */
    pn_put_u16be(resp, rpos, 200);    rpos += 2;   /* MaxAlarmDataLength */
    pn_put_u16be(resp, blk_len_off, (uint16_t)(rpos - blk_start - 4));

    /* ModuleDiffBlock 0x8104 */
    blk_start = rpos;
    pn_put_u16be(resp, rpos, 0x8104); rpos += 2;
    blk_len_off = rpos; rpos += 2;
    resp[rpos++] = 0x01; resp[rpos++] = 0x00;
    pn_put_u16be(resp, rpos, 1); rpos += 2;  /* NumberOfAPIs */
    pn_put_u32be(resp, rpos, 0); rpos += 4;  /* API = 0 */
    pn_put_u16be(resp, rpos, 0); rpos += 2;  /* NumberOfModules */
    pn_put_u16be(resp, blk_len_off, (uint16_t)(rpos - blk_start - 4));

    rpc_put_frag_len(resp, rpos);

    dev->ctrl_addr = *from;
    dev->state = DEV_CONNECTED;
    PostMessage(dev->notify_hwnd, WM_DEV_STATE_CHANGED, 0, 0);

    sendto(dev->rpc_sock, (char *)resp, rpos, 0,
           (struct sockaddr *)&dev->ctrl_addr, sizeof(dev->ctrl_addr));
}

/* ─── RPC Write (IODWrite) handler — respond OK ───────────────────────────── */
static void rpc_handle_write(DevState *dev, const uint8_t *buf, int len,
                             const struct sockaddr_in *from)
{
    (void)len;
    uint8_t resp[128];
    int rpos = rpc_write_hdr(resp, buf + 12, 5, 2);

    /* ObjUUID */
    memcpy(resp + rpos, PN_OBJ_UUID, 16); rpos += 16;

    /* Return code 0 */
    memset(resp + rpos, 0, 4); rpos += 4;

    /* IODWriteResHeader: BlockType=0x8008, BlockLen=0x0038, ver, seqnum, ARUUID,
     *   API, SlotNumber, SubslotNumber, padding, Index, RecordDataLen, AdditionalValue,
     *   padding, Status.  Keep it simple: 64 bytes of zeros except type/len */
    pn_put_u16be(resp, rpos, 0x8008); rpos += 2;
    pn_put_u16be(resp, rpos, 0x003C); rpos += 2;
    memset(resp + rpos, 0, 60); rpos += 60;

    rpc_put_frag_len(resp, rpos);
    sendto(dev->rpc_sock, (char *)resp, rpos, 0,
           (struct sockaddr *)from, sizeof(*from));
}

/* ─── Cyclic TX thread ────────────────────────────────────────────────────── */
static DWORD WINAPI dev_cyclic_thread(LPVOID param)
{
    DevState *dev = (DevState *)param;

    uint64_t period_ns = (uint64_t)dev->send_clock_factor * 31250ULL;
    uint64_t next_ns   = pn_time_ns() + period_ns;
    uint16_t cc = 0;

    dev_log(dev, "Cyclic thread started (period=%ums)", (unsigned)(period_ns/1000000));

    while (WaitForSingleObject(dev->stop_event, 0) == WAIT_TIMEOUT) {
        /* Busy-spin for precise timing */
        while (pn_time_ns() < next_ns) Sleep(0);

        uint8_t frame[64];
        memset(frame, 0, sizeof(frame));

        /* Ethernet header */
        memcpy(frame,     dev->ctrl_mac,  6);
        memcpy(frame + 6, dev->local_mac, 6);
        frame[12] = 0x88; frame[13] = 0x92;

        /* FrameID */
        frame[14] = (uint8_t)(dev->input_frame_id >> 8);
        frame[15] = (uint8_t)(dev->input_frame_id);

        /* IOPS = GOOD */
        frame[16] = 0x80;

        /* ZSW1 / NIST_A little-endian */
        EnterCriticalSection(&dev->io_lock);
        frame[17] = (uint8_t)(dev->zsw1);
        frame[18] = (uint8_t)(dev->zsw1 >> 8);
        frame[19] = (uint8_t)(dev->nist);
        frame[20] = (uint8_t)(dev->nist  >> 8);
        LeaveCriticalSection(&dev->io_lock);

        /* IOCS = GOOD */
        frame[21] = 0x80;

        /* CycleCounter big-endian */
        frame[22] = (uint8_t)(cc >> 8);
        frame[23] = (uint8_t)(cc);
        cc++;

        /* DataStatus: valid(0x04) + run(0x10) + primary(0x20) */
        frame[24] = 0x35;
        frame[25] = 0x00; /* TransferStatus */

        frameio_send(dev->pcap, frame, 60);
        InterlockedIncrement(&dev->frames_tx);

        next_ns += period_ns;
    }

    dev_log(dev, "Cyclic thread stopped");
    return 0;
}

/* ─── RPC DControl handler ────────────────────────────────────────────────── */
static void rpc_handle_dcontrol(DevState *dev, const uint8_t *buf, int len,
                                const struct sockaddr_in *from)
{
    /* Payload at +56; ControlCommand is at payload[24..25] = buf[80..81] */
    if (len < 82) return;
    const uint8_t *payload = buf + 56;

    /* ControlBlockConnect: BlockType(2) BlockLen(2) BlockVer(2) padding(2) ARUUID(16)
     * SessionKey(2) padding(2) ControlCommand(2) ControlBlockProperties(2) */
    uint16_t cmd = pn_get_u16be(payload, 24);

    /* ApplicationReady = 0x0004 */
    if (cmd & 0x0004) {
        dev->state = DEV_CYCLIC;
        PostMessage(dev->notify_hwnd, WM_DEV_STATE_CHANGED, 0, 0);
        dev_log(dev, "DControl ApplicationReady — starting cyclic");

        /* Start cyclic thread */
        ResetEvent(dev->stop_event);  /* shouldn't be set, but be safe */
        dev->cyclic_thread = CreateThread(NULL, 0, dev_cyclic_thread, dev, 0, NULL);
    }

    /* Send DControl response (OpNum=2) */
    uint8_t resp[256];
    int rpos = rpc_write_hdr(resp, buf + 12, 2, 2);
    memcpy(resp + rpos, PN_OBJ_UUID, 16); rpos += 16;
    memset(resp + rpos, 0, 4); rpos += 4; /* status OK */

    /* ControlBlockConnect response */
    pn_put_u16be(resp, rpos, 0x8110); rpos += 2;
    pn_put_u16be(resp, rpos, 28);     rpos += 2; /* block len */
    resp[rpos++] = 0x01; resp[rpos++] = 0x00;    /* version */
    resp[rpos++] = 0x00; resp[rpos++] = 0x00;    /* padding */
    memcpy(resp + rpos, dev->ar_uuid, 16); rpos += 16;
    pn_put_u16be(resp, rpos, dev->session_key); rpos += 2;
    resp[rpos++] = 0x00; resp[rpos++] = 0x00;    /* padding */
    pn_put_u16be(resp, rpos, cmd); rpos += 2;     /* echo command */
    pn_put_u16be(resp, rpos, 0);   rpos += 2;     /* properties */

    rpc_put_frag_len(resp, rpos);
    sendto(dev->rpc_sock, (char *)resp, rpos, 0,
           (struct sockaddr *)from, sizeof(*from));

    /* Send CControl to controller (OpNum=3, pkt_type=0=request) */
    if (cmd & 0x0004) {
        uint8_t cc_req[256];
        static uint32_t ccontrol_callid = 0x10000000;
        ccontrol_callid++;

        int cpos = rpc_write_hdr(cc_req, (uint8_t *)&ccontrol_callid, 3, 0);
        memcpy(cc_req + cpos, PN_OBJ_UUID, 16); cpos += 16;

        /* ControlBlockConnect 0x0110 */
        pn_put_u16be(cc_req, cpos, 0x0110); cpos += 2;
        pn_put_u16be(cc_req, cpos, 28);     cpos += 2;
        cc_req[cpos++] = 0x01; cc_req[cpos++] = 0x00;
        cc_req[cpos++] = 0x00; cc_req[cpos++] = 0x00;
        memcpy(cc_req + cpos, dev->ar_uuid, 16); cpos += 16;
        pn_put_u16be(cc_req, cpos, dev->session_key); cpos += 2;
        cc_req[cpos++] = 0x00; cc_req[cpos++] = 0x00;
        pn_put_u16be(cc_req, cpos, 0x0004); cpos += 2; /* ApplicationReady */
        pn_put_u16be(cc_req, cpos, 0x0000); cpos += 2;

        rpc_put_frag_len(cc_req, cpos);
        sendto(dev->rpc_sock, (char *)cc_req, cpos, 0,
               (struct sockaddr *)&dev->ctrl_addr, sizeof(dev->ctrl_addr));
        dev_log(dev, "CControl sent to controller");
    }
}

/* ─── RPC Release handler ────────────────────────────────────────────────── */
static void rpc_handle_release(DevState *dev, const uint8_t *buf,
                               const struct sockaddr_in *from)
{
    /* Stop cyclic thread */
    if (dev->cyclic_thread) {
        SetEvent(dev->stop_event);
        WaitForSingleObject(dev->cyclic_thread, 1000);
        CloseHandle(dev->cyclic_thread);
        dev->cyclic_thread = NULL;
        ResetEvent(dev->stop_event);
    }

    dev->state = DEV_LISTENING;
    PostMessage(dev->notify_hwnd, WM_DEV_STATE_CHANGED, 0, 0);
    dev_log(dev, "RPC Release — returning to LISTENING");

    /* Send Release response */
    uint8_t resp[128];
    int rpos = rpc_write_hdr(resp, buf + 12, 1, 2);
    memcpy(resp + rpos, PN_OBJ_UUID, 16); rpos += 16;
    memset(resp + rpos, 0, 4); rpos += 4; /* status OK */

    /* ReleaseBlock response 0x8111 */
    pn_put_u16be(resp, rpos, 0x8111); rpos += 2;
    pn_put_u16be(resp, rpos, 28);     rpos += 2;
    resp[rpos++] = 0x01; resp[rpos++] = 0x00;
    resp[rpos++] = 0x00; resp[rpos++] = 0x00;
    memcpy(resp + rpos, dev->ar_uuid, 16); rpos += 16;
    pn_put_u16be(resp, rpos, dev->session_key); rpos += 2;
    resp[rpos++] = 0x00; resp[rpos++] = 0x00;
    pn_put_u16be(resp, rpos, 0x0010); rpos += 2; /* DoneWithPeer */
    pn_put_u16be(resp, rpos, 0);      rpos += 2;

    rpc_put_frag_len(resp, rpos);
    sendto(dev->rpc_sock, (char *)resp, rpos, 0,
           (struct sockaddr *)from, sizeof(*from));
}

/* ─── RPC server thread ───────────────────────────────────────────────────── */
static DWORD WINAPI dev_rpc_thread(LPVOID param)
{
    DevState *dev = (DevState *)param;
    uint8_t  buf[2048];
    struct sockaddr_in from;
    int from_len = sizeof(from);

    dev_log(dev, "RPC thread started (UDP port %d)", RPC_PORT);

    DWORD timeout_ms = 20;
    setsockopt(dev->rpc_sock, SOL_SOCKET, SO_RCVTIMEO,
               (char *)&timeout_ms, sizeof(timeout_ms));

    while (WaitForSingleObject(dev->stop_event, 0) == WAIT_TIMEOUT) {
        from_len = sizeof(from);
        int n = recvfrom(dev->rpc_sock, (char *)buf, sizeof(buf), 0,
                         (struct sockaddr *)&from, &from_len);
        if (n < 24) continue;

        uint16_t opnum = pn_get_u16le(buf, 22);

        switch (opnum) {
        case 0: rpc_handle_connect(dev, buf, n, &from);  break;
        case 1: rpc_handle_release(dev, buf, &from);     break;
        case 2: rpc_handle_dcontrol(dev, buf, n, &from); break;
        case 5: rpc_handle_write(dev, buf, n, &from);    break;
        default:
            dev_log(dev, "RPC unknown opnum %u", opnum);
            break;
        }
    }

    dev_log(dev, "RPC thread stopped");
    return 0;
}

/* ─── Public API ──────────────────────────────────────────────────────────── */

int dev_init(DevState *dev, const char *adapter, HWND hwnd)
{
    memset(dev, 0, sizeof(*dev));
    dev->notify_hwnd = hwnd;
    dev->vendor_id   = 0x002A;
    dev->device_id   = 0x0001;
    dev->auto_sim    = 1;
    dev->zsw1        = 0x0F21; /* Ready to switch on */
    strcpy(dev->station_name, "vfd-simulator");

    InitializeCriticalSection(&dev->io_lock);
    InitializeCriticalSection(&dev->stats_lock);

    dev->stop_event = CreateEvent(NULL, TRUE, FALSE, NULL);
    if (!dev->stop_event) {
        snprintf(dev->last_error, sizeof(dev->last_error), "CreateEvent failed: %lu", GetLastError());
        return -1;
    }

    char errbuf[256] = {0};
    dev->pcap = frameio_open(adapter, dev->local_mac, errbuf);
    if (!dev->pcap) {
        snprintf(dev->last_error, sizeof(dev->last_error), "frameio_open: %s", errbuf);
        return -1;
    }

    /* Bind RPC UDP socket */
    WSADATA wsa;
    WSAStartup(MAKEWORD(2,2), &wsa);

    dev->rpc_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (dev->rpc_sock == INVALID_SOCKET) {
        snprintf(dev->last_error, sizeof(dev->last_error), "socket: %d", WSAGetLastError());
        frameio_close(dev->pcap); dev->pcap = NULL;
        return -1;
    }

    struct sockaddr_in sa = {0};
    sa.sin_family      = AF_INET;
    sa.sin_addr.s_addr = INADDR_ANY;
    sa.sin_port        = htons(RPC_PORT);

    if (bind(dev->rpc_sock, (struct sockaddr *)&sa, sizeof(sa)) != 0) {
        snprintf(dev->last_error, sizeof(dev->last_error), "bind port %d: %d", RPC_PORT, WSAGetLastError());
        closesocket(dev->rpc_sock); dev->rpc_sock = INVALID_SOCKET;
        frameio_close(dev->pcap); dev->pcap = NULL;
        return -1;
    }

    InterlockedExchange(&dev->running, 1);

    dev->pkt_thread = CreateThread(NULL, 0, dev_pkt_thread, dev, 0, NULL);
    dev->rpc_thread = CreateThread(NULL, 0, dev_rpc_thread, dev, 0, NULL);

    dev->state = DEV_LISTENING;
    PostMessage(hwnd, WM_DEV_STATE_CHANGED, 0, 0);

    dev_log(dev, "Device started — MAC=%02X:%02X:%02X:%02X:%02X:%02X",
            dev->local_mac[0], dev->local_mac[1], dev->local_mac[2],
            dev->local_mac[3], dev->local_mac[4], dev->local_mac[5]);
    return 0;
}

void dev_stop(DevState *dev)
{
    if (InterlockedCompareExchange(&dev->running, 0, 1) == 0) return;
    SetEvent(dev->stop_event);

    HANDLE threads[3];
    DWORD n = 0;
    if (dev->pkt_thread)    threads[n++] = dev->pkt_thread;
    if (dev->rpc_thread)    threads[n++] = dev->rpc_thread;
    if (dev->cyclic_thread) threads[n++] = dev->cyclic_thread;

    if (n > 0) WaitForMultipleObjects(n, threads, TRUE, 3000);

    if (dev->pkt_thread)    { CloseHandle(dev->pkt_thread);    dev->pkt_thread    = NULL; }
    if (dev->rpc_thread)    { CloseHandle(dev->rpc_thread);    dev->rpc_thread    = NULL; }
    if (dev->cyclic_thread) { CloseHandle(dev->cyclic_thread); dev->cyclic_thread = NULL; }

    if (dev->pcap)    { frameio_close(dev->pcap); dev->pcap = NULL; }
    if (dev->rpc_sock != INVALID_SOCKET) {
        closesocket(dev->rpc_sock);
        dev->rpc_sock = INVALID_SOCKET;
    }

    dev->state = DEV_IDLE;
    PostMessage(dev->notify_hwnd, WM_DEV_STATE_CHANGED, 0, 0);
}

void dev_cleanup(DevState *dev)
{
    dev_stop(dev);
    if (dev->stop_event) { CloseHandle(dev->stop_event); dev->stop_event = NULL; }
    DeleteCriticalSection(&dev->io_lock);
    DeleteCriticalSection(&dev->stats_lock);
}

void dev_set_ip(DevState *dev, uint32_t ip, uint32_t subnet, uint32_t gw)
{
    dev->ip      = ip;
    dev->subnet  = subnet;
    dev->gateway = gw;
}

void dev_set_name(DevState *dev, const char *name)
{
    EnterCriticalSection(&dev->io_lock);
    pn_strlcpy(dev->station_name, name, sizeof(dev->station_name));
    LeaveCriticalSection(&dev->io_lock);
}

void dev_set_tx_manual(DevState *dev, uint16_t zsw1, uint16_t nist)
{
    EnterCriticalSection(&dev->io_lock);
    dev->auto_sim = 0;
    dev->zsw1 = zsw1;
    dev->nist  = nist;
    LeaveCriticalSection(&dev->io_lock);
}

void dev_set_auto_sim(DevState *dev, int enable)
{
    EnterCriticalSection(&dev->io_lock);
    dev->auto_sim = enable;
    if (enable) compute_profidrive(dev);
    LeaveCriticalSection(&dev->io_lock);
}

void dev_get_io(DevState *dev, uint16_t *stw1, uint16_t *nsoll,
                uint16_t *zsw1, uint16_t *nist)
{
    EnterCriticalSection(&dev->io_lock);
    if (stw1)  *stw1  = dev->stw1;
    if (nsoll) *nsoll = dev->nsoll;
    if (zsw1)  *zsw1  = dev->zsw1;
    if (nist)  *nist  = dev->nist;
    LeaveCriticalSection(&dev->io_lock);
}

void dev_get_stats(DevState *dev, uint64_t *rx, uint64_t *tx, uint64_t *missed)
{
    if (rx)     *rx     = (uint64_t)dev->frames_rx;
    if (tx)     *tx     = (uint64_t)dev->frames_tx;
    if (missed) *missed = (uint64_t)dev->missed;
}

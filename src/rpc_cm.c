#include "rpc_cm.h"
#include "frame_io.h"
#include "utils.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#ifdef _WIN32
#include <winsock2.h>
#include <windows.h>
#include <ws2tcpip.h>
#else
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
typedef int SOCKET;
#define INVALID_SOCKET (-1)
#define closesocket close
#endif

/* ─── PNIO CM Interface UUID: {DEA00001-6C97-11D1-8271-00A02442DF7D} ─────── */
/* (used in object_uuid field of RPC header) */
static const uint8_t PNIO_CM_IF_UUID[16] = {
    0x01, 0x00, 0xA0, 0xDE,
    0x97, 0x6C, 0xD1, 0x11,
    0x82, 0x71, 0x00, 0xA0,
    0x24, 0x42, 0xDF, 0x7D
};

/* ─── RPC buffer size ────────────────────────────────────────────────────── */
#define RPC_BUF_SIZE  2048
#define RPC_HDR_SIZE  80   /* fixed RPC header up to payload */

/* ─── RPC Header layout (little-endian fields, per DCE/RPC spec) ─────────── */
/*  0: version(1)  1: ptype(1)  2: flags1(1)  3: flags2(1)
 *  4: data_rep[4]  8: frag_len(2)  10: auth_len(2)  12: call_id(4)
 * 16: alloc_hint(4)  20: p_cont_elem(2=0)  22: opnum(2)
 * 24: object_uuid[16]
 * 40: payload starts */
#define RPC_OFF_VERSION   0
#define RPC_OFF_PTYPE     1
#define RPC_OFF_FLAGS1    2
#define RPC_OFF_FLAGS2    3
#define RPC_OFF_DATAREP   4
#define RPC_OFF_FRAGLEN   8
#define RPC_OFF_AUTHLEN   10
#define RPC_OFF_CALLID    12
#define RPC_OFF_ALLOCHINT 16
#define RPC_OFF_CTXID     20
#define RPC_OFF_OPNUM     22
#define RPC_OFF_OBJUUID   24
#define RPC_OFF_PAYLOAD   40

static void rpc_build_header(uint8_t *buf, uint8_t ptype, uint16_t opnum,
                               uint32_t call_id, const uint8_t obj_uuid[16])
{
    memset(buf, 0, RPC_OFF_PAYLOAD);
    buf[RPC_OFF_VERSION] = RPC_VERSION;
    buf[RPC_OFF_PTYPE]   = ptype;
    buf[RPC_OFF_FLAGS1]  = RPC_FLAG_LAST_FRAG | RPC_FLAG_NO_FACK;
    buf[RPC_OFF_FLAGS2]  = 0;
    /* Data representation: little-endian, ASCII, IEEE float */
    buf[RPC_OFF_DATAREP]     = 0x10;
    buf[RPC_OFF_DATAREP + 1] = 0x00;
    buf[RPC_OFF_DATAREP + 2] = 0x00;
    buf[RPC_OFF_DATAREP + 3] = 0x00;
    /* frag_len filled by caller after payload known */
    pn_put_u32le(buf, RPC_OFF_CALLID, call_id);
    pn_put_u16le(buf, RPC_OFF_CTXID, 0);
    pn_put_u16le(buf, RPC_OFF_OPNUM, opnum);
    if (obj_uuid)
        memcpy(buf + RPC_OFF_OBJUUID, obj_uuid, 16);
}

/* ─── Block builder helpers ──────────────────────────────────────────────── */
/* Write a Profinet block header: BlockType(2), BlockLength(2), Version(2).
 * BlockLength = total block size - 4 (excludes BlockType and BlockLength). */
static int pn_block_hdr(uint8_t *buf, int off, uint16_t btype,
                         uint16_t blklen)
{
    pn_put_u16be(buf, off, btype);   off += 2;
    pn_put_u16be(buf, off, blklen);  off += 2;
    buf[off++] = 0x01; /* version high */
    buf[off++] = 0x00; /* version low  */
    return off;
}

/* ─── Build ARBlockReq ───────────────────────────────────────────────────── */
/* Total: 6(hdr)+52(body) = 58 bytes */
static int build_ar_block(uint8_t *buf, int off, const PN_Context *ctx)
{
    /* Block body: ARType(2) ARUUID(16) SessionKey(2) CMInitiatorMAC(6)
     *             CMInitiatorObjectUUID(16) ARProperties(4) CMInitiatorActivity(2)
     *             CMInitiatorUDPPort(2) StationNameLength(2) StationName */
    /* Block length = total - 4 (type+len fields) = body including ver */
    const char *station_name = "profinet-controller";
    int sname_len = (int)strlen(station_name);
    /* body = 2(ver)+2(ARType)+16(ARUUID)+2(SessionKey)+6(MAC)+16(UUID)
     *        +4(ARProp)+2(ActTimeout)+2(UDPPort)+2(SNLen)+sname_len */
    int body_len = 2 + 2 + 16 + 2 + 6 + 16 + 4 + 2 + 2 + 2 + sname_len;
    off = pn_block_hdr(buf, off, PN_BLK_AR_REQ, (uint16_t)(body_len - 2));
    /* ARType: 0x0001 = IO Controller */
    pn_put_u16be(buf, off, 0x0001); off += 2;
    /* ARUUID */
    memcpy(buf + off, ctx->ar_uuid, 16); off += 16;
    /* SessionKey */
    pn_put_u16be(buf, off, (uint16_t)ctx->session_key); off += 2;
    /* CMInitiatorMAC */
    memcpy(buf + off, ctx->local_mac, 6); off += 6;
    /* CMInitiatorObjectUUID = activity UUID */
    memcpy(buf + off, ctx->activity_uuid, 16); off += 16;
    /* ARProperties: bit 1=supervisor takeover allowed, others=0 */
    pn_put_u32be(buf, off, 0x00000000); off += 4;
    /* CMInitiatorActivityTimeout (seconds × 100ms): 50 = 5s */
    pn_put_u16be(buf, off, 50); off += 2;
    /* CMInitiatorUDPPort: use port 34964 */
    pn_put_u16be(buf, off, RPC_PNIO_PORT); off += 2;
    /* StationNameLength */
    pn_put_u16be(buf, off, (uint16_t)sname_len); off += 2;
    memcpy(buf + off, station_name, (size_t)sname_len); off += sname_len;
    return off;
}

/* ─── Build IOCRBlockReq ─────────────────────────────────────────────────── */
static int build_iocr_block(uint8_t *buf, int off, uint16_t iocr_type,
                              uint16_t frame_id, uint16_t data_len,
                              uint16_t send_clock, uint16_t reduction_ratio,
                              uint16_t watchdog_factor,
                              uint32_t module_ident, uint32_t submodule_ident,
                              uint16_t slot, uint16_t subslot)
{
    /* IOCR body: IOCRType(2) IOCRReference(2) LT(2) IOCRProperties(4)
     *   DataLength(2) FrameID(2) SendClockFactor(2) ReductionRatio(2)
     *   Phase(2) Sequence(2) FrameSendOffset(4) WatchdogFactor(2)
     *   DataHoldFactor(2) IOCRTagHeader(2) IOCRMulticastMACAdd(6)
     *   NumberOfAPIs(2) [API(4) NumberOfIODataObjects(2)
     *     [SlotNumber(2) SubslotNumber(2) FrameOffset(2)]] */
    /* We use 1 API, 1 IODataObject (simplified) */
    int body_len = 2 + 2 + 2 + 4 + 2 + 2 + 2 + 2 + 2 + 2 + 4 + 2 + 2 + 2 + 6
                 + 2                    /* NumberOfAPIs */
                 + 4 + 2 + (2 + 2 + 2) /* API + IODS count + 1 data obj */
                 + 2                    /* NumberOfIOCS */
                 + (2 + 2 + 2)         /* 1 IOCS object */
                 + 2;                   /* version field */
    off = pn_block_hdr(buf, off, PN_BLK_IOCR_REQ, (uint16_t)(body_len - 2));
    pn_put_u16be(buf, off, iocr_type);   off += 2;  /* IOCRType */
    pn_put_u16be(buf, off, iocr_type == IOCR_TYPE_OUTPUT ? 1 : 2); off += 2; /* IOCRRef */
    pn_put_u16be(buf, off, ETHERTYPE_PROFINET); off += 2; /* LT */
    /* IOCRProperties: RT_CLASS_2 (bit 2..0 = 010) */
    pn_put_u32be(buf, off, 0x00000002); off += 4;
    pn_put_u16be(buf, off, data_len);    off += 2;  /* DataLength */
    pn_put_u16be(buf, off, frame_id);    off += 2;  /* FrameID */
    pn_put_u16be(buf, off, send_clock);  off += 2;  /* SendClockFactor */
    pn_put_u16be(buf, off, reduction_ratio); off += 2;
    pn_put_u16be(buf, off, 1);           off += 2;  /* Phase */
    pn_put_u16be(buf, off, 0);           off += 2;  /* Sequence */
    pn_put_u32be(buf, off, 0xFFFFFFFF);  off += 4;  /* FrameSendOffset: best effort */
    pn_put_u16be(buf, off, watchdog_factor); off += 2;
    pn_put_u16be(buf, off, watchdog_factor); off += 2; /* DataHoldFactor */
    pn_put_u16be(buf, off, 0xC000);      off += 2;  /* IOCRTagHeader: no VLAN */
    memset(buf + off, 0, 6);             off += 6;  /* IOCRMulticastMACAdd */
    /* NumberOfAPIs */
    pn_put_u16be(buf, off, 1); off += 2;
    pn_put_u32be(buf, off, 0); off += 4; /* API = 0 */
    /* NumberOfIODataObjects */
    pn_put_u16be(buf, off, 1); off += 2;
    pn_put_u16be(buf, off, slot);    off += 2;
    pn_put_u16be(buf, off, subslot); off += 2;
    pn_put_u16be(buf, off, 0);       off += 2; /* FrameOffset */
    /* NumberOfIOCS */
    pn_put_u16be(buf, off, 1); off += 2;
    pn_put_u16be(buf, off, slot);    off += 2;
    pn_put_u16be(buf, off, subslot); off += 2;
    pn_put_u16be(buf, off, 0);       off += 2; /* IOCSFrameOffset */
    (void)module_ident; (void)submodule_ident;
    return off;
}

/* ─── Build ExpectedSubmoduleBlock ──────────────────────────────────────── */
static int build_exp_submod_block(uint8_t *buf, int off,
                                   const PN_ARConfig *cfg)
{
    /* Body: NumberOfAPIs(2) + API(4) + SlotNumber(2) + ModuleIdentNumber(4)
     *       + ModuleProperties(2) + NumberOfSubmodules(2)
     *       + SubslotNumber(2) + SubmoduleIdentNumber(4)
     *       + SubmoduleProperties(2) + DataDescription(2) + InputLength(2)
     *       + InputIOPS(1) + OutputLength(2) + OutputIOCS(1) */
    /* For simplicity, input_length = output_length from ar_cfg
     * use 4 each (Telegram 1: 4 bytes each direction) */
    uint16_t in_len  = 4;
    uint16_t out_len = 4;
    int body_len = 2 + 2 + 4 + 2 + 4 + 2 + 2 + 2 + 4 + 2 + 2 + 2 + 1 + 2 + 1;
    off = pn_block_hdr(buf, off, PN_BLK_EXP_SUBMOD, (uint16_t)(body_len - 2));
    /* NumberOfAPIs */
    pn_put_u16be(buf, off, 1); off += 2;
    pn_put_u32be(buf, off, cfg->api); off += 4;     /* API */
    pn_put_u16be(buf, off, cfg->slot); off += 2;    /* SlotNumber */
    pn_put_u32be(buf, off, cfg->module_ident); off += 4;
    pn_put_u16be(buf, off, 0x0000); off += 2;       /* ModuleProperties */
    pn_put_u16be(buf, off, 1); off += 2;            /* NumberOfSubmodules */
    pn_put_u16be(buf, off, cfg->subslot); off += 2;
    pn_put_u32be(buf, off, cfg->submodule_ident); off += 4;
    pn_put_u16be(buf, off, 0x0000); off += 2;       /* SubmoduleProperties */
    /* DataDescription: 1 = Input, 2 = Output */
    pn_put_u16be(buf, off, 1); off += 2;  /* use Input descriptor */
    pn_put_u16be(buf, off, in_len); off += 2;
    buf[off++] = 0x01; /* input_length_iops: 1 byte */
    pn_put_u16be(buf, off, out_len); off += 2;
    buf[off++] = 0x01; /* output_length_iocs: 1 byte */
    return off;
}

/* ─── Build AlarmCRBlock ─────────────────────────────────────────────────── */
static int build_alarm_cr_block(uint8_t *buf, int off)
{
    /* AlarmCRType(2) LT(2) AlarmCRProperties(4) RTA_TimeoutFactor(2)
     * RTA_RetryFactor(2) LocalAlarmReference(2) MaxAlarmDataLength(2)
     * AlarmCRTagHeader(2) */
    int body_len = 2 + 2 + 2 + 4 + 2 + 2 + 2 + 2 + 2;
    off = pn_block_hdr(buf, off, PN_BLK_ALARM_CR_REQ, (uint16_t)(body_len - 2));
    pn_put_u16be(buf, off, 0x0001); off += 2; /* AlarmCRType: controller */
    pn_put_u16be(buf, off, ETHERTYPE_PROFINET); off += 2;
    pn_put_u32be(buf, off, 0x00000000); off += 4; /* AlarmCRProperties */
    pn_put_u16be(buf, off, 200);        off += 2; /* RTA_TimeoutFactor: 200 × 100µs */
    pn_put_u16be(buf, off, 3);          off += 2; /* RTA_RetryFactor */
    pn_put_u16be(buf, off, 0x0001);     off += 2; /* LocalAlarmReference */
    pn_put_u16be(buf, off, 200);        off += 2; /* MaxAlarmDataLength */
    pn_put_u16be(buf, off, 0xC000);     off += 2; /* AlarmCRTagHeader */
    return off;
}

/* ─── Socket helpers ─────────────────────────────────────────────────────── */
#ifdef _WIN32
static int rpc_send(SOCKET s, const struct sockaddr_in *dst,
                    const uint8_t *buf, int len)
{
    return sendto(s, (const char *)buf, len, 0,
                  (const struct sockaddr *)dst, sizeof(*dst));
}

static int rpc_recv(SOCKET s, uint8_t *buf, int bufsz, int timeout_ms)
{
    struct timeval tv;
    tv.tv_sec  = timeout_ms / 1000;
    tv.tv_usec = (timeout_ms % 1000) * 1000;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char *)&tv, sizeof(tv));
    return recv(s, (char *)buf, bufsz, 0);
}
#else
static int rpc_send(int s, const struct sockaddr_in *dst,
                    const uint8_t *buf, int len)
{
    return (int)sendto(s, buf, (size_t)len, 0,
                       (const struct sockaddr *)dst, sizeof(*dst));
}
static int rpc_recv(int s, uint8_t *buf, int bufsz, int timeout_ms)
{
    struct timeval tv;
    tv.tv_sec  = timeout_ms / 1000;
    tv.tv_usec = (timeout_ms % 1000) * 1000;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (char *)&tv, sizeof(tv));
    return (int)recv(s, buf, (size_t)bufsz, 0);
}
#endif

/* ─── Init / cleanup ─────────────────────────────────────────────────────── */
int rpc_cm_init(PN_Context *ctx)
{
#ifdef _WIN32
    WSADATA wsaData;
    WSAStartup(MAKEWORD(2, 2), &wsaData);
    ctx->rpc_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (ctx->rpc_sock == INVALID_SOCKET) return PN_ERR_RPC_CONNECT;
#else
    ctx->rpc_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (ctx->rpc_sock < 0) return PN_ERR_RPC_CONNECT;
#endif
    /* Bind to local port (OS picks ephemeral) */
    struct sockaddr_in local = {0};
    local.sin_family      = AF_INET;
    local.sin_addr.s_addr = INADDR_ANY;
    local.sin_port        = 0;
    bind(ctx->rpc_sock, (struct sockaddr *)&local, sizeof(local));
    return PN_OK;
}

void rpc_cm_cleanup(PN_Context *ctx)
{
#ifdef _WIN32
    if (ctx->rpc_sock != INVALID_SOCKET) {
        closesocket(ctx->rpc_sock);
        ctx->rpc_sock = INVALID_SOCKET;
        WSACleanup();
    }
#else
    if (ctx->rpc_sock >= 0) {
        closesocket(ctx->rpc_sock);
        ctx->rpc_sock = -1;
    }
#endif
}

/* ─── Connect sequence ───────────────────────────────────────────────────── */
int rpc_cm_connect(PN_Context *ctx)
{
    uint8_t txbuf[RPC_BUF_SIZE];
    uint8_t rxbuf[RPC_BUF_SIZE];

    /* Generate fresh AR UUID and activity UUID */
    pn_uuid_generate(ctx->ar_uuid);
    pn_uuid_generate(ctx->activity_uuid);
    ctx->session_key  = 0x0001;
    ctx->rpc_call_id  = 1;

    /* Default frame IDs if not set by GSDML */
    if (ctx->output_frame_id == 0) ctx->output_frame_id = 0xC000;
    if (ctx->input_frame_id  == 0) ctx->input_frame_id  = 0xC001;

    const PN_ARConfig *cfg = &ctx->ar_cfg;
    uint16_t sc   = cfg->send_clock_factor ? cfg->send_clock_factor : 128;
    uint16_t rr   = cfg->reduction_ratio   ? cfg->reduction_ratio   : 1;
    uint16_t wdog = cfg->watchdog_factor    ? cfg->watchdog_factor   : 3;

    /* ── 1. Build ConnectRequest payload ─────────────────────────────────── */
    /* RPC header placeholder */
    memset(txbuf, 0, sizeof(txbuf));
    int payload_start = RPC_OFF_PAYLOAD;
    int off = payload_start;

    /* Blocks */
    off = build_ar_block(txbuf, off, ctx);
    off = build_iocr_block(txbuf, off, IOCR_TYPE_OUTPUT,
                            ctx->output_frame_id, 6 /* IOPS+4data+IOCS */,
                            sc, rr, wdog,
                            cfg->module_ident, cfg->submodule_ident,
                            cfg->slot, cfg->subslot);
    off = build_iocr_block(txbuf, off, IOCR_TYPE_INPUT,
                            ctx->input_frame_id, 6,
                            sc, rr, wdog,
                            cfg->module_ident, cfg->submodule_ident,
                            cfg->slot, cfg->subslot);
    off = build_exp_submod_block(txbuf, off, cfg);
    off = build_alarm_cr_block(txbuf, off);

    /* Fill RPC header */
    rpc_build_header(txbuf, RPC_PTYPE_REQUEST, RPC_OPNUM_CONNECT,
                     ctx->rpc_call_id++, PNIO_CM_IF_UUID);
    pn_put_u32le(txbuf, RPC_OFF_ALLOCHINT, (uint32_t)(off - payload_start));
    pn_put_u16le(txbuf, RPC_OFF_FRAGLEN,   (uint16_t)off);

    /* ── 2. Send to device port 34964 ────────────────────────────────────── */
    struct sockaddr_in dst = {0};
    dst.sin_family = AF_INET;
    dst.sin_port   = htons(RPC_PNIO_PORT);
    /* device_ip stored big-endian; inet_addr expects host order dotted string */
    char ip_str[16];
    pn_ip_to_str(ctx->device_ip, ip_str);
    dst.sin_addr.s_addr = inet_addr(ip_str);

    if (rpc_send(ctx->rpc_sock, &dst, txbuf, off) < 0) {
        pn_set_error(ctx, "RPC Connect: send failed");
        return PN_ERR_RPC_CONNECT;
    }
    pn_log("RPC ConnectRequest sent (%d bytes)", off);

    /* ── 3. Wait for ConnectResponse ─────────────────────────────────────── */
    int rlen = rpc_recv(ctx->rpc_sock, rxbuf, sizeof(rxbuf), 5000);
    if (rlen < RPC_OFF_PAYLOAD) {
        pn_set_error(ctx, "RPC Connect: no response");
        return PN_ERR_RPC_TIMEOUT;
    }
    if (rxbuf[RPC_OFF_PTYPE] == RPC_PTYPE_FAULT) {
        pn_set_error(ctx, "RPC Connect: device returned FAULT");
        return PN_ERR_RPC_CONNECT;
    }
    pn_log("RPC ConnectResponse received (%d bytes)", rlen);

    /* Parse ConnectResponse to extract negotiated frame IDs */
    {
        int p = RPC_OFF_PAYLOAD;
        while (p + 6 <= rlen) {
            uint16_t btype = pn_get_u16be(rxbuf, p);
            uint16_t blen  = pn_get_u16be(rxbuf, p + 2);
            if (btype == PN_BLK_IOCR_RES && blen >= 6) {
                /* IOCRBlockRes: BlockHdr(6) + IOCRType(2) + IOCRRef(2) + FrameID(2) */
                uint16_t iocr_type = pn_get_u16be(rxbuf, p + 6);
                uint16_t fid       = pn_get_u16be(rxbuf, p + 10);
                if (iocr_type == IOCR_TYPE_OUTPUT) ctx->output_frame_id = fid;
                else if (iocr_type == IOCR_TYPE_INPUT)  ctx->input_frame_id  = fid;
            }
            int next = p + 4 + blen;
            if (next <= p) break;
            p = next + (blen & 1); /* word-align */
        }
    }
    pn_log("Frame IDs: output=0x%04X input=0x%04X",
           ctx->output_frame_id, ctx->input_frame_id);

    /* ── 4. IODWrite record 0 (empty parametrization) ────────────────────── */
    memset(txbuf, 0, RPC_OFF_PAYLOAD);
    int wr_off = RPC_OFF_PAYLOAD;
    /* IODWriteReqHeader: SeqNumber(2) ARUUID(16) API(4) SlotNumber(2)
     *   SubslotNumber(2) padding(2) Index(2) RecordDataLength(4)
     *   RWPadding(24) Data(0) */
    pn_put_u16be(txbuf, wr_off, 0x0000); wr_off += 2; /* SeqNumber */
    memcpy(txbuf + wr_off, ctx->ar_uuid, 16); wr_off += 16;
    pn_put_u32be(txbuf, wr_off, 0); wr_off += 4; /* API */
    pn_put_u16be(txbuf, wr_off, cfg->slot);    wr_off += 2;
    pn_put_u16be(txbuf, wr_off, cfg->subslot); wr_off += 2;
    pn_put_u16be(txbuf, wr_off, 0); wr_off += 2; /* padding */
    pn_put_u16be(txbuf, wr_off, 0x8028); wr_off += 2; /* Index: SubmoduleState */
    pn_put_u32be(txbuf, wr_off, 0); wr_off += 4; /* RecordDataLength = 0 */
    memset(txbuf + wr_off, 0, 24); wr_off += 24; /* RWPadding */

    rpc_build_header(txbuf, RPC_PTYPE_REQUEST, RPC_OPNUM_WRITE,
                     ctx->rpc_call_id++, ctx->activity_uuid);
    pn_put_u32le(txbuf, RPC_OFF_ALLOCHINT, (uint32_t)(wr_off - RPC_OFF_PAYLOAD));
    pn_put_u16le(txbuf, RPC_OFF_FRAGLEN, (uint16_t)wr_off);

    rpc_send(ctx->rpc_sock, &dst, txbuf, wr_off);

    /* Wait for Write response */
    rlen = rpc_recv(ctx->rpc_sock, rxbuf, sizeof(rxbuf), 3000);
    if (rlen > 0) pn_log("IODWrite response (%d bytes)", rlen);

    /* ── 5. DControl — ApplicationReady ──────────────────────────────────── */
    memset(txbuf, 0, RPC_OFF_PAYLOAD);
    int dc_off = RPC_OFF_PAYLOAD;
    /* DControlReq: ARUUID(16) SessionKey(2) padding(2) ControlCommand(2) ControlBlockProperties(2) */
    memcpy(txbuf + dc_off, ctx->ar_uuid, 16); dc_off += 16;
    pn_put_u16be(txbuf, dc_off, (uint16_t)ctx->session_key); dc_off += 2;
    pn_put_u16be(txbuf, dc_off, 0); dc_off += 2; /* padding */
    pn_put_u16be(txbuf, dc_off, 0x0004); dc_off += 2; /* ControlCommand: ApplicationReady */
    pn_put_u16be(txbuf, dc_off, 0x0000); dc_off += 2;

    rpc_build_header(txbuf, RPC_PTYPE_REQUEST, RPC_OPNUM_DCONTROL,
                     ctx->rpc_call_id++, ctx->activity_uuid);
    pn_put_u32le(txbuf, RPC_OFF_ALLOCHINT, (uint32_t)(dc_off - RPC_OFF_PAYLOAD));
    pn_put_u16le(txbuf, RPC_OFF_FRAGLEN, (uint16_t)dc_off);

    rpc_send(ctx->rpc_sock, &dst, txbuf, dc_off);

    /* ── 6. Wait for DControl response, then CControl from device ─────────── */
    int got_app_ready = 0;
    uint64_t deadline = pn_time_ns() + 10000ULL * 1000000ULL; /* 10s */
    while (!got_app_ready && pn_time_ns() < deadline) {
        rlen = rpc_recv(ctx->rpc_sock, rxbuf, sizeof(rxbuf), 200);
        if (rlen < RPC_OFF_PAYLOAD) continue;
        uint8_t ptype  = rxbuf[RPC_OFF_PTYPE];
        uint16_t opnum = pn_get_u16le(rxbuf, RPC_OFF_OPNUM);
        pn_log("RPC ptype=%u opnum=%u", ptype, opnum);

        if (ptype == RPC_PTYPE_RESPONSE && opnum == RPC_OPNUM_DCONTROL) {
            pn_log("DControl response received");
        } else if (opnum == RPC_OPNUM_CCONTROL) {
            /* CControl from device — check ControlCommand */
            if (rlen >= RPC_OFF_PAYLOAD + 22) {
                uint16_t cmd = pn_get_u16be(rxbuf, RPC_OFF_PAYLOAD + 20);
                if (cmd & 0x0004) { /* ApplicationReady */
                    got_app_ready = 1;
                    pn_log("CControl ApplicationReady from device");
                }
            }
            /* Send CControl response */
            memset(txbuf, 0, RPC_OFF_PAYLOAD + 24);
            int cc_off = RPC_OFF_PAYLOAD;
            memcpy(txbuf + cc_off, ctx->ar_uuid, 16); cc_off += 16;
            pn_put_u16be(txbuf, cc_off, (uint16_t)ctx->session_key); cc_off += 2;
            pn_put_u16be(txbuf, cc_off, 0); cc_off += 2;
            pn_put_u16be(txbuf, cc_off, 0x0000); cc_off += 2; /* no command */
            pn_put_u16be(txbuf, cc_off, 0x0000); cc_off += 2;
            uint32_t cid = pn_get_u32le(rxbuf, RPC_OFF_CALLID);
            rpc_build_header(txbuf, RPC_PTYPE_RESPONSE, RPC_OPNUM_CCONTROL,
                             cid, ctx->activity_uuid);
            pn_put_u16le(txbuf, RPC_OFF_FRAGLEN, (uint16_t)cc_off);
            rpc_send(ctx->rpc_sock, &dst, txbuf, cc_off);
        }
    }

    if (!got_app_ready) {
        pn_set_error(ctx, "RPC Connect: no ApplicationReady from device");
        return PN_ERR_RPC_TIMEOUT;
    }

    pn_log("AR established successfully");
    return PN_OK;
}

/* ─── Release ────────────────────────────────────────────────────────────── */
int rpc_cm_release(PN_Context *ctx)
{
    uint8_t txbuf[RPC_BUF_SIZE];
    memset(txbuf, 0, RPC_OFF_PAYLOAD + 24);

    int off = RPC_OFF_PAYLOAD;
    memcpy(txbuf + off, ctx->ar_uuid, 16); off += 16;
    pn_put_u16be(txbuf, off, (uint16_t)ctx->session_key); off += 2;
    pn_put_u16be(txbuf, off, 0); off += 2;
    pn_put_u16be(txbuf, off, 0x0002); off += 2; /* ControlCommand: Release */
    pn_put_u16be(txbuf, off, 0x0000); off += 2;

    rpc_build_header(txbuf, RPC_PTYPE_REQUEST, RPC_OPNUM_RELEASE,
                     ctx->rpc_call_id++, ctx->activity_uuid);
    pn_put_u16le(txbuf, RPC_OFF_FRAGLEN, (uint16_t)off);

    char ip_str[16];
    pn_ip_to_str(ctx->device_ip, ip_str);
    struct sockaddr_in dst = {0};
    dst.sin_family = AF_INET;
    dst.sin_port   = htons(RPC_PNIO_PORT);
    dst.sin_addr.s_addr = inet_addr(ip_str);

    rpc_send(ctx->rpc_sock, &dst, txbuf, off);
    pn_log("RPC Release sent");

    /* Best-effort receive */
    uint8_t rxbuf[RPC_BUF_SIZE];
    rpc_recv(ctx->rpc_sock, rxbuf, sizeof(rxbuf), 1000);
    return PN_OK;
}

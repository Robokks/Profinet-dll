#include "dcp.h"
#include "utils.h"
#include <string.h>
#include <stdio.h>

/* ─── DCP Header offsets (after 14-byte Ethernet header) ────────────────── */
/*  0: FrameID[2], 2: ServiceID[1], 3: ServiceType[1],
 *  4: Xid[4], 8: ResponseDelay[2], 10: DataLength[2], 12: blocks... */
#define DCP_HDR_LEN   12
#define DCP_FRAME_OFF 14  /* after Ethernet header */

static uint32_t g_xid = 1;

/* Build the common DCP frame header into buf+ETH_HDR_LEN. Returns offset
 * after the DCP header (=ETH_HDR_LEN+DCP_HDR_LEN) ready for blocks. */
static int dcp_build_header(uint8_t *buf, const uint8_t src_mac[6],
                              const uint8_t dst_mac[6], uint16_t frame_id,
                              uint8_t srv_id, uint8_t srv_type,
                              uint16_t response_delay)
{
    eth_build_header(buf, dst_mac, src_mac, ETHERTYPE_PROFINET);
    int off = ETH_HDR_LEN;
    pn_put_u16be(buf, off, frame_id);          off += 2;
    buf[off++] = srv_id;
    buf[off++] = srv_type;
    pn_put_u32be(buf, off, g_xid++);           off += 4;
    pn_put_u16be(buf, off, response_delay);    off += 2;
    pn_put_u16be(buf, off, 0);  /* DataLength — fill later */ off += 2;
    return off;
}

/* Fill DataLength field (offset 10 from start of DCP header). */
static void dcp_set_data_length(uint8_t *buf, int data_len)
{
    pn_put_u16be(buf, ETH_HDR_LEN + 10, (uint16_t)data_len);
}

/* ─── Build DCP block: Option, Suboption, BlockLength, [Data, Padding] ───── */
static int dcp_put_block(uint8_t *buf, int off, uint8_t opt, uint8_t sub,
                          const uint8_t *data, int data_len)
{
    buf[off++] = opt;
    buf[off++] = sub;
    pn_put_u16be(buf, off, (uint16_t)data_len);  off += 2;
    if (data && data_len > 0) {
        memcpy(buf + off, data, (size_t)data_len);
        off += data_len;
    }
    if (data_len & 1) buf[off++] = 0; /* padding to word boundary */
    return off;
}

/* ─── Parse a single Identify response frame ─────────────────────────────── */
static int dcp_parse_identify_rsp(const uint8_t *frame, int len,
                                   PN_DeviceInfo *out)
{
    if (len < ETH_HDR_LEN + DCP_HDR_LEN) return -1;

    uint16_t fid = pn_get_u16be(frame, ETH_HDR_LEN);
    if (fid != DCP_FRAMEID_IDENTIFY_RSP) return -1;
    if (frame[ETH_HDR_LEN + 2] != DCP_SRV_IDENTIFY) return -1;
    if (frame[ETH_HDR_LEN + 3] != DCP_SRVTYPE_SUCCESS) return -1;

    memcpy(out->mac, frame + 6, 6);
    memset(out->ip_str, 0, sizeof(out->ip_str));
    memset(out->name_of_station, 0, sizeof(out->name_of_station));
    out->vendor_id = 0;
    out->device_id = 0;

    uint16_t data_len = pn_get_u16be(frame, ETH_HDR_LEN + 10);
    int off = ETH_HDR_LEN + DCP_HDR_LEN;
    int end = off + data_len;
    if (end > len) end = len;

    while (off + 4 <= end) {
        uint8_t  opt     = frame[off];
        uint8_t  sub     = frame[off + 1];
        uint16_t blen    = pn_get_u16be(frame, off + 2);
        int      payload = off + 4;
        int      next    = payload + blen + (blen & 1);
        off = next;

        if (opt == DCP_OPT_IP && sub == DCP_SUB_IP_PARAM && blen >= 14) {
            /* 2 bytes block info, then IP(4)+subnet(4)+gateway(4) */
            uint32_t ip  = pn_get_u32be(frame, payload + 2);
            pn_ip_to_str(ip, out->ip_str);
        } else if (opt == DCP_OPT_NAME && sub == DCP_SUB_NAME_STN) {
            int nlen = blen < (int)sizeof(out->name_of_station) - 1 ? blen
                                                                     : (int)sizeof(out->name_of_station) - 1;
            memcpy(out->name_of_station, frame + payload, (size_t)nlen);
            out->name_of_station[nlen] = '\0';
        } else if (opt == DCP_OPT_HWID && sub == DCP_SUB_HWID_VENDOR && blen >= 4) {
            out->vendor_id = pn_get_u16be(frame, payload);
            out->device_id = pn_get_u16be(frame, payload + 2);
        }
    }
    return 0;
}

/* ─── DCP Identify-All ───────────────────────────────────────────────────── */
int dcp_discover(PN_Context *ctx, uint32_t timeout_ms)
{
    uint8_t frame[128];
    int off = dcp_build_header(frame, ctx->local_mac, PN_DCP_MULTICAST_MAC,
                                DCP_FRAMEID_IDENTIFY_REQ,
                                DCP_SRV_IDENTIFY, DCP_SRVTYPE_REQUEST, 1);

    /* Block: All/All */
    uint8_t all_block[2] = {0};
    off = dcp_put_block(frame, off, DCP_OPT_ALL, DCP_SUB_ALL, all_block, 0);

    dcp_set_data_length(frame, off - ETH_HDR_LEN - DCP_HDR_LEN);

    /* Pad to minimum Ethernet frame size */
    if (off < ETH_MIN_FRAME) {
        memset(frame + off, 0, (size_t)(ETH_MIN_FRAME - off));
        off = ETH_MIN_FRAME;
    }

    ctx->discovered_count = 0;

    if (frameio_send((FrameIO *)ctx->pcap, frame, off) != 0) {
        pn_set_error(ctx, "DCP Identify: send failed");
        return PN_ERR_DCP_TIMEOUT;
    }

    uint8_t rbuf[ETH_MAX_FRAME];
    uint64_t deadline = pn_time_ns() + (uint64_t)timeout_ms * 1000000ULL;

    while (pn_time_ns() < deadline) {
        int rlen = frameio_recv((FrameIO *)ctx->pcap, rbuf, sizeof(rbuf), 10);
        if (rlen <= 0) continue;
        if (ctx->discovered_count >= PN_MAX_DEVICES) break;
        PN_DeviceInfo dev;
        if (dcp_parse_identify_rsp(rbuf, rlen, &dev) == 0) {
            /* Avoid duplicates (same MAC) */
            int dup = 0;
            for (int i = 0; i < ctx->discovered_count; i++) {
                if (memcmp(ctx->discovered[i].mac, dev.mac, 6) == 0) {
                    dup = 1; break;
                }
            }
            if (!dup) {
                ctx->discovered[ctx->discovered_count++] = dev;
                pn_log("DCP: found device %s at %s (%s)",
                       dev.name_of_station, dev.ip_str,
                       dev.mac[0] == 0 ? "?" : "ok");
            }
        }
    }
    return PN_OK;
}

/* ─── DCP Set IP ─────────────────────────────────────────────────────────── */
int dcp_set_ip(PN_Context *ctx, const uint8_t device_mac[6],
               uint32_t ip, uint32_t subnet, uint32_t gateway)
{
    uint8_t frame[256];
    int off = dcp_build_header(frame, ctx->local_mac, device_mac,
                                DCP_FRAMEID_SET_REQ,
                                DCP_SRV_SET, DCP_SRVTYPE_REQUEST, 0);

    /* BlockQualifier(2) + IP(4) + Subnet(4) + GW(4) = 14 bytes */
    uint8_t ipblock[14];
    pn_put_u16be(ipblock, 0, 0x0001); /* BlockQualifier: store permanently */
    pn_put_u32be(ipblock, 2, ip);
    pn_put_u32be(ipblock, 6, subnet);
    pn_put_u32be(ipblock, 10, gateway);
    off = dcp_put_block(frame, off, DCP_OPT_IP, DCP_SUB_IP_PARAM, ipblock, 14);

    dcp_set_data_length(frame, off - ETH_HDR_LEN - DCP_HDR_LEN);
    if (off < ETH_MIN_FRAME) {
        memset(frame + off, 0, (size_t)(ETH_MIN_FRAME - off));
        off = ETH_MIN_FRAME;
    }

    if (frameio_send((FrameIO *)ctx->pcap, frame, off) != 0) {
        pn_set_error(ctx, "DCP SetIP: send failed");
        return PN_ERR_DCP_SET_FAIL;
    }

    /* Wait for Set Response */
    uint8_t rbuf[ETH_MAX_FRAME];
    uint64_t deadline = pn_time_ns() + 3000ULL * 1000000ULL;
    while (pn_time_ns() < deadline) {
        int rlen = frameio_recv((FrameIO *)ctx->pcap, rbuf, sizeof(rbuf), 10);
        if (rlen < ETH_HDR_LEN + DCP_HDR_LEN) continue;
        uint16_t fid = pn_get_u16be(rbuf, ETH_HDR_LEN);
        if (fid != DCP_FRAMEID_SET_RSP) continue;
        if (memcmp(rbuf + 6, device_mac, 6) != 0) continue;
        /* Check block result */
        if (rlen >= ETH_HDR_LEN + DCP_HDR_LEN + 6) {
            uint16_t result = pn_get_u16be(rbuf, ETH_HDR_LEN + DCP_HDR_LEN + 4);
            if (result != DCP_RESULT_OK) {
                pn_set_error(ctx, "DCP SetIP: device returned error 0x%04X", result);
                return PN_ERR_DCP_SET_FAIL;
            }
        }
        char ipstr[16];
        pn_ip_to_str(ip, ipstr);
        pn_log("DCP SetIP OK: %s", ipstr);
        return PN_OK;
    }

    pn_set_error(ctx, "DCP SetIP: no response from device");
    return PN_ERR_DCP_TIMEOUT;
}

/* ─── DCP Set Name ───────────────────────────────────────────────────────── */
int dcp_set_name(PN_Context *ctx, const uint8_t device_mac[6],
                 const char *name)
{
    uint8_t frame[512];
    int off = dcp_build_header(frame, ctx->local_mac, device_mac,
                                DCP_FRAMEID_SET_REQ,
                                DCP_SRV_SET, DCP_SRVTYPE_REQUEST, 0);

    int nlen = (int)strlen(name);
    /* 2 bytes BlockQualifier then name bytes */
    uint8_t nameblock[256];
    pn_put_u16be(nameblock, 0, 0x0001);
    memcpy(nameblock + 2, name, (size_t)nlen);
    off = dcp_put_block(frame, off, DCP_OPT_NAME, DCP_SUB_NAME_STN,
                        nameblock, 2 + nlen);

    dcp_set_data_length(frame, off - ETH_HDR_LEN - DCP_HDR_LEN);
    if (off < ETH_MIN_FRAME) {
        memset(frame + off, 0, (size_t)(ETH_MIN_FRAME - off));
        off = ETH_MIN_FRAME;
    }

    if (frameio_send((FrameIO *)ctx->pcap, frame, off) != 0) {
        pn_set_error(ctx, "DCP SetName: send failed");
        return PN_ERR_DCP_SET_FAIL;
    }

    /* Wait for response */
    uint8_t rbuf[ETH_MAX_FRAME];
    uint64_t deadline = pn_time_ns() + 3000ULL * 1000000ULL;
    while (pn_time_ns() < deadline) {
        int rlen = frameio_recv((FrameIO *)ctx->pcap, rbuf, sizeof(rbuf), 10);
        if (rlen < ETH_HDR_LEN + DCP_HDR_LEN) continue;
        uint16_t fid = pn_get_u16be(rbuf, ETH_HDR_LEN);
        if (fid != DCP_FRAMEID_SET_RSP) continue;
        if (memcmp(rbuf + 6, device_mac, 6) != 0) continue;
        pn_log("DCP SetName OK: %s", name);
        return PN_OK;
    }

    pn_set_error(ctx, "DCP SetName: no response");
    return PN_ERR_DCP_TIMEOUT;
}

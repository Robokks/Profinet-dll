/* sim_test.c — CLI integration test: master (IO-Controller) vs VFD simulator.
 * Sends DCP Identify-All, DCP SetName, DCP SetIP over the first Npcap adapter.
 * Exits 0 if all exchanges succeed, 1 on failure.
 * Output goes to stdout so Wine can pipe it. */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winsock2.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>

#include "../src/frame_io.h"
#include "../src/utils.h"

/* ─── Profinet constants ─────────────────────────────────────────────────── */
#define ETH_PN           0x8892u
#define FID_DCP_IDENTIFY 0xFEFEu
#define FID_DCP_IDENTIFY_R 0xFEFFu
#define FID_DCP_SET      0xFEFDu
#define FID_DCP_SET_R    0xFEFCu

static const uint8_t DCP_MCAST[6] = {0x01, 0x0E, 0xCF, 0x00, 0x00, 0x00};

static int g_pass = 0;
static int g_fail = 0;

static void check(int cond, const char *label)
{
    if (cond) { printf("  PASS  %s\n", label); g_pass++; }
    else       { printf("  FAIL  %s\n", label); g_fail++; }
}

/* ─── Build a DCP Identify-All request ──────────────────────────────────── */
static int build_dcp_identify_all(uint8_t *buf, const uint8_t src_mac[6],
                                   uint32_t xid)
{
    int pos = 0;
    memcpy(buf + pos, DCP_MCAST, 6); pos += 6;
    memcpy(buf + pos, src_mac, 6);   pos += 6;
    buf[pos++] = 0x88; buf[pos++] = 0x92;    /* EtherType */
    buf[pos++] = 0xFE; buf[pos++] = 0xFE;    /* FrameID Identify-All */
    buf[pos++] = 0x05; /* ServiceID = Identify */
    buf[pos++] = 0x00; /* ServiceType = request */
    pn_put_u32be(buf, pos, xid); pos += 4;
    buf[pos++] = 0x00; buf[pos++] = 0x00;    /* ResponseDelay */
    buf[pos++] = 0x00; buf[pos++] = 0x04;    /* DataLength = 4 */
    /* AllStations block: option=0xFF sub=0xFF len=0x0002 */
    buf[pos++] = 0xFF; buf[pos++] = 0xFF;
    buf[pos++] = 0x00; buf[pos++] = 0x02;
    buf[pos++] = 0xFF; buf[pos++] = 0xFF;    /* all stations qualifier */
    while (pos < 60) buf[pos++] = 0x00;
    return pos;
}

/* ─── Build a DCP SetName request ────────────────────────────────────────── */
static int build_dcp_setname(uint8_t *buf, const uint8_t dst[6],
                              const uint8_t src[6], uint32_t xid,
                              const char *name)
{
    int pos = 0;
    memcpy(buf + pos, dst, 6); pos += 6;
    memcpy(buf + pos, src, 6); pos += 6;
    buf[pos++] = 0x88; buf[pos++] = 0x92;
    buf[pos++] = 0xFE; buf[pos++] = 0xFD;   /* FID DCP Set */
    buf[pos++] = 0x04; /* ServiceID = Set */
    buf[pos++] = 0x00; /* request */
    pn_put_u32be(buf, pos, xid); pos += 4;
    buf[pos++] = 0x00; buf[pos++] = 0x00;   /* ResponseDelay */
    int data_len_off = pos;
    buf[pos++] = 0x00; buf[pos++] = 0x00;   /* DataLength (filled in) */
    int data_start = pos;

    uint16_t name_len = (uint16_t)strlen(name);
    buf[pos++] = 0x02; buf[pos++] = 0x02;   /* option, sub */
    pn_put_u16be(buf, pos, name_len + 2); pos += 2;
    buf[pos++] = 0x00; buf[pos++] = 0x01;   /* BlockQualifier: store permanently */
    memcpy(buf + pos, name, name_len); pos += name_len;
    if (name_len & 1) buf[pos++] = 0x00;    /* pad */

    pn_put_u16be(buf, data_len_off, (uint16_t)(pos - data_start));
    while (pos < 60) buf[pos++] = 0x00;
    return pos;
}

/* ─── Build a DCP SetIP request ──────────────────────────────────────────── */
static int build_dcp_setip(uint8_t *buf, const uint8_t dst[6],
                            const uint8_t src[6], uint32_t xid,
                            uint32_t ip, uint32_t subnet, uint32_t gw)
{
    int pos = 0;
    memcpy(buf + pos, dst, 6); pos += 6;
    memcpy(buf + pos, src, 6); pos += 6;
    buf[pos++] = 0x88; buf[pos++] = 0x92;
    buf[pos++] = 0xFE; buf[pos++] = 0xFD;
    buf[pos++] = 0x04; buf[pos++] = 0x00;
    pn_put_u32be(buf, pos, xid); pos += 4;
    buf[pos++] = 0x00; buf[pos++] = 0x00;
    /* DataLength = 4 (block header) + 14 (ip block data) */
    buf[pos++] = 0x00; buf[pos++] = 0x12;
    /* IP block: option=0x01 sub=0x02 len=14 */
    buf[pos++] = 0x01; buf[pos++] = 0x02;
    pn_put_u16be(buf, pos, 14); pos += 2;
    buf[pos++] = 0x00; buf[pos++] = 0x01;   /* BlockQualifier: store permanently */
    /* IP, subnet, gateway in network byte order */
    memcpy(buf + pos, &ip, 4);     pos += 4;
    memcpy(buf + pos, &subnet, 4); pos += 4;
    memcpy(buf + pos, &gw, 4);     pos += 4;
    while (pos < 60) buf[pos++] = 0x00;
    return pos;
}

/* ─── Wait for a specific FrameID with timeout (ms) ────────────────────── */
static int recv_frame_id(FrameIO *fio, uint8_t *buf, int buf_size,
                          uint16_t want_fid, int timeout_ms)
{
    uint64_t deadline = pn_time_ns() + (uint64_t)timeout_ms * 1000000ULL;
    while (pn_time_ns() < deadline) {
        int n = frameio_recv(fio, buf, buf_size, 20);
        if (n < 16) continue;
        if (buf[12] != 0x88 || buf[13] != 0x92) continue;
        uint16_t fid = pn_get_u16be(buf, 14);
        if (fid == want_fid) return n;
    }
    return 0;
}

/* ─── Main ───────────────────────────────────────────────────────────────── */
int main(void)
{
    WSADATA wsa;
    WSAStartup(MAKEWORD(2,2), &wsa);

    printf("\n=== Profinet Simulator Integration Test ===\n\n");

    /* ── Step 1: enumerate adapters ─────────────────────────────────────── */
    char names[8][256];
    int  adapter_count = 0;
    frameio_enum_adapters(names, 8, &adapter_count);

    printf("[1] Adapter enumeration\n");
    check(adapter_count > 0, "at least one adapter found");
    for (int i = 0; i < adapter_count; i++)
        printf("      [%d] %s\n", i, names[i]);

    if (adapter_count == 0) {
        printf("\nNo adapters — install Npcap.\n");
        return 1;
    }

    /* Use first adapter */
    const char *adapter = names[0];
    printf("      Using: %s\n\n", adapter);

    /* ── Step 2: open adapter ────────────────────────────────────────────── */
    char errbuf[256] = {0};
    uint8_t local_mac[6] = {0};
    FrameIO *fio = frameio_open(adapter, local_mac, errbuf);

    printf("[2] Open adapter\n");
    check(fio != NULL, "frameio_open succeeded");
    if (!fio) {
        printf("      Error: %s\n", errbuf);
        return 1;
    }
    printf("      Local MAC: %02X:%02X:%02X:%02X:%02X:%02X\n\n",
           local_mac[0], local_mac[1], local_mac[2],
           local_mac[3], local_mac[4], local_mac[5]);

    /* ── Step 3: DCP Identify-All, wait for simulator response ──────────── */
    printf("[3] DCP Identify-All (waiting up to 2s for simulator response)\n");
    uint8_t buf[2048];
    uint8_t dev_mac[6] = {0};
    char    dev_name[240] = {0};

    /* Send multiple identify requests (the simulator may take a moment) */
    int identified = 0;
    for (int attempt = 0; attempt < 5 && !identified; attempt++) {
        uint8_t req[64];
        int n = build_dcp_identify_all(req, local_mac, 0x10000001 + attempt);
        frameio_send(fio, req, n);

        /* Wait up to 400ms per attempt */
        int rn = recv_frame_id(fio, buf, sizeof(buf), FID_DCP_IDENTIFY_R, 400);
        if (rn < 20) continue;

        /* Don't accept our own identify-all echo */
        if (memcmp(buf + 6, local_mac, 6) == 0) continue;

        memcpy(dev_mac, buf + 6, 6);  /* source of response = device MAC */

        /* Parse name from NameOfStation block */
        uint16_t data_len = pn_get_u16be(buf, 22);
        int pos = 26, end = 26 + (int)data_len;
        if (end > rn) end = rn;
        while (pos + 4 <= end) {
            uint8_t  opt = buf[pos];
            uint8_t  sub = buf[pos + 1];
            uint16_t blen = pn_get_u16be(buf, pos + 2);
            if (opt == 0x02 && sub == 0x02 && blen >= 2) {
                uint16_t nlen = blen - 2;
                if (nlen > 239) nlen = 239;
                memcpy(dev_name, buf + pos + 6, nlen);
                dev_name[nlen] = '\0';
            }
            pos += 4 + (int)blen;
            if (blen & 1) pos++;
        }
        identified = 1;
    }

    check(identified, "received DCP Identify Response from simulator");
    if (identified) {
        printf("      Device MAC:  %02X:%02X:%02X:%02X:%02X:%02X\n",
               dev_mac[0], dev_mac[1], dev_mac[2],
               dev_mac[3], dev_mac[4], dev_mac[5]);
        printf("      Station Name: \"%s\"\n", dev_name);
        check(strcmp(dev_name, "vfd-simulator") == 0,
              "station name is 'vfd-simulator'");
    }
    printf("\n");

    /* ── Step 4: DCP SetName ─────────────────────────────────────────────── */
    printf("[4] DCP SetName -> 'test-drive'\n");
    {
        uint8_t req[64];
        int n = build_dcp_setname(req, dev_mac, local_mac, 0x20000001, "test-drive");
        frameio_send(fio, req, n);

        int rn = recv_frame_id(fio, buf, sizeof(buf), FID_DCP_SET_R, 1000);
        check(rn > 0, "received DCP SetName response");
        if (rn > 0) {
            uint8_t svc = buf[14];
            uint8_t typ = buf[15];
            check(svc == 0x04 && typ == 0x01, "response is Set/response (0x04/0x01)");
        }
    }
    printf("\n");

    /* ── Step 5: DCP SetIP ───────────────────────────────────────────────── */
    printf("[5] DCP SetIP -> 192.168.1.50\n");
    {
        uint32_t ip     = inet_addr("192.168.1.50");
        uint32_t subnet = inet_addr("255.255.255.0");
        uint32_t gw     = inet_addr("192.168.1.1");

        uint8_t req[64];
        int n = build_dcp_setip(req, dev_mac, local_mac, 0x30000001, ip, subnet, gw);
        frameio_send(fio, req, n);

        int rn = recv_frame_id(fio, buf, sizeof(buf), FID_DCP_SET_R, 1000);
        check(rn > 0, "received DCP SetIP response");
        if (rn > 0) {
            uint8_t svc = buf[14];
            uint8_t typ = buf[15];
            check(svc == 0x04 && typ == 0x01, "response is Set/response (0x04/0x01)");
        }
    }
    printf("\n");

    /* ── Step 6: DCP Identify again, verify updated name ──────────────────── */
    printf("[6] DCP Identify-All again (verify name changed to 'test-drive')\n");
    {
        char new_name[240] = {0};
        int re_identified = 0;
        for (int attempt = 0; attempt < 5 && !re_identified; attempt++) {
            uint8_t req[64];
            int n = build_dcp_identify_all(req, local_mac, 0x40000001 + attempt);
            frameio_send(fio, req, n);
            int rn = recv_frame_id(fio, buf, sizeof(buf), FID_DCP_IDENTIFY_R, 400);
            if (rn < 20) continue;
            if (memcmp(buf + 6, local_mac, 6) == 0) continue;
            if (memcmp(buf + 6, dev_mac, 6) != 0) continue;
            /* Parse name */
            uint16_t dlen = pn_get_u16be(buf, 22);
            int pos = 26, end = 26 + (int)dlen;
            if (end > rn) end = rn;
            while (pos + 4 <= end) {
                uint8_t  opt  = buf[pos];
                uint8_t  sub  = buf[pos + 1];
                uint16_t blen = pn_get_u16be(buf, pos + 2);
                if (opt == 0x02 && sub == 0x02 && blen >= 2) {
                    uint16_t nlen = blen - 2;
                    if (nlen > 239) nlen = 239;
                    memcpy(new_name, buf + pos + 6, nlen);
                    new_name[nlen] = '\0';
                }
                pos += 4 + (int)blen;
                if (blen & 1) pos++;
            }
            re_identified = 1;
        }
        check(re_identified, "simulator re-identified after SetName");
        check(strcmp(new_name, "test-drive") == 0, "station name updated to 'test-drive'");
        printf("      New station name: \"%s\"\n", new_name);
    }
    printf("\n");

    /* ── Summary ─────────────────────────────────────────────────────────── */
    frameio_close(fio);
    printf("=== Results: %d passed, %d failed ===\n", g_pass, g_fail);
    return g_fail > 0 ? 1 : 0;
}

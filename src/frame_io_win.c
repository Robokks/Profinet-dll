/* Windows raw Ethernet I/O via Npcap loaded at runtime.
 * Npcap DLL is resolved via LoadLibrary — no wpcap.lib needed at link time.
 * Requires Npcap installed on the target Windows machine. */

#include "frame_io.h"
#include "utils.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <winsock2.h>
#include <windows.h>
#include <iphlpapi.h>
#include <pcap.h>

/* ─── Runtime-loaded Npcap function pointers ─────────────────────────────── */
typedef pcap_t *(*pfn_pcap_open_live)(const char *, int, int, int, char *);
typedef int     (*pfn_pcap_sendpacket)(pcap_t *, const u_char *, int);
typedef int     (*pfn_pcap_next_ex)(pcap_t *, struct pcap_pkthdr **,
                                    const u_char **);
typedef int     (*pfn_pcap_compile)(pcap_t *, struct bpf_program *,
                                    const char *, int, uint32_t);
typedef int     (*pfn_pcap_setfilter)(pcap_t *, struct bpf_program *);
typedef void    (*pfn_pcap_freecode)(struct bpf_program *);
typedef void    (*pfn_pcap_close)(pcap_t *);
typedef int     (*pfn_pcap_findalldevs)(pcap_if_t **, char *);
typedef void    (*pfn_pcap_freealldevs)(pcap_if_t *);
typedef char   *(*pfn_pcap_geterr)(pcap_t *);

struct NpcapFuncs {
    HMODULE              hDll;
    pfn_pcap_open_live   open_live;
    pfn_pcap_sendpacket  sendpacket;
    pfn_pcap_next_ex     next_ex;
    pfn_pcap_compile     compile;
    pfn_pcap_setfilter   setfilter;
    pfn_pcap_freecode    freecode;
    pfn_pcap_close       close;
    pfn_pcap_findalldevs findalldevs;
    pfn_pcap_freealldevs freealldevs;
    pfn_pcap_geterr      geterr;
};

static struct NpcapFuncs g_npcap = {0};

int frameio_load_npcap(void)
{
    if (g_npcap.hDll) return 0;

    g_npcap.hDll = LoadLibraryA("C:\\Windows\\System32\\Npcap\\wpcap.dll");
    if (!g_npcap.hDll)
        g_npcap.hDll = LoadLibraryA("wpcap.dll");
    if (!g_npcap.hDll)
        return -1;

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wcast-function-type"
#define LOAD(name) \
    g_npcap.name = (pfn_pcap_##name)GetProcAddress(g_npcap.hDll, "pcap_" #name)

    LOAD(open_live);
    LOAD(sendpacket);
    LOAD(next_ex);
    LOAD(compile);
    LOAD(setfilter);
    LOAD(freecode);
    LOAD(close);
    LOAD(findalldevs);
    LOAD(freealldevs);
    LOAD(geterr);
#undef LOAD
#pragma GCC diagnostic pop

    if (!g_npcap.open_live || !g_npcap.sendpacket || !g_npcap.next_ex ||
        !g_npcap.findalldevs) {
        FreeLibrary(g_npcap.hDll);
        g_npcap.hDll = NULL;
        return -1;
    }
    pn_log("Npcap loaded OK");
    return 0;
}

void frameio_unload_npcap(void)
{
    if (g_npcap.hDll) {
        FreeLibrary(g_npcap.hDll);
        g_npcap.hDll = NULL;
    }
}

/* ─── Get MAC address of a named Npcap adapter via iphlpapi ──────────────── */
static int get_adapter_mac(const char *adapter_name, uint8_t mac[6])
{
    const char *guid_start = strstr(adapter_name, "{");
    if (!guid_start) return -1;

    PIP_ADAPTER_INFO pInfo = NULL;
    ULONG bufLen = 0;
    GetAdaptersInfo(NULL, &bufLen);
    pInfo = (PIP_ADAPTER_INFO)malloc(bufLen);
    if (!pInfo) return -1;

    if (GetAdaptersInfo(pInfo, &bufLen) != ERROR_SUCCESS) {
        free(pInfo);
        return -1;
    }

    int found = -1;
    for (PIP_ADAPTER_INFO p = pInfo; p; p = p->Next) {
        char candidate[64];
        snprintf(candidate, sizeof(candidate), "{%s}", p->AdapterName);
        if (strstr(guid_start, p->AdapterName) ||
            strstr(adapter_name, candidate)) {
            if (p->AddressLength == 6) {
                memcpy(mac, p->Address, 6);
                found = 0;
                break;
            }
        }
    }
    free(pInfo);
    return found;
}

/* ─── frameio_open ───────────────────────────────────────────────────────── */
FrameIO *frameio_open(const char *adapter_name, uint8_t out_local_mac[6],
                      char *errbuf)
{
    if (frameio_load_npcap() != 0) {
        if (errbuf)
            snprintf(errbuf, PCAP_ERRBUF_SIZE,
                     "Npcap not found. Install from https://npcap.com/");
        return NULL;
    }

    const char *dev = adapter_name;
    pcap_if_t *alldevs = NULL;
    char tmpbuf[PCAP_ERRBUF_SIZE] = {0};

    if (!dev) {
        if (g_npcap.findalldevs(&alldevs, tmpbuf) != 0 || !alldevs) {
            if (errbuf) snprintf(errbuf, PCAP_ERRBUF_SIZE, "No adapters: %s", tmpbuf);
            return NULL;
        }
        dev = alldevs->name;
    }

    pcap_t *p = g_npcap.open_live(dev, ETH_MAX_FRAME, 1, 1, tmpbuf);
    if (!p) {
        if (errbuf) snprintf(errbuf, PCAP_ERRBUF_SIZE, "pcap_open_live: %s", tmpbuf);
        if (alldevs) g_npcap.freealldevs(alldevs);
        return NULL;
    }

    struct bpf_program fp;
    if (g_npcap.compile(p, &fp, "ether proto 0x8892", 1, 0xFFFFFFFF) == 0) {
        g_npcap.setfilter(p, &fp);
        g_npcap.freecode(&fp);
    }

    if (out_local_mac) {
        memset(out_local_mac, 0, 6);
        get_adapter_mac(dev, out_local_mac);
    }

    if (alldevs) g_npcap.freealldevs(alldevs);
    pn_log("Opened adapter: %s", dev);
    return (FrameIO *)p;
}

/* ─── frameio_close ──────────────────────────────────────────────────────── */
void frameio_close(FrameIO *fio)
{
    if (fio && g_npcap.close)
        g_npcap.close((pcap_t *)fio);
}

/* ─── frameio_send ───────────────────────────────────────────────────────── */
int frameio_send(FrameIO *fio, const uint8_t *frame, int len)
{
    if (!fio || !frame || len < ETH_HDR_LEN) return -1;
    return g_npcap.sendpacket((pcap_t *)fio, (const u_char *)frame, len);
}

/* ─── frameio_recv ───────────────────────────────────────────────────────── */
int frameio_recv(FrameIO *fio, uint8_t *buf, int buf_size, int timeout_ms)
{
    if (!fio || !buf) return -1;
    struct pcap_pkthdr *hdr = NULL;
    const u_char *data = NULL;

    uint64_t deadline = pn_time_ns() +
                        (uint64_t)(timeout_ms > 0 ? timeout_ms : 0) * 1000000ULL;
    do {
        int rc = g_npcap.next_ex((pcap_t *)fio, &hdr, &data);
        if (rc == 1 && hdr && data) {
            int copy = hdr->caplen < (uint32_t)buf_size
                     ? (int)hdr->caplen : buf_size;
            memcpy(buf, data, (size_t)copy);
            return copy;
        }
        if (rc == -1) return -1;
        if (timeout_ms == 0) return 0;
    } while (timeout_ms < 0 || pn_time_ns() < deadline);

    return 0;
}

/* ─── frameio_enum_adapters ──────────────────────────────────────────────── */
int frameio_enum_adapters(char names[][256], int max_count, int *out_count)
{
    if (frameio_load_npcap() != 0) return -1;
    char errbuf[PCAP_ERRBUF_SIZE];
    pcap_if_t *alldevs = NULL;
    if (g_npcap.findalldevs(&alldevs, errbuf) != 0) return -1;

    int n = 0;
    for (pcap_if_t *d = alldevs; d && n < max_count; d = d->next, n++)
        pn_strlcpy(names[n], d->name, 256);

    if (out_count) *out_count = n;
    g_npcap.freealldevs(alldevs);
    return 0;
}

/* ─── Profinet DCP multicast MAC ─────────────────────────────────────────── */
const uint8_t PN_DCP_MULTICAST_MAC[6] = {0x01, 0x0E, 0xCF, 0x00, 0x00, 0x00};

/* ─── Ethernet header builder ────────────────────────────────────────────── */
int eth_build_header(uint8_t *buf, const uint8_t dst[6],
                     const uint8_t src[6], uint16_t ethertype)
{
    memcpy(buf,     dst, 6);
    memcpy(buf + 6, src, 6);
    buf[12] = (uint8_t)(ethertype >> 8);
    buf[13] = (uint8_t)(ethertype);
    return ETH_HDR_LEN;
}

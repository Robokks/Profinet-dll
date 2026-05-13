#ifndef FRAME_IO_H
#define FRAME_IO_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ─── Ethernet constants ─────────────────────────────────────────────────── */
#define ETHERTYPE_PROFINET  0x8892u
#define ETH_ALEN            6
#define ETH_HDR_LEN         14
#define ETH_MIN_FRAME       60
#define ETH_MAX_FRAME       1514

/* Profinet DCP multicast MAC */
extern const uint8_t PN_DCP_MULTICAST_MAC[6];

/* ─── Npcap function table (loaded at runtime) ───────────────────────────── */
typedef struct NpcapFuncs NpcapFuncs;

/* Load wpcap.dll at runtime; returns 0 on success. */
int  frameio_load_npcap(void);
void frameio_unload_npcap(void);

/* ─── Per-adapter handle ─────────────────────────────────────────────────── */
typedef void FrameIO;  /* opaque, actually pcap_t* */

/* Open an adapter by name (Npcap "\Device\NPF_{...}" name).
 * Pass NULL to open first available adapter.
 * out_local_mac: filled with the NIC's MAC address.
 * Returns non-NULL on success. */
FrameIO *frameio_open(const char *adapter_name, uint8_t out_local_mac[6],
                      char *errbuf);

void frameio_close(FrameIO *fio);

/* Send a raw Ethernet frame. Returns 0 on success. */
int frameio_send(FrameIO *fio, const uint8_t *frame, int len);

/* Receive next Ethernet frame with EtherType 0x8892 (Profinet RT).
 * timeout_ms: 0 = non-blocking, <0 = block forever.
 * Returns frame length on success, 0 = timeout, -1 = error. */
int frameio_recv(FrameIO *fio, uint8_t *buf, int buf_size, int timeout_ms);

/* ─── Adapter enumeration ─────────────────────────────────────────────────── */
int frameio_enum_adapters(char names[][256], int max_count, int *out_count);

/* ─── Ethernet frame builder helpers ─────────────────────────────────────── */
/* Write 14-byte Ethernet header into buf. Returns 14. */
int eth_build_header(uint8_t *buf, const uint8_t dst[6],
                     const uint8_t src[6], uint16_t ethertype);

#ifdef __cplusplus
}
#endif
#endif /* FRAME_IO_H */

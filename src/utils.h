#ifndef PN_UTILS_H
#define PN_UTILS_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ─── Byte-order helpers (Profinet wire = big-endian except RPC data) ─────── */
static inline uint16_t pn_htons(uint16_t v)
{
    return (uint16_t)((v >> 8) | (v << 8));
}

static inline uint32_t pn_htonl(uint32_t v)
{
    return ((v & 0xFF000000u) >> 24) |
           ((v & 0x00FF0000u) >>  8) |
           ((v & 0x0000FF00u) <<  8) |
           ((v & 0x000000FFu) << 24);
}

#define pn_ntohs(v) pn_htons(v)
#define pn_ntohl(v) pn_htonl(v)

/* Write big-endian u16/u32 into byte buffer at offset. */
static inline void pn_put_u16be(uint8_t *buf, int off, uint16_t v)
{
    buf[off]     = (uint8_t)(v >> 8);
    buf[off + 1] = (uint8_t)(v);
}

static inline void pn_put_u32be(uint8_t *buf, int off, uint32_t v)
{
    buf[off]     = (uint8_t)(v >> 24);
    buf[off + 1] = (uint8_t)(v >> 16);
    buf[off + 2] = (uint8_t)(v >>  8);
    buf[off + 3] = (uint8_t)(v);
}

static inline uint16_t pn_get_u16be(const uint8_t *buf, int off)
{
    return (uint16_t)((buf[off] << 8) | buf[off + 1]);
}

static inline uint32_t pn_get_u32be(const uint8_t *buf, int off)
{
    return ((uint32_t)buf[off] << 24) | ((uint32_t)buf[off + 1] << 16) |
           ((uint32_t)buf[off + 2] << 8) | (uint32_t)buf[off + 3];
}

/* Write little-endian u16/u32 (used in RPC headers). */
static inline void pn_put_u16le(uint8_t *buf, int off, uint16_t v)
{
    buf[off]     = (uint8_t)(v);
    buf[off + 1] = (uint8_t)(v >> 8);
}

static inline void pn_put_u32le(uint8_t *buf, int off, uint32_t v)
{
    buf[off]     = (uint8_t)(v);
    buf[off + 1] = (uint8_t)(v >>  8);
    buf[off + 2] = (uint8_t)(v >> 16);
    buf[off + 3] = (uint8_t)(v >> 24);
}

static inline uint16_t pn_get_u16le(const uint8_t *buf, int off)
{
    return (uint16_t)(buf[off] | ((uint16_t)buf[off + 1] << 8));
}

static inline uint32_t pn_get_u32le(const uint8_t *buf, int off)
{
    return (uint32_t)buf[off]           |
           ((uint32_t)buf[off + 1] <<  8) |
           ((uint32_t)buf[off + 2] << 16) |
           ((uint32_t)buf[off + 3] << 24);
}

/* ─── String helpers ─────────────────────────────────────────────────────── */
void    pn_mac_to_str(const uint8_t mac[6], char *buf);
int     pn_str_to_mac(const char *str, uint8_t mac[6]);
int     pn_str_to_ip(const char *str, uint32_t *out_ip);
void    pn_ip_to_str(uint32_t ip, char *buf);

/* ─── UUID ───────────────────────────────────────────────────────────────── */
void pn_uuid_generate(uint8_t uuid[16]);
void pn_uuid_to_str(const uint8_t uuid[16], char *buf);

/* ─── High-resolution timestamp (nanoseconds) ───────────────────────────── */
uint64_t pn_time_ns(void);

/* ─── Thread-safe debug log (OutputDebugStringA on Windows) ─────────────── */
void pn_log(const char *fmt, ...);

/* ─── Safe string copy ───────────────────────────────────────────────────── */
void pn_strlcpy(char *dst, const char *src, size_t size);

#ifdef __cplusplus
}
#endif
#endif /* PN_UTILS_H */

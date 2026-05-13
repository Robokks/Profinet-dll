#include "utils.h"

#include <stdio.h>
#include <string.h>
#include <stdarg.h>

#ifdef _WIN32
#include <winsock2.h>
#include <windows.h>
#else
#include <time.h>
#include <arpa/inet.h>
#include <stdlib.h>
#endif

void pn_mac_to_str(const uint8_t mac[6], char *buf)
{
    snprintf(buf, 18, "%02X:%02X:%02X:%02X:%02X:%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

int pn_str_to_mac(const char *str, uint8_t mac[6])
{
    unsigned int v[6];
    if (sscanf(str, "%02X:%02X:%02X:%02X:%02X:%02X",
               &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) != 6)
        return -1;
    for (int i = 0; i < 6; i++)
        mac[i] = (uint8_t)v[i];
    return 0;
}

int pn_str_to_ip(const char *str, uint32_t *out_ip)
{
    unsigned int a, b, c, d;
    if (sscanf(str, "%u.%u.%u.%u", &a, &b, &c, &d) != 4)
        return -1;
    if (a > 255 || b > 255 || c > 255 || d > 255)
        return -1;
    /* Store as big-endian (network byte order) */
    *out_ip = ((uint32_t)a << 24) | ((uint32_t)b << 16) |
              ((uint32_t)c << 8)  |  (uint32_t)d;
    return 0;
}

void pn_ip_to_str(uint32_t ip, char *buf)
{
    snprintf(buf, 16, "%u.%u.%u.%u",
             (ip >> 24) & 0xFF, (ip >> 16) & 0xFF,
             (ip >>  8) & 0xFF,  ip         & 0xFF);
}

void pn_uuid_generate(uint8_t uuid[16])
{
#ifdef _WIN32
    HCRYPTPROV prov = 0;
    if (CryptAcquireContextA(&prov, NULL, NULL, PROV_RSA_FULL,
                              CRYPT_VERIFYCONTEXT | CRYPT_SILENT)) {
        CryptGenRandom(prov, 16, uuid);
        CryptReleaseContext(prov, 0);
    } else {
        /* Fallback: time-seeded pseudo-random */
        ULARGE_INTEGER t;
        GetSystemTimeAsFileTime((FILETIME *)&t);
        srand((unsigned)t.LowPart);
        for (int i = 0; i < 16; i++)
            uuid[i] = (uint8_t)(rand() & 0xFF);
    }
#else
    srand((unsigned)pn_time_ns());
    for (int i = 0; i < 16; i++)
        uuid[i] = (uint8_t)(rand() & 0xFF);
#endif
    /* RFC 4122 version 4 */
    uuid[6] = (uuid[6] & 0x0F) | 0x40;
    uuid[8] = (uuid[8] & 0x3F) | 0x80;
}

void pn_uuid_to_str(const uint8_t uuid[16], char *buf)
{
    snprintf(buf, 37,
             "%02X%02X%02X%02X-%02X%02X-%02X%02X-%02X%02X-"
             "%02X%02X%02X%02X%02X%02X",
             uuid[0],  uuid[1],  uuid[2],  uuid[3],
             uuid[4],  uuid[5],  uuid[6],  uuid[7],
             uuid[8],  uuid[9],  uuid[10], uuid[11],
             uuid[12], uuid[13], uuid[14], uuid[15]);
}

uint64_t pn_time_ns(void)
{
#ifdef _WIN32
    static LARGE_INTEGER freq = {0};
    LARGE_INTEGER cnt;
    if (freq.QuadPart == 0)
        QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&cnt);
    return (uint64_t)(cnt.QuadPart * 1000000000LL / freq.QuadPart);
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
#endif
}

void pn_log(const char *fmt, ...)
{
#ifdef _WIN32
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    OutputDebugStringA("[PN] ");
    OutputDebugStringA(buf);
    OutputDebugStringA("\n");
#else
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "[PN] ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
#endif
}

void pn_strlcpy(char *dst, const char *src, size_t size)
{
    if (!size) return;
    size_t i;
    for (i = 0; i + 1 < size && src[i]; i++)
        dst[i] = src[i];
    dst[i] = '\0';
}

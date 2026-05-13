/* Platform-agnostic utility functions: string helpers, UUID formatting,
 * endian helpers (inline in utils.h), strlcpy. */

#include "utils.h"
#include <stdio.h>
#include <string.h>

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
    *out_ip = ((uint32_t)a << 24) | ((uint32_t)b << 16) |
              ((uint32_t)c <<  8) |  (uint32_t)d;
    return 0;
}

void pn_ip_to_str(uint32_t ip, char *buf)
{
    snprintf(buf, 16, "%u.%u.%u.%u",
             (ip >> 24) & 0xFF, (ip >> 16) & 0xFF,
             (ip >>  8) & 0xFF,  ip         & 0xFF);
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

void pn_strlcpy(char *dst, const char *src, size_t size)
{
    if (!size) return;
    size_t i;
    for (i = 0; i + 1 < size && src[i]; i++)
        dst[i] = src[i];
    dst[i] = '\0';
}

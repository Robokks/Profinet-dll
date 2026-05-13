/* Windows-specific utilities: UUID (CryptGenRandom), timestamp
 * (QueryPerformanceCounter), debug log (OutputDebugStringA). */

#include "utils.h"
#include <winsock2.h>
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

void pn_uuid_generate(uint8_t uuid[16])
{
    HCRYPTPROV prov = 0;
    if (CryptAcquireContextA(&prov, NULL, NULL, PROV_RSA_FULL,
                              CRYPT_VERIFYCONTEXT | CRYPT_SILENT)) {
        CryptGenRandom(prov, 16, uuid);
        CryptReleaseContext(prov, 0);
    } else {
        ULARGE_INTEGER t;
        GetSystemTimeAsFileTime((FILETIME *)&t);
        srand((unsigned)t.LowPart);
        for (int i = 0; i < 16; i++)
            uuid[i] = (uint8_t)(rand() & 0xFF);
    }
    /* RFC 4122 version 4 */
    uuid[6] = (uuid[6] & 0x0F) | 0x40;
    uuid[8] = (uuid[8] & 0x3F) | 0x80;
}

uint64_t pn_time_ns(void)
{
    static LARGE_INTEGER freq = {0};
    LARGE_INTEGER cnt;
    if (freq.QuadPart == 0)
        QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&cnt);
    return (uint64_t)(cnt.QuadPart * 1000000000LL / freq.QuadPart);
}

void pn_log(const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    OutputDebugStringA("[PN] ");
    OutputDebugStringA(buf);
    OutputDebugStringA("\n");
}

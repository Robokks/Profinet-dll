/* NI Linux RT utilities: UUID (/dev/urandom), timestamp
 * (clock_gettime MONOTONIC), log (fprintf stderr). */

#include "utils.h"
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>

void pn_uuid_generate(uint8_t uuid[16])
{
    int fd = open("/dev/urandom", O_RDONLY);
    if (fd >= 0) {
        ssize_t n = read(fd, uuid, 16);
        close(fd);
        if (n == 16) {
            /* RFC 4122 version 4 */
            uuid[6] = (uuid[6] & 0x0F) | 0x40;
            uuid[8] = (uuid[8] & 0x3F) | 0x80;
            return;
        }
    }
    /* Fallback: time-seeded PRNG */
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    srand((unsigned)(ts.tv_nsec ^ ts.tv_sec));
    for (int i = 0; i < 16; i++)
        uuid[i] = (uint8_t)(rand() & 0xFF);
    uuid[6] = (uuid[6] & 0x0F) | 0x40;
    uuid[8] = (uuid[8] & 0x3F) | 0x80;
}

uint64_t pn_time_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

void pn_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "[PN] ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
}

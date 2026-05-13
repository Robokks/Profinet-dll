/* NI Linux RT raw Ethernet I/O using AF_PACKET sockets.
 * No external libraries needed — kernel built-in.
 * Requires CAP_NET_RAW (NI Linux RT runs LabVIEW as root). */

#include "frame_io.h"
#include "utils.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <sys/time.h>
#include <net/if.h>
#include <net/ethernet.h>
#include <arpa/inet.h>
#include <ifaddrs.h>
#include <linux/if_packet.h>
#include <linux/if_ether.h>

/* Internal handle stored as FrameIO* */
typedef struct {
    int  fd;
    int  ifindex;
    char ifname[IFNAMSIZ];
} LinuxFrameIO;

const uint8_t PN_DCP_MULTICAST_MAC[6] = {0x01, 0x0E, 0xCF, 0x00, 0x00, 0x00};

/* ─── Get interface index and MAC via ioctl ──────────────────────────────── */
static int get_iface_info(int fd, const char *ifname,
                           int *out_ifindex, uint8_t out_mac[6])
{
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, ifname, IFNAMSIZ - 1);

    if (ioctl(fd, SIOCGIFINDEX, &ifr) < 0) return -1;
    *out_ifindex = ifr.ifr_ifindex;

    if (ioctl(fd, SIOCGIFHWADDR, &ifr) < 0) return -1;
    memcpy(out_mac, ifr.ifr_hwaddr.sa_data, 6);
    return 0;
}

/* ─── Find first non-loopback Ethernet interface ─────────────────────────── */
static int find_first_adapter(char *ifname_out)
{
    struct ifaddrs *ifap = NULL;
    if (getifaddrs(&ifap) != 0) return -1;

    int found = -1;
    for (struct ifaddrs *ifa = ifap; ifa; ifa = ifa->ifa_next) {
        if (!ifa->ifa_name) continue;
        if (ifa->ifa_flags & IFF_LOOPBACK) continue;
        if (!(ifa->ifa_flags & IFF_UP)) continue;
        /* Accept only AF_PACKET (link-layer) entries */
        if (!ifa->ifa_addr || ifa->ifa_addr->sa_family != AF_PACKET) continue;
        strncpy(ifname_out, ifa->ifa_name, IFNAMSIZ - 1);
        found = 0;
        break;
    }
    freeifaddrs(ifap);
    return found;
}

/* ─── frameio_open ───────────────────────────────────────────────────────── */
FrameIO *frameio_open(const char *adapter_name, uint8_t out_local_mac[6],
                      char *errbuf)
{
    char ifname[IFNAMSIZ] = {0};

    if (adapter_name && adapter_name[0]) {
        strncpy(ifname, adapter_name, IFNAMSIZ - 1);
    } else {
        if (find_first_adapter(ifname) != 0) {
            if (errbuf) snprintf(errbuf, 256, "No usable network adapter found");
            return NULL;
        }
    }

    /* Open AF_PACKET socket — receives ALL EtherTypes */
    int fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
    if (fd < 0) {
        if (errbuf) snprintf(errbuf, 256, "socket(AF_PACKET): %s", strerror(errno));
        return NULL;
    }

    int ifindex = 0;
    uint8_t mac[6] = {0};
    if (get_iface_info(fd, ifname, &ifindex, mac) != 0) {
        if (errbuf) snprintf(errbuf, 256, "Interface '%s' not found", ifname);
        close(fd);
        return NULL;
    }

    /* Bind socket to this interface */
    struct sockaddr_ll sll = {0};
    sll.sll_family   = AF_PACKET;
    sll.sll_protocol = htons(ETH_P_ALL);
    sll.sll_ifindex  = ifindex;
    if (bind(fd, (struct sockaddr *)&sll, sizeof(sll)) < 0) {
        if (errbuf) snprintf(errbuf, 256, "bind AF_PACKET: %s", strerror(errno));
        close(fd);
        return NULL;
    }

    /* Default receive timeout 1ms (overridden per call via SO_RCVTIMEO) */
    struct timeval tv = { .tv_sec = 0, .tv_usec = 1000 };
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    if (out_local_mac) memcpy(out_local_mac, mac, 6);

    LinuxFrameIO *fio = (LinuxFrameIO *)malloc(sizeof(LinuxFrameIO));
    if (!fio) { close(fd); return NULL; }
    fio->fd       = fd;
    fio->ifindex  = ifindex;
    strncpy(fio->ifname, ifname, IFNAMSIZ - 1);

    pn_log("AF_PACKET opened: %s (ifindex=%d MAC=%02X:%02X:%02X:%02X:%02X:%02X)",
           ifname, ifindex,
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    return (FrameIO *)fio;
}

/* ─── frameio_close ──────────────────────────────────────────────────────── */
void frameio_close(FrameIO *fio)
{
    if (!fio) return;
    LinuxFrameIO *l = (LinuxFrameIO *)fio;
    close(l->fd);
    free(l);
}

/* ─── frameio_send ───────────────────────────────────────────────────────── */
int frameio_send(FrameIO *fio, const uint8_t *frame, int len)
{
    if (!fio || !frame || len < ETH_HDR_LEN) return -1;
    LinuxFrameIO *l = (LinuxFrameIO *)fio;

    struct sockaddr_ll sll = {0};
    sll.sll_family  = AF_PACKET;
    sll.sll_ifindex = l->ifindex;
    sll.sll_halen   = 6;
    memcpy(sll.sll_addr, frame, 6); /* destination MAC from frame */

    ssize_t sent = sendto(l->fd, frame, (size_t)len, 0,
                          (struct sockaddr *)&sll, sizeof(sll));
    return (sent == len) ? 0 : -1;
}

/* ─── frameio_recv ───────────────────────────────────────────────────────── */
int frameio_recv(FrameIO *fio, uint8_t *buf, int buf_size, int timeout_ms)
{
    if (!fio || !buf) return -1;
    LinuxFrameIO *l = (LinuxFrameIO *)fio;

    /* Set receive timeout */
    struct timeval tv;
    tv.tv_sec  = timeout_ms > 0 ? timeout_ms / 1000 : 0;
    tv.tv_usec = timeout_ms > 0 ? (timeout_ms % 1000) * 1000 : 500;
    setsockopt(l->fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    uint64_t deadline = timeout_ms > 0
                      ? pn_time_ns() + (uint64_t)timeout_ms * 1000000ULL
                      : 0;

    for (;;) {
        ssize_t n = recv(l->fd, buf, (size_t)buf_size, 0);
        if (n > 0) {
            /* User-space EtherType filter: only 0x8892 (Profinet RT) */
            if (n >= ETH_HDR_LEN) {
                uint16_t et = (uint16_t)((buf[12] << 8) | buf[13]);
                if (et == ETHERTYPE_PROFINET)
                    return (int)n;
            }
            /* Not Profinet — loop back if time remains */
        } else if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
                /* Timeout or interrupted */
            } else {
                return -1;
            }
        }

        if (timeout_ms == 0) return 0;
        if (timeout_ms > 0 && pn_time_ns() >= deadline) return 0;
    }
}

/* ─── frameio_enum_adapters ──────────────────────────────────────────────── */
int frameio_enum_adapters(char names[][256], int max_count, int *out_count)
{
    struct ifaddrs *ifap = NULL;
    if (getifaddrs(&ifap) != 0) return -1;

    int n = 0;
    for (struct ifaddrs *ifa = ifap; ifa && n < max_count; ifa = ifa->ifa_next) {
        if (!ifa->ifa_name) continue;
        if (ifa->ifa_flags & IFF_LOOPBACK) continue;
        if (!ifa->ifa_addr || ifa->ifa_addr->sa_family != AF_PACKET) continue;
        strncpy(names[n], ifa->ifa_name, 255);
        names[n][255] = '\0';
        n++;
    }
    freeifaddrs(ifap);
    if (out_count) *out_count = n;
    return 0;
}

/* ─── Ethernet header builder (shared) ──────────────────────────────────── */
int eth_build_header(uint8_t *buf, const uint8_t dst[6],
                     const uint8_t src[6], uint16_t ethertype)
{
    memcpy(buf,     dst, 6);
    memcpy(buf + 6, src, 6);
    buf[12] = (uint8_t)(ethertype >> 8);
    buf[13] = (uint8_t)(ethertype);
    return ETH_HDR_LEN;
}

/* ─── Npcap stubs (not used on Linux) ───────────────────────────────────── */
int  frameio_load_npcap(void)    { return 0; }
void frameio_unload_npcap(void)  {}

/* pcap/pcap.h — minimal stub for cross-compilation.
 * The real wpcap.dll is loaded at runtime via LoadLibrary on Windows.
 * These declarations satisfy the compiler; no linking against wpcap.lib. */
#ifndef PCAP_PCAP_H
#define PCAP_PCAP_H

#include <stdint.h>

#ifdef _WIN32
#include <winsock2.h>
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define PCAP_ERRBUF_SIZE 256

typedef struct pcap         pcap_t;
typedef struct pcap_if      pcap_if_t;
typedef struct pcap_addr    pcap_addr_t;

struct pcap_pkthdr {
    struct timeval ts;
    uint32_t caplen;
    uint32_t len;
};

struct bpf_insn {
    uint16_t code;
    uint8_t  jt;
    uint8_t  jf;
    uint32_t k;
};

struct bpf_program {
    uint32_t         bf_len;
    struct bpf_insn *bf_insns;
};

struct pcap_addr {
    struct pcap_addr *next;
    struct sockaddr  *addr;
    struct sockaddr  *netmask;
    struct sockaddr  *broadaddr;
    struct sockaddr  *dstaddr;
};

struct pcap_if {
    struct pcap_if  *next;
    char            *name;
    char            *description;
    struct pcap_addr *addresses;
    uint32_t         flags;
};

/* Function declarations — resolved at runtime via LoadLibrary */
pcap_t *pcap_open_live(const char *device, int snaplen, int promisc,
                        int to_ms, char *errbuf);
int     pcap_sendpacket(pcap_t *p, const unsigned char *buf, int size);
int     pcap_next_ex(pcap_t *p, struct pcap_pkthdr **pkt_header,
                     const unsigned char **pkt_data);
int     pcap_compile(pcap_t *p, struct bpf_program *fp, const char *str,
                     int optimize, uint32_t netmask);
int     pcap_setfilter(pcap_t *p, struct bpf_program *fp);
void    pcap_freecode(struct bpf_program *fp);
void    pcap_close(pcap_t *p);
int     pcap_findalldevs(pcap_if_t **alldevsp, char *errbuf);
void    pcap_freealldevs(pcap_if_t *alldevs);
char   *pcap_geterr(pcap_t *p);
int     pcap_setnonblock(pcap_t *p, int nonblock, char *errbuf);

#ifdef __cplusplus
}
#endif
#endif /* PCAP_PCAP_H */

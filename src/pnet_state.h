#ifndef PNET_STATE_H
#define PNET_STATE_H

#include <stdint.h>
#include "../include/profinet_api.h"

#ifdef _WIN32
/* winsock2.h must be included before windows.h */
#include <winsock2.h>
#include <windows.h>
#endif

/* Forward declaration for pcap types (resolved via frame_io at runtime) */
typedef void pcap_t_opaque;

/* Maximum IO data buffer size */
#define PN_MAX_IO_LEN   256
/* Maximum discovered devices stored */
#define PN_MAX_DEVICES  32
/* Maximum modules from GSDML */
#define PN_MAX_MODULES  64

/* ─── Connection state machine states ───────────────────────────────────── */
typedef enum {
    PN_STATE_IDLE        = 0,
    PN_STATE_DISCOVERING,
    PN_STATE_DCP_SET,
    PN_STATE_RPC_CONNECTING,
    PN_STATE_RPC_WRITING,
    PN_STATE_RPC_APP_READY,
    PN_STATE_CYCLIC_ACTIVE,
    PN_STATE_DISCONNECTING
} PN_State;

/* ─── Main controller context ────────────────────────────────────────────── */
typedef struct PN_Context {
    /* Raw Ethernet (Npcap) */
    void       *pcap;          /* pcap_t* — opaque to avoid header dep */
    uint8_t     local_mac[6];
    uint32_t    local_ip;      /* big-endian */
    char        adapter_name[256];

    /* Discovered device / AR target */
    uint8_t     device_mac[6];
    uint32_t    device_ip;     /* big-endian */
    char        station_name[240];

    /* AR parameters (filled by PN_Connect) */
    PN_ARConfig ar_cfg;
    uint8_t     ar_uuid[16];
    uint32_t    session_key;

    /* RPC state */
    uint8_t     activity_uuid[16];
    uint32_t    rpc_call_id;
#ifdef _WIN32
    SOCKET      rpc_sock;
#else
    int         rpc_sock;
#endif

    /* Cyclic IO — RT frame IDs negotiated during connect */
    uint16_t    output_frame_id;  /* controller → device */
    uint16_t    input_frame_id;   /* device → controller */

    /* Double-buffered IO data */
#ifdef _WIN32
    CRITICAL_SECTION io_lock;
#endif
    uint8_t     output_buf[PN_MAX_IO_LEN];
    uint16_t    output_len;
    uint8_t     input_buf[PN_MAX_IO_LEN];
    uint16_t    input_len;
    uint8_t     output_iops;
    uint8_t     input_iocs;

    /* Cyclic thread */
#ifdef _WIN32
    HANDLE      cyclic_thread;
    HANDLE      stop_event;
    volatile LONG cyclic_running;
#endif
    uint32_t    send_clock_factor;   /* 128 = 1ms */
    uint16_t    cycle_counter;

    /* GSDML */
    PN_ModuleDesc modules[PN_MAX_MODULES];
    int32_t       module_count;
    uint32_t      dap_ident;
    uint16_t      gsdml_send_clock;
    uint16_t      gsdml_reduction;
    uint16_t      gsdml_watchdog;

    /* Discovery cache */
    PN_DeviceInfo discovered[PN_MAX_DEVICES];
    int32_t       discovered_count;

    /* Stats */
#ifdef _WIN32
    CRITICAL_SECTION stats_lock;
#endif
    PN_Stats      stats;

    /* State */
    volatile int  state;
    char          last_error[512];
} PN_Context;

/* ─── Convenience cast ───────────────────────────────────────────────────── */
static inline PN_Context *pn_ctx(PN_HANDLE h) { return (PN_Context *)h; }

/* ─── Internal state helpers ─────────────────────────────────────────────── */
void pn_set_error(PN_Context *ctx, const char *fmt, ...);

#endif /* PNET_STATE_H */

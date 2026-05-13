/* pn_device.h — Profinet IO Device (slave) state and API for VFD simulator.
 * Implements DCP server, RPC/CM server, and cyclic RT exchange. */

#ifndef PN_DEVICE_H
#define PN_DEVICE_H

#include <stdint.h>
#include <winsock2.h>
#include <windows.h>
#include "../src/frame_io.h"

/* ─── Window notification messages (PostMessage from threads → GUI) ───────── */
#define WM_DEV_BASE           (WM_USER + 100)
#define WM_DEV_STATE_CHANGED  (WM_DEV_BASE + 0)   /* connection state changed */
#define WM_DEV_DCP_SETIP      (WM_DEV_BASE + 1)   /* DCP SetIP received */
#define WM_DEV_DCP_SETNAME    (WM_DEV_BASE + 2)   /* DCP SetName received */
#define WM_DEV_IO_UPDATE      (WM_DEV_BASE + 3)   /* new stw1/nsoll decoded */
#define WM_DEV_LOG            (WM_DEV_BASE + 4)   /* log string (lParam=heap ptr) */

/* ─── Connection state ────────────────────────────────────────────────────── */
typedef enum {
    DEV_IDLE       = 0,
    DEV_LISTENING  = 1,
    DEV_CONNECTED  = 2,
    DEV_CYCLIC     = 3
} DevConnState;

/* ─── Main device state ───────────────────────────────────────────────────── */
typedef struct {
    /* Identity */
    uint8_t  local_mac[6];
    uint32_t ip;            /* network byte order */
    uint32_t subnet;
    uint32_t gateway;
    char     station_name[240];
    uint16_t vendor_id;     /* default 0x002A */
    uint16_t device_id;     /* default 0x0001 */

    /* Raw Ethernet via Npcap */
    FrameIO *pcap;

    /* RPC/CM UDP socket */
    SOCKET            rpc_sock;
    struct sockaddr_in ctrl_addr;   /* controller UDP endpoint (for CControl) */

    /* AR (Application Relationship) state */
    DevConnState state;
    uint8_t  ar_uuid[16];
    uint16_t session_key;
    uint8_t  ctrl_mac[6];
    uint16_t output_frame_id;   /* controller → device RT frame ID */
    uint16_t input_frame_id;    /* device → controller RT frame ID */
    uint16_t iocr_ref_out;
    uint16_t iocr_ref_in;
    uint16_t send_clock_factor; /* 128 → 4 ms */
    uint16_t data_length;

    /* IO double-buffer */
    CRITICAL_SECTION io_lock;
    uint8_t  rx_data[256];      /* last received output frame payload */
    uint8_t  tx_data[256];      /* current input frame payload to send */
    uint16_t cycle_counter;

    /* PROFIdrive Telegram 1 values */
    int      auto_sim;          /* 1 = auto-compute ZSW1/NIST_A from STW1/NSOLL */
    uint16_t stw1, nsoll;      /* received from controller */
    uint16_t zsw1, nist;       /* sent to controller */

    /* Statistics */
    CRITICAL_SECTION stats_lock;
    volatile LONG    frames_rx;
    volatile LONG    frames_tx;
    volatile LONG    missed;

    /* Background threads */
    HANDLE   pkt_thread;    /* DCP + cyclic RX */
    HANDLE   rpc_thread;    /* RPC/CM UDP server */
    HANDLE   cyclic_thread; /* cyclic TX */
    HANDLE   stop_event;    /* manual-reset, signals threads to exit */
    volatile LONG running;

    char     last_error[512];
    HWND     notify_hwnd;
} DevState;

/* ─── Public API ──────────────────────────────────────────────────────────── */
int  dev_init(DevState *dev, const char *adapter, HWND hwnd);
void dev_stop(DevState *dev);
void dev_cleanup(DevState *dev);

void dev_set_ip(DevState *dev, uint32_t ip, uint32_t subnet, uint32_t gw);
void dev_set_name(DevState *dev, const char *name);
void dev_set_tx_manual(DevState *dev, uint16_t zsw1, uint16_t nist);
void dev_set_auto_sim(DevState *dev, int enable);

void dev_get_io(DevState *dev, uint16_t *stw1, uint16_t *nsoll,
                uint16_t *zsw1, uint16_t *nist);
void dev_get_stats(DevState *dev, uint64_t *rx, uint64_t *tx, uint64_t *missed);

#endif /* PN_DEVICE_H */

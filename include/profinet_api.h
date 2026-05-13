#ifndef PROFINET_API_H
#define PROFINET_API_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

/* ─── DLL export/import ──────────────────────────────────────────────────── */
#ifdef _WIN32
#  ifdef PROFINET_DLL_EXPORTS
#    define PNAPI __declspec(dllexport) __stdcall
#  else
#    define PNAPI __declspec(dllimport) __stdcall
#  endif
#else
#  define PNAPI
#endif

/* ─── Return codes ───────────────────────────────────────────────────────── */
#define PN_OK                      0
#define PN_ERR_INVALID_PARAM      -1
#define PN_ERR_NOT_INITIALIZED    -2
#define PN_ERR_ALREADY_INIT       -3
#define PN_ERR_NO_ADAPTER         -4
#define PN_ERR_PCAP_OPEN          -5
#define PN_ERR_GSDML_NOT_FOUND    -6
#define PN_ERR_GSDML_PARSE        -7
#define PN_ERR_DCP_TIMEOUT        -8
#define PN_ERR_DCP_SET_FAIL       -9
#define PN_ERR_RPC_CONNECT        -10
#define PN_ERR_RPC_TIMEOUT        -11
#define PN_ERR_CYCLIC_ALREADY     -12
#define PN_ERR_CYCLIC_NOT_RUNNING -13
#define PN_ERR_BUFFER_TOO_SMALL   -14
#define PN_ERR_NOT_CONNECTED      -15
#define PN_ERR_INTERNAL           -99

/* ─── Opaque handle (use U32 in LabVIEW — 32-bit DLL) ───────────────────── */
typedef void *PN_HANDLE;

/* ─── Discovered device info ─────────────────────────────────────────────── */
typedef struct {
    uint8_t  mac[6];
    uint8_t  _pad[2];
    char     ip_str[16];
    char     name_of_station[240];
    uint16_t vendor_id;
    uint16_t device_id;
    char     order_id[64];
} PN_DeviceInfo;

/* ─── GSDML module/submodule descriptor ─────────────────────────────────── */
typedef struct {
    uint32_t module_ident;
    char     module_name[128];
    uint32_t submodule_ident;
    char     submodule_name[128];
    uint16_t input_length;
    uint16_t output_length;
} PN_ModuleDesc;

/* ─── AR configuration for PN_Connect ───────────────────────────────────── */
typedef struct {
    uint8_t  device_mac[6];
    uint8_t  _pad[2];
    char     device_ip[16];
    uint16_t send_clock_factor; /* 128 = 1ms cycle */
    uint8_t  reduction_ratio;   /* 1 = every cycle */
    uint8_t  _pad2;
    uint16_t watchdog_factor;   /* cycles before timeout, e.g. 3 */
    uint8_t  _pad3[2];
    uint32_t api;               /* typically 0 */
    uint16_t slot;              /* slot from GSDML, typically 1 */
    uint16_t subslot;           /* subslot, typically 1 */
    uint32_t module_ident;
    uint32_t submodule_ident;
} PN_ARConfig;

/* ─── Runtime statistics ─────────────────────────────────────────────────── */
typedef struct {
    uint64_t frames_sent;
    uint64_t frames_received;
    uint64_t missed_cycles;
    uint64_t watchdog_timeouts;
    uint32_t cycle_counter;
    uint8_t  connected;
    uint8_t  cyclic_running;
    uint8_t  _pad[2];
} PN_Stats;

/* ════════════════════════════════════════════════════════════════════════════
 * Lifecycle
 * ════════════════════════════════════════════════════════════════════════════ */

/* Create a controller instance.
 * adapter_name: Windows Npcap adapter, e.g. "\\Device\\NPF_{GUID}".
 *               Pass NULL to use the first available adapter.
 * out_handle: receives opaque handle on success. */
int32_t PNAPI PN_Initialize(const char *adapter_name, PN_HANDLE *out_handle);

/* Disconnect, stop all threads, release all resources. */
int32_t PNAPI PN_Shutdown(PN_HANDLE handle);

/* Return version string "major.minor.patch" into buf. */
void    PNAPI PN_GetVersion(char *buf, uint32_t buf_size);

/* ════════════════════════════════════════════════════════════════════════════
 * Adapter enumeration
 * ════════════════════════════════════════════════════════════════════════════ */

/* Fill names[][256] with up to max_count adapter names.
 * out_count receives number found. */
int32_t PNAPI PN_EnumerateAdapters(char names[][256], int32_t max_count,
                                    int32_t *out_count);

/* Single-item accessor (easier for LabVIEW): get adapter name by index. */
int32_t PNAPI PN_GetAdapterName(int32_t index, char *buf, int32_t buf_size);

/* ════════════════════════════════════════════════════════════════════════════
 * GSDML
 * ════════════════════════════════════════════════════════════════════════════ */

/* Parse a GSDML (.xml) file. Must be called before PN_Connect. */
int32_t PNAPI PN_LoadGSDML(PN_HANDLE handle, const char *gsdml_path);

/* Retrieve module/submodule descriptors extracted from GSDML.
 * modules: caller-allocated array; max_count = capacity.
 * out_count: actual number found. */
int32_t PNAPI PN_GetModuleList(PN_HANDLE handle, PN_ModuleDesc *modules,
                                int32_t max_count, int32_t *out_count);

/* ════════════════════════════════════════════════════════════════════════════
 * DCP — Discovery and Configuration
 * ════════════════════════════════════════════════════════════════════════════ */

/* Broadcast DCP Identify-All and collect responses.
 * timeout_ms: collection window (e.g. 3000). */
int32_t PNAPI PN_DCPDiscover(PN_HANDLE handle, PN_DeviceInfo *devices,
                               int32_t max_count, int32_t *out_count,
                               uint32_t timeout_ms);

/* Per-item accessor after PN_DCPDiscover. */
int32_t PNAPI PN_GetDiscoveredDevice(PN_HANDLE handle, int32_t index,
                                      PN_DeviceInfo *out);

/* Assign IP address to a device (by MAC). */
int32_t PNAPI PN_DCPSetIP(PN_HANDLE handle, const uint8_t device_mac[6],
                            const char *ip, const char *subnet,
                            const char *gateway);

/* Assign station name to a device (by MAC). */
int32_t PNAPI PN_DCPSetName(PN_HANDLE handle, const uint8_t device_mac[6],
                              const char *station_name);

/* ════════════════════════════════════════════════════════════════════════════
 * Connection Management
 * ════════════════════════════════════════════════════════════════════════════ */

/* Establish Profinet Application Relation with device.
 * Performs RPC Connect → IODWrite → DControl(AppReady) → waits for CControl. */
int32_t PNAPI PN_Connect(PN_HANDLE handle, const PN_ARConfig *ar_config);

/* Orderly release of Application Relation. */
int32_t PNAPI PN_Disconnect(PN_HANDLE handle);

/* Returns 1 if AR established and cyclic exchange running, 0 otherwise. */
int32_t PNAPI PN_IsConnected(PN_HANDLE handle);

/* ════════════════════════════════════════════════════════════════════════════
 * Cyclic IO — Generic raw bytes
 * ════════════════════════════════════════════════════════════════════════════ */

/* Copy output data into TX buffer; sent on next cycle.
 * length must match negotiated output_length from GSDML. */
int32_t PNAPI PN_WriteOutputs(PN_HANDLE handle, const uint8_t *data,
                                uint16_t length);

/* Copy last received input data into caller buffer.
 * length must match negotiated input_length from GSDML. */
int32_t PNAPI PN_ReadInputs(PN_HANDLE handle, uint8_t *data, uint16_t length);

/* ════════════════════════════════════════════════════════════════════════════
 * Cyclic IO — PROFIdrive Telegram 1 convenience wrappers
 * ════════════════════════════════════════════════════════════════════════════ */

/* Write STW1 (control word) and NSOLL_A (speed setpoint) for next cycle.
 * NSOLL_A: 0x4000 = 100% = nominal speed, 0x2000 = 50%, etc. */
int32_t PNAPI PN_DriveSetpoint(PN_HANDLE handle, uint16_t STW1,
                                 uint16_t NSOLL_A);

/* Read ZSW1 (status word) and NIST_A (actual speed) from last cycle. */
int32_t PNAPI PN_DriveStatus(PN_HANDLE handle, uint16_t *ZSW1,
                               uint16_t *NIST_A);

/* ════════════════════════════════════════════════════════════════════════════
 * Statistics and diagnostics
 * ════════════════════════════════════════════════════════════════════════════ */

int32_t PNAPI PN_GetStats(PN_HANDLE handle, PN_Stats *out_stats);
int32_t PNAPI PN_ResetStats(PN_HANDLE handle);

/* Get last human-readable error for this handle (recommend buf_size=512). */
int32_t PNAPI PN_GetLastError(PN_HANDLE handle, char *buf, uint32_t buf_size);

/* ════════════════════════════════════════════════════════════════════════════
 * STW1 / ZSW1 bit definitions (PROFIdrive)
 * ════════════════════════════════════════════════════════════════════════════ */

/* STW1 — Control Word 1 (PC → VFD) */
#define STW1_ON              (1u << 0)  /* 0 = OFF1 (ramp down) */
#define STW1_NO_COAST        (1u << 1)  /* 0 = coast stop */
#define STW1_NO_QSTOP        (1u << 2)  /* 0 = quick stop */
#define STW1_ENABLE_OP       (1u << 3)  /* enable operation */
#define STW1_ENABLE_RAMP     (1u << 4)  /* enable ramp generator */
#define STW1_UNFREEZE_RAMP   (1u << 5)  /* unfreeze ramp output */
#define STW1_ENABLE_SETPOINT (1u << 6)  /* enable speed setpoint */
#define STW1_FAULT_ACK       (1u << 7)  /* rising edge = fault acknowledge */

/* STW1 presets */
#define STW1_READY_FOR_OP    (STW1_ON | STW1_NO_COAST | STW1_NO_QSTOP | STW1_ENABLE_OP)
#define STW1_FULL_ENABLE     (STW1_READY_FOR_OP | STW1_ENABLE_RAMP | STW1_UNFREEZE_RAMP | STW1_ENABLE_SETPOINT)
#define STW1_OFF1            (0x0000u)  /* controlled stop */
#define STW1_OFF2_COAST      (0x0000u)  /* bit1=0 → coast */
#define STW1_OFF3_QSTOP      (0x0000u)  /* bit2=0 → quick stop */

/* ZSW1 — Status Word 1 (VFD → PC) */
#define ZSW1_READY_SWITCH_ON (1u << 0)
#define ZSW1_READY           (1u << 1)
#define ZSW1_OPERATION       (1u << 2)  /* drive running */
#define ZSW1_FAULT           (1u << 3)
#define ZSW1_NO_COAST        (1u << 4)
#define ZSW1_NO_QSTOP        (1u << 5)
#define ZSW1_SWITCH_INHIBIT  (1u << 6)
#define ZSW1_WARNING         (1u << 7)
#define ZSW1_SPEED_DEVIATION (1u << 8)
#define ZSW1_CTRL_REQUESTED  (1u << 9)
#define ZSW1_SPEED_REACHED   (1u << 10)

#ifdef __cplusplus
}
#endif
#endif /* PROFINET_API_H */

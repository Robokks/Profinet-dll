#include "../include/profinet_api.h"
#include "pnet_state.h"
#include "frame_io.h"
#include "dcp.h"
#include "rpc_cm.h"
#include "rt_cyclic.h"
#include "profidrive.h"
#include "gsdml_parser.h"
#include "utils.h"

#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* ════════════════════════════════════════════════════════════════════════════
 * Lifecycle
 * ════════════════════════════════════════════════════════════════════════════ */

int32_t PNAPI PN_Initialize(const char *adapter_name, PN_HANDLE *out_handle)
{
    if (!out_handle) return PN_ERR_INVALID_PARAM;
    *out_handle = NULL;

    PN_Context *ctx = (PN_Context *)calloc(1, sizeof(PN_Context));
    if (!ctx) return PN_ERR_INTERNAL;

    PN_LOCK_INIT(ctx->io_lock);
    PN_LOCK_INIT(ctx->stats_lock);
    PN_EVENT_INIT(ctx->stop_event);
    ctx->rpc_sock      = PN_SOCK_INVALID;
    ctx->cyclic_thread = PN_THREAD_NULL;

    ctx->state = PN_STATE_IDLE;
    ctx->output_frame_id = 0xC000;
    ctx->input_frame_id  = 0xC001;
    ctx->send_clock_factor = 128;
    ctx->output_iops = RT_IOPS_GOOD;
    ctx->input_iocs  = RT_IOCS_GOOD;

    ctx->output_len = PROFIDRIVE_T1_DATA_LEN;
    ctx->input_len  = PROFIDRIVE_T1_DATA_LEN;
    profidrive_encode_t1(ctx->output_buf, PROFIDRIVE_STW1_STOP, 0);

    char errbuf[256] = {0};
    ctx->pcap = frameio_open(adapter_name, ctx->local_mac, errbuf);
    if (!ctx->pcap) {
        pn_set_error(ctx, "PN_Initialize: %s", errbuf);
        PN_LOCK_DESTROY(ctx->io_lock);
        PN_LOCK_DESTROY(ctx->stats_lock);
        PN_EVENT_DESTROY(ctx->stop_event);
        free(ctx);
        return PN_ERR_PCAP_OPEN;
    }

    if (adapter_name)
        pn_strlcpy(ctx->adapter_name, adapter_name, sizeof(ctx->adapter_name));

    *out_handle = (PN_HANDLE)ctx;
    pn_log("PN_Initialize OK (MAC=%02X:%02X:%02X:%02X:%02X:%02X)",
           ctx->local_mac[0], ctx->local_mac[1], ctx->local_mac[2],
           ctx->local_mac[3], ctx->local_mac[4], ctx->local_mac[5]);
    return PN_OK;
}

int32_t PNAPI PN_Shutdown(PN_HANDLE handle)
{
    if (!handle) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);

    PN_Disconnect(handle);

    if (ctx->pcap) {
        frameio_close((FrameIO *)ctx->pcap);
        ctx->pcap = NULL;
    }

    rpc_cm_cleanup(ctx);

    PN_EVENT_DESTROY(ctx->stop_event);
    PN_LOCK_DESTROY(ctx->io_lock);
    PN_LOCK_DESTROY(ctx->stats_lock);

    free(ctx);
    return PN_OK;
}

/* ════════════════════════════════════════════════════════════════════════════
 * Adapter enumeration
 * ════════════════════════════════════════════════════════════════════════════ */

int32_t PNAPI PN_EnumerateAdapters(char names[][256], int32_t max_count,
                                    int32_t *out_count)
{
    if (!names || max_count <= 0) return PN_ERR_INVALID_PARAM;
    int cnt = 0;
    int rc = frameio_enum_adapters(names, max_count, &cnt);
    if (out_count) *out_count = cnt;
    return rc == 0 ? PN_OK : PN_ERR_NO_ADAPTER;
}

int32_t PNAPI PN_GetAdapterName(int32_t index, char *buf, int32_t buf_size)
{
    if (!buf || buf_size <= 0 || index < 0) return PN_ERR_INVALID_PARAM;
    char names[64][256];
    int cnt = 0;
    if (frameio_enum_adapters(names, 64, &cnt) != 0) return PN_ERR_NO_ADAPTER;
    if (index >= cnt) return PN_ERR_INVALID_PARAM;
    pn_strlcpy(buf, names[index], (size_t)buf_size);
    return PN_OK;
}

/* ════════════════════════════════════════════════════════════════════════════
 * GSDML
 * ════════════════════════════════════════════════════════════════════════════ */

int32_t PNAPI PN_LoadGSDML(PN_HANDLE handle, const char *gsdml_path)
{
    if (!handle || !gsdml_path) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);

    GSDML_Device dev;
    int rc = gsdml_parse(gsdml_path, &dev);
    if (rc != PN_OK) {
        pn_set_error(ctx, "GSDML parse failed: %s", gsdml_path);
        return rc;
    }

    ctx->module_count = dev.module_count;
    memcpy(ctx->modules, dev.modules,
           (size_t)dev.module_count * sizeof(PN_ModuleDesc));
    ctx->dap_ident = dev.dap_ident;

    if (dev.send_clock > 0 && dev.send_clock <= 4096)
        ctx->send_clock_factor = dev.send_clock;
    if (dev.reduction_ratio > 0)
        ctx->ar_cfg.reduction_ratio = (uint8_t)dev.reduction_ratio;
    if (dev.watchdog_factor > 0)
        ctx->ar_cfg.watchdog_factor = dev.watchdog_factor;
    if (dev.output_frame_id)
        ctx->output_frame_id = dev.output_frame_id;
    if (dev.input_frame_id)
        ctx->input_frame_id = dev.input_frame_id;

    pn_log("GSDML loaded: %d modules, vendor=0x%04X device=0x%04X",
           ctx->module_count, dev.vendor_id, dev.device_id);
    return PN_OK;
}

int32_t PNAPI PN_GetModuleList(PN_HANDLE handle, PN_ModuleDesc *modules,
                                int32_t max_count, int32_t *out_count)
{
    if (!handle || !modules || max_count <= 0) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);
    int n = ctx->module_count < max_count ? ctx->module_count : max_count;
    memcpy(modules, ctx->modules, (size_t)n * sizeof(PN_ModuleDesc));
    if (out_count) *out_count = n;
    return PN_OK;
}

/* ════════════════════════════════════════════════════════════════════════════
 * DCP
 * ════════════════════════════════════════════════════════════════════════════ */

int32_t PNAPI PN_DCPDiscover(PN_HANDLE handle, PN_DeviceInfo *devices,
                               int32_t max_count, int32_t *out_count,
                               uint32_t timeout_ms)
{
    if (!handle) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);

    ctx->state = PN_STATE_DISCOVERING;
    int rc = dcp_discover(ctx, timeout_ms > 0 ? timeout_ms : 3000);
    ctx->state = PN_STATE_IDLE;

    if (rc != PN_OK) return rc;
    if (!devices || max_count <= 0) {
        if (out_count) *out_count = ctx->discovered_count;
        return PN_OK;
    }
    int n = ctx->discovered_count < max_count ? ctx->discovered_count : max_count;
    memcpy(devices, ctx->discovered, (size_t)n * sizeof(PN_DeviceInfo));
    if (out_count) *out_count = n;
    return PN_OK;
}

int32_t PNAPI PN_GetDiscoveredDevice(PN_HANDLE handle, int32_t index,
                                      PN_DeviceInfo *out)
{
    if (!handle || !out || index < 0) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);
    if (index >= ctx->discovered_count) return PN_ERR_INVALID_PARAM;
    *out = ctx->discovered[index];
    return PN_OK;
}

int32_t PNAPI PN_DCPSetIP(PN_HANDLE handle, const uint8_t device_mac[6],
                            const char *ip, const char *subnet,
                            const char *gateway)
{
    if (!handle || !device_mac || !ip || !subnet || !gateway)
        return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);
    uint32_t ip_n = 0, sn_n = 0, gw_n = 0;
    if (pn_str_to_ip(ip,      &ip_n) != 0) return PN_ERR_INVALID_PARAM;
    if (pn_str_to_ip(subnet,  &sn_n) != 0) return PN_ERR_INVALID_PARAM;
    if (pn_str_to_ip(gateway, &gw_n) != 0) return PN_ERR_INVALID_PARAM;
    ctx->state = PN_STATE_DCP_SET;
    int rc = dcp_set_ip(ctx, device_mac, ip_n, sn_n, gw_n);
    ctx->state = PN_STATE_IDLE;
    return rc;
}

int32_t PNAPI PN_DCPSetName(PN_HANDLE handle, const uint8_t device_mac[6],
                              const char *station_name)
{
    if (!handle || !device_mac || !station_name) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);
    ctx->state = PN_STATE_DCP_SET;
    int rc = dcp_set_name(ctx, device_mac, station_name);
    ctx->state = PN_STATE_IDLE;
    return rc;
}

/* ════════════════════════════════════════════════════════════════════════════
 * Connection
 * ════════════════════════════════════════════════════════════════════════════ */

int32_t PNAPI PN_Connect(PN_HANDLE handle, const PN_ARConfig *ar_config)
{
    if (!handle || !ar_config) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);

    if (ctx->state == PN_STATE_CYCLIC_ACTIVE)
        return PN_ERR_CYCLIC_ALREADY;

    ctx->ar_cfg = *ar_config;
    memcpy(ctx->device_mac, ar_config->device_mac, 6);
    if (pn_str_to_ip(ar_config->device_ip, &ctx->device_ip) != 0)
        return PN_ERR_INVALID_PARAM;

    if (ar_config->send_clock_factor > 0)
        ctx->send_clock_factor = ar_config->send_clock_factor;

    ctx->state = PN_STATE_RPC_CONNECTING;

    int rc = rpc_cm_init(ctx);
    if (rc != PN_OK) {
        pn_set_error(ctx, "RPC socket init failed");
        ctx->state = PN_STATE_IDLE;
        return rc;
    }

    ctx->state = PN_STATE_RPC_WRITING;
    rc = rpc_cm_connect(ctx);
    if (rc != PN_OK) {
        rpc_cm_cleanup(ctx);
        ctx->state = PN_STATE_IDLE;
        return rc;
    }

    ctx->state = PN_STATE_CYCLIC_ACTIVE;
    rc = rt_cyclic_start(ctx);
    if (rc != PN_OK) {
        rpc_cm_release(ctx);
        rpc_cm_cleanup(ctx);
        ctx->state = PN_STATE_IDLE;
        return rc;
    }

    PN_LOCK(ctx->stats_lock);
    ctx->stats.connected      = 1;
    ctx->stats.cyclic_running = 1;
    PN_UNLOCK(ctx->stats_lock);

    pn_log("PN_Connect: cyclic IO active");
    return PN_OK;
}

int32_t PNAPI PN_Disconnect(PN_HANDLE handle)
{
    if (!handle) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);

    if (ctx->state != PN_STATE_CYCLIC_ACTIVE &&
        ctx->state != PN_STATE_DISCONNECTING)
        return PN_OK;

    ctx->state = PN_STATE_DISCONNECTING;

    rt_cyclic_stop(ctx);
    rpc_cm_release(ctx);
    rpc_cm_cleanup(ctx);

    PN_LOCK(ctx->stats_lock);
    ctx->stats.connected      = 0;
    ctx->stats.cyclic_running = 0;
    PN_UNLOCK(ctx->stats_lock);

    ctx->state = PN_STATE_IDLE;
    pn_log("PN_Disconnect: done");
    return PN_OK;
}

int32_t PNAPI PN_IsConnected(PN_HANDLE handle)
{
    if (!handle) return 0;
    PN_Context *ctx = pn_ctx(handle);
    return (ctx->state == PN_STATE_CYCLIC_ACTIVE) ? 1 : 0;
}

/* ════════════════════════════════════════════════════════════════════════════
 * Cyclic IO — generic
 * ════════════════════════════════════════════════════════════════════════════ */

int32_t PNAPI PN_WriteOutputs(PN_HANDLE handle, const uint8_t *data,
                                uint16_t length)
{
    if (!handle || !data) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);
    if (ctx->state != PN_STATE_CYCLIC_ACTIVE) return PN_ERR_NOT_CONNECTED;
    if (length > PN_MAX_IO_LEN) return PN_ERR_BUFFER_TOO_SMALL;
    PN_LOCK(ctx->io_lock);
    memcpy(ctx->output_buf, data, length);
    ctx->output_len = length;
    PN_UNLOCK(ctx->io_lock);
    return PN_OK;
}

int32_t PNAPI PN_ReadInputs(PN_HANDLE handle, uint8_t *data, uint16_t length)
{
    if (!handle || !data) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);
    if (ctx->state != PN_STATE_CYCLIC_ACTIVE) return PN_ERR_NOT_CONNECTED;
    if (length > PN_MAX_IO_LEN) return PN_ERR_BUFFER_TOO_SMALL;
    PN_LOCK(ctx->io_lock);
    memcpy(data, ctx->input_buf, length < ctx->input_len ? length : ctx->input_len);
    PN_UNLOCK(ctx->io_lock);
    return PN_OK;
}

/* ════════════════════════════════════════════════════════════════════════════
 * PROFIdrive Telegram 1 convenience wrappers
 * ════════════════════════════════════════════════════════════════════════════ */

int32_t PNAPI PN_DriveSetpoint(PN_HANDLE handle, uint16_t STW1, uint16_t NSOLL_A)
{
    if (!handle) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);
    if (ctx->state != PN_STATE_CYCLIC_ACTIVE) return PN_ERR_NOT_CONNECTED;
    uint8_t buf[PROFIDRIVE_T1_DATA_LEN];
    profidrive_encode_t1(buf, STW1, NSOLL_A);
    PN_LOCK(ctx->io_lock);
    memcpy(ctx->output_buf, buf, PROFIDRIVE_T1_DATA_LEN);
    ctx->output_len = PROFIDRIVE_T1_DATA_LEN;
    PN_UNLOCK(ctx->io_lock);
    return PN_OK;
}

int32_t PNAPI PN_DriveStatus(PN_HANDLE handle, uint16_t *ZSW1, uint16_t *NIST_A)
{
    if (!handle || !ZSW1 || !NIST_A) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);
    if (ctx->state != PN_STATE_CYCLIC_ACTIVE) return PN_ERR_NOT_CONNECTED;
    uint8_t buf[PROFIDRIVE_T1_DATA_LEN] = {0};
    PN_LOCK(ctx->io_lock);
    memcpy(buf, ctx->input_buf, PROFIDRIVE_T1_DATA_LEN);
    PN_UNLOCK(ctx->io_lock);
    profidrive_decode_t1(buf, ZSW1, NIST_A);
    return PN_OK;
}

/* ════════════════════════════════════════════════════════════════════════════
 * Statistics and diagnostics
 * ════════════════════════════════════════════════════════════════════════════ */

int32_t PNAPI PN_GetStats(PN_HANDLE handle, PN_Stats *out_stats)
{
    if (!handle || !out_stats) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);
    PN_LOCK(ctx->stats_lock);
    *out_stats = ctx->stats;
    PN_UNLOCK(ctx->stats_lock);
    return PN_OK;
}

int32_t PNAPI PN_ResetStats(PN_HANDLE handle)
{
    if (!handle) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);
    PN_LOCK(ctx->stats_lock);
    memset(&ctx->stats, 0, sizeof(ctx->stats));
    ctx->stats.connected      = (uint8_t)(ctx->state == PN_STATE_CYCLIC_ACTIVE);
    ctx->stats.cyclic_running = (uint8_t)PN_ATOMIC_GET(ctx->cyclic_running);
    PN_UNLOCK(ctx->stats_lock);
    return PN_OK;
}

int32_t PNAPI PN_GetLastError(PN_HANDLE handle, char *buf, uint32_t buf_size)
{
    if (!handle || !buf || buf_size == 0) return PN_ERR_INVALID_PARAM;
    PN_Context *ctx = pn_ctx(handle);
    pn_strlcpy(buf, ctx->last_error, (size_t)buf_size);
    return PN_OK;
}

/* ════════════════════════════════════════════════════════════════════════════
 * Version
 * ════════════════════════════════════════════════════════════════════════════ */

void PNAPI PN_GetVersion(char *buf, uint32_t buf_size)
{
    if (!buf || buf_size == 0) return;
    snprintf(buf, (size_t)buf_size, "%d.%d.%d",
             PROFINET_VERSION_MAJOR,
             PROFINET_VERSION_MINOR,
             PROFINET_VERSION_PATCH);
}

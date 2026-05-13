/* Windows RT cyclic IO thread.
 * Uses CreateThread at THREAD_PRIORITY_TIME_CRITICAL and a high-resolution
 * waitable timer for deterministic 1ms Profinet RT frame exchange. */

#include "rt_cyclic.h"
#include "frame_io.h"
#include "profidrive.h"
#include "alarm.h"
#include "utils.h"
#include "platform_win.h"

#include <string.h>
#include <windows.h>
#include <timeapi.h>

/* ─── Build output RT frame ──────────────────────────────────────────────── */
static int rt_build_output(PN_Context *ctx, uint8_t *frame)
{
    int off = eth_build_header(frame, ctx->device_mac, ctx->local_mac,
                                ETHERTYPE_PROFINET);
    frame[off++] = (uint8_t)(ctx->output_frame_id >> 8);
    frame[off++] = (uint8_t)(ctx->output_frame_id);
    frame[off++] = ctx->output_iops ? ctx->output_iops : RT_IOPS_GOOD;

    PN_LOCK(ctx->io_lock);
    memcpy(frame + off, ctx->output_buf, ctx->output_len);
    off += ctx->output_len;
    PN_UNLOCK(ctx->io_lock);

    frame[off++] = RT_IOCS_GOOD;
    frame[off++] = (uint8_t)(ctx->cycle_counter >> 8);
    frame[off++] = (uint8_t)(ctx->cycle_counter);
    frame[off++] = RT_DATA_STATUS_OK;
    frame[off++] = 0x00;
    return off;
}

/* ─── Parse input RT frame ───────────────────────────────────────────────── */
static void rt_parse_input(PN_Context *ctx, const uint8_t *frame, int len)
{
    if (len < ETH_HDR_LEN + 2 + 1 + (int)ctx->input_len + 1 + 4) return;
    uint16_t fid = ((uint16_t)frame[ETH_HDR_LEN] << 8) | frame[ETH_HDR_LEN + 1];
    if (fid != ctx->input_frame_id) return;

    const uint8_t *payload = frame + ETH_HDR_LEN + 2;
    if (!(payload[0] & 0x80)) return; /* IOPS not GOOD */

    PN_LOCK(ctx->io_lock);
    memcpy(ctx->input_buf, payload + 1, ctx->input_len);
    ctx->input_iocs = payload[1 + ctx->input_len];
    PN_UNLOCK(ctx->io_lock);

    PN_LOCK(ctx->stats_lock);
    ctx->stats.frames_received++;
    PN_UNLOCK(ctx->stats_lock);
}

/* ─── Cyclic thread function ─────────────────────────────────────────────── */
static DWORD WINAPI rt_thread_func(LPVOID arg)
{
    PN_Context *ctx = (PN_Context *)arg;

    /* Send clock period: SendClockFactor × 31.25µs */
    uint64_t period_us = (uint64_t)ctx->send_clock_factor * 3125u / 100u;
    if (period_us < 250) period_us = 1000;

    /* High-resolution waitable timer (Windows 10 1803+), fallback to standard */
    HANDLE timer = CreateWaitableTimerExW(NULL, NULL,
                       CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    if (!timer)
        timer = CreateWaitableTimerW(NULL, TRUE, NULL);

    uint8_t txframe[ETH_MAX_FRAME];
    uint8_t rxframe[ETH_MAX_FRAME];

    uint64_t next_wake_ns = pn_time_ns() + period_us * 1000ULL;

    while (WaitForSingleObject(ctx->stop_event, 0) == WAIT_TIMEOUT) {
        /* ── Send output frame ── */
        int flen = rt_build_output(ctx, txframe);
        if (flen < ETH_MIN_FRAME) {
            memset(txframe + flen, 0, (size_t)(ETH_MIN_FRAME - flen));
            flen = ETH_MIN_FRAME;
        }
        frameio_send((FrameIO *)ctx->pcap, txframe, flen);

        PN_LOCK(ctx->stats_lock);
        ctx->stats.frames_sent++;
        ctx->cycle_counter++;
        ctx->stats.cycle_counter = ctx->cycle_counter;
        PN_UNLOCK(ctx->stats_lock);

        /* ── Receive with half-cycle timeout ── */
        int rlen = frameio_recv((FrameIO *)ctx->pcap, rxframe,
                                 (int)sizeof(rxframe),
                                 (int)(period_us / 2000));
        if (rlen > ETH_HDR_LEN + 2) {
            uint16_t fid = ((uint16_t)rxframe[ETH_HDR_LEN] << 8) |
                            rxframe[ETH_HDR_LEN + 1];
            if (fid == ctx->input_frame_id)
                rt_parse_input(ctx, rxframe, rlen);
            else
                alarm_handle_frame(ctx, rxframe, rlen);
        }

        /* ── Precise sleep until next period ── */
        if (timer) {
            int64_t due = -(int64_t)((next_wake_ns - pn_time_ns()) / 100ULL);
            if (due < 0) {
                PN_LOCK(ctx->stats_lock);
                ctx->stats.missed_cycles++;
                PN_UNLOCK(ctx->stats_lock);
                due = 0;
            }
            LARGE_INTEGER li;
            li.QuadPart = due;
            SetWaitableTimer(timer, &li, 0, NULL, NULL, FALSE);
            WaitForSingleObject(timer, (DWORD)(period_us / 1000 + 2));
        } else {
            while (pn_time_ns() < next_wake_ns) ; /* busy spin fallback */
        }
        next_wake_ns += period_us * 1000ULL;
    }

    if (timer) CloseHandle(timer);
    PN_ATOMIC_SET(ctx->cyclic_running, 0);
    pn_log("RT cyclic thread stopped");
    return 0;
}

/* ─── Public: start ──────────────────────────────────────────────────────── */
int rt_cyclic_start(PN_Context *ctx)
{
    if (PN_ATOMIC_SET(ctx->cyclic_running, 1) == 1) return PN_ERR_CYCLIC_ALREADY;

    ResetEvent(ctx->stop_event);

    ctx->cycle_counter = 0;
    ctx->output_iops   = RT_IOPS_GOOD;
    ctx->input_iocs    = RT_IOCS_GOOD;

    if (ctx->output_len == 0) ctx->output_len = PROFIDRIVE_T1_DATA_LEN;
    if (ctx->input_len  == 0) ctx->input_len  = PROFIDRIVE_T1_DATA_LEN;

    profidrive_encode_t1(ctx->output_buf, PROFIDRIVE_STW1_STOP, 0);

    ctx->cyclic_thread = CreateThread(NULL, 0, rt_thread_func, ctx, 0, NULL);
    if (!ctx->cyclic_thread) {
        PN_ATOMIC_SET(ctx->cyclic_running, 0);
        return PN_ERR_INTERNAL;
    }
    SetThreadPriority(ctx->cyclic_thread, THREAD_PRIORITY_TIME_CRITICAL);

    pn_log("RT cyclic thread started (period=%llu µs)",
           (unsigned long long)((uint64_t)ctx->send_clock_factor * 3125u / 100u));
    return PN_OK;
}

/* ─── Public: stop ───────────────────────────────────────────────────────── */
void rt_cyclic_stop(PN_Context *ctx)
{
    if (!PN_ATOMIC_GET(ctx->cyclic_running)) return;
    SetEvent(ctx->stop_event);
    if (ctx->cyclic_thread) {
        WaitForSingleObject(ctx->cyclic_thread, 3000);
        CloseHandle(ctx->cyclic_thread);
        ctx->cyclic_thread = PN_THREAD_NULL;
    }
}

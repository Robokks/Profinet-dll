/* NI Linux RT cyclic IO thread.
 * Uses pthreads with SCHED_FIFO and clock_nanosleep(TIMER_ABSTIME)
 * for deterministic 1ms Profinet RT frame exchange. */

#include "rt_cyclic.h"
#include "frame_io.h"
#include "profidrive.h"
#include "alarm.h"
#include "utils.h"

#include <string.h>
#include <pthread.h>
#include <sched.h>
#include <time.h>
#include <stdatomic.h>

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

/* ─── Add nanoseconds to a timespec ─────────────────────────────────────── */
static void timespec_add_ns(struct timespec *ts, uint64_t ns)
{
    ts->tv_nsec += (long)ns;
    while (ts->tv_nsec >= 1000000000L) {
        ts->tv_sec++;
        ts->tv_nsec -= 1000000000L;
    }
}

/* ─── Cyclic thread function ─────────────────────────────────────────────── */
static void *rt_thread_func(void *arg)
{
    PN_Context *ctx = (PN_Context *)arg;

    /* Period: SendClockFactor × 31.25µs = SendClockFactor × 31250ns */
    uint64_t period_ns = (uint64_t)ctx->send_clock_factor * 31250ULL;
    if (period_ns < 250000ULL) period_ns = 1000000ULL; /* minimum 250µs */

    /* Elevate to SCHED_FIFO real-time priority */
    struct sched_param sp = { .sched_priority = 60 };
    if (pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp) != 0)
        pn_log("rt_cyclic: WARNING — could not set SCHED_FIFO (need root)");

    uint8_t txframe[ETH_MAX_FRAME];
    uint8_t rxframe[ETH_MAX_FRAME];

    /* Anchor first wake time */
    struct timespec next;
    clock_gettime(CLOCK_MONOTONIC, &next);

    while (PN_ATOMIC_GET(ctx->cyclic_running)) {
        timespec_add_ns(&next, period_ns);

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
        int half_ms = (int)(period_ns / 2000000ULL);
        if (half_ms < 1) half_ms = 1;
        int rlen = frameio_recv((FrameIO *)ctx->pcap, rxframe,
                                 (int)sizeof(rxframe), half_ms);
        if (rlen > ETH_HDR_LEN + 2) {
            uint16_t fid = ((uint16_t)rxframe[ETH_HDR_LEN] << 8) |
                            rxframe[ETH_HDR_LEN + 1];
            if (fid == ctx->input_frame_id)
                rt_parse_input(ctx, rxframe, rlen);
            else
                alarm_handle_frame(ctx, rxframe, rlen);
        }

        /* ── Check for missed cycle ── */
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        int64_t remaining = ((int64_t)next.tv_sec  - now.tv_sec)  * 1000000000LL
                          + ((int64_t)next.tv_nsec - now.tv_nsec);
        if (remaining < 0) {
            PN_LOCK(ctx->stats_lock);
            ctx->stats.missed_cycles++;
            PN_UNLOCK(ctx->stats_lock);
        }

        /* ── Sleep until next absolute wake time ── */
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
    }

    pn_log("RT cyclic thread stopped");
    return NULL;
}

/* ─── Public: start ──────────────────────────────────────────────────────── */
int rt_cyclic_start(PN_Context *ctx)
{
    int expected = 0;
    if (!atomic_compare_exchange_strong(
            (atomic_int *)&ctx->cyclic_running, &expected, 1))
        return PN_ERR_CYCLIC_ALREADY;

    ctx->cycle_counter = 0;
    ctx->output_iops   = RT_IOPS_GOOD;
    ctx->input_iocs    = RT_IOCS_GOOD;

    if (ctx->output_len == 0) ctx->output_len = PROFIDRIVE_T1_DATA_LEN;
    if (ctx->input_len  == 0) ctx->input_len  = PROFIDRIVE_T1_DATA_LEN;

    profidrive_encode_t1(ctx->output_buf, PROFIDRIVE_STW1_STOP, 0);

    pthread_attr_t attr;
    pthread_attr_init(&attr);
    /* Request SCHED_FIFO — may silently downgrade if not root */
    pthread_attr_setinheritsched(&attr, PTHREAD_EXPLICIT_SCHED);
    pthread_attr_setschedpolicy(&attr, SCHED_FIFO);
    struct sched_param sp = { .sched_priority = 60 };
    pthread_attr_setschedparam(&attr, &sp);

    int rc = pthread_create(&ctx->cyclic_thread, &attr, rt_thread_func, ctx);
    pthread_attr_destroy(&attr);

    if (rc != 0) {
        PN_ATOMIC_SET(ctx->cyclic_running, 0);
        return PN_ERR_INTERNAL;
    }

    pn_log("RT cyclic thread started (period=%llu µs)",
           (unsigned long long)((uint64_t)ctx->send_clock_factor * 3125ULL / 100ULL));
    return PN_OK;
}

/* ─── Public: stop ───────────────────────────────────────────────────────── */
void rt_cyclic_stop(PN_Context *ctx)
{
    if (!PN_ATOMIC_GET(ctx->cyclic_running)) return;
    PN_ATOMIC_SET(ctx->cyclic_running, 0);
    pthread_join(ctx->cyclic_thread, NULL);
    ctx->cyclic_thread = PN_THREAD_NULL;
}

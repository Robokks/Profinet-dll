#ifndef PROFIDRIVE_H
#define PROFIDRIVE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* PROFIdrive Telegram 1 — 4 bytes each direction (excluding IOPS/IOCS bytes).
 * Wire layout (little-endian per IEC 61800-7-304):
 *   Output (controller→VFD): [STW1 lo][STW1 hi][NSOLL_A lo][NSOLL_A hi]
 *   Input  (VFD→controller): [ZSW1 lo][ZSW1 hi][NIST_A lo][NIST_A hi]
 * NSOLL_A/NIST_A: 0x4000 = 100% = nominal speed. */

#define PROFIDRIVE_T1_DATA_LEN  4  /* bytes, not counting IOPS/IOCS */

/* Encode output telegram into buf (4 bytes). */
void profidrive_encode_t1(uint8_t *buf, uint16_t STW1, uint16_t NSOLL_A);

/* Decode input telegram from buf (4 bytes). */
void profidrive_decode_t1(const uint8_t *buf, uint16_t *ZSW1,
                           uint16_t *NIST_A);

/* Convert speed percentage (0–10000 = 0.00%–100.00%) to NSOLL_A. */
uint16_t profidrive_pct_to_nsoll(uint16_t pct_x100);

/* Convert NIST_A to speed percentage (0–10000). */
uint16_t profidrive_nist_to_pct(uint16_t NIST_A);

/* PROFIdrive state from ZSW1 bits. */
typedef enum {
    PD_STATE_UNKNOWN              = 0,
    PD_STATE_SWITCHING_ON_INHIBIT = 1,
    PD_STATE_READY_TO_SWITCH_ON   = 2,
    PD_STATE_SWITCHED_ON          = 3,
    PD_STATE_OPERATION            = 4,
    PD_STATE_FAULT                = 5
} PDriveState;

PDriveState profidrive_get_state(uint16_t ZSW1);

/* Return STW1 needed to transition toward Operation state from current state. */
uint16_t profidrive_stw1_for_enable(PDriveState state, uint16_t speed_setpoint);

/* STW1 for controlled stop (OFF1). */
#define PROFIDRIVE_STW1_STOP  0x0476u

#ifdef __cplusplus
}
#endif
#endif /* PROFIDRIVE_H */

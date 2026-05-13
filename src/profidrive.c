#include "profidrive.h"
#include "../include/profinet_api.h"

void profidrive_encode_t1(uint8_t *buf, uint16_t STW1, uint16_t NSOLL_A)
{
    /* Little-endian on wire per PROFIdrive spec */
    buf[0] = (uint8_t)(STW1);
    buf[1] = (uint8_t)(STW1 >> 8);
    buf[2] = (uint8_t)(NSOLL_A);
    buf[3] = (uint8_t)(NSOLL_A >> 8);
}

void profidrive_decode_t1(const uint8_t *buf, uint16_t *ZSW1, uint16_t *NIST_A)
{
    if (ZSW1)  *ZSW1  = (uint16_t)(buf[0] | ((uint16_t)buf[1] << 8));
    if (NIST_A)*NIST_A = (uint16_t)(buf[2] | ((uint16_t)buf[3] << 8));
}

uint16_t profidrive_pct_to_nsoll(uint16_t pct_x100)
{
    /* pct_x100: 0=0%, 10000=100%. 0x4000 = 16384 = 100%. */
    if (pct_x100 > 10000) pct_x100 = 10000;
    return (uint16_t)((uint32_t)pct_x100 * 16384u / 10000u);
}

uint16_t profidrive_nist_to_pct(uint16_t NIST_A)
{
    return (uint16_t)((uint32_t)NIST_A * 10000u / 16384u);
}

PDriveState profidrive_get_state(uint16_t ZSW1)
{
    if (ZSW1 & ZSW1_FAULT) return PD_STATE_FAULT;
    /* State bits: bit2(op) | bit1(ready) | bit0(ready_switch_on) */
    uint8_t s = (uint8_t)(ZSW1 & 0x07u);
    switch (s) {
    case 0x00: return PD_STATE_SWITCHING_ON_INHIBIT;
    case 0x01: return PD_STATE_READY_TO_SWITCH_ON;
    case 0x03: return PD_STATE_SWITCHED_ON;
    case 0x07: return PD_STATE_OPERATION;
    default:   return PD_STATE_UNKNOWN;
    }
}

uint16_t profidrive_stw1_for_enable(PDriveState state, uint16_t speed_setpoint)
{
    (void)speed_setpoint;
    switch (state) {
    case PD_STATE_SWITCHING_ON_INHIBIT:
        /* Need OFF1 then re-enable */
        return STW1_NO_COAST | STW1_NO_QSTOP;
    case PD_STATE_READY_TO_SWITCH_ON:
        /* S2→S3: set bit 0 (ON) */
        return STW1_ON | STW1_NO_COAST | STW1_NO_QSTOP | STW1_ENABLE_OP;
    case PD_STATE_SWITCHED_ON:
        /* S3→S4: set bits 4,5,6 */
        return STW1_FULL_ENABLE;
    case PD_STATE_OPERATION:
        return STW1_FULL_ENABLE;
    default:
        return 0;
    }
}

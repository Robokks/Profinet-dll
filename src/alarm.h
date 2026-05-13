#ifndef PN_ALARM_H
#define PN_ALARM_H

#include "pnet_state.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ─── Alarm Frame IDs ────────────────────────────────────────────────────── */
#define ALARM_FRAMEID_HIGH   0xFC01u  /* high priority alarm */
#define ALARM_FRAMEID_LOW    0xFE01u  /* low  priority alarm */

/* ─── RTA (Real-Time Acyclic) PDU types ─────────────────────────────────── */
#define RTA_PDU_TYPE_DATA    0x0001u
#define RTA_PDU_TYPE_NACK    0x0002u
#define RTA_PDU_TYPE_ACK     0x0003u
#define RTA_PDU_TYPE_ERR     0x0004u

/* Handle an incoming Ethernet frame that may be an alarm.
 * Sends AlarmAck if required to keep AR alive. */
void alarm_handle_frame(PN_Context *ctx, const uint8_t *frame, int len);

#ifdef __cplusplus
}
#endif
#endif /* PN_ALARM_H */

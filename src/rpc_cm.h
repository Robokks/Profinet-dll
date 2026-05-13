#ifndef PN_RPC_CM_H
#define PN_RPC_CM_H

#include <stdint.h>
#include "pnet_state.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ─── DCE/RPC constants ──────────────────────────────────────────────────── */
#define RPC_PNIO_PORT          34964u   /* UDP port for Profinet CM */
#define RPC_VERSION            4u
#define RPC_PTYPE_REQUEST      0u
#define RPC_PTYPE_RESPONSE     2u
#define RPC_PTYPE_FAULT        3u
#define RPC_PTYPE_NOCALL       12u

#define RPC_FLAG_LAST_FRAG     0x02u
#define RPC_FLAG_FRAG          0x04u
#define RPC_FLAG_NO_FACK       0x20u

/* ─── PNIO CM operation numbers ─────────────────────────────────────────── */
#define RPC_OPNUM_CONNECT      0u
#define RPC_OPNUM_RELEASE      1u
#define RPC_OPNUM_DCONTROL     2u  /* Controller sends DControl */
#define RPC_OPNUM_CCONTROL     3u  /* Device sends CControl */
#define RPC_OPNUM_READ         4u  /* IODRead */
#define RPC_OPNUM_WRITE        5u  /* IODWrite */

/* ─── AR Block types ─────────────────────────────────────────────────────── */
#define PN_BLK_AR_REQ          0x0101u
#define PN_BLK_IOCR_REQ        0x0102u
#define PN_BLK_EXP_SUBMOD      0x0104u
#define PN_BLK_ALARM_CR_REQ    0x0103u
#define PN_BLK_AR_RES          0x8101u
#define PN_BLK_IOCR_RES        0x8102u
#define PN_BLK_MOD_DIFF        0x8104u
#define PN_BLK_ALARM_CR_RES    0x8103u

/* ─── IOCR types ─────────────────────────────────────────────────────────── */
#define IOCR_TYPE_OUTPUT       1u  /* Controller → Device */
#define IOCR_TYPE_INPUT        2u  /* Device → Controller */

/* ─── Public functions ───────────────────────────────────────────────────── */

/* Open UDP socket to device for RPC. */
int rpc_cm_init(PN_Context *ctx);

/* Close UDP socket. */
void rpc_cm_cleanup(PN_Context *ctx);

/* Perform full Connect sequence:
 *   ConnectRequest → ConnectResponse → IODWrite → DControl(AppReady)
 *   → wait for CControl(AppReady).
 * Fills ctx->output_frame_id and ctx->input_frame_id. */
int rpc_cm_connect(PN_Context *ctx);

/* Send RPC Release request and close AR. */
int rpc_cm_release(PN_Context *ctx);

#ifdef __cplusplus
}
#endif
#endif /* PN_RPC_CM_H */

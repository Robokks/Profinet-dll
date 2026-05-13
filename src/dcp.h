#ifndef PN_DCP_H
#define PN_DCP_H

#include <stdint.h>
#include "pnet_state.h"
#include "frame_io.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ─── DCP Frame IDs (big-endian in frame, stored as host uint16) ─────────── */
#define DCP_FRAMEID_IDENTIFY_REQ   0xFEFEu
#define DCP_FRAMEID_IDENTIFY_RSP   0xFEFFu
#define DCP_FRAMEID_SET_REQ        0xFEFDu
#define DCP_FRAMEID_SET_RSP        0xFEFCu
#define DCP_FRAMEID_GET_REQ        0xFEFBu
#define DCP_FRAMEID_GET_RSP        0xFEFAu

/* ─── DCP Service IDs ────────────────────────────────────────────────────── */
#define DCP_SRV_GET        0x03u
#define DCP_SRV_SET        0x04u
#define DCP_SRV_IDENTIFY   0x05u
#define DCP_SRV_HELLO      0x06u

/* ─── DCP Service types ──────────────────────────────────────────────────── */
#define DCP_SRVTYPE_REQUEST  0x00u
#define DCP_SRVTYPE_RESPONSE 0x01u
#define DCP_SRVTYPE_SUCCESS  0x05u

/* ─── DCP Block options/suboptions ──────────────────────────────────────── */
#define DCP_OPT_IP           0x01u
#define DCP_SUB_IP_PARAM     0x02u   /* IP address + subnet + gateway */

#define DCP_OPT_NAME         0x02u
#define DCP_SUB_NAME_STN     0x02u   /* Name of station */

#define DCP_OPT_HWID         0x03u
#define DCP_SUB_HWID_DEV     0x01u
#define DCP_SUB_HWID_VENDOR  0x02u

#define DCP_OPT_CTRL         0x05u
#define DCP_SUB_CTRL_START   0x01u
#define DCP_SUB_CTRL_STOP    0x02u
#define DCP_SUB_CTRL_SIGNAL  0x03u
#define DCP_SUB_CTRL_RESET   0x04u
#define DCP_SUB_CTRL_FACTORY 0x06u

#define DCP_OPT_ALL          0xFFu
#define DCP_SUB_ALL          0xFFu

/* ─── DCP block result codes ─────────────────────────────────────────────── */
#define DCP_RESULT_OK        0x0000u
#define DCP_RESULT_NOT_SET   0x0001u
#define DCP_RESULT_RESOURCE  0x0002u
#define DCP_RESULT_NO_DB     0x0003u

/* ─── Public API ─────────────────────────────────────────────────────────── */

/* Send DCP Identify-All, collect responses for timeout_ms.
 * Fills ctx->discovered[] and ctx->discovered_count. */
int dcp_discover(PN_Context *ctx, uint32_t timeout_ms);

/* Send DCP Set IP to device_mac. */
int dcp_set_ip(PN_Context *ctx, const uint8_t device_mac[6],
               uint32_t ip, uint32_t subnet, uint32_t gateway);

/* Send DCP Set Name to device_mac. */
int dcp_set_name(PN_Context *ctx, const uint8_t device_mac[6],
                 const char *name);

#ifdef __cplusplus
}
#endif
#endif /* PN_DCP_H */

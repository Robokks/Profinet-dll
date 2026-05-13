#ifndef RT_CYCLIC_H
#define RT_CYCLIC_H

#include "pnet_state.h"

#ifdef __cplusplus
extern "C" {
#endif

/* RT frame layout (after 14-byte Ethernet header):
 * [FrameID 2][IOPS 1][Data N][IOCS 1][CycleCounter 2][DataStatus 1][TransferStatus 1] */

#define RT_IOPS_GOOD      0x80u
#define RT_IOCS_GOOD      0x80u
#define RT_DATA_STATUS_OK 0x35u  /* bits: valid|run|primary */

/* Start/stop the cyclic IO background thread. */
int  rt_cyclic_start(PN_Context *ctx);
void rt_cyclic_stop(PN_Context *ctx);

#ifdef __cplusplus
}
#endif
#endif /* RT_CYCLIC_H */

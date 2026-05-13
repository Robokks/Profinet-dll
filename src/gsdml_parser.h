#ifndef GSDML_PARSER_H
#define GSDML_PARSER_H

#include <stdint.h>
#include "../include/profinet_api.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Parsed GSDML device description */
typedef struct {
    uint16_t      vendor_id;
    uint16_t      device_id;
    char          vendor_name[64];
    char          device_family[128];
    uint32_t      dap_ident;       /* Device Access Point module ident */
    uint16_t      send_clock;      /* SendClockFactor, default 128 */
    uint16_t      reduction_ratio; /* default 1 */
    uint16_t      watchdog_factor; /* default 3 */
    uint16_t      output_frame_id; /* suggested output IOCR frame ID */
    uint16_t      input_frame_id;  /* suggested input  IOCR frame ID */
    PN_ModuleDesc modules[64];
    int           module_count;
} GSDML_Device;

/* Parse a GSDML file at path. Returns 0 on success. */
int  gsdml_parse(const char *path, GSDML_Device *out);

/* Helper: find the first module in dev that matches ident numbers.
 * Pass 0 to skip matching that field.
 * Returns index into dev->modules, or -1 if not found. */
int  gsdml_find_module(const GSDML_Device *dev,
                        uint32_t module_ident,
                        uint32_t submodule_ident);

#ifdef __cplusplus
}
#endif
#endif /* GSDML_PARSER_H */

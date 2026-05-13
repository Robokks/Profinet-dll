#include "pnet_state.h"
#include "utils.h"
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

void pn_set_error(PN_Context *ctx, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(ctx->last_error, sizeof(ctx->last_error), fmt, ap);
    va_end(ap);
    pn_log("%s", ctx->last_error);
}

/*
 * Lightweight GSDML parser for Profinet device description files.
 * Handles GSDML schema versions V2.2 through V3.x without external dependencies.
 * Uses a simple SAX-like tokeniser over the XML text.
 */
#include "gsdml_parser.h"
#include "utils.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

/* ─── Text map entry (TextId → value) ───────────────────────────────────── */
#define TEXT_MAP_MAX 512
#define TEXT_ID_LEN   64
#define TEXT_VAL_LEN 128

typedef struct {
    char id[TEXT_ID_LEN];
    char value[TEXT_VAL_LEN];
} TextEntry;

typedef struct {
    TextEntry entries[TEXT_MAP_MAX];
    int       count;
} TextMap;

/* ─── Parser state ───────────────────────────────────────────────────────── */
typedef struct {
    const char *buf;
    size_t      len;
    size_t      pos;
} XmlBuf;

/* ─── Tokeniser helpers ──────────────────────────────────────────────────── */

/* Skip to and past a given string */
static int skip_to(XmlBuf *x, const char *marker)
{
    size_t ml = strlen(marker);
    while (x->pos + ml <= x->len) {
        if (memcmp(x->buf + x->pos, marker, ml) == 0) {
            x->pos += ml;
            return 1;
        }
        x->pos++;
    }
    return 0;
}

/* Get value of an attribute by name in an attribute string like:
 * name="foo" TextId="TOK_X" DataLength="4"
 * Copies value (possibly with XML entity decoding) into val. */
static int get_attr(const char *attrs, const char *name, char *val, int val_size)
{
    const char *p = attrs;
    int nlen = (int)strlen(name);
    while (*p) {
        /* Skip whitespace */
        while (*p && (unsigned char)*p <= 32) p++;
        /* Check attribute name */
        if (strncmp(p, name, (size_t)nlen) == 0 && p[nlen] == '=') {
            p += nlen + 1;
            char quote = *p;
            if (quote != '"' && quote != '\'') return 0;
            p++;
            int n = 0;
            while (*p && *p != quote) {
                if (n < val_size - 1) val[n++] = *p;
                p++;
            }
            val[n] = '\0';
            return 1;
        }
        /* Skip to next whitespace or end */
        while (*p && *p != ' ' && *p != '\t' && *p != '\n' && *p != '\r') {
            if (*p == '"' || *p == '\'') {
                char q = *p++;
                while (*p && *p != q) p++;
                if (*p) p++;
            } else {
                p++;
            }
        }
    }
    return 0;
}

/* ─── Read entire file into heap buffer ──────────────────────────────────── */
static char *read_file(const char *path, size_t *out_len)
{
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz <= 0) { fclose(f); return NULL; }
    char *buf = (char *)malloc((size_t)sz + 1);
    if (!buf) { fclose(f); return NULL; }
    size_t rd = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    buf[rd] = '\0';
    *out_len = rd;
    return buf;
}

/* ─── Collect all <Text TextId="..." Value="..."/> into map ──────────────── */
static void build_text_map(const char *buf, size_t len, TextMap *map)
{
    map->count = 0;
    XmlBuf x;
    x.buf = buf; x.len = len; x.pos = 0;

    /* Only parse from PrimaryLanguage section */
    while (x.pos < x.len) {
        /* Find <Text */
        if (!skip_to(&x, "<Text ")) break;
        /* Read attributes until > */
        char attrs[512];
        int n = 0;
        while (x.pos < x.len && x.buf[x.pos] != '>' && n < 510) {
            attrs[n++] = x.buf[x.pos++];
        }
        attrs[n] = '\0';
        if (map->count >= TEXT_MAP_MAX) break;
        TextEntry *e = &map->entries[map->count];
        if (get_attr(attrs, "TextId", e->id, TEXT_ID_LEN) &&
            get_attr(attrs, "Value",  e->value, TEXT_VAL_LEN)) {
            map->count++;
        }
    }
}

static const char *text_lookup(const TextMap *map, const char *id)
{
    for (int i = 0; i < map->count; i++)
        if (strcmp(map->entries[i].id, id) == 0)
            return map->entries[i].value;
    return id; /* fallback: return raw TextId */
}

/* ─── Resolve Name: try Value attr first, then TextId lookup ─────────────── */
static void resolve_name(const char *attrs, const TextMap *tmap,
                          char *out, int out_size)
{
    char textid[TEXT_ID_LEN];
    /* Some GSDML: <Name TextId="TOK_NAME"/> */
    if (get_attr(attrs, "TextId", textid, TEXT_ID_LEN)) {
        pn_strlcpy(out, text_lookup(tmap, textid), (size_t)out_size);
        return;
    }
    /* Others: <Name Value="My Module"/> */
    if (get_attr(attrs, "Value", out, out_size)) return;
    out[0] = '\0';
}

/* ─── Parse main structures ───────────────────────────────────────────────── */
static void parse_device_identity(const char *buf, size_t len,
                                   GSDML_Device *dev)
{
    XmlBuf x;
    x.buf = buf; x.len = len; x.pos = 0;
    char attrs[512];

    /* VendorID / DeviceID from <DeviceIdentity> */
    while (x.pos < x.len) {
        if (!skip_to(&x, "<DeviceIdentity")) continue;
        int n = 0;
        while (x.pos < x.len && x.buf[x.pos] != '>' && n < 510)
            attrs[n++] = x.buf[x.pos++];
        attrs[n] = '\0';
        char tmp[32];
        if (get_attr(attrs, "VendorID", tmp, sizeof(tmp)))
            dev->vendor_id = (uint16_t)strtoul(tmp, NULL, 0);
        if (get_attr(attrs, "DeviceID", tmp, sizeof(tmp)))
            dev->device_id = (uint16_t)strtoul(tmp, NULL, 0);
        break;
    }
}

static void parse_vendor_name(const char *buf, size_t len, GSDML_Device *dev,
                                const TextMap *tmap)
{
    XmlBuf x;
    x.buf = buf; x.len = len; x.pos = 0;
    while (x.pos < x.len) {
        if (!skip_to(&x, "<VendorName")) break;
        char attrs[256]; int n = 0;
        while (x.pos < x.len && x.buf[x.pos] != '>' && n < 254)
            attrs[n++] = x.buf[x.pos++];
        attrs[n] = '\0';
        resolve_name(attrs, tmap, dev->vendor_name, sizeof(dev->vendor_name));
        break;
    }
}

/* Parse IO data length from <Input DataLength="N"/> or <Output DataLength="N"/> */
static uint16_t parse_io_data_len(const char *buf, size_t buf_len,
                                   size_t search_from, const char *tag,
                                   size_t *end_pos)
{
    XmlBuf x;
    x.buf = buf; x.len = buf_len; x.pos = search_from;
    char needle[32];
    snprintf(needle, sizeof(needle), "<%s ", tag);
    if (!skip_to(&x, needle)) {
        if (end_pos) *end_pos = x.pos;
        return 0;
    }
    char attrs[256]; int n = 0;
    while (x.pos < x.len && x.buf[x.pos] != '>' && n < 254)
        attrs[n++] = x.buf[x.pos++];
    attrs[n] = '\0';
    if (end_pos) *end_pos = x.pos;
    char tmp[16];
    if (get_attr(attrs, "DataLength", tmp, sizeof(tmp)))
        return (uint16_t)strtoul(tmp, NULL, 10);
    return 0;
}

static void parse_modules(const char *buf, size_t len,
                            GSDML_Device *dev, const TextMap *tmap)
{
    XmlBuf x;
    x.buf = buf; x.len = len; x.pos = 0;

    while (x.pos < x.len && dev->module_count < 64) {
        /* Find <ModuleItem */
        size_t mod_start = x.pos;
        if (!skip_to(&x, "<ModuleItem")) break;
        /* Find end of this element (</ModuleItem>) */
        size_t mod_end = x.pos;
        {
            XmlBuf tmp; tmp.buf = buf; tmp.len = len; tmp.pos = x.pos;
            if (skip_to(&tmp, "</ModuleItem>") || skip_to(&tmp, "/>"))
                mod_end = tmp.pos;
            else
                mod_end = len;
        }

        /* Read ModuleItem attributes */
        char mod_attrs[512]; int n = 0;
        while (x.pos < x.len && x.buf[x.pos] != '>' && n < 510)
            mod_attrs[n++] = x.buf[x.pos++];
        mod_attrs[n] = '\0';

        char ident_str[32];
        uint32_t mod_ident = 0;
        if (get_attr(mod_attrs, "ModuleIdentNumber", ident_str, sizeof(ident_str)))
            mod_ident = (uint32_t)strtoul(ident_str, NULL, 0);
        if (mod_ident == 0) {
            if (get_attr(mod_attrs, "ID", ident_str, sizeof(ident_str)))
                mod_ident = (uint32_t)strtoul(ident_str, NULL, 0);
        }

        /* Module name: look for <Name TextId="..." or Value="..."/> inside */
        char mod_name[128] = {0};
        {
            XmlBuf tmp; tmp.buf = buf; tmp.len = mod_end; tmp.pos = x.pos;
            if (skip_to(&tmp, "<Name ")) {
                char na[256]; int nn = 0;
                while (tmp.pos < tmp.len && tmp.buf[tmp.pos] != '>' && nn < 254)
                    na[nn++] = tmp.buf[tmp.pos++];
                na[nn] = '\0';
                resolve_name(na, tmap, mod_name, sizeof(mod_name));
            }
        }
        if (!mod_name[0]) snprintf(mod_name, sizeof(mod_name), "Module_0x%08X", mod_ident);

        /* Find VirtualSubmoduleItem(s) inside this module */
        XmlBuf sub; sub.buf = buf; sub.len = mod_end; sub.pos = x.pos;
        while (sub.pos < sub.len && dev->module_count < 64) {
            if (!skip_to(&sub, "<VirtualSubmoduleItem")) break;

            char sub_attrs[512]; n = 0;
            while (sub.pos < sub.len && sub.buf[sub.pos] != '>' && n < 510)
                sub_attrs[n++] = sub.buf[sub.pos++];
            sub_attrs[n] = '\0';

            /* Find end of submodule element */
            size_t sub_end = sub.pos;
            {
                XmlBuf t; t.buf = buf; t.len = mod_end; t.pos = sub.pos;
                if (skip_to(&t, "</VirtualSubmoduleItem>"))
                    sub_end = t.pos;
                else
                    sub_end = mod_end;
            }

            char sub_ident_str[32];
            uint32_t sub_ident = 0;
            if (get_attr(sub_attrs, "SubmoduleIdentNumber", sub_ident_str,
                         sizeof(sub_ident_str)))
                sub_ident = (uint32_t)strtoul(sub_ident_str, NULL, 0);

            char sub_name[128] = {0};
            {
                XmlBuf t; t.buf = buf; t.len = sub_end; t.pos = sub.pos;
                if (skip_to(&t, "<Name ")) {
                    char na[256]; int nn = 0;
                    while (t.pos < t.len && t.buf[t.pos] != '>' && nn < 254)
                        na[nn++] = t.buf[t.pos++];
                    na[nn] = '\0';
                    resolve_name(na, tmap, sub_name, sizeof(sub_name));
                }
            }
            if (!sub_name[0])
                snprintf(sub_name, sizeof(sub_name), "Sub_0x%08X", sub_ident);

            /* Parse IO data lengths */
            size_t end_pos;
            uint16_t in_len  = parse_io_data_len(buf, sub_end, sub.pos,
                                                  "Input", &end_pos);
            uint16_t out_len = parse_io_data_len(buf, sub_end, sub.pos,
                                                  "Output", &end_pos);

            PN_ModuleDesc *m = &dev->modules[dev->module_count++];
            m->module_ident    = mod_ident;
            m->submodule_ident = sub_ident;
            m->input_length    = in_len;
            m->output_length   = out_len;
            pn_strlcpy(m->module_name, mod_name, sizeof(m->module_name));
            pn_strlcpy(m->submodule_name, sub_name, sizeof(m->submodule_name));
        }

        /* Advance past this ModuleItem */
        if (x.pos < mod_end) x.pos = mod_end;
        else x.pos++;
        (void)mod_start;
    }
}

static void parse_dap(const char *buf, size_t len, GSDML_Device *dev)
{
    XmlBuf x; x.buf = buf; x.len = len; x.pos = 0;
    while (x.pos < x.len) {
        if (!skip_to(&x, "<DeviceAccessPointItem")) break;
        char attrs[512]; int n = 0;
        while (x.pos < x.len && x.buf[x.pos] != '>' && n < 510)
            attrs[n++] = x.buf[x.pos++];
        attrs[n] = '\0';
        char tmp[32];
        if (get_attr(attrs, "ModuleIdentNumber", tmp, sizeof(tmp)))
            dev->dap_ident = (uint32_t)strtoul(tmp, NULL, 0);
        break;
    }
}

static void parse_timing(const char *buf, size_t len, GSDML_Device *dev)
{
    XmlBuf x; x.buf = buf; x.len = len; x.pos = 0;
    while (x.pos < x.len) {
        /* Look for SendClockFactor in TimingProperties or IODeviceAccessPoint */
        if (!skip_to(&x, "SendClockFactor=\"")) break;
        char tmp[16]; int n = 0;
        while (x.pos < x.len && x.buf[x.pos] != '"' && n < 14)
            tmp[n++] = x.buf[x.pos++];
        tmp[n] = '\0';
        uint16_t sc = (uint16_t)strtoul(tmp, NULL, 10);
        if (sc > 0) { dev->send_clock = sc; break; }
    }
}

/* ─── Main parse entry point ─────────────────────────────────────────────── */
int gsdml_parse(const char *path, GSDML_Device *out)
{
    if (!path || !out) return -1;
    memset(out, 0, sizeof(*out));

    /* Defaults */
    out->send_clock      = 128;
    out->reduction_ratio = 1;
    out->watchdog_factor = 3;
    out->output_frame_id = 0xC000;
    out->input_frame_id  = 0xC001;

    size_t flen;
    char *buf = read_file(path, &flen);
    if (!buf) {
        pn_log("GSDML: cannot open '%s'", path);
        return PN_ERR_GSDML_NOT_FOUND;
    }

    /* Pass 1: build text map */
    TextMap *tmap = (TextMap *)calloc(1, sizeof(TextMap));
    if (!tmap) { free(buf); return PN_ERR_INTERNAL; }
    build_text_map(buf, flen, tmap);
    pn_log("GSDML: %d text entries", tmap->count);

    /* Pass 2: extract structures */
    parse_device_identity(buf, flen, out);
    parse_vendor_name(buf, flen, out, tmap);
    parse_dap(buf, flen, out);
    parse_timing(buf, flen, out);
    parse_modules(buf, flen, out, tmap);

    free(tmap);
    free(buf);

    pn_log("GSDML: vendor=0x%04X device=0x%04X dap=0x%08X modules=%d",
           out->vendor_id, out->device_id, out->dap_ident, out->module_count);
    return PN_OK;
}

int gsdml_find_module(const GSDML_Device *dev,
                       uint32_t module_ident,
                       uint32_t submodule_ident)
{
    for (int i = 0; i < dev->module_count; i++) {
        const PN_ModuleDesc *m = &dev->modules[i];
        int ok = 1;
        if (module_ident    && m->module_ident    != module_ident)    ok = 0;
        if (submodule_ident && m->submodule_ident != submodule_ident) ok = 0;
        if (ok) return i;
    }
    return -1;
}

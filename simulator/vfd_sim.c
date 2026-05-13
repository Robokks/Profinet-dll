/* vfd_sim.c — Profinet VFD Slave Simulator GUI (Win32, 32-bit)
 * 4-tab interface: Device Setup | Network/DCP | IO Data | Connection Info
 * Always-visible log area at bottom. */

#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <commctrl.h>
#include <commdlg.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>

#include "pn_device.h"
#include "../src/frame_io.h"
#include "../src/utils.h"
#include <mmsystem.h>
#include <stdlib.h>

/* ─── Resource IDs ───────────────────────────────────────────────────────── */
#define IDC_TAB            100
#define IDC_LOG            101

/* Tab 0 — Device Setup */
#define IDC_COMBO_ADAPTER  200
#define IDC_BTN_ENUM       201
#define IDC_BTN_START      202
#define IDC_BTN_STOP       203
#define IDC_EDIT_MAC       204
#define IDC_EDIT_VENDOR    205
#define IDC_EDIT_DEVICE    206
#define IDC_LABEL_STATUS   207

/* Tab 1 — Network/DCP */
#define IDC_EDIT_IP        300
#define IDC_EDIT_SUBNET    301
#define IDC_EDIT_GW        302
#define IDC_BTN_APPLYIP    303
#define IDC_EDIT_STATION   304
#define IDC_BTN_APPLYNAME  305
#define IDC_LABEL_DCPEVENT 306
#define IDC_LIST_DCP       307

/* Tab 2 — IO Data */
#define IDC_EDIT_STW1      400
#define IDC_LABEL_STW1     401
#define IDC_EDIT_NSOLL     402
#define IDC_LABEL_NSOLL    403
#define IDC_EDIT_ZSW1      404
#define IDC_LABEL_ZSW1     405
#define IDC_EDIT_NIST      406
#define IDC_LABEL_NIST     407
#define IDC_BTN_APPLYIO    408
#define IDC_CHK_AUTOSIM    409
#define IDC_LABEL_STATS    410

/* Tab 3 — Connection Info */
#define IDC_INFO_STATE     500
#define IDC_INFO_CTRLMAC   501
#define IDC_INFO_CTRLIP    502
#define IDC_INFO_OUTFID    503
#define IDC_INFO_INFID     504
#define IDC_INFO_CYCLE     505
#define IDC_INFO_ARUUID    506
#define IDC_INFO_SESSIONK  507
#define IDC_INFO_DATALEN   508

#define IDT_REFRESH        1

/* ─── Globals ────────────────────────────────────────────────────────────── */
static DevState  g_dev;
static HWND      g_hwnd;
static HWND      g_tab;
static HWND      g_log;
static HWND      g_tab_pages[4];   /* panel HWNDs for each tab */
static int       g_cur_tab = 0;

/* Controls referenced across handlers */
static HWND g_combo_adapter, g_btn_start, g_btn_stop;
static HWND g_edit_mac, g_edit_vendor, g_edit_device, g_label_status;
static HWND g_edit_ip, g_edit_subnet, g_edit_gw;
static HWND g_edit_station, g_label_dcpevent, g_list_dcp;
static HWND g_edit_stw1, g_label_stw1, g_edit_nsoll, g_label_nsoll;
static HWND g_edit_zsw1, g_label_zsw1, g_edit_nist, g_label_nist;
static HWND g_chk_autosim, g_label_stats;
static HWND g_info[9]; /* Tab 3 info labels */

static HFONT g_font_mono;

/* ─── Utility ────────────────────────────────────────────────────────────── */
static void log_append(const char *text)
{
    int len = (int)GetWindowTextLength(g_log);
    SendMessage(g_log, EM_SETSEL, (WPARAM)len, (LPARAM)len);
    SendMessage(g_log, EM_REPLACESEL, 0, (LPARAM)text);
    SendMessage(g_log, EM_REPLACESEL, 0, (LPARAM)"\r\n");
    SendMessage(g_log, EM_SCROLLCARET, 0, 0);
}

static HWND make_label(HWND parent, const char *text, int x, int y, int w, int h, DWORD style)
{
    return CreateWindowA("STATIC", text, WS_CHILD | WS_VISIBLE | style,
                         x, y, w, h, parent, NULL, GetModuleHandle(NULL), NULL);
}

static HWND make_edit(HWND parent, const char *text, int id, int x, int y, int w, int h,
                      BOOL readonly, BOOL multiline)
{
    DWORD style = WS_CHILD | WS_VISIBLE | WS_BORDER | ES_AUTOHSCROLL;
    if (readonly)  style |= ES_READONLY;
    if (multiline) style |= ES_MULTILINE | ES_AUTOVSCROLL | WS_VSCROLL;
    HWND hw = CreateWindowA("EDIT", text, style, x, y, w, h, parent, (HMENU)(intptr_t)id,
                            GetModuleHandle(NULL), NULL);
    return hw;
}

static HWND make_button(HWND parent, const char *text, int id, int x, int y, int w, int h)
{
    return CreateWindowA("BUTTON", text, WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
                         x, y, w, h, parent, (HMENU)(intptr_t)id,
                         GetModuleHandle(NULL), NULL);
}

static HWND make_checkbox(HWND parent, const char *text, int id, int x, int y, int w, int h, BOOL checked)
{
    HWND hw = CreateWindowA("BUTTON", text, WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
                            x, y, w, h, parent, (HMENU)(intptr_t)id,
                            GetModuleHandle(NULL), NULL);
    CheckDlgButton(parent, id, checked ? BST_CHECKED : BST_UNCHECKED);
    return hw;
}

/* ─── Connection state string ────────────────────────────────────────────── */
static const char *state_str(DevConnState s)
{
    switch (s) {
    case DEV_IDLE:      return "● IDLE";
    case DEV_LISTENING: return "● LISTENING";
    case DEV_CONNECTED: return "● CONNECTED";
    case DEV_CYCLIC:    return "● CYCLIC ACTIVE";
    }
    return "● UNKNOWN";
}

/* ─── Build tab 0 — Device Setup ─────────────────────────────────────────── */
static void build_tab0(HWND p)
{
    int y = 10;
    make_label(p, "Adapter:", 10, y+3, 60, 20, 0);
    g_combo_adapter = CreateWindowA("COMBOBOX", "", WS_CHILD|WS_VISIBLE|CBS_DROPDOWNLIST|WS_VSCROLL,
                                    75, y, 370, 200, p, (HMENU)IDC_COMBO_ADAPTER,
                                    GetModuleHandle(NULL), NULL);
    make_button(p, "Enumerate", IDC_BTN_ENUM,   455, y, 80, 24);
    g_btn_start = make_button(p, "Start",  IDC_BTN_START, 545, y, 60, 24);
    g_btn_stop  = make_button(p, "Stop",   IDC_BTN_STOP,  615, y, 60, 24);
    EnableWindow(g_btn_stop, FALSE);
    y += 34;

    make_label(p, "MAC:", 10, y+3, 40, 20, 0);
    g_edit_mac = make_edit(p, "00:00:00:00:00:00", IDC_EDIT_MAC, 55, y, 160, 22, TRUE, FALSE);

    make_label(p, "Vendor ID:", 230, y+3, 65, 20, 0);
    g_edit_vendor = make_edit(p, "002A", IDC_EDIT_VENDOR, 300, y, 60, 22, FALSE, FALSE);

    make_label(p, "Device ID:", 370, y+3, 65, 20, 0);
    g_edit_device = make_edit(p, "0001", IDC_EDIT_DEVICE, 440, y, 60, 22, FALSE, FALSE);
    y += 34;

    g_label_status = make_label(p, "● IDLE", 10, y, 400, 26, SS_LEFT);
    /* Make the status bold/large via font later */
}

/* ─── Build tab 1 — Network/DCP ──────────────────────────────────────────── */
static void build_tab1(HWND p)
{
    int y = 10;
    make_label(p, "IP Address:", 10, y+3, 80, 20, 0);
    g_edit_ip = make_edit(p, "192.168.1.50", IDC_EDIT_IP, 95, y, 120, 22, FALSE, FALSE);

    make_label(p, "Subnet:", 225, y+3, 50, 20, 0);
    g_edit_subnet = make_edit(p, "255.255.255.0", IDC_EDIT_SUBNET, 280, y, 120, 22, FALSE, FALSE);

    make_label(p, "Gateway:", 410, y+3, 55, 20, 0);
    g_edit_gw = make_edit(p, "192.168.1.1", IDC_EDIT_GW, 470, y, 120, 22, FALSE, FALSE);

    make_button(p, "Apply IP", IDC_BTN_APPLYIP, 600, y, 70, 24);
    y += 34;

    make_label(p, "Station Name:", 10, y+3, 90, 20, 0);
    g_edit_station = make_edit(p, "vfd-simulator", IDC_EDIT_STATION, 105, y, 300, 22, FALSE, FALSE);
    make_button(p, "Apply Name", IDC_BTN_APPLYNAME, 415, y, 80, 24);
    y += 34;

    make_label(p, "─────────────────────────── DCP Events ───────────────────────────────────", 10, y, 760, 16, 0);
    y += 20;

    make_label(p, "Last Event:", 10, y+3, 70, 20, 0);
    g_label_dcpevent = make_label(p, "(none)", 85, y, 600, 20, 0);
    y += 26;

    /* DCP history list view */
    g_list_dcp = CreateWindowA(WC_LISTVIEWA, "",
        WS_CHILD | WS_VISIBLE | WS_BORDER | LVS_REPORT | LVS_SHOWSELALWAYS | LVS_NOSORTHEADER,
        10, y, 760, 220, p, (HMENU)IDC_LIST_DCP, GetModuleHandle(NULL), NULL);

    LVCOLUMNA col = {0};
    col.mask    = LVCF_TEXT | LVCF_WIDTH;
    col.pszText = "Time";    col.cx = 80;  SendMessage(g_list_dcp, LVM_INSERTCOLUMNA, 0, (LPARAM)&col);
    col.pszText = "Event";   col.cx = 120; SendMessage(g_list_dcp, LVM_INSERTCOLUMNA, 1, (LPARAM)&col);
    col.pszText = "From MAC";col.cx = 150; SendMessage(g_list_dcp, LVM_INSERTCOLUMNA, 2, (LPARAM)&col);
    col.pszText = "Value";   col.cx = 400; SendMessage(g_list_dcp, LVM_INSERTCOLUMNA, 3, (LPARAM)&col);
}

/* ─── Build tab 2 — IO Data / PROFIdrive ─────────────────────────────────── */
static void build_tab2(HWND p)
{
    int y = 10;
    make_label(p, "── Received from Controller ───────────────────────────────────────────────", 10, y, 760, 16, 0);
    y += 20;

    make_label(p, "STW1:", 10, y+3, 45, 20, 0);
    g_edit_stw1 = make_edit(p, "0000", IDC_EDIT_STW1, 60, y, 70, 22, TRUE, FALSE);
    g_label_stw1 = make_label(p, "ON=0  EN=0  SP=0  FAULT_ACK=0", 140, y+3, 400, 20, 0);
    y += 28;

    make_label(p, "NSOLL_A:", 10, y+3, 55, 20, 0);
    g_edit_nsoll = make_edit(p, "0000", IDC_EDIT_NSOLL, 70, y, 70, 22, TRUE, FALSE);
    g_label_nsoll = make_label(p, "0.0%", 150, y+3, 200, 20, 0);
    y += 34;

    make_label(p, "── Sent to Controller ─────────────────────────────────────────────────────", 10, y, 760, 16, 0);
    y += 20;

    make_label(p, "ZSW1:", 10, y+3, 45, 20, 0);
    g_edit_zsw1 = make_edit(p, "0F21", IDC_EDIT_ZSW1, 60, y, 70, 22, FALSE, FALSE);
    g_label_zsw1 = make_label(p, "RDY=1  ON=0  RUN=0  FAULT=0", 140, y+3, 400, 20, 0);
    y += 28;

    make_label(p, "NIST_A:", 10, y+3, 50, 20, 0);
    g_edit_nist = make_edit(p, "0000", IDC_EDIT_NIST, 65, y, 70, 22, FALSE, FALSE);
    g_label_nist = make_label(p, "0.0%", 145, y+3, 200, 20, 0);

    make_button(p, "Apply ZSW1/NIST", IDC_BTN_APPLYIO, 350, y, 120, 24);
    y += 34;

    g_chk_autosim = make_checkbox(p, "Auto-simulate (compute ZSW1/NIST from STW1/NSOLL automatically)",
                                  IDC_CHK_AUTOSIM, 10, y, 600, 22, TRUE);
    y += 30;

    make_label(p, "── Statistics ─────────────────────────────────────────────────────────────", 10, y, 760, 16, 0);
    y += 20;
    g_label_stats = make_label(p, "Cycle: 0  TX: 0  RX: 0  Missed: 0", 10, y, 600, 20, 0);
}

/* ─── Build tab 3 — Connection Info ──────────────────────────────────────── */
static void build_tab3(HWND p)
{
    static const char *labels[] = {
        "State:", "Controller MAC:", "Controller IP:",
        "Output Frame ID:", "Input Frame ID:", "Cycle Time (ms):",
        "AR UUID:", "Session Key:", "Data Length:"
    };
    static const char *defaults[] = {
        "IDLE", "", "", "—", "—", "—", "—", "—", "—"
    };
    int y = 10;
    for (int i = 0; i < 9; i++) {
        make_label(p, labels[i], 10, y+3, 140, 20, SS_RIGHT);
        g_info[i] = make_label(p, defaults[i], 160, y, 600, 20, 0);
        y += 26;
    }
}

/* ─── Enumerate adapters into combo ──────────────────────────────────────── */
static void enumerate_adapters(void)
{
    char names[32][256];
    int count = 0;
    frameio_enum_adapters(names, 32, &count);

    SendMessage(g_combo_adapter, CB_RESETCONTENT, 0, 0);
    if (count == 0) {
        SendMessage(g_combo_adapter, CB_ADDSTRING, 0, (LPARAM)"(no adapters — install Npcap)");
    } else {
        for (int i = 0; i < count; i++)
            SendMessage(g_combo_adapter, CB_ADDSTRING, 0, (LPARAM)names[i]);
    }
    SendMessage(g_combo_adapter, CB_SETCURSEL, 0, 0);
}

/* ─── STW1 bit decode ────────────────────────────────────────────────────── */
static void decode_stw1(uint16_t v, char *buf, int size)
{
    snprintf(buf, size, "ON=%d  EN=%d  SP=%d  DIR=%d  FAULT_ACK=%d  RFG_EN=%d",
             (v>>0)&1, (v>>3)&1, (v>>6)&1, (v>>11)&1, (v>>7)&1, (v>>4)&1);
}

/* ─── ZSW1 bit decode ────────────────────────────────────────────────────── */
static void decode_zsw1(uint16_t v, char *buf, int size)
{
    snprintf(buf, size, "RDY=%d  ON=%d  RUN=%d  FAULT=%d  WARN=%d  CTRL_REQ=%d",
             (v>>0)&1, (v>>1)&1, (v>>2)&1, (v>>3)&1, (v>>7)&1, (v>>9)&1);
}

/* ─── Refresh IO tab ─────────────────────────────────────────────────────── */
static void refresh_io_tab(void)
{
    uint16_t stw1, nsoll, zsw1, nist;
    dev_get_io(&g_dev, &stw1, &nsoll, &zsw1, &nist);

    char tmp[128];
    snprintf(tmp, sizeof(tmp), "%04X", stw1);
    SetWindowTextA(g_edit_stw1, tmp);
    decode_stw1(stw1, tmp, sizeof(tmp));
    SetWindowTextA(g_label_stw1, tmp);

    snprintf(tmp, sizeof(tmp), "%04X  (%.1f%%)", nsoll, (double)nsoll/327.67);
    SetWindowTextA(g_edit_nsoll, tmp);
    snprintf(tmp, sizeof(tmp), "%.1f%%", (double)nsoll/327.67);
    SetWindowTextA(g_label_nsoll, tmp);

    snprintf(tmp, sizeof(tmp), "%04X", zsw1);
    SetWindowTextA(g_edit_zsw1, tmp);
    decode_zsw1(zsw1, tmp, sizeof(tmp));
    SetWindowTextA(g_label_zsw1, tmp);

    snprintf(tmp, sizeof(tmp), "%04X  (%.1f%%)", nist, (double)nist/327.67);
    SetWindowTextA(g_edit_nist, tmp);
    snprintf(tmp, sizeof(tmp), "%.1f%%", (double)nist/327.67);
    SetWindowTextA(g_label_nist, tmp);

    uint64_t rx, tx, missed;
    dev_get_stats(&g_dev, &rx, &tx, &missed);
    snprintf(tmp, sizeof(tmp), "Cycle: %u  TX: %llu  RX: %llu  Missed: %llu",
             (unsigned)g_dev.cycle_counter, (unsigned long long)tx,
             (unsigned long long)rx, (unsigned long long)missed);
    SetWindowTextA(g_label_stats, tmp);
}

/* ─── Refresh connection info tab ────────────────────────────────────────── */
static void refresh_conn_tab(void)
{
    char tmp[128];
    SetWindowTextA(g_info[0], state_str(g_dev.state));

    snprintf(tmp, sizeof(tmp), "%02X:%02X:%02X:%02X:%02X:%02X",
             g_dev.ctrl_mac[0], g_dev.ctrl_mac[1], g_dev.ctrl_mac[2],
             g_dev.ctrl_mac[3], g_dev.ctrl_mac[4], g_dev.ctrl_mac[5]);
    SetWindowTextA(g_info[1], tmp);

    /* Controller IP from ctrl_addr */
    {
        struct in_addr a;
        a.s_addr = g_dev.ctrl_addr.sin_addr.s_addr;
        SetWindowTextA(g_info[2], inet_ntoa(a));
    }

    snprintf(tmp, sizeof(tmp), "0x%04X", g_dev.output_frame_id);
    SetWindowTextA(g_info[3], tmp);
    snprintf(tmp, sizeof(tmp), "0x%04X", g_dev.input_frame_id);
    SetWindowTextA(g_info[4], tmp);

    double cycle_ms = (double)g_dev.send_clock_factor * 0.03125;
    snprintf(tmp, sizeof(tmp), "%.3f ms (ClockFactor=%u)",
             cycle_ms, g_dev.send_clock_factor);
    SetWindowTextA(g_info[5], tmp);

    snprintf(tmp, sizeof(tmp),
             "%02X%02X%02X%02X-%02X%02X-%02X%02X-%02X%02X-%02X%02X%02X%02X%02X%02X",
             g_dev.ar_uuid[0], g_dev.ar_uuid[1], g_dev.ar_uuid[2], g_dev.ar_uuid[3],
             g_dev.ar_uuid[4], g_dev.ar_uuid[5], g_dev.ar_uuid[6], g_dev.ar_uuid[7],
             g_dev.ar_uuid[8], g_dev.ar_uuid[9], g_dev.ar_uuid[10],g_dev.ar_uuid[11],
             g_dev.ar_uuid[12],g_dev.ar_uuid[13],g_dev.ar_uuid[14],g_dev.ar_uuid[15]);
    SetWindowTextA(g_info[6], tmp);

    snprintf(tmp, sizeof(tmp), "0x%04X", g_dev.session_key);
    SetWindowTextA(g_info[7], tmp);
    snprintf(tmp, sizeof(tmp), "%u bytes", g_dev.data_length);
    SetWindowTextA(g_info[8], tmp);
}

/* ─── Add DCP event to list view ─────────────────────────────────────────── */
static void dcp_list_add(const char *event, const char *value)
{
    /* Time */
    SYSTEMTIME st;
    GetLocalTime(&st);
    char timebuf[32];
    snprintf(timebuf, sizeof(timebuf), "%02d:%02d:%02d", st.wHour, st.wMinute, st.wSecond);

    /* From MAC */
    char mac[24];
    snprintf(mac, sizeof(mac), "%02X:%02X:%02X:%02X:%02X:%02X",
             g_dev.ctrl_mac[0], g_dev.ctrl_mac[1], g_dev.ctrl_mac[2],
             g_dev.ctrl_mac[3], g_dev.ctrl_mac[4], g_dev.ctrl_mac[5]);

    int row = (int)SendMessage(g_list_dcp, LVM_GETITEMCOUNT, 0, 0);

    LVITEMA item = {0};
    item.mask    = LVIF_TEXT;
    item.iItem   = row;
    item.iSubItem = 0;
    item.pszText  = timebuf;
    SendMessage(g_list_dcp, LVM_INSERTITEMA, 0, (LPARAM)&item);

    item.iSubItem = 1; item.pszText = (char *)event;
    SendMessage(g_list_dcp, LVM_SETITEMA, 0, (LPARAM)&item);
    item.iSubItem = 2; item.pszText = mac;
    SendMessage(g_list_dcp, LVM_SETITEMA, 0, (LPARAM)&item);
    item.iSubItem = 3; item.pszText = (char *)value;
    SendMessage(g_list_dcp, LVM_SETITEMA, 0, (LPARAM)&item);

    /* Scroll to bottom */
    SendMessage(g_list_dcp, LVM_ENSUREVISIBLE, row, FALSE);
}

/* ─── Handle device notifications ───────────────────────────────────────── */
static void on_dev_state_changed(void)
{
    const char *s = state_str(g_dev.state);
    SetWindowTextA(g_label_status, s);
    refresh_conn_tab();

    /* Update MAC display whenever state changes */
    char mac[24];
    snprintf(mac, sizeof(mac), "%02X:%02X:%02X:%02X:%02X:%02X",
             g_dev.local_mac[0], g_dev.local_mac[1], g_dev.local_mac[2],
             g_dev.local_mac[3], g_dev.local_mac[4], g_dev.local_mac[5]);
    SetWindowTextA(g_edit_mac, mac);

    /* Update Start/Stop button state */
    BOOL running = (g_dev.state != DEV_IDLE);
    EnableWindow(g_btn_start, !running);
    EnableWindow(g_btn_stop, running);
}

static void on_dcp_setip(void)
{
    /* IP fields stored in network byte order — inet_ntoa converts correctly */
    char ip_str[24], sub_str[24], gw_str[24];
    struct in_addr a;
    a.s_addr = g_dev.ip;      strncpy(ip_str,  inet_ntoa(a), sizeof(ip_str)-1);
    a.s_addr = g_dev.subnet;  strncpy(sub_str, inet_ntoa(a), sizeof(sub_str)-1);
    a.s_addr = g_dev.gateway; strncpy(gw_str,  inet_ntoa(a), sizeof(gw_str)-1);

    SetWindowTextA(g_edit_ip,     ip_str);
    SetWindowTextA(g_edit_subnet, sub_str);
    SetWindowTextA(g_edit_gw,     gw_str);

    char value[128];
    snprintf(value, sizeof(value), "IP=%s  Subnet=%s  GW=%s", ip_str, sub_str, gw_str);
    SetWindowTextA(g_label_dcpevent, value);
    dcp_list_add("SetIP", value);
}

static void on_dcp_setname(void)
{
    SetWindowTextA(g_edit_station, g_dev.station_name);
    char value[260];
    snprintf(value, sizeof(value), "Name=\"%s\"", g_dev.station_name);
    SetWindowTextA(g_label_dcpevent, value);
    dcp_list_add("SetName", value);
}

/* ─── Handle button commands ────────────────────────────────────────────── */
static void on_btn_enum(void)
{
    enumerate_adapters();
    log_append("Adapters enumerated.");
}

static void on_btn_start(void)
{
    char adapter[256] = {0};
    int sel = (int)SendMessage(g_combo_adapter, CB_GETCURSEL, 0, 0);
    if (sel < 0) {
        MessageBoxA(g_hwnd, "Select an adapter first.", "VFD Simulator", MB_ICONWARNING);
        return;
    }
    SendMessage(g_combo_adapter, CB_GETLBTEXT, (WPARAM)sel, (LPARAM)adapter);

    /* Read vendor/device IDs */
    char vid_s[16], did_s[16];
    GetWindowTextA(g_edit_vendor, vid_s, sizeof(vid_s));
    GetWindowTextA(g_edit_device, did_s, sizeof(did_s));
    g_dev.vendor_id = (uint16_t)strtoul(vid_s, NULL, 16);
    g_dev.device_id = (uint16_t)strtoul(did_s, NULL, 16);

    /* Apply station name from tab 1 */
    char name[240];
    GetWindowTextA(g_edit_station, name, sizeof(name));
    pn_strlcpy(g_dev.station_name, name, sizeof(g_dev.station_name));

    /* Apply IP */
    char ip_s[24], sub_s[24], gw_s[24];
    GetWindowTextA(g_edit_ip,     ip_s,  sizeof(ip_s));
    GetWindowTextA(g_edit_subnet, sub_s, sizeof(sub_s));
    GetWindowTextA(g_edit_gw,     gw_s,  sizeof(gw_s));
    g_dev.ip      = inet_addr(ip_s);
    g_dev.subnet  = inet_addr(sub_s);
    g_dev.gateway = inet_addr(gw_s);

    if (dev_init(&g_dev, adapter, g_hwnd) != 0) {
        char msg[600];
        snprintf(msg, sizeof(msg), "Failed to start device:\n%s", g_dev.last_error);
        MessageBoxA(g_hwnd, msg, "VFD Simulator", MB_ICONERROR);
        return;
    }
    log_append("Device started.");
}

static void on_btn_stop(void)
{
    dev_stop(&g_dev);
    log_append("Device stopped.");
}

static void on_btn_applyip(void)
{
    char ip_s[24], sub_s[24], gw_s[24];
    GetWindowTextA(g_edit_ip,     ip_s,  sizeof(ip_s));
    GetWindowTextA(g_edit_subnet, sub_s, sizeof(sub_s));
    GetWindowTextA(g_edit_gw,     gw_s,  sizeof(gw_s));
    uint32_t ip  = inet_addr(ip_s);
    uint32_t sub = inet_addr(sub_s);
    uint32_t gw  = inet_addr(gw_s);
    dev_set_ip(&g_dev, ip, sub, gw);
    char msg[128];
    snprintf(msg, sizeof(msg), "IP set to %s / %s gw %s", ip_s, sub_s, gw_s);
    log_append(msg);
}

static void on_btn_applyname(void)
{
    char name[240];
    GetWindowTextA(g_edit_station, name, sizeof(name));
    dev_set_name(&g_dev, name);
    char msg[260];
    snprintf(msg, sizeof(msg), "Station name set to \"%s\"", name);
    log_append(msg);
}

static void on_btn_applyio(void)
{
    if (IsDlgButtonChecked(g_tab_pages[2], IDC_CHK_AUTOSIM) == BST_CHECKED) {
        dev_set_auto_sim(&g_dev, 1);
        log_append("Auto-simulate ON.");
        return;
    }
    char zsw1_s[16], nist_s[16];
    GetWindowTextA(g_edit_zsw1, zsw1_s, sizeof(zsw1_s));
    GetWindowTextA(g_edit_nist, nist_s, sizeof(nist_s));
    uint16_t zsw1 = (uint16_t)strtoul(zsw1_s, NULL, 16);
    uint16_t nist  = (uint16_t)strtoul(nist_s, NULL, 16);
    dev_set_tx_manual(&g_dev, zsw1, nist);
    char msg[128];
    snprintf(msg, sizeof(msg), "Manual ZSW1=0x%04X NIST=0x%04X applied", zsw1, nist);
    log_append(msg);
}

static void on_chk_autosim(void)
{
    BOOL checked = (IsDlgButtonChecked(g_tab_pages[2], IDC_CHK_AUTOSIM) == BST_CHECKED);
    dev_set_auto_sim(&g_dev, checked ? 1 : 0);
}

/* ─── Tab page management ────────────────────────────────────────────────── */
static void show_tab(int idx)
{
    for (int i = 0; i < 4; i++)
        ShowWindow(g_tab_pages[i], i == idx ? SW_SHOW : SW_HIDE);
    g_cur_tab = idx;
}

/* ─── Main window proc ───────────────────────────────────────────────────── */
static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_CREATE: {
        g_hwnd = hwnd;

        /* Monospace font */
        g_font_mono = CreateFontA(16, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                                  ANSI_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                                  DEFAULT_QUALITY, FIXED_PITCH|FF_MODERN, "Courier New");

        /* Tab control */
        INITCOMMONCONTROLSEX ice = {sizeof(ice), ICC_TAB_CLASSES | ICC_LISTVIEW_CLASSES};
        InitCommonControlsEx(&ice);

        g_tab = CreateWindowA(WC_TABCONTROLA, "",
            WS_CHILD | WS_VISIBLE | TCS_FIXEDWIDTH,
            5, 5, 810, 440, hwnd, (HMENU)IDC_TAB, GetModuleHandle(NULL), NULL);

        TCITEMA ti = {0};
        ti.mask    = TCIF_TEXT;
        ti.pszText = "Device Setup";  TabCtrl_InsertItem(g_tab, 0, &ti);
        ti.pszText = "Network / DCP"; TabCtrl_InsertItem(g_tab, 1, &ti);
        ti.pszText = "IO Data";       TabCtrl_InsertItem(g_tab, 2, &ti);
        ti.pszText = "Connection";    TabCtrl_InsertItem(g_tab, 3, &ti);

        /* Tab pages (child of main window, positioned within tab) */
        RECT rc = {0, 0, 800, 400};
        TabCtrl_AdjustRect(g_tab, FALSE, &rc);

        for (int i = 0; i < 4; i++) {
            g_tab_pages[i] = CreateWindowExA(0, "PnPanel", "",
                WS_CHILD,
                rc.left + 5, rc.top + 10,
                rc.right - rc.left - 4, rc.bottom - rc.top - 10,
                hwnd, NULL, GetModuleHandle(NULL), NULL);
        }

        build_tab0(g_tab_pages[0]);
        build_tab1(g_tab_pages[1]);
        build_tab2(g_tab_pages[2]);
        build_tab3(g_tab_pages[3]);

        show_tab(0);

        /* Log area */
        g_log = make_edit(hwnd, "", IDC_LOG, 5, 450, 810, 115, TRUE, TRUE);
        SendMessage(g_log, WM_SETFONT, (WPARAM)g_font_mono, TRUE);
        log_append("VFD Simulator started. Click 'Enumerate' then 'Start'.");

        enumerate_adapters();
        SetTimer(hwnd, IDT_REFRESH, 250, NULL);
        return 0;
    }

    case WM_TIMER:
        if (wp == IDT_REFRESH) {
            refresh_io_tab();
            if (g_cur_tab == 3) refresh_conn_tab();
        }
        return 0;

    case WM_NOTIFY: {
        NMHDR *nm = (NMHDR *)lp;
        if (nm->hwndFrom == g_tab && nm->code == TCN_SELCHANGE) {
            show_tab(TabCtrl_GetCurSel(g_tab));
        }
        return 0;
    }

    case WM_COMMAND: {
        int id = LOWORD(wp);
        switch (id) {
        case IDC_BTN_ENUM:      on_btn_enum();     break;
        case IDC_BTN_START:     on_btn_start();    break;
        case IDC_BTN_STOP:      on_btn_stop();     break;
        case IDC_BTN_APPLYIP:   on_btn_applyip();  break;
        case IDC_BTN_APPLYNAME: on_btn_applyname();break;
        case IDC_BTN_APPLYIO:   on_btn_applyio();  break;
        case IDC_CHK_AUTOSIM:   on_chk_autosim();  break;
        }
        return 0;
    }

    /* ── Device thread notifications ── */
    case WM_DEV_STATE_CHANGED:
        on_dev_state_changed();
        break;

    case WM_DEV_DCP_SETIP:
        on_dcp_setip();
        break;

    case WM_DEV_DCP_SETNAME:
        on_dcp_setname();
        break;

    case WM_DEV_IO_UPDATE:
        refresh_io_tab();
        break;

    case WM_DEV_LOG: {
        char *heap = (char *)(intptr_t)lp;
        if (heap) {
            log_append(heap);
            free(heap);
        }
        break;
    }

    case WM_DESTROY:
        KillTimer(hwnd, IDT_REFRESH);
        dev_cleanup(&g_dev);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcA(hwnd, msg, wp, lp);
}

/* ─── Panel window proc: forwards WM_COMMAND/WM_NOTIFY to main window ─────── */
static LRESULT CALLBACK PanelProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    if (msg == WM_COMMAND || msg == WM_NOTIFY)
        return SendMessage(GetParent(hwnd), msg, wp, lp);
    return DefWindowProcA(hwnd, msg, wp, lp);
}

/* ─── Entry point ────────────────────────────────────────────────────────── */
int WINAPI WinMain(HINSTANCE hInst, HINSTANCE hPrev, LPSTR lpCmd, int nShow)
{
    (void)hPrev; (void)lpCmd;

    /* Raise timer resolution */
    timeBeginPeriod(1);

    /* Register panel class (tab page container that forwards commands) */
    WNDCLASSA pc = {0};
    pc.lpfnWndProc   = PanelProc;
    pc.hInstance     = hInst;
    pc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    pc.lpszClassName = "PnPanel";
    RegisterClassA(&pc);

    WNDCLASSA wc = {0};
    wc.lpfnWndProc   = WndProc;
    wc.hInstance     = hInst;
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.lpszClassName = "VFDSimClass";
    wc.hCursor       = LoadCursor(NULL, IDC_ARROW);
    wc.hIcon         = LoadIcon(NULL, IDI_APPLICATION);
    RegisterClassA(&wc);

    HWND hwnd = CreateWindowExA(0, "VFDSimClass",
        "Profinet VFD Simulator v1.0",
        WS_OVERLAPPEDWINDOW & ~WS_MAXIMIZEBOX & ~WS_THICKFRAME,
        CW_USEDEFAULT, CW_USEDEFAULT, 840, 610,
        NULL, NULL, hInst, NULL);

    ShowWindow(hwnd, nShow);
    UpdateWindow(hwnd);

    MSG msg;
    while (GetMessage(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }

    timeEndPeriod(1);
    return (int)msg.wParam;
}

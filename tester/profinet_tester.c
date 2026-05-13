/* profinet_tester.c — Standalone Win32 GUI tester for profinet.dll
 * Loads profinet.dll dynamically from same directory as EXE.
 * Tabs: Setup | Discover | Set IP/Name | GSDML | Connect | IO
 */

#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0601
#define WINVER       0x0601
#include <windows.h>
#include <commctrl.h>
#include <commdlg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

/* ═══════════════════════════════════════════════════════════════════════════
 * Profinet API type definitions (copied from profinet_api.h without DLL macros)
 * ═══════════════════════════════════════════════════════════════════════════ */
typedef void *PN_HANDLE;

typedef struct {
    uint8_t  mac[6];
    char     ip_str[16];
    char     name_of_station[240];
    uint16_t vendor_id;
    uint16_t device_id;
} PN_DeviceInfo;

typedef struct {
    uint32_t module_ident;
    char     module_name[128];
    uint32_t submodule_ident;
    char     submodule_name[128];
    uint16_t input_length;
    uint16_t output_length;
} PN_ModuleDesc;

typedef struct {
    uint8_t  device_mac[6];
    char     device_ip[16];
    uint16_t send_clock_factor;
    uint8_t  reduction_ratio;
    uint16_t watchdog_factor;
    uint32_t api;
    uint16_t slot;
    uint16_t subslot;
    uint32_t module_ident;
    uint32_t submodule_ident;
} PN_ARConfig;

typedef struct {
    uint64_t frames_sent;
    uint64_t frames_received;
    uint64_t missed_cycles;
    uint32_t cycle_counter;
    uint8_t  connected;
    uint8_t  cyclic_running;
} PN_Stats;

/* ═══════════════════════════════════════════════════════════════════════════
 * DLL function pointer types (__stdcall to match PNAPI)
 * ═══════════════════════════════════════════════════════════════════════════ */
typedef int32_t (WINAPI *pfn_PN_Initialize)(const char *, PN_HANDLE *);
typedef int32_t (WINAPI *pfn_PN_Shutdown)(PN_HANDLE);
typedef void    (WINAPI *pfn_PN_GetVersion)(char *, uint32_t);
typedef int32_t (WINAPI *pfn_PN_EnumerateAdapters)(char (*)[256], int32_t, int32_t *);
typedef int32_t (WINAPI *pfn_PN_GetAdapterName)(int32_t, char *, int32_t);
typedef int32_t (WINAPI *pfn_PN_LoadGSDML)(PN_HANDLE, const char *);
typedef int32_t (WINAPI *pfn_PN_GetModuleList)(PN_HANDLE, PN_ModuleDesc *, int32_t, int32_t *);
typedef int32_t (WINAPI *pfn_PN_DCPDiscover)(PN_HANDLE, PN_DeviceInfo *, int32_t, int32_t *, uint32_t);
typedef int32_t (WINAPI *pfn_PN_DCPSetIP)(PN_HANDLE, const uint8_t *, const char *, const char *, const char *);
typedef int32_t (WINAPI *pfn_PN_DCPSetName)(PN_HANDLE, const uint8_t *, const char *);
typedef int32_t (WINAPI *pfn_PN_Connect)(PN_HANDLE, const PN_ARConfig *);
typedef int32_t (WINAPI *pfn_PN_Disconnect)(PN_HANDLE);
typedef int32_t (WINAPI *pfn_PN_IsConnected)(PN_HANDLE);
typedef int32_t (WINAPI *pfn_PN_WriteOutputs)(PN_HANDLE, const uint8_t *, uint16_t);
typedef int32_t (WINAPI *pfn_PN_ReadInputs)(PN_HANDLE, uint8_t *, uint16_t);
typedef int32_t (WINAPI *pfn_PN_DriveSetpoint)(PN_HANDLE, uint16_t, uint16_t);
typedef int32_t (WINAPI *pfn_PN_DriveStatus)(PN_HANDLE, uint16_t *, uint16_t *);
typedef int32_t (WINAPI *pfn_PN_GetStats)(PN_HANDLE, PN_Stats *);
typedef int32_t (WINAPI *pfn_PN_GetLastError)(PN_HANDLE, char *, uint32_t);

typedef struct {
    HMODULE                  hDll;
    pfn_PN_Initialize        Initialize;
    pfn_PN_Shutdown          Shutdown;
    pfn_PN_GetVersion        GetVersion;
    pfn_PN_EnumerateAdapters EnumerateAdapters;
    pfn_PN_GetAdapterName    GetAdapterName;
    pfn_PN_LoadGSDML         LoadGSDML;
    pfn_PN_GetModuleList     GetModuleList;
    pfn_PN_DCPDiscover       DCPDiscover;
    pfn_PN_DCPSetIP          DCPSetIP;
    pfn_PN_DCPSetName        DCPSetName;
    pfn_PN_Connect           Connect;
    pfn_PN_Disconnect        Disconnect;
    pfn_PN_IsConnected       IsConnected;
    pfn_PN_WriteOutputs      WriteOutputs;
    pfn_PN_ReadInputs        ReadInputs;
    pfn_PN_DriveSetpoint     DriveSetpoint;
    pfn_PN_DriveStatus       DriveStatus;
    pfn_PN_GetStats          GetStats;
    pfn_PN_GetLastError      GetLastError;
} PN_DLL;

/* ═══════════════════════════════════════════════════════════════════════════
 * Global state
 * ═══════════════════════════════════════════════════════════════════════════ */
static PN_DLL   g_dll;
static PN_HANDLE g_handle = NULL;
static PN_DeviceInfo g_devices[32];
static int       g_device_count = 0;
static PN_ModuleDesc g_modules[64];
static int       g_module_count = 0;
static int       g_sel_module   = -1;  /* selected module in GSDML list */
static int       g_sel_device   = -1;  /* selected device in discover list */

/* ═══════════════════════════════════════════════════════════════════════════
 * Control IDs
 * ═══════════════════════════════════════════════════════════════════════════ */
#define IDC_TAB     100
#define IDC_LOG     101
#define IDT_AUTO    1001  /* timer for auto-refresh */

/* Tab 0 – Setup */
#define ID_S_STATUS 200
#define ID_S_LOAD   201
#define ID_S_COMBO  202
#define ID_S_ENUM   203
#define ID_S_INIT   204
#define ID_S_VER    205

/* Tab 1 – Discover */
#define ID_D_TIMEOUT 300
#define ID_D_SCAN   301
#define ID_D_LIST   302
#define ID_D_FILL   303

/* Tab 2 – Set IP/Name */
#define ID_C_MAC    400
#define ID_C_IP     401
#define ID_C_SUBNET 402
#define ID_C_GW     403
#define ID_C_SETIP  404
#define ID_C_NAME   405
#define ID_C_SNAME  406

/* Tab 3 – GSDML */
#define ID_G_PATH   500
#define ID_G_BROWSE 501
#define ID_G_LOAD   502
#define ID_G_LIST   503
#define ID_G_USE    504

/* Tab 4 – Connect */
#define ID_N_IP     600
#define ID_N_SLOT   601
#define ID_N_SUB    602
#define ID_N_MODID  603
#define ID_N_SUBID  604
#define ID_N_CLOCK  605
#define ID_N_WDOG   606
#define ID_N_API    607
#define ID_N_CONN   608
#define ID_N_DISC   609
#define ID_N_STAT   610

/* Tab 5 – IO */
#define ID_IO_OUT   700
#define ID_IO_WOUT  701
#define ID_IO_IN    702
#define ID_IO_RIN   703
#define ID_IO_STW1  704
#define ID_IO_NSOLL 705
#define ID_IO_DSET  706
#define ID_IO_ZSW1  707
#define ID_IO_NIST  708
#define ID_IO_AUTO  709
#define ID_IO_IVAL  710
#define ID_IO_STATS 711

/* ═══════════════════════════════════════════════════════════════════════════
 * Window handles
 * ═══════════════════════════════════════════════════════════════════════════ */
static HWND g_hwnd;
static HWND g_tab;
static HWND g_log_wnd;
static HWND g_panels[6];

/* per-tab controls */
static HWND gs_status, gs_combo, gs_ver;
static HWND gd_timeout, gd_list;
static HWND gc_mac, gc_ip, gc_subnet, gc_gw, gc_name;
static HWND gg_path, gg_list;
static HWND gn_ip, gn_slot, gn_sub, gn_modid, gn_subid, gn_clock, gn_wdog, gn_api, gn_stat;
static HWND gi_out, gi_in, gi_stw1, gi_nsoll, gi_zsw1, gi_nist, gi_ival, gi_stats, gi_auto;

/* ═══════════════════════════════════════════════════════════════════════════
 * Helper: create common controls
 * ═══════════════════════════════════════════════════════════════════════════ */
static HINSTANCE g_hInst;

static HWND mklbl(HWND p, int x, int y, int w, int h, const char *t)
{
    return CreateWindowExA(0,"STATIC",t,WS_CHILD|WS_VISIBLE|SS_LEFT,
        x,y,w,h,p,NULL,g_hInst,NULL);
}
static HWND mkedit(HWND p, int x, int y, int w, int h, int id, const char *t)
{
    return CreateWindowExA(WS_EX_CLIENTEDGE,"EDIT",t,
        WS_CHILD|WS_VISIBLE|ES_AUTOHSCROLL,
        x,y,w,h,p,(HMENU)(intptr_t)id,g_hInst,NULL);
}
static HWND mkbtn(HWND p, int x, int y, int w, int h, int id, const char *t)
{
    return CreateWindowExA(0,"BUTTON",t,WS_CHILD|WS_VISIBLE|BS_PUSHBUTTON,
        x,y,w,h,p,(HMENU)(intptr_t)id,g_hInst,NULL);
}
static HWND mkcombo(HWND p, int x, int y, int w, int h, int id)
{
    return CreateWindowExA(WS_EX_CLIENTEDGE,"COMBOBOX",NULL,
        WS_CHILD|WS_VISIBLE|CBS_DROPDOWNLIST|WS_VSCROLL,
        x,y,w,h,p,(HMENU)(intptr_t)id,g_hInst,NULL);
}
static HWND mklv(HWND p, int x, int y, int w, int h, int id)
{
    return CreateWindowExA(WS_EX_CLIENTEDGE,WC_LISTVIEWA,NULL,
        WS_CHILD|WS_VISIBLE|LVS_REPORT|LVS_SINGLESEL|LVS_SHOWSELALWAYS|LVS_NOSORTHEADER,
        x,y,w,h,p,(HMENU)(intptr_t)id,g_hInst,NULL);
}
static HWND mkcheck(HWND p, int x, int y, int w, int h, int id, const char *t)
{
    return CreateWindowExA(0,"BUTTON",t,WS_CHILD|WS_VISIBLE|BS_AUTOCHECKBOX,
        x,y,w,h,p,(HMENU)(intptr_t)id,g_hInst,NULL);
}
static void lv_col(HWND lv, int i, const char *n, int w)
{
    LVCOLUMNA c={0}; c.mask=LVCF_TEXT|LVCF_WIDTH|LVCF_SUBITEM;
    c.iSubItem=i; c.pszText=(char*)n; c.cx=w;
    SendMessageA(lv,LVM_INSERTCOLUMNA,i,(LPARAM)&c);
}
static void lv_set(HWND lv, int row, int col, const char *t)
{
    LVITEMA it={0};
    it.mask=LVIF_TEXT; it.iItem=row; it.iSubItem=col; it.pszText=(char*)t;
    if(col==0) SendMessageA(lv,LVM_INSERTITEMA,0,(LPARAM)&it);
    else        SendMessageA(lv,LVM_SETITEMA,0,(LPARAM)&it);
}
static void lv_clear(HWND lv) { SendMessageA(lv,LVM_DELETEALLITEMS,0,0); }

static void log_msg(const char *fmt, ...)
{
    char buf[512];
    va_list ap; va_start(ap,fmt);
    _vsnprintf(buf,sizeof(buf)-1,fmt,ap);
    va_end(ap);
    buf[sizeof(buf)-1]='\0';
    char line[600];
    _snprintf(line,sizeof(line)-1,"%s\r\n",buf);
    int n=GetWindowTextLengthA(g_log_wnd);
    SendMessageA(g_log_wnd,EM_SETSEL,n,n);
    SendMessageA(g_log_wnd,EM_REPLACESEL,FALSE,(LPARAM)line);
}
static void get_txt(HWND h, char *buf, int n){ GetWindowTextA(h,buf,n); }

/* ═══════════════════════════════════════════════════════════════════════════
 * DLL loader
 * ═══════════════════════════════════════════════════════════════════════════ */
static BOOL load_dll(void)
{
    if (g_dll.hDll) return TRUE;

    char path[MAX_PATH];
    GetModuleFileNameA(NULL, path, MAX_PATH);
    char *sep = strrchr(path, '\\');
    if (sep) { *(sep+1)='\0'; strcat(path, "profinet.dll"); }
    else      { strcpy(path, "profinet.dll"); }

    g_dll.hDll = LoadLibraryA(path);
    if (!g_dll.hDll) g_dll.hDll = LoadLibraryA("profinet.dll");
    if (!g_dll.hDll) return FALSE;

#define LOAD(fn) g_dll.fn = (pfn_PN_##fn)GetProcAddress(g_dll.hDll,"PN_"#fn)
    LOAD(Initialize); LOAD(Shutdown); LOAD(GetVersion);
    LOAD(EnumerateAdapters); LOAD(GetAdapterName);
    LOAD(LoadGSDML); LOAD(GetModuleList);
    LOAD(DCPDiscover); LOAD(DCPSetIP); LOAD(DCPSetName);
    LOAD(Connect); LOAD(Disconnect); LOAD(IsConnected);
    LOAD(WriteOutputs); LOAD(ReadInputs);
    LOAD(DriveSetpoint); LOAD(DriveStatus);
    LOAD(GetStats); LOAD(GetLastError);
#undef LOAD
    return g_dll.Initialize != NULL;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Panel creation
 * ═══════════════════════════════════════════════════════════════════════════ */
static void create_panel0(HWND p)  /* Setup */
{
    int y=10;
    mklbl(p,8,y+3,70,18,"DLL:");
    gs_status=CreateWindowExA(0,"STATIC","Not loaded",
        WS_CHILD|WS_VISIBLE|SS_LEFT,82,y,440,20,p,(HMENU)ID_S_STATUS,g_hInst,NULL);
    mkbtn(p,530,y,110,22,ID_S_LOAD,"Load DLL"); y+=30;

    mklbl(p,8,y+3,70,18,"Adapter:");
    gs_combo=mkcombo(p,82,y,380,200,ID_S_COMBO);
    mkbtn(p,470,y,90,22,ID_S_ENUM,"Enumerate");
    mkbtn(p,570,y,90,22,ID_S_INIT,"Initialize"); y+=34;

    mklbl(p,8,y+3,70,18,"Version:");
    gs_ver=CreateWindowExA(0,"STATIC","-",
        WS_CHILD|WS_VISIBLE|SS_LEFT,82,y,300,20,p,(HMENU)ID_S_VER,g_hInst,NULL); y+=34;

    HWND info=CreateWindowExA(WS_EX_CLIENTEDGE,"EDIT",
        "How to use:\r\n"
        "1. Click 'Load DLL' to load profinet.dll from same folder as this EXE.\r\n"
        "2. Click 'Enumerate' to list network adapters, select one.\r\n"
        "3. Click 'Initialize' to open the adapter.\r\n"
        "4. Use 'Discover' tab to find devices on the network.\r\n"
        "5. Use 'Set IP/Name' to assign IP address to a device.\r\n"
        "6. Load the GSDML file to get module info.\r\n"
        "7. Use 'Connect' tab to establish Profinet AR connection.\r\n"
        "8. Use 'IO' tab to exchange cyclic data and control a drive.",
        WS_CHILD|WS_VISIBLE|ES_MULTILINE|ES_READONLY|WS_VSCROLL,
        8,y,640,200,p,NULL,g_hInst,NULL);
    (void)info;
}

static void create_panel1(HWND p)  /* Discover */
{
    int y=10;
    mklbl(p,8,y+3,90,18,"Timeout (ms):");
    gd_timeout=mkedit(p,102,y,80,22,ID_D_TIMEOUT,"3000");
    mkbtn(p,196,y,120,22,ID_D_SCAN,"Scan for Devices"); y+=34;

    gd_list=mklv(p,8,y,744,310,ID_D_LIST);
    lv_col(gd_list,0,"MAC Address",130);
    lv_col(gd_list,1,"IP Address",110);
    lv_col(gd_list,2,"Station Name",230);
    lv_col(gd_list,3,"Vendor ID",80);
    lv_col(gd_list,4,"Device ID",80);
    y+=316;
    mkbtn(p,8,y,200,24,ID_D_FILL,">> Use Selected in Config");
}

static void create_panel2(HWND p)  /* Set IP/Name */
{
    int y=10;
    mklbl(p,8,y+3,100,18,"Device MAC:");
    gc_mac=mkedit(p,112,y,180,22,ID_C_MAC,"00:00:00:00:00:00"); y+=30;

    mklbl(p,8,y+3,100,18,"IP Address:");
    gc_ip=mkedit(p,112,y,140,22,ID_C_IP,"192.168.0.1"); y+=30;

    mklbl(p,8,y+3,100,18,"Subnet Mask:");
    gc_subnet=mkedit(p,112,y,140,22,ID_C_SUBNET,"255.255.255.0"); y+=30;

    mklbl(p,8,y+3,100,18,"Gateway:");
    gc_gw=mkedit(p,112,y,140,22,ID_C_GW,"0.0.0.0"); y+=30;

    mkbtn(p,112,y,120,24,ID_C_SETIP,"Set IP Address"); y+=36;

    mklbl(p,8,y+3,100,18,"Station Name:");
    gc_name=mkedit(p,112,y,240,22,ID_C_NAME,"profinet-device"); y+=30;

    mkbtn(p,112,y,120,24,ID_C_SNAME,"Set Name"); y+=36;

    mklbl(p,8,y,620,60,
        "Note: Device must be powered on and connected.\r\n"
        "MAC address is auto-filled when you click 'Use Selected' on the Discover tab.");
}

static void create_panel3(HWND p)  /* GSDML */
{
    int y=10;
    mklbl(p,8,y+3,80,18,"GSD/GSDML:");
    gg_path=mkedit(p,92,y,480,22,ID_G_PATH,"");
    mkbtn(p,580,y,90,22,ID_G_BROWSE,"Browse...");
    mkbtn(p,680,y,70,22,ID_G_LOAD,"Load"); y+=34;

    gg_list=mklv(p,8,y,744,300,ID_G_LIST);
    lv_col(gg_list,0,"Module ID",90);
    lv_col(gg_list,1,"Module Name",230);
    lv_col(gg_list,2,"Submod ID",90);
    lv_col(gg_list,3,"Submod Name",160);
    lv_col(gg_list,4,"In",40);
    lv_col(gg_list,5,"Out",40);
    y+=306;
    mkbtn(p,8,y,230,24,ID_G_USE,">> Use Selected in Connect Tab");
}

static void create_panel4(HWND p)  /* Connect */
{
    int x=8, y=10;
    mklbl(p,x,y+3,90,18,"Device IP:");
    gn_ip=mkedit(p,x+94,y,140,22,ID_N_IP,"192.168.0.1"); y+=30;

    mklbl(p,x,y+3,40,18,"Slot:");
    gn_slot=mkedit(p,x+44,y,50,22,ID_N_SLOT,"1");
    mklbl(p,x+106,y+3,60,18,"Subslot:");
    gn_sub=mkedit(p,x+168,y,50,22,ID_N_SUB,"1"); y+=30;

    mklbl(p,x,y+3,100,18,"Module ID (hex):");
    gn_modid=mkedit(p,x+104,y,100,22,ID_N_MODID,"0x00000001");
    mklbl(p,x+216,y+3,110,18,"Submod ID (hex):");
    gn_subid=mkedit(p,x+328,y,100,22,ID_N_SUBID,"0x00000001"); y+=30;

    mklbl(p,x,y+3,110,18,"Send Clock Factor:");
    gn_clock=mkedit(p,x+114,y,60,22,ID_N_CLOCK,"128");
    mklbl(p,x+186,y+3,80,18,"(128=1ms)"); y+=30;

    mklbl(p,x,y+3,100,18,"Watchdog Factor:");
    gn_wdog=mkedit(p,x+104,y,60,22,ID_N_WDOG,"3"); y+=30;

    mklbl(p,x,y+3,35,18,"API:");
    gn_api=mkedit(p,x+38,y,60,22,ID_N_API,"0"); y+=34;

    mkbtn(p,x,y,100,28,ID_N_CONN,"Connect");
    mkbtn(p,x+112,y,100,28,ID_N_DISC,"Disconnect"); y+=38;

    gn_stat=CreateWindowExA(0,"STATIC","Status: Not connected",
        WS_CHILD|WS_VISIBLE|SS_LEFT,x,y,500,20,p,(HMENU)ID_N_STAT,g_hInst,NULL); y+=28;

    mklbl(p,x,y,640,40,
        "Note: Connect requires Profinet AR handshake (DCP SetIP first).\r\n"
        "Module/Submodule IDs and Slot/Subslot must match the device GSDML.");
}

static void create_panel5(HWND p)  /* IO */
{
    int x=8, y=6;
    /* ── Output (write) ── */
    mklbl(p,x,y+3,130,18,"Output data (hex):");
    gi_out=mkedit(p,x+134,y,380,22,ID_IO_OUT,"00 00 00 00");
    mkbtn(p,x+522,y,110,22,ID_IO_WOUT,"Write Outputs"); y+=30;

    /* ── Input (read) ── */
    mklbl(p,x,y+3,130,18,"Input data (hex):");
    gi_in=CreateWindowExA(WS_EX_CLIENTEDGE,"EDIT","",
        WS_CHILD|WS_VISIBLE|ES_READONLY|ES_AUTOHSCROLL,
        x+134,y,380,22,p,(HMENU)ID_IO_IN,g_hInst,NULL);
    mkbtn(p,x+522,y,110,22,ID_IO_RIN,"Read Inputs"); y+=30;

    /* ── Drive (PROFIdrive Telegram 1) ── */
    HWND sep=CreateWindowExA(0,"STATIC","─── PROFIdrive Telegram 1 ──────────────────────",
        WS_CHILD|WS_VISIBLE|SS_LEFT,x,y,600,18,p,NULL,g_hInst,NULL);
    (void)sep; y+=22;

    mklbl(p,x,y+3,80,18,"STW1 (hex):");
    gi_stw1=mkedit(p,x+84,y,70,22,ID_IO_STW1,"047F");
    mklbl(p,x+168,y+3,100,18,"NSOLL_A (hex):");
    gi_nsoll=mkedit(p,x+272,y,70,22,ID_IO_NSOLL,"0000");
    mkbtn(p,x+354,y,120,22,ID_IO_DSET,"Drive Setpoint"); y+=30;

    mklbl(p,x,y+3,40,18,"ZSW1:");
    gi_zsw1=CreateWindowExA(WS_EX_CLIENTEDGE,"EDIT","0000",
        WS_CHILD|WS_VISIBLE|ES_READONLY,x+44,y,80,22,p,(HMENU)ID_IO_ZSW1,g_hInst,NULL);
    mklbl(p,x+140,y+3,60,18,"NIST_A:");
    gi_nist=CreateWindowExA(WS_EX_CLIENTEDGE,"EDIT","0000",
        WS_CHILD|WS_VISIBLE|ES_READONLY,x+204,y,80,22,p,(HMENU)ID_IO_NIST,g_hInst,NULL); y+=30;

    mklbl(p,x+300,y+3,120,18,"STW1 bit refs:");
    mklbl(p,x+430,y,330,18,"0=ON 1=no-coast 2=no-qstop 3=en 6=sp-en 7=flt-ack"); y+=20;
    mklbl(p,x+300,y+3,120,18,"ZSW1 bit refs:");
    mklbl(p,x+430,y,330,18,"2=running 3=fault 10=speed-reached"); y+=24;

    /* ── Auto-refresh ── */
    gi_auto=mkcheck(p,x,y,120,22,ID_IO_AUTO,"Auto-refresh");
    mklbl(p,x+128,y+3,70,18,"Interval(ms):");
    gi_ival=mkedit(p,x+200,y,60,22,ID_IO_IVAL,"500"); y+=30;

    /* ── Stats ── */
    gi_stats=CreateWindowExA(WS_EX_CLIENTEDGE,"EDIT","Stats: not connected",
        WS_CHILD|WS_VISIBLE|ES_MULTILINE|ES_READONLY|WS_VSCROLL,
        x,y,752,90,p,(HMENU)ID_IO_STATS,g_hInst,NULL);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Update connection status display
 * ═══════════════════════════════════════════════════════════════════════════ */
static void update_status(void)
{
    if (!g_handle || !g_dll.IsConnected) {
        SetWindowTextA(gn_stat, "Status: Not initialized");
        return;
    }
    int conn = g_dll.IsConnected(g_handle);
    SetWindowTextA(gn_stat, conn ? "Status: CONNECTED (cyclic IO active)"
                                 : "Status: Disconnected");
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Parse "AA:BB:CC:DD:EE:FF" → 6 bytes
 * ═══════════════════════════════════════════════════════════════════════════ */
static int parse_mac(const char *s, uint8_t mac[6])
{
    unsigned a,b,c,d,e,f;
    if (sscanf(s, "%02X:%02X:%02X:%02X:%02X:%02X",&a,&b,&c,&d,&e,&f)==6 ||
        sscanf(s, "%02x:%02x:%02x:%02x:%02x:%02x",&a,&b,&c,&d,&e,&f)==6) {
        mac[0]=(uint8_t)a; mac[1]=(uint8_t)b; mac[2]=(uint8_t)c;
        mac[3]=(uint8_t)d; mac[4]=(uint8_t)e; mac[5]=(uint8_t)f;
        return 0;
    }
    return -1;
}

/* Parse "AA BB CC DD ..." hex string → bytes. Returns count. */
static int parse_hex_bytes(const char *s, uint8_t *out, int maxlen)
{
    int n=0;
    while (*s && n<maxlen) {
        while (*s==' '||*s=='\t') s++;
        if (!*s) break;
        char *end;
        unsigned v=(unsigned)strtoul(s,&end,16);
        if(end==s) break;
        out[n++]=(uint8_t)v;
        s=end;
    }
    return n;
}

/* Convert bytes to "AA BB CC DD" hex string */
static void bytes_to_hex(const uint8_t *b, int n, char *out, int outsz)
{
    int pos=0;
    for(int i=0;i<n&&pos+3<outsz;i++)
        pos+=_snprintf(out+pos,outsz-pos,"%02X ",b[i]);
    if(pos>0&&out[pos-1]==' ') out[pos-1]='\0';
    else if(pos<outsz) out[pos]='\0';
}

/* ═══════════════════════════════════════════════════════════════════════════
 * IO refresh (called by timer or button)
 * ═══════════════════════════════════════════════════════════════════════════ */
static void do_io_refresh(void)
{
    if (!g_handle) return;

    /* Read inputs */
    if (g_dll.ReadInputs) {
        uint8_t buf[256]={0};
        int rc=g_dll.ReadInputs(g_handle,buf,256);
        if(rc==0){
            char hex[600]; bytes_to_hex(buf,256,hex,sizeof(hex));
            SetWindowTextA(gi_in,hex);
        }
    }

    /* Drive status */
    if (g_dll.DriveStatus) {
        uint16_t zsw1=0,nist=0;
        g_dll.DriveStatus(g_handle,&zsw1,&nist);
        char tmp[16];
        _snprintf(tmp,sizeof(tmp),"%04X",zsw1); SetWindowTextA(gi_zsw1,tmp);
        _snprintf(tmp,sizeof(tmp),"%04X",nist);  SetWindowTextA(gi_nist,tmp);
    }

    /* Stats */
    if (g_dll.GetStats) {
        PN_Stats s={0};
        g_dll.GetStats(g_handle,&s);
        char buf[300];
        _snprintf(buf,sizeof(buf),
            "Frames sent: %I64u    Frames received: %I64u    Missed cycles: %I64u\r\n"
            "Cycle counter: %u    Connected: %s    Cyclic running: %s",
            (unsigned long long)s.frames_sent,
            (unsigned long long)s.frames_received,
            (unsigned long long)s.missed_cycles,
            s.cycle_counter,
            s.connected      ? "YES" : "NO",
            s.cyclic_running ? "YES" : "NO");
        SetWindowTextA(gi_stats,buf);
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Button handlers (WM_COMMAND dispatch)
 * ═══════════════════════════════════════════════════════════════════════════ */
static void on_load_dll(void)
{
    if (load_dll()) {
        char ver[64]="";
        if(g_dll.GetVersion) g_dll.GetVersion(ver,sizeof(ver));
        SetWindowTextA(gs_status,"Loaded OK");
        SetWindowTextA(gs_ver,ver[0]?ver:"-");
        log_msg("[DLL] profinet.dll loaded. Version: %s",ver[0]?ver:"?");
    } else {
        char path[MAX_PATH];
        GetModuleFileNameA(NULL,path,MAX_PATH);
        char *sep=strrchr(path,'\\'); if(sep) *(sep+1)='\0';
        char msg[400]; _snprintf(msg,sizeof(msg),"FAILED — not found in: %s",path);
        SetWindowTextA(gs_status,msg);
        log_msg("[DLL] Load failed. Put profinet.dll next to %s",path);
        MessageBoxA(g_hwnd,
            "profinet.dll not found.\r\n\r\n"
            "Copy profinet.dll to the same folder as this EXE and try again.",
            "DLL not found",MB_ICONWARNING|MB_OK);
    }
}

static void on_enumerate(void)
{
    if(!g_dll.EnumerateAdapters){ log_msg("[ENUM] Load DLL first."); return; }
    static char names[64][256];
    int cnt=0;
    g_dll.EnumerateAdapters(names,64,&cnt);
    SendMessageA(gs_combo,CB_RESETCONTENT,0,0);
    for(int i=0;i<cnt;i++) SendMessageA(gs_combo,CB_ADDSTRING,0,(LPARAM)names[i]);
    if(cnt>0) SendMessageA(gs_combo,CB_SETCURSEL,0,0);
    log_msg("[ENUM] Found %d adapter(s).",cnt);
}

static void on_initialize(void)
{
    if(!g_dll.Initialize){ log_msg("[INIT] Load DLL first."); return; }
    if(g_handle){ g_dll.Shutdown(g_handle); g_handle=NULL; }

    char adapter[256]="";
    int sel=(int)SendMessageA(gs_combo,CB_GETCURSEL,0,0);
    if(sel>=0) SendMessageA(gs_combo,CB_GETLBTEXT,sel,(LPARAM)adapter);

    int rc=g_dll.Initialize(adapter[0]?adapter:NULL,&g_handle);
    if(rc==0){
        log_msg("[INIT] OK. Adapter: %s",adapter[0]?adapter:"(auto)");
        update_status();
    } else {
        char err[256]="";
        if(g_dll.GetLastError && g_handle) g_dll.GetLastError(g_handle,err,sizeof(err));
        log_msg("[INIT] FAILED (rc=%d) %s",rc,err);
        g_handle=NULL;
        MessageBoxA(g_hwnd,"Initialize failed.\r\n\r\nCheck that Npcap is installed and the adapter is valid.",
            "Init failed",MB_ICONERROR|MB_OK);
    }
}

static void on_scan(void)
{
    if(!g_handle){ log_msg("[DISC] Initialize first."); return; }
    char tbuf[16]="3000"; get_txt(gd_timeout,tbuf,sizeof(tbuf));
    uint32_t ms=(uint32_t)atoi(tbuf);
    if(!ms||ms>15000) ms=3000;

    log_msg("[DISC] Scanning (timeout=%ums)...",ms);
    SetWindowTextA(g_hwnd,"Profinet Tester — Scanning...");
    UpdateWindow(g_hwnd);

    lv_clear(gd_list); g_device_count=0;
    int rc=g_dll.DCPDiscover(g_handle,g_devices,32,&g_device_count,ms);

    SetWindowTextA(g_hwnd,"Profinet Tester v1.0");
    log_msg("[DISC] Found %d device(s) (rc=%d).",g_device_count,rc);

    for(int i=0;i<g_device_count;i++){
        char mac[20];
        _snprintf(mac,sizeof(mac),"%02X:%02X:%02X:%02X:%02X:%02X",
            g_devices[i].mac[0],g_devices[i].mac[1],g_devices[i].mac[2],
            g_devices[i].mac[3],g_devices[i].mac[4],g_devices[i].mac[5]);
        lv_set(gd_list,i,0,mac);
        lv_set(gd_list,i,1,g_devices[i].ip_str);
        lv_set(gd_list,i,2,g_devices[i].name_of_station);
        char tmp[16];
        _snprintf(tmp,sizeof(tmp),"0x%04X",g_devices[i].vendor_id);
        lv_set(gd_list,i,3,tmp);
        _snprintf(tmp,sizeof(tmp),"0x%04X",g_devices[i].device_id);
        lv_set(gd_list,i,4,tmp);
    }
    g_sel_device=-1;
}

static void on_fill_from_device(void)
{
    int sel=(int)SendMessageA(gd_list,LVM_GETNEXTITEM,-1,LVNI_SELECTED);
    if(sel<0||sel>=g_device_count){ log_msg("[DISC] No device selected."); return; }
    char mac[20];
    _snprintf(mac,sizeof(mac),"%02X:%02X:%02X:%02X:%02X:%02X",
        g_devices[sel].mac[0],g_devices[sel].mac[1],g_devices[sel].mac[2],
        g_devices[sel].mac[3],g_devices[sel].mac[4],g_devices[sel].mac[5]);
    SetWindowTextA(gc_mac,mac);
    SetWindowTextA(gc_ip, g_devices[sel].ip_str);
    SetWindowTextA(gn_ip, g_devices[sel].ip_str);
    g_sel_device=sel;
    log_msg("[DISC] Filled config from: %s (%s)",mac,g_devices[sel].name_of_station);
    /* Switch to Config tab */
    SendMessageA(g_tab,TCM_SETCURSEL,2,0);
    for(int i=0;i<6;i++) ShowWindow(g_panels[i],i==2?SW_SHOW:SW_HIDE);
}

static void on_set_ip(void)
{
    if(!g_handle){ log_msg("[SETIP] Initialize first."); return; }
    char mac_s[24],ip[20],sn[20],gw[20];
    get_txt(gc_mac,mac_s,sizeof(mac_s));
    get_txt(gc_ip,ip,sizeof(ip));
    get_txt(gc_subnet,sn,sizeof(sn));
    get_txt(gc_gw,gw,sizeof(gw));

    uint8_t mac[6]={0};
    if(parse_mac(mac_s,mac)!=0){
        log_msg("[SETIP] Invalid MAC: %s",mac_s);
        MessageBoxA(g_hwnd,"Invalid MAC address format.\r\nExpected: AA:BB:CC:DD:EE:FF",
            "Error",MB_ICONWARNING|MB_OK);
        return;
    }
    log_msg("[SETIP] Setting IP %s on %s...",ip,mac_s);
    int rc=g_dll.DCPSetIP(g_handle,mac,ip,sn,gw);
    if(rc==0) log_msg("[SETIP] OK.");
    else {
        char err[256]=""; if(g_dll.GetLastError&&g_handle) g_dll.GetLastError(g_handle,err,256);
        log_msg("[SETIP] FAILED (rc=%d) %s",rc,err);
    }
}

static void on_set_name(void)
{
    if(!g_handle){ log_msg("[SETNAME] Initialize first."); return; }
    char mac_s[24],name[250];
    get_txt(gc_mac,mac_s,sizeof(mac_s));
    get_txt(gc_name,name,sizeof(name));

    uint8_t mac[6]={0};
    if(parse_mac(mac_s,mac)!=0){
        log_msg("[SETNAME] Invalid MAC.");
        return;
    }
    log_msg("[SETNAME] Setting name '%s' on %s...",name,mac_s);
    int rc=g_dll.DCPSetName(g_handle,mac,name);
    log_msg("[SETNAME] rc=%d",rc);
}

static void on_browse_gsd(void)
{
    OPENFILENAMEA ofn={0};
    char path[MAX_PATH]="";
    ofn.lStructSize=sizeof(ofn);
    ofn.hwndOwner=g_hwnd;
    ofn.lpstrFilter="GSD/GSDML Files\0*.gsd;*.gsdml;*.xml\0All Files\0*.*\0\0";
    ofn.lpstrFile=path;
    ofn.nMaxFile=MAX_PATH;
    ofn.Flags=OFN_FILEMUSTEXIST|OFN_PATHMUSTEXIST;
    if(GetOpenFileNameA(&ofn))
        SetWindowTextA(gg_path,path);
}

static void on_load_gsd(void)
{
    if(!g_handle){ log_msg("[GSDML] Initialize first."); return; }
    char path[MAX_PATH]="";
    get_txt(gg_path,path,sizeof(path));
    if(!path[0]){ log_msg("[GSDML] No file selected."); return; }

    log_msg("[GSDML] Loading: %s",path);
    int rc=g_dll.LoadGSDML(g_handle,path);
    if(rc!=0){
        char err[256]=""; if(g_dll.GetLastError&&g_handle) g_dll.GetLastError(g_handle,err,256);
        log_msg("[GSDML] FAILED (rc=%d) %s",rc,err);
        return;
    }

    g_module_count=0;
    g_dll.GetModuleList(g_handle,g_modules,64,&g_module_count);
    lv_clear(gg_list);
    for(int i=0;i<g_module_count;i++){
        char tmp[32];
        _snprintf(tmp,sizeof(tmp),"0x%08X",g_modules[i].module_ident);
        lv_set(gg_list,i,0,tmp);
        lv_set(gg_list,i,1,g_modules[i].module_name);
        _snprintf(tmp,sizeof(tmp),"0x%08X",g_modules[i].submodule_ident);
        lv_set(gg_list,i,2,tmp);
        lv_set(gg_list,i,3,g_modules[i].submodule_name);
        _snprintf(tmp,sizeof(tmp),"%d",g_modules[i].input_length);
        lv_set(gg_list,i,4,tmp);
        _snprintf(tmp,sizeof(tmp),"%d",g_modules[i].output_length);
        lv_set(gg_list,i,5,tmp);
    }
    log_msg("[GSDML] Loaded %d module(s).",g_module_count);
}

static void on_use_module(void)
{
    int sel=(int)SendMessageA(gg_list,LVM_GETNEXTITEM,-1,LVNI_SELECTED);
    if(sel<0||sel>=g_module_count){ log_msg("[GSDML] No module selected."); return; }
    char tmp[32];
    _snprintf(tmp,sizeof(tmp),"0x%08X",g_modules[sel].module_ident);
    SetWindowTextA(gn_modid,tmp);
    _snprintf(tmp,sizeof(tmp),"0x%08X",g_modules[sel].submodule_ident);
    SetWindowTextA(gn_subid,tmp);
    g_sel_module=sel;
    log_msg("[GSDML] Module '%s' filled into Connect tab.",g_modules[sel].module_name);
    SendMessageA(g_tab,TCM_SETCURSEL,4,0);
    for(int i=0;i<6;i++) ShowWindow(g_panels[i],i==4?SW_SHOW:SW_HIDE);
}

static void on_connect(void)
{
    if(!g_handle){ log_msg("[CONN] Initialize first."); return; }
    char ip[20],slot[8],sub[8],modid[16],subid[16],clock[8],wdog[8],api_s[8];
    get_txt(gn_ip,ip,sizeof(ip));
    get_txt(gn_slot,slot,sizeof(slot));
    get_txt(gn_sub,sub,sizeof(sub));
    get_txt(gn_modid,modid,sizeof(modid));
    get_txt(gn_subid,subid,sizeof(subid));
    get_txt(gn_clock,clock,sizeof(clock));
    get_txt(gn_wdog,wdog,sizeof(wdog));
    get_txt(gn_api,api_s,sizeof(api_s));

    PN_ARConfig cfg; memset(&cfg,0,sizeof(cfg));
    /* device_mac from discover selection, or zero if unknown */
    if(g_sel_device>=0 && g_sel_device<g_device_count)
        memcpy(cfg.device_mac,g_devices[g_sel_device].mac,6);
    strncpy(cfg.device_ip,ip,sizeof(cfg.device_ip)-1);
    cfg.device_ip[sizeof(cfg.device_ip)-1]='\0';
    cfg.send_clock_factor = (uint16_t)atoi(clock);
    if(!cfg.send_clock_factor) cfg.send_clock_factor=128;
    cfg.watchdog_factor = (uint16_t)atoi(wdog);
    if(!cfg.watchdog_factor) cfg.watchdog_factor=3;
    cfg.reduction_ratio=1;
    cfg.slot    = (uint16_t)atoi(slot);
    cfg.subslot = (uint16_t)atoi(sub);
    cfg.module_ident    = (uint32_t)strtoul(modid,NULL,0);
    cfg.submodule_ident = (uint32_t)strtoul(subid,NULL,0);
    cfg.api             = (uint32_t)strtoul(api_s,NULL,0);

    log_msg("[CONN] Connecting to %s slot=%u subslot=%u clock=%u wdog=%u...",
        ip,cfg.slot,cfg.subslot,cfg.send_clock_factor,cfg.watchdog_factor);

    int rc=g_dll.Connect(g_handle,&cfg);
    if(rc==0){
        log_msg("[CONN] Connected OK.");
        update_status();
    } else {
        char err[256]=""; if(g_dll.GetLastError&&g_handle) g_dll.GetLastError(g_handle,err,256);
        log_msg("[CONN] FAILED (rc=%d) %s",rc,err);
        update_status();
    }
}

static void on_disconnect(void)
{
    if(!g_handle) return;
    /* Stop auto-refresh timer */
    if(SendMessageA(gi_auto,BM_GETCHECK,0,0)==BST_CHECKED){
        SendMessageA(gi_auto,BM_SETCHECK,BST_UNCHECKED,0);
        KillTimer(g_hwnd,IDT_AUTO);
    }
    int rc=g_dll.Disconnect(g_handle);
    log_msg("[CONN] Disconnect rc=%d",rc);
    update_status();
}

static void on_write_outputs(void)
{
    if(!g_handle){ log_msg("[IO] Initialize first."); return; }
    char buf[512]=""; get_txt(gi_out,buf,sizeof(buf));
    uint8_t data[256]; int n=parse_hex_bytes(buf,data,256);
    if(n<=0){ log_msg("[IO] No valid hex bytes in output field."); return; }
    int rc=g_dll.WriteOutputs(g_handle,data,(uint16_t)n);
    log_msg("[IO] WriteOutputs(%d bytes) rc=%d",n,rc);
}

static void on_read_inputs(void)
{
    if(!g_handle){ log_msg("[IO] Initialize first."); return; }
    do_io_refresh();
}

static void on_drive_set(void)
{
    if(!g_handle){ log_msg("[IO] Initialize first."); return; }
    char s1[10],s2[10];
    get_txt(gi_stw1,s1,sizeof(s1)); get_txt(gi_nsoll,s2,sizeof(s2));
    uint16_t stw1=(uint16_t)strtoul(s1,NULL,16);
    uint16_t nsoll=(uint16_t)strtoul(s2,NULL,16);
    int rc=g_dll.DriveSetpoint(g_handle,stw1,nsoll);
    log_msg("[IO] DriveSetpoint(STW1=0x%04X NSOLL=0x%04X) rc=%d",stw1,nsoll,rc);
}

static void on_auto_toggle(void)
{
    if(SendMessageA(gi_auto,BM_GETCHECK,0,0)==BST_CHECKED){
        char buf[16]="500"; get_txt(gi_ival,buf,sizeof(buf));
        int ms=atoi(buf); if(ms<50) ms=50;
        SetTimer(g_hwnd,IDT_AUTO,(UINT)ms,NULL);
        log_msg("[IO] Auto-refresh started (%dms).",ms);
    } else {
        KillTimer(g_hwnd,IDT_AUTO);
        log_msg("[IO] Auto-refresh stopped.");
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Show the correct panel when tab changes
 * ═══════════════════════════════════════════════════════════════════════════ */
static void show_panel(int idx)
{
    for(int i=0;i<6;i++) ShowWindow(g_panels[i],i==idx?SW_SHOW:SW_HIDE);
    if(idx==5) do_io_refresh();
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Main window procedure
 * ═══════════════════════════════════════════════════════════════════════════ */
static LRESULT CALLBACK WndProc(HWND hw, UINT msg, WPARAM wp, LPARAM lp)
{
    switch(msg) {

    case WM_CREATE: {
        /* Tab control */
        INITCOMMONCONTROLSEX icc={sizeof(icc),ICC_TAB_CLASSES|ICC_LISTVIEW_CLASSES};
        InitCommonControlsEx(&icc);

        g_tab=CreateWindowExA(0,WC_TABCONTROLA,NULL,
            WS_CHILD|WS_VISIBLE|TCS_HOTTRACK,
            5,5,790,455,hw,(HMENU)IDC_TAB,g_hInst,NULL);

        const char *tabs[]={"Setup","Discover","Set IP/Name","GSDML","Connect","IO"};
        for(int i=0;i<6;i++){
            TCITEMA ti={0}; ti.mask=TCIF_TEXT; ti.pszText=(char*)tabs[i];
            SendMessageA(g_tab,TCM_INSERTITEMA,i,(LPARAM)&ti);
        }

        /* Create panels as child windows of main window */
        DWORD ps=WS_CHILD|WS_VISIBLE;
        for(int i=0;i<6;i++){
            g_panels[i]=CreateWindowExA(0,"STATIC",NULL,
                WS_CHILD|(i==0?WS_VISIBLE:0),
                10,33,770,415,hw,NULL,g_hInst,NULL);
        }
        /* Build each panel's controls */
        create_panel0(g_panels[0]);
        create_panel1(g_panels[1]);
        create_panel2(g_panels[2]);
        create_panel3(g_panels[3]);
        create_panel4(g_panels[4]);
        create_panel5(g_panels[5]);
        (void)ps;

        /* Log area */
        g_log_wnd=CreateWindowExA(WS_EX_CLIENTEDGE,"EDIT",NULL,
            WS_CHILD|WS_VISIBLE|ES_MULTILINE|ES_READONLY|WS_VSCROLL|ES_AUTOVSCROLL,
            5,465,790,120,hw,(HMENU)IDC_LOG,g_hInst,NULL);

        log_msg("Profinet DLL Tester ready.  Click 'Load DLL' on the Setup tab.");
        return 0;
    }

    case WM_NOTIFY: {
        NMHDR *nm=(NMHDR*)lp;
        if(nm->idFrom==IDC_TAB && nm->code==TCN_SELCHANGE){
            int sel=(int)SendMessageA(g_tab,TCM_GETCURSEL,0,0);
            show_panel(sel);
        }
        return 0;
    }

    case WM_TIMER:
        if(wp==IDT_AUTO) do_io_refresh();
        return 0;

    case WM_COMMAND:
        switch(LOWORD(wp)){
        case ID_S_LOAD:   on_load_dll();       break;
        case ID_S_ENUM:   on_enumerate();      break;
        case ID_S_INIT:   on_initialize();     break;
        case ID_D_SCAN:   on_scan();           break;
        case ID_D_FILL:   on_fill_from_device();break;
        case ID_C_SETIP:  on_set_ip();         break;
        case ID_C_SNAME:  on_set_name();       break;
        case ID_G_BROWSE: on_browse_gsd();     break;
        case ID_G_LOAD:   on_load_gsd();       break;
        case ID_G_USE:    on_use_module();     break;
        case ID_N_CONN:   on_connect();        break;
        case ID_N_DISC:   on_disconnect();     break;
        case ID_IO_WOUT:  on_write_outputs();  break;
        case ID_IO_RIN:   on_read_inputs();    break;
        case ID_IO_DSET:  on_drive_set();      break;
        case ID_IO_AUTO:  on_auto_toggle();    break;
        }
        return 0;

    case WM_CLOSE:
        if(g_handle && g_dll.Shutdown){
            KillTimer(hw,IDT_AUTO);
            g_dll.Shutdown(g_handle);
        }
        DestroyWindow(hw);
        return 0;

    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;

    case WM_GETMINMAXINFO: {
        MINMAXINFO *mm=(MINMAXINFO*)lp;
        mm->ptMinTrackSize.x=820;
        mm->ptMinTrackSize.y=640;
        return 0;
    }
    }
    return DefWindowProcA(hw,msg,wp,lp);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Entry point
 * ═══════════════════════════════════════════════════════════════════════════ */
int WINAPI WinMain(HINSTANCE hInst, HINSTANCE hPrev, LPSTR lpCmd, int nShow)
{
    (void)hPrev; (void)lpCmd;
    g_hInst = hInst;

    /* Register window class */
    WNDCLASSEXA wc = {0};
    wc.cbSize        = sizeof(wc);
    wc.style         = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc   = WndProc;
    wc.hInstance     = hInst;
    wc.hCursor       = LoadCursorA(NULL, IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.lpszClassName = "ProfinetTesterWnd";
    wc.hIcon         = LoadIconA(NULL, IDI_APPLICATION);
    RegisterClassExA(&wc);

    g_hwnd = CreateWindowExA(0,
        "ProfinetTesterWnd",
        "Profinet DLL Tester v1.0",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 820, 650,
        NULL, NULL, hInst, NULL);

    ShowWindow(g_hwnd, nShow);
    UpdateWindow(g_hwnd);

    MSG msg;
    while (GetMessageA(&msg, NULL, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }
    return (int)msg.wParam;
}

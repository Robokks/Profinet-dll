# Using profinet.dll in LabVIEW

## Prerequisites

1. **Windows 7 or later** (32-bit or 64-bit, but use 32-bit LabVIEW)
2. **Npcap** installed on the Windows machine: https://npcap.com/ (free)
   - During install, enable "WinPcap API-compatible mode"
3. **profinet.dll** in the same folder as your `.vi` file (or in `System32`)

---

## Call Library Function Node Configuration

In LabVIEW: **Functions → Connectivity → Libraries & Executables → Call Library Function Node**

### Important: All functions use `__stdcall` (default for Windows)

| LabVIEW type      | C type         | Notes                              |
|-------------------|----------------|------------------------------------|
| `U32`             | `PN_HANDLE`    | Store handle as U32                |
| `I32`             | `int32_t`      | Return value / error code          |
| `U16`             | `uint16_t`     |                                    |
| `U8 1D array`     | `uint8_t *`    | For IO data buffers                |
| `String`          | `const char *` | Pass as C String Pointer           |

---

## Typical Workflow

```
1. PN_Initialize      → get handle
2. PN_LoadGSDML       → parse VFD's GSDML file (optional but recommended)
3. PN_DCPDiscover     → find VFD on network
4. PN_DCPSetIP        → assign IP to VFD (if needed)
5. PN_Connect         → establish Profinet AR (cyclic IO starts)
6. Loop:
     PN_DriveSetpoint → send STW1 + speed setpoint
     PN_DriveStatus   → read ZSW1 + actual speed
7. PN_Disconnect      → orderly release
8. PN_Shutdown        → free resources
```

---

## Function Reference

### PN_Initialize
```
Library: profinet.dll
Function: PN_Initialize
Return: I32 (0 = OK)
Parameters:
  adapter_name  → String, C String Pointer (NULL for auto-select)
  out_handle    → U32 *, Pointer to Value
```
Pass empty string `""` as adapter_name to use the first Npcap adapter.

---

### PN_GetAdapterName (enumerate adapters)
```
Function: PN_GetAdapterName
Return: I32
Parameters:
  index     → I32, Value  (0, 1, 2, ...)
  buf       → String, C String Pointer (pre-allocate 256 bytes)
  buf_size  → I32, Value  (256)
```

---

### PN_LoadGSDML
```
Function: PN_LoadGSDML
Return: I32
Parameters:
  handle     → U32, Value
  gsdml_path → String, C String Pointer  (e.g. "C:\\GSD\\Siemens_G120.xml")
```

---

### PN_DCPDiscover
```
Function: PN_DCPDiscover
Return: I32
Parameters:
  handle     → U32, Value
  devices    → (pass NULL = 0) U32, Value
  max_count  → I32, Value  (0 if devices=NULL)
  out_count  → I32 *, Pointer to Value
  timeout_ms → U32, Value  (3000 recommended)
```
Then call `PN_GetDiscoveredDevice(handle, 0, &devinfo)` to retrieve index 0.

---

### PN_DCPSetIP
```
Function: PN_DCPSetIP
Return: I32
Parameters:
  handle     → U32, Value
  device_mac → U8 Array, Array Data Pointer  (6 bytes)
  ip         → String, C String Pointer      ("192.168.1.10")
  subnet     → String, C String Pointer      ("255.255.255.0")
  gateway    → String, C String Pointer      ("192.168.1.1")
```

---

### PN_Connect
Requires a `PN_ARConfig` cluster. Because passing raw C structs through LabVIEW is complex, use `PN_WriteOutputs`/`PN_ReadInputs` after connecting.

`PN_ARConfig` cluster layout (must match exactly):
```
device_mac[6]       U8  × 6
_pad[2]             U8  × 2   (padding)
device_ip[16]       U8  × 16  (null-terminated ASCII)
send_clock_factor   U16
reduction_ratio     U8
_pad2               U8
watchdog_factor     U16
_pad3[2]            U8  × 2
api                 U32
slot                U16
subslot             U16
module_ident        U32
submodule_ident     U32
```

**Typical values for Siemens G120/V90 Telegram 1:**
- `send_clock_factor = 128` (1ms cycle)
- `reduction_ratio = 1`
- `watchdog_factor = 3`
- `api = 0`
- `slot = 1`, `subslot = 1`
- `module_ident` and `submodule_ident` from GSDML (or 0 for generic)

---

### PN_DriveSetpoint (VFD control — most used)
```
Function: PN_DriveSetpoint
Return: I32
Parameters:
  handle  → U32, Value
  STW1    → U16, Value   (e.g. 0x047F = full enable)
  NSOLL_A → U16, Value   (e.g. 0x2000 = 50% speed)
```

**STW1 quick reference:**
| STW1 value | Meaning                    |
|------------|----------------------------|
| `0x0000`   | OFF1 (controlled stop)     |
| `0x047E`   | Ready (inhibit setpoint)   |
| `0x047F`   | Run (full enable + setpoint)|
| `0x04FF`   | Run with fault acknowledge |

**NSOLL_A speed values:**
| NSOLL_A | Speed      |
|---------|------------|
| `0x0000`| 0%         |
| `0x2000`| 50%        |
| `0x4000`| 100% (nom) |

---

### PN_DriveStatus (read VFD feedback)
```
Function: PN_DriveStatus
Return: I32
Parameters:
  handle  → U32, Value
  ZSW1    → U16 *, Pointer to Value
  NIST_A  → U16 *, Pointer to Value
```

**ZSW1 key bits:**
| Bit | Meaning                |
|-----|------------------------|
| 2   | Drive running          |
| 3   | Fault                  |
| 10  | Speed reached/steady   |

---

### Error Codes
| Code | Name                    |
|------|-------------------------|
| 0    | OK                      |
| -4   | No Npcap adapter found  |
| -5   | Failed to open adapter  |
| -6   | GSDML file not found    |
| -7   | GSDML parse error       |
| -8   | DCP timeout             |
| -9   | DCP SetIP failed        |
| -10  | RPC connect failed      |
| -11  | RPC timeout             |

Call `PN_GetLastError(handle, buf, 512)` for human-readable description.

---

## Example: Start VFD at 50% speed

```
[PN_Initialize("", &handle)]
    ↓ handle
[PN_LoadGSDML(handle, "C:\\GSD\\siemens_g120.xml")]
[PN_DCPDiscover(handle, 0, 0, &count, 3000)]
[PN_GetDiscoveredDevice(handle, 0, &devinfo)]  → MAC, IP
[PN_DCPSetIP(handle, mac, "192.168.1.10", "255.255.255.0", "192.168.1.1")]
[PN_Connect(handle, &ar_config)]
  ↓ connected
WHILE running:
  [PN_DriveSetpoint(handle, 0x047F, 0x2000)]   ← 50% setpoint
  [PN_DriveStatus(handle, &zsw1, &nist_a)]      ← read feedback
  [Wait 10ms]
[PN_DriveSetpoint(handle, 0x0476, 0x0000)]     ← stop
[PN_Disconnect(handle)]
[PN_Shutdown(handle)]
```

---

## Supported VFDs

| VFD Series       | Telegram | Notes                     |
|-----------------|----------|---------------------------|
| Siemens SINAMICS G120 | Telegram 1 | Standard PROFIdrive |
| Siemens SINAMICS S120 | Telegram 1 | Standard PROFIdrive |
| Siemens V90      | Telegram 1 | Standard PROFIdrive |
| Siemens ET 200SP | Module     | Via GSDML               |
| ABB ACS series   | Telegram 1 | Use ABB GSDML           |
| Schneider Altivar| Telegram 1 | Use Schneider GSDML     |

Any Profinet IO Device with a GSDML file is supported.

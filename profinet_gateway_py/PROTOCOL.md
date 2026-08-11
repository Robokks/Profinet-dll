# Gateway TCP/UDP/STM Frame Protocol

How a client (LabVIEW, Hercules, etc.) exchanges data with the Profinet Gateway.

```
[ LabVIEW / TCP client ] --TCP/UDP/STM--> [ Gateway ] --Profinet--> [ drive(s) ]
        outputs (setpoints)  ------------------------------------>  STW1, NSOLL, ...
        inputs  (actuals)    <------------------------------------  ZSW1, NIST,  ...
```

## Byte order — BIG-ENDIAN
All 16-bit process words are **big-endian** (Profinet wire order): high byte first.
A 32-bit value = two words, **high word first**.

```
STW1 = 0x047F  ->  04 7F
MDI_TARPOS (32-bit) = 0x00012345  ->  00 01 23 45
```

## Frame layout (per device)
A frame is the **concatenation of all configured devices, in config order**.
Each device contributes:

- **output** = `output_length` bytes (client → gateway → drive)
- **input**  = `input_length`  bytes (drive → gateway → client)

`output_length` / `input_length` come from the selected **telegram** (see the
device dialog's "State of data length"). Examples:

| Telegram | Words in/out | Bytes in/out |
|---|---|---|
| Standard Telegram 1 | 2 / 2 | 4 / 4 |
| Standard Telegram 2 | 4 / 4 | 8 / 8 |
| SIEMENS Telegram 111 | 12 / 12 | 24 / 24 |
| SIEMENS Telegram 352 | 6 / 6 | 12 / 12 |
| Free Telegram PZD-32/32 | 32 / 32 | 64 / 64 |

---

## Telegram examples

### Standard Telegram 1 (PZD-2/2 — 4 bytes each way)
**Output (client → gateway):**
```
byte:  0    1    2    3
       STW1(BE)  NSOLL_A(BE)
e.g.:  04   7F   20   00      (STW1=0x047F "run", NSOLL=0x2000 = 50%)
```
**Input (gateway → client):**
```
byte:  0    1    2    3
       ZSW1(BE)  NIST_A(BE)
e.g.:  0F   37   20   00      (ZSW1=0x0F37 "running", NIST=0x2000)
```

### SIEMENS Telegram 111 (PZD-12/12 — 24 bytes each way)
**Output (client → gateway), 12 words:**
```
word  bytes  field         notes
 1    0-1    STW1          control word 1
 2    2-3    POS_STW1      EPOS control word 1
 3    4-5    POS_STW2      EPOS control word 2
 4    6-7    STW2          control word 2
 5    8-9    OVERRIDE      velocity override (0x4000 = 100%)
 6-7  10-13  MDI_TARPOS    target position (32-bit)
 8-9  14-17  MDI_VELOCITY  velocity (32-bit)
 10   18-19  MDI_ACC       acceleration override
 11   20-21  MDI_DEC       deceleration override
 12   22-23  reserved      0
```
**Input (gateway → client), 12 words:**
```
word  bytes  field         notes
 1    0-1    ZSW1          status word 1
 2    2-3    POS_ZSW1      EPOS status word 1
 3    4-5    POS_ZSW2      EPOS status word 2
 4    6-7    ZSW2          status word 2
 5    8-9    MELDW         message word
 6-7  10-13  XIST_A        actual position (32-bit)
 8-9  14-17  NIST_B        actual velocity (32-bit)
 10   18-19  FAULT_CODE    active fault
 11   20-21  WARN_CODE     active alarm
 12   22-23  reserved
```
(Standard PROFIdrive telegram 111 / p0922=111 — confirm against your drive.)

---

## Protocol modes

### TCP — request/response (default)
Lock-step: send the whole output frame, then read the whole input frame. Repeat.
The gateway replies **once per output frame**. An idle connection stays open.

```
client → gateway :  <output frame>            (out_size bytes)
gateway → client :  <input frame>             (in_size bytes)
```
Example, one Telegram-1 device:
```
send: 04 7F 20 00        recv: 0F 37 20 00
```

### TCP — stream inputs (Gateway option "TCP: Stream inputs")
Full-duplex. The gateway streams the input frame continuously (~50 Hz, raw
bytes); the client may send output frames anytime. A passive tool receives
data **without sending**.
```
gateway → client :  <input frame><input frame><input frame> ...   (in_size each)
client → gateway :  <output frame> whenever it wants             (out_size each)
```

### UDP — datagram request/response
One datagram out, one datagram back.
```
client → gateway :  <output frame>   (one datagram)
gateway → client :  <input frame>    (one datagram)
```

### STM — LabVIEW streaming (NI Simple TCP Messaging)
Each message is length-prefixed: `[4-byte BE length][frame]`, full-duplex.
Read with the NI STM library or plain TCP Read (read 4 bytes = length N, then
read N bytes).
```
gateway → client :  00 00 00 04  0F 37 20 00      (len=4, then the input frame)
client → gateway :  00 00 00 04  04 7F 20 00      (len=4, then the output frame)
```

---

## Per-drive framing (Gateway option "Per-drive header")
Optional. Each device's data is wrapped so the client can locate/validate each
drive's block:
```
[SOF][DriveID][Len][data ...][EOF]
 AA 55  1 byte  2B BE  Len bytes   55 AA
```
Overhead = **7 bytes per drive**. Works in every mode (the STM length still
wraps the whole framed payload).

**Example — 2 devices (Tel 1 = 4 B, Tel 111 = 24 B), input frame:**
```
Drive 0:  AA 55  00  00 04  0F 37 20 00                                55 AA
Drive 1:  AA 55  01  00 18  <24 input bytes ...>                       55 AA
total = (4+7) + (24+7) = 42 bytes
```
Parse: sync on `AA 55` → DriveID → Len → read Len data bytes → check `55 AA`.

---

## Multiple devices (flat, no framing)
Frames simply concatenate in config order. Two Telegram-1 devices:
```
output: [Dev0: STW1 NSOLL][Dev1: STW1 NSOLL]  = 8 bytes
input:  [Dev0: ZSW1 NIST ][Dev1: ZSW1 NIST ]  = 8 bytes
```

---

## Comms watchdog
If **Watchdog (ms)** > 0 and the client stops sending output frames for longer
than the timeout, the gateway zeros every device's output buffer
(STW1 = 0 → OFF1 ramp-stop) so drives don't keep running on a dead link. It
clears automatically when traffic resumes.

## Common STW1 / ZSW1 values (PROFIdrive)
```
STW1  0x047E  ready / OFF1 (ON bit cleared)
STW1  0x047F  run (full enable + ON)
STW1  0x0000  OFF2 coast
NSOLL 0x4000  = 100% nominal speed;  0x2000 = 50%

ZSW1  0x0F21  ready to switch on, stopped
ZSW1  0x0F37  running
ZSW1  bit3    fault
```

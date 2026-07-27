# GSDML device description files

Siemens SINAMICS GSDML (PROFINET device description) files used to configure
devices in the gateway. Each device folder keeps the `.xml` **and** its `.bmp`
device symbol together — the parser reads the icon from the file referenced by
`<GraphicItem GraphicFile=.../>`, so the `.bmp` must stay next to the `.xml`.

| Folder | Device | GSDML versions | Notes |
|---|---|---|---|
| `sinamics_s120/` | SINAMICS S120 (CU320-2 / CU310-2, CBE20) | V2.25, V2.34 (2022-05-06) | Full SERVO/VECTOR/infeed catalog: Standard 1–9, SIEMENS 102–220, PROFIsafe, Free, Supplementary |
| `sinamics_g120p/` | SINAMICS G120P (CU230P-2 PN) | V2.25, V2.31 (2020-05-11) | Pump/fan drive |
| `sinamics_v90/` | SINAMICS V90 PN | V2.32 (2025-03-06) | Servo drive |

Load one from the app: **Configuration → Load GSDML →** pick the `.xml`,
drag the device to the canvas, double-click → the **Submodule** dropdown lists
every telegram the file defines.

Source: Siemens Industry Online Support. Redistributed here for configuration
convenience; all rights remain with Siemens.

/* Windows platform lifecycle: DllMain entry point, timer resolution. */

#include "platform_win.h"
#include <stdio.h>
#include <timeapi.h>

BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpvReserved)
{
    (void)hinstDLL;
    (void)lpvReserved;
    switch (fdwReason) {
    case DLL_PROCESS_ATTACH:
        /* Raise timer resolution so Sleep(1) is accurate */
        timeBeginPeriod(1);
        break;
    case DLL_PROCESS_DETACH:
        timeEndPeriod(1);
        break;
    default:
        break;
    }
    return TRUE;
}

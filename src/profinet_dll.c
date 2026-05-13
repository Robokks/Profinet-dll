#include "../include/profinet_api.h"
#include "utils.h"

#include <stdio.h>

#ifdef _WIN32
#include <winsock2.h>
#include <windows.h>

BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpvReserved)
{
    (void)hinstDLL;
    (void)lpvReserved;
    switch (fdwReason) {
    case DLL_PROCESS_ATTACH:
        /* Raise Windows timer resolution so Sleep(1) is accurate */
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
#endif

void PNAPI PN_GetVersion(char *buf, uint32_t buf_size)
{
    if (!buf || buf_size == 0) return;
    snprintf(buf, (size_t)buf_size, "%d.%d.%d",
             PROFINET_VERSION_MAJOR,
             PROFINET_VERSION_MINOR,
             PROFINET_VERSION_PATCH);
}

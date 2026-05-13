#ifndef PLATFORM_WIN_H
#define PLATFORM_WIN_H

/* Windows platform types and synchronization macros.
 * Included only when building for Windows (WIN32 defined). */

#include <winsock2.h>
#include <windows.h>

/* ─── Mutex ──────────────────────────────────────────────────────────────── */
typedef CRITICAL_SECTION  PN_MUTEX;
#define PN_LOCK_INIT(m)    InitializeCriticalSection(&(m))
#define PN_LOCK_DESTROY(m) DeleteCriticalSection(&(m))
#define PN_LOCK(m)         EnterCriticalSection(&(m))
#define PN_UNLOCK(m)       LeaveCriticalSection(&(m))

/* ─── Thread ─────────────────────────────────────────────────────────────── */
typedef HANDLE  PN_THREAD;
#define PN_THREAD_NULL  NULL

/* ─── Stop event (manual-reset Windows event) ───────────────────────────── */
typedef HANDLE  PN_EVENT;
#define PN_EVENT_NULL         NULL
#define PN_EVENT_INIT(e)      ((e) = CreateEventW(NULL, TRUE, FALSE, NULL))
#define PN_EVENT_DESTROY(e)   do { if (e) { CloseHandle(e); (e) = NULL; } } while(0)

/* ─── Atomic cyclic_running flag ─────────────────────────────────────────── */
typedef volatile LONG PN_ATOMIC;
#define PN_ATOMIC_SET(a, v)  InterlockedExchange(&(a), (v))
#define PN_ATOMIC_GET(a)     ((a))

/* ─── RPC socket type ────────────────────────────────────────────────────── */
typedef SOCKET PN_SOCK;
#define PN_SOCK_INVALID    INVALID_SOCKET
#define PN_SOCK_VALID(s)   ((s) != INVALID_SOCKET)
#define PN_SOCK_STARTUP()  do { WSADATA _wd; WSAStartup(MAKEWORD(2, 2), &_wd); } while(0)
#define PN_SOCK_CLEANUP()  WSACleanup()
#define pn_sock_close(s)   closesocket(s)

#endif /* PLATFORM_WIN_H */

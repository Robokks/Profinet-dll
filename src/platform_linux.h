#ifndef PLATFORM_LINUX_H
#define PLATFORM_LINUX_H

/* NI Linux RT platform types and synchronization macros.
 * Included only when building for Linux (cRIO ARM/Intel, PXI x64). */

#include <pthread.h>
#include <stdatomic.h>
#include <unistd.h>

/* ─── Mutex ──────────────────────────────────────────────────────────────── */
typedef pthread_mutex_t  PN_MUTEX;
#define PN_LOCK_INIT(m)    pthread_mutex_init(&(m), NULL)
#define PN_LOCK_DESTROY(m) pthread_mutex_destroy(&(m))
#define PN_LOCK(m)         pthread_mutex_lock(&(m))
#define PN_UNLOCK(m)       pthread_mutex_unlock(&(m))

/* ─── Thread ─────────────────────────────────────────────────────────────── */
typedef pthread_t  PN_THREAD;
#define PN_THREAD_NULL  ((pthread_t)0)

/* ─── Stop flag (not needed on Linux — cyclic_running atomic serves this role) */
typedef int  PN_EVENT;
#define PN_EVENT_NULL        0
#define PN_EVENT_INIT(e)     ((e) = 0)
#define PN_EVENT_DESTROY(e)  ((void)0)

/* ─── Atomic cyclic_running flag ─────────────────────────────────────────── */
typedef atomic_int  PN_ATOMIC;
#define PN_ATOMIC_SET(a, v)  atomic_store(&(a), (v))
#define PN_ATOMIC_GET(a)     atomic_load(&(a))

/* ─── RPC socket type ────────────────────────────────────────────────────── */
typedef int  PN_SOCK;
#define PN_SOCK_INVALID    (-1)
#define PN_SOCK_VALID(s)   ((s) >= 0)
#define PN_SOCK_STARTUP()  ((void)0)
#define PN_SOCK_CLEANUP()  ((void)0)
#define pn_sock_close(s)   close(s)

#endif /* PLATFORM_LINUX_H */

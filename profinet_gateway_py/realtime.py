"""realtime.py — best-effort low-latency process tuning (Windows-focused).

On Windows: raise the timer resolution to 1 ms (so time.sleep(0.001) is
actually ~1 ms instead of the default ~15.6 ms), raise the process priority,
and optionally pin the process to specific CPU cores. All best-effort — any
failure is logged and ignored.
"""

import sys

# Windows priority class constants
_HIGH_PRIORITY_CLASS = 0x00000080
_REALTIME_PRIORITY_CLASS = 0x00000100
_ABOVE_NORMAL = 0x00008000


def enable_realtime(priority: str = "high", affinity=None, timer_1ms: bool = True,
                    log=print):
    """Apply low-latency tuning.

    priority : "high" (safe, recommended), "above" , "realtime" (dangerous —
               can starve the OS/network), or "normal"/None to skip.
    affinity : list of core indices to pin to, e.g. [2, 3]; None = all cores.
    timer_1ms: raise the multimedia timer resolution to 1 ms (Windows).
    """
    if sys.platform != "win32":
        _enable_posix(priority, affinity, log)
        return
    import ctypes
    kernel32 = ctypes.windll.kernel32

    if timer_1ms:
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
            log("[RT] timer resolution set to 1 ms")
        except Exception as e:
            log(f"[RT] timeBeginPeriod failed: {e}")

    cls = {"high": _HIGH_PRIORITY_CLASS, "above": _ABOVE_NORMAL,
           "realtime": _REALTIME_PRIORITY_CLASS}.get((priority or "").lower())
    if cls:
        try:
            if kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), cls):
                log(f"[RT] process priority = {priority}")
            else:
                log("[RT] SetPriorityClass failed (need admin for realtime)")
        except Exception as e:
            log(f"[RT] SetPriorityClass error: {e}")

    if affinity:
        try:
            mask = 0
            for c in affinity:
                mask |= (1 << int(c))
            if kernel32.SetProcessAffinityMask(kernel32.GetCurrentProcess(), mask):
                log(f"[RT] CPU affinity = cores {affinity} (mask 0x{mask:X})")
            else:
                log("[RT] SetProcessAffinityMask failed")
        except Exception as e:
            log(f"[RT] affinity error: {e}")


def _enable_posix(priority, affinity, log):
    try:
        import os
        if affinity and hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, set(int(c) for c in affinity))
            log(f"[RT] CPU affinity = cores {affinity}")
        if priority and priority.lower() in ("high", "above", "realtime"):
            try:
                os.nice(-10)   # needs privilege; best effort
                log("[RT] nice(-10) applied")
            except OSError:
                pass
    except Exception as e:
        log(f"[RT] posix tuning skipped: {e}")


def disable_realtime():
    """Release the 1 ms timer resolution (Windows)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.winmm.timeEndPeriod(1)
        except Exception:
            pass

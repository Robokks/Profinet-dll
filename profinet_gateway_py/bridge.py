"""bridge.py — Data bridge between GatewayServer and ProfinetCtrl."""

import threading
import time
import struct
from typing import Optional, Callable

# Bridge poll period. 1 ms keeps the gateway<->Profinet buffer hop low-latency
# (was 10 ms). Lower = less latency, more CPU.
BRIDGE_PERIOD_MS = 1.0


class Bridge:
    def __init__(self, pn_ctrl, gw_server,
                 log_cb: Optional[Callable[[str], None]] = None):
        self._pn  = pn_ctrl
        self._gw  = gw_server
        self._log = log_cb or print
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # loop-jitter / spike tracking
        self._jit_lock = threading.Lock()
        self._jit_threshold_ms = 20.0
        self._reset_jitter_locked()

    def _reset_jitter_locked(self):
        self._jit_count = 0
        self._jit_sum = 0.0
        self._jit_max = 0.0
        self._jit_last = 0.0
        self._jit_spikes = 0

    def reset_jitter(self):
        with self._jit_lock:
            self._reset_jitter_locked()

    def set_jitter_threshold(self, ms: float):
        with self._jit_lock:
            self._jit_threshold_ms = max(1.0, float(ms))

    def get_jitter(self) -> dict:
        with self._jit_lock:
            avg = self._jit_sum / self._jit_count if self._jit_count else 0.0
            return {"count": self._jit_count, "avg_ms": avg, "max_ms": self._jit_max,
                    "last_ms": self._jit_last, "spikes": self._jit_spikes,
                    "threshold_ms": self._jit_threshold_ms}

    def _track(self, interval_ms: float):
        with self._jit_lock:
            self._jit_count += 1
            self._jit_sum += interval_ms
            self._jit_last = interval_ms
            if interval_ms > self._jit_max:
                self._jit_max = interval_ms
            if interval_ms > self._jit_threshold_ms:
                self._jit_spikes += 1

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="bridge")
        self._thread.start()
        self._log("[BR] Bridge started")

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._log("[BR] Bridge stopped")

    def _run(self):
        last = time.perf_counter()
        while self._running:
            n = self._pn.device_count()
            for i in range(n):
                if not self._pn.is_connected(i):
                    continue
                ds = self._pn.device_state(i)
                if ds is None:
                    continue
                dc = ds.config
                out_total = dc.total_output_length()
                in_total = dc.total_input_length()

                # Gateway → Profinet outputs (raw frame split across the rack)
                out_data = bytes(self._gw.get_outputs(i))
                if out_total > 0 and out_data:
                    self._pn.write_raw_outputs(i, out_data)
                    # mirror first word pair into ds for the IO diagnostic view
                    # (big-endian — matches the Profinet wire order end-to-end)
                    if len(out_data) >= 4:
                        ds.stw1 = struct.unpack_from(">H", out_data, 0)[0]
                        ds.nsoll = struct.unpack_from(">H", out_data, 2)[0]

                # Profinet inputs → Gateway (concatenated rack frame)
                if in_total > 0:
                    inp = self._pn.read_raw_inputs(i, in_total)
                    self._gw.set_inputs(i, inp)
                    if len(inp) >= 4:
                        ds.zsw1 = struct.unpack_from(">H", inp, 0)[0]
                        ds.nist = struct.unpack_from(">H", inp, 2)[0]

            time.sleep(BRIDGE_PERIOD_MS / 1000.0)
            now = time.perf_counter()
            self._track((now - last) * 1000.0)
            last = now

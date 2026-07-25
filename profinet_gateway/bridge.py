"""bridge.py — Data bridge between GatewayServer and ProfinetCtrl (10ms loop)."""

import threading
import time
import struct
from typing import Optional, Callable


class Bridge:
    def __init__(self, pn_ctrl, gw_server,
                 log_cb: Optional[Callable[[str], None]] = None):
        self._pn  = pn_ctrl
        self._gw  = gw_server
        self._log = log_cb or print
        self._thread: Optional[threading.Thread] = None
        self._running = False

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
        while self._running:
            n = self._pn.device_count()
            for i in range(n):
                if not self._pn.is_connected(i):
                    continue
                ds = self._pn.device_state(i)
                if ds is None:
                    continue
                dc = ds.config

                # Gateway → Profinet outputs
                out_data = self._gw.get_outputs(i)
                if dc.output_length >= 4 and len(out_data) >= 4:
                    stw1  = struct.unpack_from("<H", out_data, 0)[0]
                    nsoll = struct.unpack_from("<H", out_data, 2)[0]
                    self._pn.write_outputs(i, stw1, nsoll)
                elif dc.output_length > 0:
                    self._pn.write_raw_outputs(i, bytes(out_data))

                # Profinet inputs → Gateway
                if dc.input_length >= 4:
                    zsw1, nist = self._pn.read_inputs(i)
                    self._gw.set_zsw1_nist(i, zsw1, nist)
                elif dc.input_length > 0:
                    inp = self._pn.read_raw_inputs(i, dc.input_length)
                    self._gw.set_inputs(i, inp)

            time.sleep(0.01)

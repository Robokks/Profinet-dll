"""gateway_server.py — TCP or UDP server bridging external clients to Profinet IO."""

import socket
import threading
import struct
import time
from typing import List, Callable, Optional


class GatewayServer:
    """
    TCP or UDP server.

    Frame layout (both directions):
      Concatenation of all devices in config order.
      Each device contributes:
        output_length bytes  (client → server → Profinet device)
        input_length  bytes  (Profinet device → server → client)

    For Telegram 1 (4B each): frame = [STW1_lo STW1_hi NSOLL_lo NSOLL_hi]
    for outputs, [ZSW1_lo ZSW1_hi NIST_lo NIST_hi] for inputs.
    """

    def __init__(self, log_cb: Optional[Callable[[str], None]] = None):
        self._log = log_cb or print
        self._protocol = "TCP"
        self._port = 5000
        self._bind = "0.0.0.0"
        self._device_configs: list = []   # list of DeviceConfig

        # Shared IO data — indexed by device index
        # Each entry: {'out': bytearray, 'inp': bytearray}
        self._io: List[dict] = []

        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._client_conn: Optional[socket.socket] = None
        self._client_addr = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def client_connected(self) -> bool:
        return self._client_conn is not None

    def configure(self, protocol: str, port: int, bind: str, device_configs: list):
        self._protocol  = protocol.upper()
        self._port      = port
        self._bind      = bind
        self._device_configs = device_configs
        with self._lock:
            self._io = [
                {"out": bytearray(dc.total_output_length()), "inp": bytearray(dc.total_input_length())}
                for dc in device_configs
            ]

    def get_outputs(self, idx: int) -> bytearray:
        with self._lock:
            if idx < len(self._io):
                return bytearray(self._io[idx]["out"])
            return bytearray()

    def get_inputs(self, idx: int) -> bytearray:
        with self._lock:
            if idx < len(self._io):
                return bytearray(self._io[idx]["inp"])
            return bytearray()

    def set_inputs(self, idx: int, data: bytes):
        with self._lock:
            if idx < len(self._io):
                n = min(len(data), len(self._io[idx]["inp"]))
                self._io[idx]["inp"][:n] = data[:n]

    def get_stw1_nsoll(self, idx: int):
        out = self.get_outputs(idx)
        if len(out) >= 4:
            stw1  = struct.unpack_from(">H", out, 0)[0]
            nsoll = struct.unpack_from(">H", out, 2)[0]
            return stw1, nsoll
        return 0, 0

    def set_zsw1_nist(self, idx: int, zsw1: int, nist: int):
        data = struct.pack(">HH", zsw1, nist)
        self.set_inputs(idx, data)

    def set_outputs(self, idx: int, data: bytes):
        """Overwrite (the head of) a device's output buffer — used by Force so
        the bridge propagates and holds the value on the wire."""
        with self._lock:
            if idx < len(self._io):
                buf = self._io[idx]["out"]
                n = min(len(data), len(buf))
                buf[:n] = data[:n]

    def start(self):
        if self._running:
            return
        self._running = True
        if self._protocol == "TCP":
            self._thread = threading.Thread(target=self._tcp_server, daemon=True)
        else:
            self._thread = threading.Thread(target=self._udp_server, daemon=True)
        self._thread.start()
        self._log(f"[GW] {self._protocol} server listening on {self._bind}:{self._port}")

    def stop(self):
        self._running = False
        if self._client_conn:
            try:
                self._client_conn.close()
            except Exception:
                pass
            self._client_conn = None
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._log("[GW] Server stopped")

    # ── TCP server ──────────────────────────────────────────────────────────
    def _tcp_server(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.settimeout(1.0)
            self._sock.bind((self._bind, self._port))
            self._sock.listen(1)
        except OSError as e:
            self._log(f"[GW] TCP bind error: {e}")
            self._running = False
            return

        while self._running:
            try:
                conn, addr = self._sock.accept()
                self._client_conn = conn
                self._client_addr = addr
                self._log(f"[GW] TCP client connected: {addr}")
                self._handle_tcp_client(conn)
                self._client_conn = None
                self._log(f"[GW] TCP client disconnected: {addr}")
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_tcp_client(self, conn: socket.socket):
        conn.settimeout(0.1)
        out_size = sum(dc.total_output_length() for dc in self._device_configs)
        inp_size = sum(dc.total_input_length()  for dc in self._device_configs)

        while self._running:
            # Receive output frame from client
            if out_size > 0:
                try:
                    data = self._recv_exact(conn, out_size)
                    if data is None:
                        break
                    if data:   # full frame (b'' = partial stall → skip this cycle)
                        offset = 0
                        with self._lock:
                            for i, dc in enumerate(self._device_configs):
                                n = dc.total_output_length()
                                self._io[i]["out"][:n] = data[offset:offset + n]
                                offset += n
                except socket.timeout:
                    pass
                except OSError:
                    break

            # Send input frame back to client
            if inp_size > 0:
                with self._lock:
                    inp_frame = b"".join(bytes(self._io[i]["inp"])
                                         for i in range(len(self._device_configs)))
                try:
                    conn.sendall(inp_frame)
                except OSError:
                    break

            # input-only config: no recv above, so pace to avoid a CPU spin
            if out_size == 0:
                time.sleep(0.02)

        try:
            conn.close()
        except Exception:
            pass

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int, deadline_s: float = 2.0):
        """Receive exactly n bytes. Returns None on disconnect, or b'' if a
        partial frame stalls past the deadline (so a client that sends the
        wrong frame size can't wedge the server thread forever)."""
        buf = b""
        waited = 0.0
        while len(buf) < n:
            try:
                chunk = conn.recv(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
                waited = 0.0
            except socket.timeout:
                if not buf:
                    return None
                waited += 0.1
                if waited >= deadline_s:
                    return b""   # partial-frame stall — resync next loop
        return buf

    # ── UDP server ──────────────────────────────────────────────────────────
    def _udp_server(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.settimeout(0.5)
            self._sock.bind((self._bind, self._port))
        except OSError as e:
            self._log(f"[GW] UDP bind error: {e}")
            self._running = False
            return

        out_size = sum(dc.total_output_length() for dc in self._device_configs)
        inp_size = sum(dc.total_input_length()  for dc in self._device_configs)

        while self._running:
            try:
                data, addr = self._sock.recvfrom(4096)
                self._client_addr = addr
                if len(data) >= out_size:
                    offset = 0
                    with self._lock:
                        for i, dc in enumerate(self._device_configs):
                            n = dc.total_output_length()
                            self._io[i]["out"][:n] = data[offset:offset + n]
                            offset += n
                # Reply with inputs
                with self._lock:
                    inp_frame = b"".join(bytes(self._io[i]["inp"])
                                          for i in range(len(self._device_configs)))
                self._sock.sendto(inp_frame, addr)
            except socket.timeout:
                continue
            except OSError:
                break

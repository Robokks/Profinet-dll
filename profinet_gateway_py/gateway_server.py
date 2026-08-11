"""gateway_server.py — TCP or UDP server bridging external clients to Profinet IO."""

import socket
import threading
import struct
import time
from typing import List, Callable, Optional

# Per-drive framing markers: [SOF 2B][DriveID 1B][Len 2B BE][data][EOF 2B]
FRAME_SOF = b"\xAA\x55"
FRAME_EOF = b"\x55\xAA"
FRAME_OVERHEAD = len(FRAME_SOF) + 1 + 2 + len(FRAME_EOF)   # = 7 bytes per drive


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
        self._framed = False
        self._tcp_stream = False
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

        # ── traffic monitoring ──
        self._mon_lock = threading.Lock()
        self._mon = {"rx_frames": 0, "rx_bytes": 0, "last_rx": b"",
                     "tx_frames": 0, "tx_bytes": 0, "last_tx": b""}

        # ── comms watchdog (fail-safe on client loss) ──
        self._watchdog_ms = 0
        self._last_rx_t = 0.0            # perf_counter of last received frame
        self._watchdog_tripped = False
        self._wd_thread: Optional[threading.Thread] = None

    def _mon_rx(self, data: bytes):
        """Record an output frame received from the client (client → EXE)."""
        with self._mon_lock:
            self._mon["rx_frames"] += 1
            self._mon["rx_bytes"] += len(data)
            self._mon["last_rx"] = bytes(data)
            self._last_rx_t = time.perf_counter()
        if self._watchdog_tripped:
            self._watchdog_tripped = False
            self._log("[GW] Watchdog cleared — client traffic resumed")

    def _mon_tx(self, data: bytes):
        """Record an input frame sent to the client (EXE → client)."""
        with self._mon_lock:
            self._mon["tx_frames"] += 1
            self._mon["tx_bytes"] += len(data)
            self._mon["last_tx"] = bytes(data)

    def get_monitor(self) -> dict:
        with self._mon_lock:
            m = dict(self._mon)
        m["protocol"] = self._protocol
        m["port"] = self._port
        m["bind"] = self._bind
        m["running"] = self._running
        m["client"] = self._client_addr if self._client_conn else None
        m["watchdog_ms"] = self._watchdog_ms
        m["watchdog_tripped"] = self._watchdog_tripped
        with self._mon_lock:
            m["ms_since_rx"] = ((time.perf_counter() - self._last_rx_t) * 1000.0
                                if self._last_rx_t else -1)
        return m

    def _zero_outputs(self):
        """Fail-safe: clear every device's output buffer (STW1=0 → OFF/stop)."""
        with self._lock:
            for io in self._io:
                io["out"][:] = bytes(len(io["out"]))

    def _watchdog_loop(self):
        while self._running:
            if self._watchdog_ms > 0:
                with self._mon_lock:
                    last = self._last_rx_t
                if last > 0 and (time.perf_counter() - last) * 1000.0 > self._watchdog_ms:
                    if not self._watchdog_tripped:
                        self._watchdog_tripped = True
                        self._log(f"[GW] WATCHDOG TRIPPED: client silent > "
                                  f"{self._watchdog_ms} ms — outputs set to SAFE (zero)")
                    self._zero_outputs()
            time.sleep(0.02)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def client_connected(self) -> bool:
        return self._client_conn is not None

    def configure(self, protocol: str, port: int, bind: str, device_configs: list,
                  framed: bool = False, watchdog_ms: int = 0, tcp_stream: bool = False):
        self._protocol  = protocol.upper()
        self._port      = port
        self._bind      = bind
        self._framed    = framed
        self._tcp_stream = tcp_stream
        self._watchdog_ms = int(watchdog_ms or 0)
        self._last_rx_t = 0.0
        self._watchdog_tripped = False
        self._device_configs = device_configs
        with self._lock:
            self._io = [
                {"out": bytearray(dc.total_output_length()), "inp": bytearray(dc.total_input_length())}
                for dc in device_configs
            ]

    # ── frame sizing / (un)framing (flat or per-drive SOF/EOF) ───────────────
    def _out_frame_size(self) -> int:
        base = sum(dc.total_output_length() for dc in self._device_configs)
        return base + (FRAME_OVERHEAD * len(self._device_configs) if self._framed else 0)

    def _in_frame_size(self) -> int:
        base = sum(dc.total_input_length() for dc in self._device_configs)
        return base + (FRAME_OVERHEAD * len(self._device_configs) if self._framed else 0)

    def _build_inputs(self) -> bytes:
        """Input frame (device→client): flat concat, or per-drive framed."""
        with self._lock:
            if not self._framed:
                return b"".join(bytes(self._io[i]["inp"])
                                for i in range(len(self._device_configs)))
            parts = []
            for i in range(len(self._device_configs)):
                data = bytes(self._io[i]["inp"])
                parts.append(FRAME_SOF + bytes([i & 0xFF]) +
                             struct.pack(">H", len(data)) + data + FRAME_EOF)
            return b"".join(parts)

    def _apply_outputs(self, data: bytes):
        """Output frame (client→device): distribute flat, or parse framed blocks."""
        if not self._framed:
            offset = 0
            with self._lock:
                for i, dc in enumerate(self._device_configs):
                    n = dc.total_output_length()
                    self._io[i]["out"][:n] = data[offset:offset + n]
                    offset += n
            return
        # framed: walk [SOF][id][len][data][EOF] blocks, resync on bad markers
        pos, n = 0, len(data)
        with self._lock:
            while pos + FRAME_OVERHEAD <= n:
                if data[pos:pos + 2] != FRAME_SOF:
                    pos += 1
                    continue
                drive = data[pos + 2]
                ln = struct.unpack(">H", data[pos + 3:pos + 5])[0]
                ds = pos + 5
                de = ds + ln
                if de + 2 > n or data[de:de + 2] != FRAME_EOF:
                    pos += 1
                    continue
                if drive < len(self._io):
                    m = min(ln, len(self._io[drive]["out"]))
                    self._io[drive]["out"][:m] = data[ds:ds + m]
                pos = de + 2

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
        if self._protocol == "UDP":
            self._thread = threading.Thread(target=self._udp_server, daemon=True)
        elif self._protocol == "STM":
            self._thread = threading.Thread(target=self._stm_server, daemon=True)
        else:
            self._thread = threading.Thread(target=self._tcp_server, daemon=True)
        self._thread.start()
        self._log(f"[GW] {self._protocol} server listening on {self._bind}:{self._port}")
        if self._watchdog_ms > 0:
            self._wd_thread = threading.Thread(target=self._watchdog_loop, daemon=True,
                                               name="gw-watchdog")
            self._wd_thread.start()
            self._log(f"[GW] Comms watchdog armed: {self._watchdog_ms} ms → safe stop")

    def stop(self):
        self._running = False
        if self._wd_thread and self._wd_thread.is_alive():
            self._wd_thread.join(timeout=1.0)
        self._wd_thread = None
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
        if self._tcp_stream:
            self._handle_tcp_stream(conn)
            return
        conn.settimeout(0.1)
        out_size = self._out_frame_size()
        inp_size = self._in_frame_size()

        while self._running:
            if out_size > 0:
                # Request/response: wait for an output frame, then reply with
                # inputs. Idle (b'') keeps the connection alive — do NOT drop.
                try:
                    data = self._recv_exact(conn, out_size)
                except OSError:
                    break
                if data is None:
                    break                      # client actually disconnected
                if not data:
                    continue                   # idle / partial — stay connected
                self._mon_rx(data)
                self._apply_outputs(data)
                if inp_size > 0:
                    inp_frame = self._build_inputs()
                    try:
                        conn.sendall(inp_frame)
                        self._mon_tx(inp_frame)
                    except OSError:
                        break
            else:
                # Input-only config: stream inputs to the client, paced.
                if inp_size > 0:
                    inp_frame = self._build_inputs()
                    try:
                        conn.sendall(inp_frame)
                        self._mon_tx(inp_frame)
                    except OSError:
                        break
                time.sleep(0.02)

        try:
            conn.close()
        except Exception:
            pass

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int, deadline_s: float = 2.0):
        """Receive exactly n bytes.
        Returns None ONLY on a real disconnect (recv returns empty).
        Returns b'' when the client is idle (nothing sent yet) or a partial
        frame stalls past the deadline — the caller stays connected and loops.
        """
        buf = b""
        waited = 0.0
        while len(buf) < n:
            try:
                chunk = conn.recv(n - len(buf))
                if not chunk:
                    return None          # peer closed the connection
                buf += chunk
                waited = 0.0
            except socket.timeout:
                if not buf:
                    return b""            # idle — no bytes yet, keep connection
                waited += 0.1
                if waited >= deadline_s:
                    return b""            # partial-frame stall — resync next loop
        return buf

    def _handle_tcp_stream(self, conn: socket.socket):
        """Full-duplex TCP: stream raw input frames continuously (~50 Hz) while
        reading raw output frames as they arrive — like STM but with no length
        prefix, so a passive tool (Hercules) receives data without sending."""
        conn.settimeout(0.2)
        out_size = self._out_frame_size()
        in_size = self._in_frame_size()
        tx_stop = threading.Event()

        def tx_loop():
            while self._running and not tx_stop.is_set():
                if in_size > 0:
                    frame = self._build_inputs()
                    try:
                        conn.sendall(frame)
                        self._mon_tx(frame)
                    except OSError:
                        break
                time.sleep(0.02)

        tx = threading.Thread(target=tx_loop, daemon=True, name="tcp-stream-tx")
        tx.start()
        try:
            while self._running:
                if out_size > 0:
                    data = self._recv_all(conn, out_size)
                    if data is None:
                        break
                    self._mon_rx(data)
                    self._apply_outputs(data)
                else:
                    time.sleep(0.05)
        except OSError:
            pass
        finally:
            tx_stop.set()
            tx.join(timeout=1.0)
            try:
                conn.close()
            except Exception:
                pass

    # ── STM server (LabVIEW streaming: [4B BE length][frame], full-duplex) ──
    def _stm_server(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.settimeout(1.0)
            self._sock.bind((self._bind, self._port))
            self._sock.listen(1)
        except OSError as e:
            self._log(f"[GW] STM bind error: {e}")
            self._running = False
            return

        while self._running:
            try:
                conn, addr = self._sock.accept()
                self._client_conn = conn
                self._client_addr = addr
                self._log(f"[GW] STM client connected: {addr}")
                self._handle_stm_client(conn)
                self._client_conn = None
                self._log(f"[GW] STM client disconnected: {addr}")
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_stm_client(self, conn: socket.socket):
        conn.settimeout(0.2)
        in_size = self._in_frame_size()
        tx_stop = threading.Event()

        # TX: stream the input frame (device→controller) continuously (~50 Hz)
        def tx_loop():
            while self._running and not tx_stop.is_set():
                if in_size > 0:
                    frame = self._build_inputs()
                    try:
                        conn.sendall(struct.pack(">I", len(frame)) + frame)
                        self._mon_tx(frame)
                    except OSError:
                        break
                time.sleep(0.02)

        tx = threading.Thread(target=tx_loop, daemon=True, name="stm-tx")
        tx.start()

        # RX: read framed output messages (controller→device) as they arrive
        try:
            while self._running:
                hdr = self._recv_all(conn, 4)
                if hdr is None:
                    break
                n = struct.unpack(">I", hdr)[0]
                if n == 0 or n > 1_000_000:
                    continue
                payload = self._recv_all(conn, n)
                if payload is None:
                    break
                self._mon_rx(payload)
                self._apply_outputs(payload)
        except OSError:
            pass
        finally:
            tx_stop.set()
            tx.join(timeout=1.0)
            try:
                conn.close()
            except Exception:
                pass

    def _recv_all(self, conn: socket.socket, n: int):
        """Block until exactly n bytes are read (retrying on timeout while the
        server runs). Returns None on clean disconnect or error."""
        buf = b""
        while len(buf) < n and self._running:
            try:
                chunk = conn.recv(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            except socket.timeout:
                continue
            except OSError:
                return None
        return buf if len(buf) == n else None

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

        out_size = self._out_frame_size()

        while self._running:
            try:
                data, addr = self._sock.recvfrom(65535)
                self._client_addr = addr
                if len(data) >= out_size:
                    self._mon_rx(data)
                    self._apply_outputs(data)
                # Reply with inputs
                inp_frame = self._build_inputs()
                self._sock.sendto(inp_frame, addr)
                self._mon_tx(inp_frame)
            except socket.timeout:
                continue
            except OSError:
                break

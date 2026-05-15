"""
network.py — simple TCP host/client for VOID MAZE multiplayer.

Wire format: 4-byte big-endian length prefix + UTF-8 JSON.
Each connection runs a background reader thread that decodes messages
into an inbox deque; the main pygame loop polls non-blocking.

Used by Maze.py — host runs a Server (and plays as player 0); each other 
peer runs a Client that connects to the host's IP (Radmin VPN IP works
just like a normal LAN IP).
"""

import socket
import struct
import json
import threading
from collections import deque

DEFAULT_PORT = 5555
MAX_PLAYERS  = 4   # host + up to 3 clients


# ── wire protocol ─────────────────────────────────────────────────────────────
def _send_message(sock, msg):
    data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)


def _recv_all(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _recv_message(sock):
    hdr = _recv_all(sock, 4)
    if hdr is None:
        return None
    (length,) = struct.unpack(">I", hdr)
    if length <= 0 or length > 10_000_000:
        return None
    data = _recv_all(sock, length)
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


# ── connection ────────────────────────────────────────────────────────────────
class Connection:
    """Wraps a socket: reader thread fills inbox; .send() writes from caller."""

    def __init__(self, sock, addr=None):
        self.sock = sock
        self.addr = addr
        self.inbox = deque()
        self.alive = True
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self):
        try:
            while self.alive:
                msg = _recv_message(self.sock)
                if msg is None:
                    break
                with self._lock:
                    self.inbox.append(msg)
        except Exception:
            pass
        finally:
            self.alive = False

    def send(self, msg):
        if not self.alive:
            return False
        try:
            _send_message(self.sock, msg)
            return True
        except Exception:
            self.alive = False
            return False

    def drain(self):
        """Return all queued messages and clear the inbox."""
        with self._lock:
            msgs = list(self.inbox)
            self.inbox.clear()
        return msgs

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except Exception:
            pass


# ── server ────────────────────────────────────────────────────────────────────
class Server:
    """TCP server. Accepts up to (MAX_PLAYERS - 1) clients on a background thread."""

    def __init__(self, port=DEFAULT_PORT, max_clients=MAX_PLAYERS - 1):
        self.port = port
        self.max_clients = max_clients
        self.connections = []
        self._lock = threading.Lock()

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", port))
        self._sock.listen(max_clients)
        self._sock.settimeout(0.5)   # so accept loop can poll alive flag

        self.alive = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while self.alive:
            try:
                client_sock, addr = self._sock.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            with self._lock:
                if len(self.connections) < self.max_clients:
                    self.connections.append(Connection(client_sock, addr))
                else:
                    try:
                        client_sock.close()
                    except Exception:
                        pass

    def broadcast(self, msg):
        with self._lock:
            for conn in list(self.connections):
                conn.send(msg)

    def send_to(self, conn_index, msg):
        with self._lock:
            if 0 <= conn_index < len(self.connections):
                return self.connections[conn_index].send(msg)
        return False

    def drain_all(self):
        """Return list of (conn_index, msg) for every queued message.

        Materialised (not a generator) so callers can safely invoke send_to /
        broadcast inside their loop without deadlocking on self._lock.
        """
        out = []
        with self._lock:
            for i, conn in enumerate(self.connections):
                for msg in conn.drain():
                    out.append((i, msg))
        return out

    def prune_dead(self):
        """Remove dead connections (shifts indices — only safe BEFORE a match starts)."""
        dead = []
        with self._lock:
            kept = []
            for i, conn in enumerate(self.connections):
                if conn.alive:
                    kept.append(conn)
                else:
                    dead.append(i)
            self.connections = kept
        return dead

    def dead_indices(self):
        """Return indices of dead connections WITHOUT removing them (indices stay stable)."""
        with self._lock:
            return [i for i, c in enumerate(self.connections) if not c.alive]

    def count(self):
        """Number of currently alive client connections."""
        with self._lock:
            return sum(1 for c in self.connections if c.alive)

    def close(self):
        self.alive = False
        try:
            self._sock.close()
        except Exception:
            pass
        with self._lock:
            for conn in self.connections:
                conn.close()
            self.connections.clear()


# ── client ────────────────────────────────────────────────────────────────────
class Client:
    """TCP client connecting to a host."""

    def __init__(self, host, port=DEFAULT_PORT, timeout=5.0):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.settimeout(None)
        self.conn = Connection(sock)

    def send(self, msg):
        return self.conn.send(msg)

    def drain(self):
        return self.conn.drain()

    @property
    def alive(self):
        return self.conn.alive

    def close(self):
        self.conn.close()

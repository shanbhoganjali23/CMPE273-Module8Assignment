"""
Inventory replica node.

Run as Primary:    python node.py primary   --port 8001 --secondary http://127.0.0.1:8002
Run as Secondary:  python node.py secondary --port 8002

Primary
  GET  /stock                 -> current value + version
  POST /stock  {"value": N}   -> write new value (primary only), queued for replication
  POST /replication/pause     -> stop sending updates to the secondary (simulates a delay/partition)
  POST /replication/resume    -> resume sending queued updates
  GET  /replication/status    -> paused flag + number of pending updates

Secondary
  GET  /stock                 -> current value + version (may be stale)
  POST /replicate {"value": N, "version": V} -> apply an update from the primary
"""

import argparse
import json
import queue
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def log(role, msg):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] [{role.upper():9}] {msg}", flush=True)


class Replica:
    def __init__(self, role, initial=10):
        self.role = role
        self.value = initial
        self.version = 0
        self.lock = threading.Lock()

    def read(self):
        with self.lock:
            return {"role": self.role, "stock": self.value, "version": self.version}

    def write(self, value):
        with self.lock:
            self.version += 1
            self.value = value
            return self.version

    def apply(self, value, version):
        """Apply a replicated update; ignore anything older than what we have."""
        with self.lock:
            if version <= self.version:
                return False
            self.value = value
            self.version = version
            return True


class Replicator(threading.Thread):
    """Background thread on the primary that ships updates to the secondary asynchronously."""

    def __init__(self, secondary_url, lag):
        super().__init__(daemon=True)
        self.secondary_url = secondary_url
        self.lag = lag                      # normal network/replication lag in seconds
        self.pending = queue.Queue()
        self.running = threading.Event()
        self.running.set()                  # replication on by default

    def enqueue(self, value, version):
        self.pending.put({"value": value, "version": version})

    def run(self):
        while True:
            update = self.pending.get()
            # While paused, hold the update here (it is not lost).
            while not self.running.is_set():
                time.sleep(0.05)
            time.sleep(self.lag)
            while True:
                try:
                    body = json.dumps(update).encode()
                    req = urllib.request.Request(
                        self.secondary_url + "/replicate", data=body,
                        headers={"Content-Type": "application/json"}, method="POST")
                    urllib.request.urlopen(req, timeout=2).read()
                    log("primary", f"replicated stock={update['value']} (v{update['version']}) -> secondary")
                    break
                except Exception as e:  # secondary down: retry until it comes back
                    log("primary", f"replication failed ({e}); retrying")
                    time.sleep(0.5)


def make_handler(replica, replicator):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence default access log
            pass

        def _send(self, code, payload):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            if self.path.split("?")[0] == "/stock":
                state = replica.read()
                if "quiet" not in self.path:  # client polling uses ?quiet to keep logs readable
                    log(replica.role, f"read  -> stock={state['stock']} (v{state['version']})")
                return self._send(200, state)
            if self.path == "/replication/status" and replicator:
                return self._send(200, {"paused": not replicator.running.is_set(),
                                        "pending": replicator.pending.qsize()})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if replica.role == "primary":
                if self.path == "/stock":
                    value = int(self._body()["value"])
                    version = replica.write(value)
                    log("primary", f"write -> stock={value} (v{version}), queued for replication")
                    replicator.enqueue(value, version)
                    return self._send(200, {"stock": value, "version": version})
                if self.path == "/replication/pause":
                    replicator.running.clear()
                    log("primary", "replication PAUSED (simulating delay to secondary)")
                    return self._send(200, {"paused": True})
                if self.path == "/replication/resume":
                    replicator.running.set()
                    log("primary", "replication RESUMED")
                    return self._send(200, {"paused": False})
            if replica.role == "secondary" and self.path == "/replicate":
                b = self._body()
                if replica.apply(int(b["value"]), int(b["version"])):
                    log("secondary", f"applied replicated stock={b['value']} (v{b['version']})")
                return self._send(200, {"ok": True})
            if replica.role == "secondary" and self.path == "/stock":
                return self._send(403, {"error": "secondary is read-only; write to the primary"})
            self._send(404, {"error": "not found"})

    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument("role", choices=["primary", "secondary"])
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--secondary", default="http://127.0.0.1:8002", help="secondary URL (primary only)")
    p.add_argument("--lag", type=float, default=0.5, help="normal replication lag in seconds")
    p.add_argument("--initial", type=int, default=10)
    args = p.parse_args()

    replica = Replica(args.role, args.initial)
    replicator = None
    if args.role == "primary":
        replicator = Replicator(args.secondary, args.lag)
        replicator.start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(replica, replicator))
    log(args.role, f"listening on 127.0.0.1:{args.port}, stock={args.initial}")
    server.serve_forever()


if __name__ == "__main__":
    main()

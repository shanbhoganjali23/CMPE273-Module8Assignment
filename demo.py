"""
End-to-end demo: starts the Primary and Secondary as separate processes,
then runs the three required tests and prints PASS/FAIL for each check.

  python demo.py
"""

import os
import subprocess
import sys
import threading
import time
from datetime import datetime

import client

HERE = os.path.dirname(os.path.abspath(__file__))


def say(msg):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] [CLIENT   ] {msg}", flush=True)


def check(label, cond):
    say(f"{'PASS' if cond else 'FAIL'}: {label}")
    return cond


def show(node):
    r = client.read(node)
    say(f"read {node:9} -> stock={r['stock']} (v{r['version']})")
    return r["stock"]


def wait_until(node, expected, timeout=5):
    start = time.time()
    while time.time() - start < timeout:
        if client.read(node, quiet=True)["stock"] == expected:
            return time.time() - start
        time.sleep(0.1)
    return None


def pipe(proc):
    for line in proc.stdout:
        print(line, end="", flush=True)


def start(role, port, *extra):
    proc = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "node.py"), role, "--port", str(port), *extra],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    threading.Thread(target=pipe, args=(proc,), daemon=True).start()
    return proc


def main():
    secondary = start("secondary", 8002)
    primary = start("primary", 8001, "--secondary", "http://127.0.0.1:8002", "--lag", "0.5")
    time.sleep(1)
    results = []
    try:
        print("\n========== TEST 1: REPLICATION ==========", flush=True)
        show("primary"); show("secondary")
        say("write stock=12 to primary")
        client.write(12)
        p = show("primary")
        results.append(check("primary has new value 12", p == 12))
        t = wait_until("secondary", 12)
        say(f"secondary caught up after {t:.2f}s" if t is not None else "secondary never caught up")
        p, s = show("primary"), show("secondary")
        results.append(check("secondary eventually received 12", s == 12))
        results.append(check("both replicas return the same value", p == s))

        # reset to the assignment's starting value
        say("reset: write stock=10 to primary")
        client.write(10)
        wait_until("secondary", 10)

        print("\n========== TEST 2: STALE READ ==========", flush=True)
        show("primary"); show("secondary")
        say("pause replication to the secondary")
        client.pause()
        say("write stock=9 to primary")
        client.write(9)
        for _ in range(3):  # read a few times over ~3s to show the stale value persists
            time.sleep(1)
            p, s = show("primary"), show("secondary")
        results.append(check("primary returns 9", p == 9))
        results.append(check("secondary returns old value 10 (STALE READ)", s == 10))

        print("\n========== TEST 3: RECOVERY AND CONVERGENCE ==========", flush=True)
        say("resume replication")
        client.resume()
        t = wait_until("secondary", 9)
        say(f"secondary converged after {t:.2f}s" if t is not None else "secondary never converged")
        p, s = show("primary"), show("secondary")
        results.append(check("secondary eventually updates to 9", s == 9))
        results.append(check("both replicas return the same value again", p == s == 9))

        print(f"\n========== {sum(results)}/{len(results)} CHECKS PASSED ==========", flush=True)
    finally:
        time.sleep(0.3)
        primary.terminate(); secondary.terminate()
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

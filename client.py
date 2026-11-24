"""
Client for the inventory replicas.

  python client.py read primary
  python client.py read secondary
  python client.py write 9          # writes always go to the primary
  python client.py pause            # pause replication to the secondary
  python client.py resume
"""

import json
import sys
import urllib.request

NODES = {"primary": "http://127.0.0.1:8001", "secondary": "http://127.0.0.1:8002"}


def _call(url, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read())


def read(node, quiet=False):
    return _call(NODES[node] + "/stock" + ("?quiet" if quiet else ""))


def write(value):
    return _call(NODES["primary"] + "/stock", "POST", {"value": value})


def pause():
    return _call(NODES["primary"] + "/replication/pause", "POST", {})


def resume():
    return _call(NODES["primary"] + "/replication/resume", "POST", {})


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "read" and len(sys.argv) == 3:
        print(read(sys.argv[2]))
    elif cmd == "write" and len(sys.argv) == 3:
        print(write(int(sys.argv[2])))
    elif cmd == "pause":
        print(pause())
    elif cmd == "resume":
        print(resume())
    else:
        print(__doc__)
        sys.exit(1)

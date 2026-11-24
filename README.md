# Module 8: Replication & Consistency

A small replicated inventory system in Python (standard library only, nothing to install). It has one **Primary** replica, one **Secondary** replica and a **client**. Writes go to the Primary and are copied to the Secondary **asynchronously**, so the Secondary can briefly serve stale data. That is how the stale-read test works.

## Files

| File | Purpose |
|---|---|
| `node.py` | A replica node. Run it as `primary` or `secondary`. Each node is its own HTTP server process. |
| `client.py` | Client that reads from either replica, writes to the Primary, and pauses or resumes replication. |
| `demo.py` | Starts both replicas, runs all three required tests, and prints PASS/FAIL for each check. |
| `evidence/demo_output.txt` | Full terminal output from a run of `demo.py`. |

## How to run

You need Python 3.8 or later. No packages are required.

### Option A: one command (recommended)

```bash
python demo.py
```

This starts the Primary on port 8001 and the Secondary on port 8002, then runs the replication, stale-read and recovery tests and stops both servers. It exits with code 0 when every check passes.

### Option B: by hand, in separate terminals

```bash
# Terminal 1
python node.py secondary --port 8002

# Terminal 2
python node.py primary --port 8001 --secondary http://127.0.0.1:8002 --lag 0.5

# Terminal 3: client
python client.py read primary        # {'role': 'primary', 'stock': 10, 'version': 0}
python client.py read secondary
python client.py write 9             # writes always go to the primary
python client.py pause               # delay replication to the secondary
python client.py resume              # let the queued update through
```

`--lag` is the normal replication delay in seconds. `pause` holds updates in the Primary's replication queue. Nothing is lost, and the updates are sent when you run `resume`.

## Architecture

```
                         writes (POST /stock)
        +--------+  ---------------------------->  +-------------------+
        |        |                                 |  PRIMARY  :8001   |
        |        |  <----------------------------  |  stock, version   |
        |        |        reads (GET /stock)       |                   |
        | CLIENT |                                 |  [ replication    |
        |        |                                 |    queue + thread ]|
        |        |                                 +---------+---------+
        |        |                                           |  async POST /replicate
        |        |                                           |  {value, version}
        |        |                                           |  (pause / resume here)
        |        |        reads (GET /stock)       +---------v---------+
        |        |  <----------------------------  |  SECONDARY :8002  |
        +--------+                                 |  read-only copy   |
                                                   +-------------------+
```

- **Primary**: accepts writes. Each write increments a version number and is put in a replication queue.
- **Replicator thread** (inside the Primary): takes updates off the queue and sends them to the Secondary after a short lag. If the Secondary can't be reached, it retries. When replication is paused, it holds updates in the queue.
- **Secondary**: read-only. It applies an update only if the update's version is newer than its own, so a late or repeated message can't roll it back.
- **Client**: reads from either replica and sends writes to the Primary.

## Evidence

All output below comes from running `python demo.py`. The full log is in [`evidence/demo_output.txt`](evidence/demo_output.txt). Lines are tagged with the process that printed them, and `vN` is the data version.

### 1. Replication

The Primary takes the write right away. The Secondary gets the same value about 0.5 s later, and after that both replicas return the same value.

```
========== TEST 1: REPLICATION ==========
[20:37:11.511] [CLIENT   ] read secondary -> stock=10 (v0)
[20:37:11.511] [CLIENT   ] write stock=12 to primary
[20:37:11.512] [PRIMARY  ] write -> stock=12 (v1), queued for replication
[20:37:11.513] [CLIENT   ] read primary   -> stock=12 (v1)
[20:37:11.513] [CLIENT   ] PASS: primary has new value 12
[20:37:12.022] [SECONDARY] applied replicated stock=12 (v1)
[20:37:12.023] [PRIMARY  ] replicated stock=12 (v1) -> secondary
[20:37:12.123] [CLIENT   ] secondary caught up after 0.61s
[20:37:12.124] [CLIENT   ] read primary   -> stock=12 (v1)
[20:37:12.125] [CLIENT   ] read secondary -> stock=12 (v1)
[20:37:12.125] [CLIENT   ] PASS: secondary eventually received 12
[20:37:12.125] [CLIENT   ] PASS: both replicas return the same value
```

(After this test the demo writes `stock=10` again so that Test 2 starts from the assignment's value.)

### 2. Stale read

Replication is paused and the Primary is updated from 10 to 9. For the next 3 seconds the Primary returns **9**, but the Secondary still returns the old value **10**. Any client that reads from the Secondary in that window gets a **stale read**.

```
========== TEST 2: STALE READ ==========
[20:37:12.634] [CLIENT   ] read primary   -> stock=10 (v2)
[20:37:12.635] [CLIENT   ] read secondary -> stock=10 (v2)
[20:37:12.635] [PRIMARY  ] replication PAUSED (simulating delay to secondary)
[20:37:12.636] [PRIMARY  ] write -> stock=9 (v3), queued for replication
[20:37:13.638] [CLIENT   ] read primary   -> stock=9 (v3)
[20:37:13.639] [CLIENT   ] read secondary -> stock=10 (v2)
[20:37:14.640] [CLIENT   ] read primary   -> stock=9 (v3)
[20:37:14.641] [CLIENT   ] read secondary -> stock=10 (v2)
[20:37:15.643] [CLIENT   ] read primary   -> stock=9 (v3)
[20:37:15.644] [CLIENT   ] read secondary -> stock=10 (v2)
[20:37:15.644] [CLIENT   ] PASS: primary returns 9
[20:37:15.644] [CLIENT   ] PASS: secondary returns old value 10 (STALE READ)
```

### 3. Recovery and convergence

When replication resumes, the queued update (v3) is sent. The Secondary moves to **9**, and both replicas agree again.

```
========== TEST 3: RECOVERY AND CONVERGENCE ==========
[20:37:15.644] [CLIENT   ] resume replication
[20:37:15.645] [PRIMARY  ] replication RESUMED
[20:37:16.148] [SECONDARY] applied replicated stock=9 (v3)
[20:37:16.148] [PRIMARY  ] replicated stock=9 (v3) -> secondary
[20:37:16.154] [CLIENT   ] secondary converged after 0.51s
[20:37:16.155] [CLIENT   ] read primary   -> stock=9 (v3)
[20:37:16.156] [CLIENT   ] read secondary -> stock=9 (v3)
[20:37:16.156] [CLIENT   ] PASS: secondary eventually updates to 9
[20:37:16.156] [CLIENT   ] PASS: both replicas return the same value again

========== 7/7 CHECKS PASSED ==========
```

## Reflection

What tradeoff exists between reading quickly from a replica and guaranteeing that the reader sees the latest value?

Reading from the nearest or least-busy replica is fast and spreads the load. But with asynchronous replication, that replica can be behind the Primary. Test 2 showed this: the Secondary returned `stock=10` for several seconds after the real value had become 9. To guarantee the latest value, the client has to read from the Primary, or the system has to wait for replicas to confirm each write before acknowledging it (synchronous replication or quorum reads). Both add latency and reduce availability when a replica is slow or unreachable. The choice is between low latency and high availability with eventual consistency, or strong consistency at the cost of slower and less available reads. For data like inventory, where a stale read could oversell stock, it is worth paying that cost for the reads that matter.

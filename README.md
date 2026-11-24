# Module 8 Assignment – Replication & Consistency

For this assignment I built a small inventory system in Python with one primary replica, one secondary replica, and a client that can read from either one. The idea was to see what happens when the secondary falls behind the primary, so I could actually observe a stale read happening and then watch the replicas catch back up.

I only used Python's standard library, so you don't need to pip install anything.

## What's in the repo

- `node.py` – the replica. You run it as either `primary` or `secondary`, and each one runs as its own little HTTP server.
- `client.py` – the client. It can read from either replica, write to the primary, and pause/resume replication.
- `demo.py` – runs everything at once: it starts both replicas, goes through all three tests, and prints PASS/FAIL for each check.
- `evidence/demo_output.txt` – the full output from when I ran the demo.


### Easiest way

```bash
python demo.py
```

This starts the primary on port 8001 and the secondary on port 8002, runs all the tests, and then shuts both of them down. It takes about 6 seconds. If everything worked, the last line says `7/7 CHECKS PASSED`.

### Running it manually


```bash
# Terminal 1 - secondary
python node.py secondary --port 8002

# Terminal 2 - primary
python node.py primary --port 8001 --secondary http://127.0.0.1:8002

# Terminal 3 - client
python client.py read primary
python client.py read secondary
python client.py write 9
python client.py pause
python client.py resume
```

Writes always go to the primary. `pause` doesn't throw away updates, it just holds them in the primary's queue until you run `resume`.

## Architecture

```
                 write (POST /stock)
   +--------+ ----------------------->  +------------------+
   |        |                           |  PRIMARY (8001)  |
   |        | <-----------------------  |  stock + version |
   |        |     read (GET /stock)     |  replication     |
   | CLIENT |                           |  queue/thread    |
   |        |                           +--------+---------+
   |        |                                    |
   |        |                                    | async update
   |        |                                    | (can be paused)
   |        |                                    v
   |        |     read (GET /stock)     +------------------+
   |        | <-----------------------  | SECONDARY (8002) |
   +--------+                           |  read-only copy  |
                                        +------------------+
```

Here's how it works:

- The **primary** is the only one that accepts writes. Every write bumps up a version number and gets put in a queue to be sent to the secondary.
- A background thread in the primary sends those updates to the secondary with a small delay (0.5 seconds by default) to act like network lag. If the secondary isn't reachable, it keeps retrying.
- The **secondary** is read-only. It only applies an update if the version is newer than what it already has, so an old or duplicate update can't move it backwards.
- The **client** can read from either replica, which is what makes the stale read possible.

Since replication is asynchronous, the primary doesn't wait for the secondary before saying the write worked. That's exactly why the secondary can end up behind.

## Results

All of this is from running `python demo.py`. The full log is in [`evidence/demo_output.txt`](evidence/demo_output.txt). Each line shows which process printed it, and `vN` is the version number.

### Test 1: Replication

I wrote `stock=12` to the primary. The primary had it right away, and the secondary got it about half a second later. After that both replicas returned 12.

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

After this, the demo sets stock back to 10 so the next test starts from the value in the assignment.

### Test 2: Stale read

For this one I paused replication and then changed the stock on the primary from 10 to 9. I read both replicas three times over about 3 seconds. The primary kept returning 9, but the secondary still said 10 the whole time. So anyone reading from the secondary during that window would get the wrong (stale) value.

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

### Test 3: Recovery and convergence

Then I resumed replication. The update that had been sitting in the queue got sent, the secondary changed to 9 about half a second later, and both replicas matched again.

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

**What tradeoff exists between reading quickly from a replica and guaranteeing that the reader sees the latest value?**

Reading from a replica is faster and takes load off the primary, but with asynchronous replication there's no guarantee the replica is up to date. I saw this in my stale read test, where the secondary kept saying the stock was 10 for a few seconds after it had already changed to 9. If you need to always see the latest value, you have to either read from the primary or make the primary wait for the replicas to confirm every write. Both of those make things slower, and if a replica is slow or goes down, writes or reads can get stuck. So basically you're choosing between speed and availability on one side and always-correct data on the other. For something like inventory, I think it makes sense to take the slower but correct reads for important things like checkout, since a stale read could mean selling something that's already out of stock.

"""Staging benchmark: Element X-style first sliding sync while probing /health.
Token arrives on stdin. Prints JSON timings only."""
import json, sys, threading, time, urllib.request
token = sys.stdin.readline().strip()
B = "http://127.0.0.1:8008"
def post(path, body, timeout=600):
    req = urllib.request.Request(B + path, json.dumps(body).encode(), {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    t = time.monotonic(); r = json.load(urllib.request.urlopen(req, timeout=timeout)); return r, time.monotonic() - t
health, stop = [], False
def probe():
    while not stop:
        t = time.monotonic()
        try: urllib.request.urlopen(B + "/health", timeout=30).read(); health.append(time.monotonic() - t)
        except Exception: health.append(30.0)
        time.sleep(0.5)
th = threading.Thread(target=probe); th.start()
S = "/_matrix/client/unstable/org.matrix.simplified_msc3575/sync?timeout=0"
lst = lambda a, b: {"lists": {"all_rooms": {"ranges": [[a, b]], "timeline_limit": 1, "required_state": [["m.room.name", ""]]}}}
r1, t1 = post(S, lst(0, 19))
r2, t2 = post(S + "&pos=" + r1["pos"], lst(0, 199))
r3, t3 = post(S, lst(0, 19))
stop = True; th.join()
health.sort()
print(json.dumps({"first_sync_s": round(t1, 2), "widened_s": round(t2, 2), "warm_repeat_s": round(t3, 2),
  "rooms_total": len(set(r1["rooms"]) | set(r2["rooms"])), "list_count": r2["lists"]["all_rooms"]["count"],
  "health_probes": len(health), "health_max_s": round(health[-1], 2), "health_p50_s": round(health[len(health)//2], 3),
  "health_over_1s": sum(1 for h in health if h > 1)}))

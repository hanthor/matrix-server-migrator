import json, sys, time, urllib.request
token = sys.stdin.readline().strip(); B = "http://127.0.0.1:8008"
def get(p, t=300):
    r = urllib.request.Request(B + p, headers={"Authorization": "Bearer " + token}); s = time.monotonic()
    return json.load(urllib.request.urlopen(r, timeout=t)), time.monotonic() - s
f = urllib.parse.quote(json.dumps({"room": {"timeline": {"limit": 5}}}))
import urllib.parse
d, t1 = get("/_matrix/client/v3/sync?timeout=0&filter=" + urllib.parse.quote(json.dumps({"room": {"timeline": {"limit": 5}}})))
join = d.get("rooms", {}).get("join", {})
rec = sum(1 for r in join.values() for e in r.get("ephemeral", {}).get("events", []) if e.get("type") == "m.receipt")
d2, t2 = get("/_matrix/client/v3/sync?timeout=0&since=" + d["next_batch"])
print(json.dumps({"initial_s": round(t1, 2), "joined_rooms": len(join), "rooms_with_receipts": rec, "incremental_s": round(t2, 2), "incremental_rooms": len(d2.get("rooms", {}).get("join", {}))}))

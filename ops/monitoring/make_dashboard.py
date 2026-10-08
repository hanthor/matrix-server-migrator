#!/usr/bin/env python3
"""Generate the Spindle Grafana dashboard (spindle-dashboard.json)."""
import json

DS = {"type": "prometheus", "uid": "victoriametrics"}
panels, y = [], 0

def row(title):
    global y
    panels.append({"type": "row", "title": title, "collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []})
    y += 1

def ts(title, exprs, unit="short", w=12, h=8, x=0, desc=""):
    panels.append({
        "type": "timeseries", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max", "lastNotNull"]}, "tooltip": {"mode": "multi"}},
        "targets": [{"datasource": DS, "expr": e, "legendFormat": l, "refId": chr(65 + i)} for i, (e, l) in enumerate(exprs)],
    })

def stat(title, expr, unit="short", x=0, w=4):
    panels.append({"type": "stat", "title": title, "datasource": DS, "gridPos": {"h": 4, "w": w, "x": x, "y": y},
                   "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
                   "targets": [{"datasource": DS, "expr": expr, "refId": "A"}]})

def nl(h=8):
    global y
    y += h

R = "[5m]"
q = lambda p, route: f'histogram_quantile({p}, sum by (le) (rate(spindle_http_request_duration_seconds_bucket{{route=~"{route}"}}{R})))'

row("Overview")
stat("Version", 'max by (version) (spindle_build_info)', x=0)
stat("Requests/s", 'sum(rate(spindle_http_requests_total[5m]))', "reqps", x=4)
stat("5xx/s", 'sum(rate(spindle_http_requests_total{status=~"5.."}[5m]))', "reqps", x=8)
stat("Long-poll syncs", 'sum(spindle_sync_subscribers)', x=12)
stat("Container restarts (24h)", 'sum(increase(container_start_time_seconds{namespace="ess",container="spindle"}[24h]) > bool 0)', x=16)
stat("Memory", 'sum(container_memory_working_set_bytes{namespace="ess",container="spindle"})', "bytes", x=20)
nl(4)

row("HTTP")
ts("Requests/s by route (top 12)", [('topk(12, sum by (route) (rate(spindle_http_requests_total[5m])))', "{{route}}")], "reqps")
ts("Errors/s by status and route", [('sum by (status, route) (rate(spindle_http_requests_total{status=~"[45].."}[5m]))', "{{status}} {{route}}")], "reqps", x=12)
nl()
ts("p95 latency by route (top 12)", [('topk(12, histogram_quantile(0.95, sum by (le, route) (rate(spindle_http_request_duration_seconds_bucket[5m]))))', "{{route}}")], "s")
ts("Health/ready latency (probe starvation)", [(q(0.99, "/health"), "/health p99"), (q(0.99, "/ready"), "/ready p99"),
   ('sum(rate(prober_probe_total{namespace="ess",pod=~"ess-spindle.*",result!="successful"}[5m]))', "failed probes/s")], "s", x=12,
   desc="If /health p99 approaches the probe timeout, the liveness probe will kill the pod (tuna-os/spindle#614).")
nl()

row("Sync (clients)")
ts("Sync latency p50/p95/p99", [(q(p, ".*sync.*"), f"p{int(p*100)}") for p in (0.5, 0.95, 0.99)], "s")
ts("Sync lag (age of newest event delivered)", [('max(spindle_sync_lag_seconds)', "max lag"), ('sum(spindle_sync_subscribers)', "subscribers")], "s", x=12)
nl()

row("Federation")
ts("Inbound /send: rate and latency", [('sum(rate(spindle_http_requests_total{route="/_matrix/federation/v1/send/{txn_id}"}[5m]))', "txn/s"),
   (q(0.95, "/_matrix/federation/v1/send/.*"), "p95 s")], "short")
ts("Events appended/s by origin", [('sum by (origin) (rate(spindle_events_appended_total[5m]))', "{{origin}}")], "short", x=12)
nl()
ts("PDUs sidelined/s by verdict (soft-fail/reject)", [('sum by (verdict) (rate(spindle_pdus_sidelined_total[5m]))', "{{verdict}}")])
ts("Outbound federation queue depth (top destinations)", [('topk(10, spindle_federation_queue_depth)', "{{destination}}")], x=12,
   desc="Events waiting to be delivered per destination. A growing line means that server is not accepting our traffic.")
nl()
ts("Inbound PDU outcomes / recovery (after gap-acceptance release)", [
   ('sum by (result) (rate({__name__=~"spindle_.*(pdu|receipt).*_total", result!=""}[5m]))', "{{__name__}} {{result}}"),
   ('sum by (result) (rate({__name__=~"spindle_.*recover.*_total"}[5m]))', "recovery {{result}}")], w=24,
   desc="Populated once the gap-acceptance build ships; empty before.")
nl()

row("Rooms and storage")
ts("Fork resolutions/s by case (case 3 = expensive)", [('sum by (case) (rate(spindle_fork_resolutions_total[5m]))', "case {{case}}")])
ts("Append latency p95 by durability", [('histogram_quantile(0.95, sum by (le, durability) (rate(spindle_append_duration_seconds_bucket[5m])))', "{{durability}}")], "s", x=12)
nl()
ts("Room / registry lock acquisitions/s", [('sum by (mode) (rate(spindle_room_lock_acquisitions_total[5m]))', "room {{mode}}"),
   ('sum by (mode) (rate(spindle_room_registry_acquisitions_total[5m]))', "registry {{mode}}")])
ts("Cold room loads (after cold-start release)", [('histogram_quantile(0.95, sum by (le, size) (rate({__name__=~"spindle_.*cold.*load.*_bucket"}[15m])))', "p95 {{size}}")], "s", x=12,
   desc="Populated once the cold-start build ships; empty before.")
nl()

row("Resources")
ts("CPU (cores) — Spindle vs limit", [('sum(rate(container_cpu_usage_seconds_total{namespace="ess",container="spindle"}[5m]))', "used"),
   ('sum(rate(container_cpu_cfs_throttled_periods_total{namespace="ess",container="spindle"}[5m])) / sum(rate(container_cpu_cfs_periods_total{namespace="ess",container="spindle"}[5m]))', "throttled fraction")])
ts("Memory working set", [('sum(container_memory_working_set_bytes{namespace="ess",container="spindle"})', "spindle"),
   ('sum by (container) (container_memory_working_set_bytes{namespace="ess",container=~"haproxy|matrix-authentication-service"})', "{{container}}")], "bytes", x=12)
nl()
ts("Disk I/O (Spindle)", [('sum(rate(container_fs_reads_bytes_total{namespace="ess",container="spindle"}[5m]))', "read"),
   ('sum(rate(container_fs_writes_bytes_total{namespace="ess",container="spindle"}[5m]))', "write")], "Bps")
ts("Postgres (MAS) CPU/memory", [('sum(rate(container_cpu_usage_seconds_total{namespace="postgres"}[5m]))', "cpu cores"),
   ('sum(container_memory_working_set_bytes{namespace="postgres"}) / 1e9', "memory GB")], x=12)
nl()

dash = {"uid": "spindle-overview", "title": "Spindle — reilly.asia", "tags": ["spindle", "matrix"], "timezone": "utc",
        "schemaVersion": 39, "refresh": "30s", "time": {"from": "now-6h", "to": "now"}, "panels": panels}
for i, p in enumerate(panels):
    p["id"] = i + 1
json.dump(dash, open("spindle-dashboard.json", "w"), indent=1)
print(len(panels), "panels")

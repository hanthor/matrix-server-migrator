#!/usr/bin/env python3
"""One-shot health report for production Spindle on reilly.asia.

Reads VictoriaMetrics (via the Grafana pod), the Spindle pod's status and its
recent WARN/ERROR log lines. Prints a compact report and exits non-zero when
something needs attention. Read-only: it changes nothing.

Usage: prod-check.py [--window 1h]
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.parse
from collections import Counter

KUBE = ["kubectl", "--kubeconfig", "/home/ubuntu/.kube/config-aws-migration", "--context", "admin@aws-migration"]


def kubectl(*args, timeout=60):
    return subprocess.run(KUBE + list(args), capture_output=True, text=True, timeout=timeout).stdout


def vm(query):
    url = "http://victoriametrics.monitoring.svc:8428/api/v1/query?query=" + urllib.parse.quote(query)
    out = kubectl("-n", "monitoring", "exec", "deploy/grafana", "--", "wget", "-qO-", url)
    try:
        return json.loads(out)["data"]["result"]
    except (ValueError, KeyError):
        return []


def scalar(query):
    result = vm(query)
    return float(result[0]["value"][1]) if result else 0.0


def by(query, label):
    return {r["metric"].get(label, ""): float(r["value"][1]) for r in vm(query) if float(r["value"][1])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", default="1h")
    w = parser.parse_args().window
    alerts = []

    pod = json.loads(kubectl("-n", "ess", "get", "pod", "-l", "app.kubernetes.io/instance=ess-spindle", "-o", "json"))["items"]
    if not pod:
        print("ALERT: no ess-spindle pod")
        return 2
    status = pod[0]["status"]
    container = next(c for c in status["containerStatuses"] if c["name"] == "spindle")
    binary = kubectl("-n", "ess", "exec", pod[0]["metadata"]["name"], "-c", "spindle", "--", "readlink", "/proc/1/exe").strip()
    print(f"pod {pod[0]['metadata']['name']} ready={container['ready']} restarts={container['restartCount']} started={container['state'].get('running', {}).get('startedAt')} binary={binary}")
    if not container["ready"]:
        alerts.append("spindle not ready")

    restarts = scalar(f'sum(changes(container_start_time_seconds{{namespace="ess",container="spindle"}}[{w}]))')
    print(f"container starts in {w}: {restarts:.0f} (deploys count too)")

    rps = scalar("sum(rate(spindle_http_requests_total[5m]))")
    e5 = by(f'sum by (route) (increase(spindle_http_requests_total{{status=~"5.."}}[{w}]))', "route")
    print(f"requests/s {rps:.2f}; 5xx in {w}: {sum(e5.values()):.0f} {dict(sorted(e5.items(), key=lambda kv: -kv[1])[:5])}")
    if sum(e5.values()) > 20:
        alerts.append(f"{sum(e5.values()):.0f} server errors")

    p95 = by('topk(6, histogram_quantile(0.95, sum by (le, route) (rate(spindle_http_request_duration_seconds_bucket[30m]))))', "route")
    print("p95 latency (30m):", {k: round(v, 2) for k, v in p95.items()})
    health = scalar('histogram_quantile(0.99, sum by (le) (rate(spindle_http_request_duration_seconds_bucket{route="/health"}[30m])))')
    if health > 0.5:
        alerts.append(f"/health p99 {health:.2f}s")

    pdus = by(f"sum by (result) (increase(spindle_federation_pdus_received_total[{w}]))", "result")
    sig = by(f"sum by (reason) (increase(spindle_federation_signature_failures_total[{w}]))", "reason")
    rec = by(f"sum by (result) (increase(spindle_federation_recovery_attempts_total[{w}]))", "result")
    gaps = scalar("max(spindle_federation_gaps_remaining)")
    out = by(f"sum by (result) (increase(spindle_federation_outbound_transactions_total[{w}]))", "result")
    queue = by("topk(5, spindle_federation_queue_depth)", "destination")
    print(f"inbound PDUs {w}: {pdus}")
    print(f"signature failures {w}: {sig}; recovery {w}: {rec}; gaps open: {gaps:.0f}")
    print(f"outbound txns {w}: {out}; deepest queues: {queue}")
    refused = sum(v for k, v in pdus.items() if k.startswith("refused") or k == "rejected")
    if pdus and refused > 0.2 * sum(pdus.values()):
        alerts.append(f"{refused:.0f} of {sum(pdus.values()):.0f} inbound PDUs refused/rejected")
    if sum(out.values()) and out.get("success", 0) < 0.8 * sum(out.values()):
        alerts.append(f"outbound federation success {out.get('success', 0):.0f}/{sum(out.values()):.0f}")
    if queue and max(queue.values()) > 200:
        alerts.append(f"federation queue {max(queue, key=queue.get)}={max(queue.values()):.0f}")

    mem = scalar('sum(container_memory_working_set_bytes{namespace="ess",container="spindle"})') / 2**30
    cpu = scalar('sum(rate(container_cpu_usage_seconds_total{namespace="ess",container="spindle"}[15m]))')
    lag = scalar("max(spindle_sync_lag_seconds)")
    print(f"memory {mem:.2f} GiB; cpu {cpu:.2f} cores; sync lag {lag:.1f}s; long-poll syncs {scalar('sum(spindle_sync_subscribers)'):.0f}")
    if mem > 9:
        alerts.append(f"memory {mem:.1f} GiB (limit 11)")

    log = kubectl("-n", "ess", "logs", pod[0]["metadata"]["name"], "-c", "spindle", f"--since={w}", timeout=120)
    log = re.sub(r"\x1b\[[0-9;]*m", "", log)
    counts = Counter()
    for line in log.splitlines():
        m = re.search(r" (WARN|ERROR) (.*)", line)
        if m:
            # Normalize IDs/numbers so similar lines group together.
            text = re.sub(r"[$!@#][A-Za-z0-9_\-.:=+/]+", "<id>", m.group(2))
            text = re.sub(r"\d+", "N", text)
            counts[(m.group(1), text[:150])] += 1
    print(f"log WARN/ERROR lines in {w}: {sum(counts.values())}")
    for (level, text), n in counts.most_common(8):
        print(f"  {n:5d} {level} {text}")
    if any(level == "ERROR" for level, _ in counts):
        alerts.append("ERROR lines in log")

    for alert in alerts:
        print("ALERT:", alert)
    print("OK" if not alerts else f"{len(alerts)} alert(s)")
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())

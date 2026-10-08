# Spindle monitoring (reilly.asia)

Namespace `monitoring` on the AWS Talos cluster:

| Component | Purpose |
|---|---|
| VictoriaMetrics (`10-victoriametrics.yaml`) | Prometheus-compatible store, 90-day retention. Scrapes Spindle `:9100/metrics`, cAdvisor (CPU/memory/IO for `ess`, `postgres`, `monitoring`, `spindle-rehearsal`) and kubelet probe results. |
| Tempo (`20-tempo.yaml`) | OpenTelemetry trace backend, OTLP on `:4318` (HTTP) and `:4317` (gRPC), 7-day retention. Its span-metrics and service graphs are written back into VictoriaMetrics. |
| Grafana (`30-grafana.yaml`) | Tailnet-only at https://spindle-grafana.manatee-basking.ts.net. The "Spindle — reilly.asia" dashboard is provisioned from `spindle-dashboard.json`. |

Get the admin password:

    kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.password}' | base64 -d

To change the dashboard, edit `make_dashboard.py`, then run:

    python3 make_dashboard.py
    kubectl -n monitoring create configmap grafana-dashboards --from-file=spindle-dashboard.json --dry-run=client -o yaml | kubectl apply -f -

## Turning on Spindle traces

Add `traces = "otlp"` under `[logging]` in the Spindle runtime config, and set these env vars on the `ess-spindle` container:

    OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo.monitoring.svc:4318
    OTEL_SERVICE_NAME=spindle
    OTEL_TRACES_SAMPLER=parentbased_traceidratio
    OTEL_TRACES_SAMPLER_ARG=0.2

Both need a Spindle restart, so ship them together with a binary deploy.

#!/bin/sh
# usage: run.sh <binary>  — (re)start staging Spindle; PID kept in /target/staging.pid.
# PID 1 here is `sleep`, which never reaps children, so an exited server
# lingers as a zombie: treat state Z as gone.
alive() { [ -r "/proc/$1/status" ] && ! grep -q "^State:.*Z" "/proc/$1/status"; }
if [ -f /target/staging.pid ] && alive "$(cat /target/staging.pid)"; then
  kill "$(cat /target/staging.pid)"
  for i in $(seq 1 120); do alive "$(cat /target/staging.pid)" || break; sleep 1; done
fi
TOKIO_WORKER_THREADS=4 nohup "$1" /target/staging.toml > "/target/staging-$(basename "$1").log" 2>&1 &
echo $! > /target/staging.pid; echo "started $(cat /target/staging.pid)"
for i in $(seq 1 120); do python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8008/ready',timeout=2)" 2>/dev/null && { echo "ready ${i}s"; exit 0; }; sleep 1; done; echo NOT-READY; exit 1

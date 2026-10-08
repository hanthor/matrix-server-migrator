#!/usr/bin/env python3
"""Wait for verified restore, clone it, import preserved fixtures and run clients."""
import base64
import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "artifacts/reilly-2026-10-06"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--binary", required=True)
parser.add_argument("--expected-sha256", required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--work", required=True)
parser.add_argument("--base-status", type=Path, default=ASSETS / "status.json")
parser.add_argument("--base-store", default="/work/reilly-restored-20261006")
parser.add_argument("--base-pidfile", default="/work/reilly-restore-server.pid")
parser.add_argument("--runtime-workers", type=int, choices=range(1, 65))
parser.add_argument("--warm-concurrent-fixture", action="store_true")
parser.add_argument("--cold-room")
parser.add_argument("--cold-max-seconds", type=float, default=120)
parser.add_argument("--reuse-imported-fixture", action="store_true",
                    help="Diagnostic only: use an already copied/imported owned fixture without another import")
args = parser.parse_args()
if not re.fullmatch(r"[0-9a-f]{64}", args.expected_sha256):
    parser.error("expected SHA must be 64 lowercase hex characters")
for path in [args.binary, args.work, args.base_store, args.base_pidfile]:
    if not re.fullmatch(r"/work/[A-Za-z0-9_./-]+", path) or ".." in Path(path).parts:
        parser.error("remote paths must be safe absolute paths below /work")
OUT = args.output.resolve()
OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
STATE = OUT / "client-witness-status.json"
WORK = args.work
BASE = args.base_store
BINARY = args.binary
K = ["kubectl", "--kubeconfig", "/home/ubuntu/.kube/config-aws-migration",
     "--context", "admin@aws-migration", "-n", "spindle-rehearsal"]
state = {"started_at": time.time(), "complete": False, "passed": False,
         "candidate_sha256": args.expected_sha256, "binary": BINARY,
         "work": WORK, "base_status": str(args.base_status), "base_store": BASE,
         "base_pidfile": args.base_pidfile, "cold_room_probe_requested": bool(args.cold_room), "runtime_workers": args.runtime_workers}


def save(phase, **details):
    state.update(phase=phase, updated_at=time.time(), **details)
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2))
    temp.replace(STATE)


def remote(args, data=None, timeout=21600):
    command = K + ["exec"] + (["-i"] if data is not None else []) + ["migrator-import", "--"] + args
    result = subprocess.run(command, input=data, capture_output=True, timeout=timeout,
                            **({"stdin": subprocess.DEVNULL} if data is None else {}))
    if result.returncode:
        raise RuntimeError("remote command failed (details suppressed)")
    return result.stdout


def shell(code, data=None):
    return remote(["sh", "-eu", "-c", code], data)


def read_json(path):
    # Retry only an immutable checkpoint read, never an import or SDK login.
    for attempt in range(4):
        try:
            return json.loads(remote(["cat", path], timeout=180))
        except (RuntimeError, json.JSONDecodeError, UnicodeDecodeError, subprocess.TimeoutExpired):
            if attempt == 3:
                raise RuntimeError("remote JSON read failed; details suppressed") from None
            time.sleep(2)


def upload_verified(path, payload, mode="600"):
    digest = hashlib.sha256(payload).hexdigest()
    temporary = path + ".transfer.tmp"
    # A failed/truncated transfer never replaces the destination; no mutation retry.
    shell(f'umask 077; cat > {temporary}; '
          f'test "$(sha256sum {temporary} | cut -d " " -f 1)" = {digest}; '
          f'chmod {mode} {temporary}; mv {temporary} {path}', payload)


def stopped(pidfile):
    return not shell(f'test -f {pidfile} || exit 0; pid=$(cat {pidfile}); '
                     'test -e /proc/$pid/stat || exit 0; '
                     'read -r number comm state rest < /proc/$pid/stat || exit 0; '
                     'case "$state" in Z|X) exit 0;; esac; echo active').strip()


def run():
    save("waiting_for_verified_restore")
    while True:
        base = json.loads(args.base_status.read_text())
        if base.get("candidate_sha256") != args.expected_sha256:
            raise RuntimeError("base candidate identity differs; witness stopped")
        if base.get("restored_store") is not None and base["restored_store"] != BASE:
            raise RuntimeError("base restored-store path differs; witness stopped")
        if base.get("complete"):
            if not base.get("passed") or base.get("phase") != "rehearsal_store_verified":
                raise RuntimeError("base restore gate failed; witness stopped")
            break
        time.sleep(60)
    if not stopped(args.base_pidfile):
        raise RuntimeError("base116 scratch server is still live")
    if not stopped(WORK + "/server.pid"):
        raise RuntimeError("previous disposable witness server is still live")
    state["binary_sha256"] = remote(["sha256sum", BINARY]).decode().split()[0]
    if state["binary_sha256"] != args.expected_sha256:
        raise RuntimeError("final witness binary checksum mismatch")
    save("copying_disposable_store")
    if args.reuse_imported_fixture:
        shell(f"test -f {WORK}/copy-complete; test -f {WORK}/fixture-report.json")
    # Resumable copy: marker exists only after a completed cold copy. Import
    # starts afterwards; reruns never recopy over an imported witness store.
    shell(f'umask 077; mkdir -p {WORK}; '
          f'if test -f {WORK}/copy-complete; then '
          f'test "$(cat {WORK}/copy-complete)" = {args.expected_sha256}; else '
          f'mkdir -p {WORK}/store; cp -a --reflink=auto {BASE}/. {WORK}/store/; '
          f'printf %s {args.expected_sha256} > {WORK}/copy-complete; fi')
    config = f'''[server]
name = "reilly.asia"
bind = "127.0.0.1:18608"
public_base_url = "http://127.0.0.1:18608"
[storage]
path = "{WORK}/store"
[federation]
enabled = false
[push]
enabled = false
[previews]
enabled = false
'''
    upload_verified(WORK + "/spindle.toml", config.encode())
    material = ASSETS / "client-witness-fixture-material"
    manifest_bytes = (material / "original-rig-manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    rooms = [room["room_id"] for room in manifest["rooms"]]
    users = [manifest["users"][u] for u in "abc"]
    assert len(rooms) == 4 and users == [f"@spindle-mig-{u}:reilly.asia" for u in "abc"]
    upload_verified(WORK + "/manifest.json", manifest_bytes)
    result = subprocess.run(K + ["get", "secret", "spindle-mig-rig", "-o", "json"],
                            capture_output=True, check=True, timeout=60)
    secrets = json.loads(result.stdout)["data"]
    if not args.reuse_imported_fixture:
        shell(f"umask 077; mkdir -p {WORK}/fixture-passwords")
        for user in "abc":
            upload_verified(f"{WORK}/fixture-passwords/spindle-mig-{user}", base64.b64decode(secrets[f"password-{user}"]))
        save("importing_preserved_fixture")
        invocation = ["env", "SPINDLE_REHEARSAL_PASSWORD_DIR=" + WORK + "/fixture-passwords",
                      BINARY, "import-synapse", WORK + "/spindle.toml",
                      "host=rehearsal-pg port=5432 user=postgres dbname=synapse_dark",
                      "--checkpoint", WORK + "/fixture-report.json", "--allow-nonempty",
                      "--rooms", ",".join(rooms), "--users", ",".join(users), "--no-validate"]
        with (OUT / "client-witness-fixture-import.log").open("ab") as log:
            result = subprocess.run(K + ["exec", "migrator-import", "--"] + invocation,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=21600)
        if result.returncode:
            raise RuntimeError("disposable fixture import failed")
    report = read_json(WORK + "/fixture-report.json")
    if set(report["rooms"]) != set(rooms) or report.get("excluded_rooms"):
        raise RuntimeError("fixture room coverage failed")
    (OUT / "client-witness-fixture-import-report.json").write_text(json.dumps(report, indent=2))
    # Copy preserved client binary, never compile or replace migration binaries.
    sdk = ASSETS / "client-witness-mig-rig"
    if not sdk.exists():
        with sdk.open("xb") as stream:
            subprocess.run(K + ["exec", "mig-rig-toolbox", "--", "cat", "/work/bin/mig-rig"],
                           stdout=stream, stderr=subprocess.DEVNULL, check=True, timeout=180)
    upload_verified(WORK + "/mig-rig", sdk.read_bytes(), mode="700")
    ca_bundle = Path("/etc/ssl/certs/ca-certificates.crt").read_bytes()
    if b"-----BEGIN CERTIFICATE-----" not in ca_bundle or b"PRIVATE KEY" in ca_bundle:
        raise RuntimeError("public SDK trust bundle unavailable")
    upload_verified(WORK + "/sdk-public-ca.pem", ca_bundle)
    state["sdk_public_ca_sha256"] = hashlib.sha256(ca_bundle).hexdigest()
    for user in "abc":
        for kind in ["password", "recovery-key"]:
            name = f"{kind}-{user}"
            upload_verified(f"{WORK}/{name}", base64.b64decode(secrets[name]))
    launch = f"env TOKIO_WORKER_THREADS={args.runtime_workers} " if args.runtime_workers else ""
    runtime_log = f"{WORK}/server-workers-{args.runtime_workers}.log" if args.runtime_workers else f"{WORK}/server.log"
    shell(f"nohup {launch}{BINARY} {WORK}/spindle.toml </dev/null >{runtime_log} 2>&1 & echo $! > {WORK}/server.pid")
    state["runtime_log"] = runtime_log
    runtime_meta = shell(f'pid=$(cat {WORK}/server.pid); i=0; while test "$(readlink /proc/$pid/exe)" != {BINARY}; do i=$((i+1)); test "$i" -lt 10 || exit 1; sleep 1; done; '
                         'printf "pid=%s\\n" "$pid"; '
                         'for t in /proc/$pid/task/*; do cat "$t/comm"; done; '
                         'cat /sys/fs/cgroup/cpu.max')
    (OUT / "runtime-threads-cgroup.txt").write_bytes(runtime_meta)
    forward = web = None
    try:
        with (OUT / "client-witness-forward.log").open("ab") as log:
            forward = subprocess.Popen(K + ["port-forward", "pod/migrator-import", "18608:18608", "--address", "127.0.0.1"], stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while True:
            if forward.poll() is not None:
                raise RuntimeError("witness forwarding exited")
            try:
                with urllib.request.urlopen("http://127.0.0.1:18608/ready", timeout=3) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("witness readiness timeout")
            time.sleep(1)
        if args.cold_room:
            save("running_cold_real_room_http")
            password = OUT / "cold-fixture-password.private"
            password.write_bytes(base64.b64decode(secrets["password-a"]))
            password.chmod(0o600)
            try:
                result = subprocess.run(["python3", str(ROOT / "scripts/client-witness/cold_http.py"),
                                         "--room", args.cold_room, "--password-file", str(password),
                                         "--max-seconds", str(args.cold_max_seconds)] +
                                        (["--warm-fixture-first"] if args.warm_concurrent_fixture else []),
                                        capture_output=True, timeout=2 * (args.cold_max_seconds + 5) + 90)
            finally:
                password.unlink(missing_ok=True)
            (OUT / "cold-real-room-http.json").write_bytes(result.stdout)
            if result.returncode or not json.loads(result.stdout).get("passed"):
                raise RuntimeError("cold real-room HTTP latency gate failed")
            cold_report = json.loads(result.stdout)
            state["cold_real_room_http_diagnostic_pass"] = True
            state["production_readiness_objective_met"] = cold_report["production_readiness_objective_met"]
            state["production_latency_accepted"] = False
        save("running_fixture_sdk")
        reports = []
        for user in "abc":
            invocation = ["env", "SSL_CERT_FILE=" + WORK + "/sdk-public-ca.pem",
                          WORK + "/mig-rig", "verify", "--homeserver", "http://127.0.0.1:18608",
                          "--user", manifest["users"][user], "--manifest", WORK + "/manifest.json",
                          "--password-file", f"{WORK}/password-{user}",
                          "--recovery-key-file", f"{WORK}/recovery-key-{user}"]
            result = subprocess.run(K + ["exec", "migrator-import", "--"] + invocation,
                                    capture_output=True, timeout=1200)
            raw = result.stdout
            (OUT / f"client-witness-sdk-{user}.json").write_bytes(raw)
            (OUT / f"client-witness-sdk-{user}.log").write_bytes(result.stderr)
            if result.returncode:
                raise RuntimeError(f"fixture SDK witness {user} failed; private report retained")
            report = json.loads(raw)
            summary = report.get("summary", {})
            if not (report.get("pass") and report.get("recovery_ok") and report.get("own_device_cross_signed")
                    and summary.get("failures") == 0 and summary.get("expected_readable", 0) > 0
                    and summary.get("readable_ok") == summary.get("expected_readable")):
                raise RuntimeError("fixture SDK witness failed")
            reports.append({"user": user, "device": report.get("device"), "summary": summary})
        state["sdk_reports"] = reports
        save("running_strict_element_fixture")
        with (OUT / "client-witness-web.log").open("ab") as log:
            web = subprocess.Popen(["python3", "-m", "http.server", "--bind", "127.0.0.1", "18609"], cwd=ASSETS / "client-witness-element-web", stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while True:
            if forward.poll() is not None or web.poll() is not None:
                raise RuntimeError("witness forwarding or web listener exited")
            try:
                with urllib.request.urlopen("http://127.0.0.1:18609/config.json", timeout=3) as response:
                    web_config = json.load(response)
                    if response.status == 200 and web_config.get("default_server_config", {}).get("m.homeserver", {}).get("base_url") == "http://127.0.0.1:18608":
                        break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("witness readiness timeout")
            time.sleep(1)
        cfg = json.loads((ASSETS / "client-witness-element-config.json").read_text())
        run_output = OUT / f"client-witness-element-run-{int(time.time())}"
        cfg["output"] = str(run_output)
        cfg["cursor_material"] = str(ASSETS / "client-witness-cursor-material.private.json")
        runtime_cfg = OUT / "client-witness-element-runtime-config.json"
        runtime_cfg.write_text(json.dumps(cfg, indent=2))
        result = subprocess.run(["node", str(ROOT / "scripts/client-witness/element.cjs"), str(runtime_cfg)], capture_output=True, timeout=10800)
        (OUT / "client-witness-element-runner.log").write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError("strict Element fixture witness failed; no pass claimed")
        element = json.loads((run_output / "element.json").read_text())
        if not element.get("history_decryption_pass") or not element.get("predecessor_cursor_http_pass"):
            raise RuntimeError("strict Element fixture hashes failed")
        save("fixture_witness_verified", complete=True, passed=True,
             element_samples=len(element["samples"]), predecessor_cursor_http_pass=True,
             scope="Preserved synthetic synapse_dark fixture on second copy of validated production restore",
             full_cutover_gate_pass=False,
             production_116_room_history_decryption_pass=False)
    finally:
        primary_error = sys.exc_info()[0] is not None
        for process in [web, forward]:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
        try:
            shell(f'pid=$(cat {WORK}/server.pid); test -e /proc/$pid/stat || exit 0; '
                  'read -r number comm state rest < /proc/$pid/stat || exit 0; '
                  'case "$state" in Z|X) exit 0;; esac; '
                  f'test "$(readlink /proc/$pid/exe)" = {BINARY}; '
                  'start=$(cut -d " " -f 22 /proc/$pid/stat); kill -TERM "$pid"; '
                  'attempt=0; while test "$attempt" -lt 45; do '
                  'test -e /proc/$pid/stat || exit 0; '
                  'read -r number comm state rest < /proc/$pid/stat || exit 0; '
                  'case "$state" in Z|X) exit 0;; esac; '
                  'test "$(cut -d " " -f 22 /proc/$pid/stat)" = "$start"; '
                  'attempt=$((attempt+1)); sleep 1; done; exit 1')
        except Exception:
            state["runtime_cleanup_failed"] = True
            if not primary_error:
                raise


if __name__ == "__main__":
    os.umask(0o077)
    lock = (OUT / "client-witness.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        run()
    except Exception as error:
        save("failed", complete=True, passed=False, error_type=type(error).__name__, error=str(error))
        raise

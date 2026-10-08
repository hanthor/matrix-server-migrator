#!/usr/bin/env python3
"""Finish the handed-off rehearsal after its sole importer exits.

Poll every 15 minutes, preserve the original checkpoint, refresh dependent
phases, validate, export the real store, and validate a restored copy. No
production routing changes. Artifacts contain private account data.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
import shlex
import subprocess
import sys
import time
import tomllib
import copy
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TAG = os.environ.get("MIGRATOR_REHEARSAL_TAG", "20261006")
if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,63}", TAG):
    raise ValueError("invalid rehearsal tag")
OUT = ROOT / ("artifacts/reilly-2026-10-06" if TAG == "20261006"
              else f"artifacts/reilly-rehearsal-{TAG}")
K = ["kubectl", "--kubeconfig", "/home/ubuntu/.kube/config-aws-migration",
     "--context", "admin@aws-migration", "-n", "spindle-rehearsal"]
BINARY = os.environ.get("MIGRATOR_RETRY_BINARY", "/work/bin/spindle-retry")
FINAL_BINARY = os.environ.get("MIGRATOR_FINAL_BINARY", "/work/bin/spindle-production-candidate-8e40719")
FINAL_SHA = os.environ.get("MIGRATOR_FINAL_SHA256", "4036a6073191eea49a8bfc58af4b1dbef096774c89cbabf6569ef158b3bba092")
SIGNING_HELPER = os.environ.get("MIGRATOR_SIGNING_HELPER", "/work/bin/signing-public-8e40719")
SIGNING_HELPER_SHA = os.environ.get("MIGRATOR_SIGNING_HELPER_SHA256", "67837c54075c386b20397fdc68e24524a591a83e10d737334ebcc4321c906021")
CONFIG = "/work/spindle.toml"
REPORT = "/work/report.json"
CONN = "host=rehearsal-pg port=5432 user=postgres dbname=synapse"
ROOM = "!iMZEhwCvbfeAYUxAjZ:t2l.io"
RESTORE = f"/work/reilly-restored-{TAG}"
REMOTE_ARCHIVE = f"/work/reilly-{TAG}.tar.gz"
EXTRACTED = f"/work/reilly-restore-extract-{TAG}"
RESTORE_CONFIG = "/work/reilly-restore.toml" if TAG == "20261006" else f"/work/reilly-restore-{TAG}.toml"
RESTORE_REPORT = "/work/reilly-restored-report.json" if TAG == "20261006" else f"/work/reilly-restored-report-{TAG}.json"
SERVER_CONFIG = "/work/reilly-restore-server.toml" if TAG == "20261006" else f"/work/reilly-restore-server-{TAG}.toml"
SERVER_LOG = "/work/reilly-restore-server.log" if TAG == "20261006" else f"/work/reilly-restore-server-{TAG}.log"
SERVER_PID = "/work/reilly-restore-server.pid" if TAG == "20261006" else f"/work/reilly-restore-server-{TAG}.pid"
state = {"started_at": time.time(), "complete": False, "passed": False}


def save(phase, **details):
    state.update(phase=phase, updated_at=time.time(), **details)
    temporary = OUT / "status.tmp"
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(OUT / "status.json")


def remote(args, data=None, timeout=21600):
    return subprocess.run(K + ["exec"] + (["-i"] if data is not None else []) + ["migrator-import", "--"] + args,
                          input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True, timeout=timeout).stdout


def shell(code, data=None):
    return remote(["sh", "-eu", "-c", code], data)


def fetch_report(name):
    # Read-only retries tolerate interrupted websocket output, including an
    # exit-zero truncated JSON stream. Never publish an unparsed artifact.
    for attempt in range(3):
        try:
            contents = remote(["cat", REPORT], timeout=60)
            report = json.loads(contents)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            if attempt == 2:
                raise
            continue
        (OUT / name).write_bytes(contents)
        return report


def write_refreshed_report(report):
    payload = json.dumps(report, indent=2).encode()
    digest = hashlib.sha256(payload).hexdigest()
    # Keep the original checkpoint if stdin transport silently truncates.
    shell("cp /work/report.json /work/report.json.pre-domain-refresh; "
          "umask 077; cat > /work/report.refresh.tmp; "
          'test "$(sha256sum /work/report.refresh.tmp | cut -d " " -f 1)" = '
          + digest + "; mv /work/report.refresh.tmp /work/report.json", payload)


def importer_alive():
    # Exited children can remain as zombies because the pod's PID 1 does not
    # reap them. Only live processes hold the store or block follow-up work.
    return bool(shell('for p in /proc/[0-9]*/comm; do '
                      'read -r name < "$p" 2>/dev/null || continue; '
                      'case "$name" in spindle*) '
                      'read -r pid comm state rest < "${p%/comm}/stat" 2>/dev/null || continue; '
                      'case "$state" in Z|X) continue;; esac; '
                      'echo active; break;; esac; done',
                      ).strip())


def wait_for_importer():
    # Bound each watch connection. timeout owns only the pidfd helper, never
    # the importer; without timeout/helper support use a short passive wait.
    watch = ('for p in /proc/[0-9]*/comm; do '
             'read -r name < "$p" 2>/dev/null || continue; '
             'case "$name" in spindle*) '
             'read -r pid comm state rest < "${p%/comm}/stat" 2>/dev/null || continue; '
             'case "$state" in Z|X) continue;; esac; '
             'if test -x /work/bin/wait-import && command -v timeout >/dev/null 2>&1; then '
             'timeout 45 /work/bin/wait-import "$pid" || sleep 5; '
             'else sleep 45; fi; break;; esac; done')
    try:
        remote(["sh", "-eu", "-c", watch], timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # A disconnected exec does not establish process exit. Reconnect and
        # let the unchanged outer loop/coverage gate decide what can proceed.
        # A failed fresh probe propagates: never treat lost connectivity as exit.
        probe = ('for p in /proc/[0-9]*/comm; do '
                 'read -r name < "$p" 2>/dev/null || continue; '
                 'case "$name" in spindle*) '
                 'read -r pid comm state rest < "${p%/comm}/stat" 2>/dev/null || continue; '
                 'case "$state" in Z|X) continue;; esac; '
                 'echo active; break;; esac; done')
        return bool(remote(["sh", "-eu", "-c", probe], timeout=30).strip())


def command(args, log):
    with (OUT / log).open("ab") as stream:
        result = subprocess.run(K + ["exec", "migrator-import", "--"] + args,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=21600)
    if result.returncode:
        raise RuntimeError(f"{log}: command exited {result.returncode}")


def gate(path):
    command_line = [str(ROOT / "target/debug/migrator-cli"), "report", str(path), "116"]
    result = subprocess.run(command_line, capture_output=True, text=True, timeout=30)
    (OUT / (path.stem + "-gates.json")).write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())


def transfer(remote_path, local_path):
    size = int(remote(["stat", "-c", "%s", remote_path]).decode().strip())
    mode = os.environ.get("MIGRATOR_ARCHIVE_TRANSFER", "s3")
    if mode not in {"s3", "local"}:
        raise RuntimeError("archive transfer mode must be s3 or local")
    if mode == "s3" or shutil.disk_usage(local_path.parent).free < size + 1024 ** 3:
        # Prefer verified durable storage and retain host build headroom.
        # An explicitly requested local transfer still falls back if it cannot fit.
        key = f"postgres/spindle-cutover/rehearsal-{TAG}-{time.time_ns()}/actual-store.tar.gz"
        uploader = ["kubectl", "--kubeconfig", "/home/ubuntu/.kube/config-aws-migration",
                    "--context", "admin@aws-migration", "-n", "postgres", "exec", "-i",
                    "spindle-cutover-stream-upload-v2", "--", "python3",
                    "/script/s3-stream.py", "--key", key]
        with (OUT / "archive-upload-producer.log").open("ab") as source_log, \
                (OUT / "archive-upload.log").open("ab") as upload_log:
            producer = subprocess.Popen(K + ["exec", "migrator-import", "--", "cat", remote_path],
                                        stdout=subprocess.PIPE, stderr=source_log)
            try:
                result = subprocess.run(uploader, stdin=producer.stdout, stdout=subprocess.PIPE,
                                        stderr=upload_log, timeout=21600)
                producer.stdout.close()
                source_exit = producer.wait(timeout=30)
            finally:
                producer.stdout.close()
                if producer.poll() is None:
                    producer.terminate()
                    try:
                        producer.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        producer.kill()
                        producer.wait(timeout=15)
        if source_exit or result.returncode:
            raise RuntimeError("off-cluster archive upload failed; private archive-upload logs retained")
        report = json.loads(result.stdout)
        expected = remote(["sha256sum", remote_path]).decode().split()[0]
        assert report["bytes"] == size and report["sha256"] == expected
        assert report["full_object_reread_verified"]
        (OUT / "archive-s3-verification.json").write_text(json.dumps(report, indent=2) + "\n")
        return {"s3_key": key, **report, "local_copy_skipped": "insufficient host headroom"}
    temporary = local_path.with_suffix(local_path.suffix + ".partial")
    with temporary.open("wb") as stream:
        subprocess.run(K + ["exec", "migrator-import", "--", "cat", remote_path],
                       stdout=stream, check=True, timeout=21600)
        stream.flush()
        os.fsync(stream.fileno())
    with temporary.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    expected = remote(["sha256sum", remote_path]).decode().split()[0]
    if digest != expected:
        raise RuntimeError("off-cluster backup checksum mismatch")
    temporary.replace(local_path)
    return {"path": str(local_path), "sha256": digest, "bytes": local_path.stat().st_size}



def inert_scratch_config(config):
    """Explicitly override delivery defaults without changing other settings."""
    before = tomllib.loads(config)
    expected = copy.deepcopy(before)
    for section in ("federation", "push", "previews"):
        expected.setdefault(section, {})["enabled"] = False
        pattern = rf"(?ms)^\[{section}\][ \t]*(?:#[^\n]*)?\n(.*?)(?=^\[|\Z)"
        found = re.search(pattern, config)
        if found is None:
            config += f"\n[{section}]\nenabled = false\n"
            continue
        block = found.group(0)
        enabled = r"(?m)^enabled[ \t]*=[ \t]*(?:true|false)[ \t]*(?:#[^\n]*)?$"
        if re.search(enabled, block):
            block = re.sub(enabled, "enabled = false", block)
        else:
            header, body = block.split("\n", 1)
            block = header + "\nenabled = false\n" + body
        config = config[:found.start()] + block + config[found.end():]
    assert tomllib.loads(config) == expected, "scratch override changed unrelated configuration"
    assert not expected.get("appservices", {}).get("registrations"), "scratch must not deliver appservice events"
    return config


def witness_server(key):
    """Check readiness and the original public signing key on the scratch copy."""
    algorithm, version, encoded = key.decode().strip().split()
    assert algorithm == "ed25519"
    seed = base64.b64decode(encoded + "=" * (-len(encoded) % 4))
    assert len(seed) == 32
    # RFC 8410 PKCS#8 encoding for an Ed25519 seed. Only the derived public
    # key is compared; private material stays in stdin and protected files.
    der = bytes.fromhex("302e020100300506032b657004220420") + seed
    public = subprocess.check_output(["openssl", "pkey", "-inform", "DER", "-pubout",
                                      "-outform", "DER"], input=der)
    expected = base64.b64encode(public[-32:]).decode().rstrip("=")
    config = inert_scratch_config(remote(["cat", RESTORE_CONFIG]).decode())
    state["scratch_delivery_disabled"] = ["federation", "push", "previews", "appservices"]
    config = config.replace('name = "reilly.asia"', 'name = "reilly.asia"\nbind = "127.0.0.1:18008"')
    shell(f"umask 077; cat > {shlex.quote(SERVER_CONFIG)}", config.encode())
    shell(f"nohup {shlex.quote(FINAL_BINARY)} {shlex.quote(SERVER_CONFIG)} "
          f"</dev/null >{shlex.quote(SERVER_LOG)} 2>&1 & "
          f"echo $! > {shlex.quote(SERVER_PID)}")
    forwarding = None
    try:
        with (OUT / "restore-forward.log").open("ab") as log:
            forwarding = subprocess.Popen(K + ["port-forward", "pod/migrator-import",
                                               "18472:18008", "--address", "127.0.0.1"],
                                          stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 120
            while True:
                assert forwarding.poll() is None, "scratch port-forward exited"
                try:
                    with urllib.request.urlopen("http://127.0.0.1:18472/ready", timeout=3) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                assert time.monotonic() < deadline, "restored server readiness timeout"
                time.sleep(1)
            state.update(restored_server_ready=True)
    except Exception:
        try:
            (OUT / "restore-server.log").write_bytes(remote(["cat", SERVER_LOG]))
        except Exception:
            pass
        raise
    finally:
        primary_error = sys.exc_info()[0] is not None
        if forwarding is not None:
            forwarding.terminate()
            try:
                forwarding.wait(timeout=15)
            except subprocess.TimeoutExpired:
                forwarding.kill()
                forwarding.wait(timeout=15)
        try:
            shell(f'pid=$(cat {shlex.quote(SERVER_PID)}); '
                  'test -e /proc/$pid/stat || exit 0; '
                  'read -r number comm state rest < /proc/$pid/stat || exit 0; '
                  'case "$state" in Z|X) exit 0;; esac; '
                  f'test "$(readlink /proc/$pid/exe)" = {shlex.quote(FINAL_BINARY)}; '
                  'kill -TERM "$pid" || true; '
                  'for attempt in $(seq 1 45); do '
                  'test -e /proc/$pid/stat || exit 0; '
                  'read -r number comm state rest < /proc/$pid/stat || exit 0; '
                  'case "$state" in Z|X) exit 0;; esac; sleep 1; done; exit 1')
        except Exception as error:
            state["scratch_cleanup_error"] = type(error).__name__
            if not primary_error:
                raise
    # Disabled federation deliberately hides /_matrix/key/*, and enabling it
    # would start delivery of the restored store's queued federation events.
    # With the scratch server stopped, use the final candidate's key loader on
    # this independent cold copy. The helper refuses absent or multiple keys
    # before load_or_create and outputs only public key data.
    document = json.loads(remote([SIGNING_HELPER, "--independent-cold-copy", RESTORE]))
    assert document["existing_key_rows"] == 1
    assert document["key_id"] == f"ed25519:{version}"
    assert document["public_key_base64"] == expected, "original signing key was not preserved"
    (OUT / "restored-signing-public.json").write_text(json.dumps(document, indent=2) + "\n")
    state.update(restored_signing_key_matches_source=True,
                 signing_check_method="offline final-candidate loader; federation disabled")



def check_final_witness_recovery(previous):
    assert previous["phase"] == "failed" and previous["complete"] and not previous["passed"]
    assert previous.get("scratch_cleanup_error") == "CalledProcessError"
    assert previous["candidate_sha256"] == previous["binary_sha256"] == FINAL_SHA
    assert previous["restored_validation_binary_sha256"] == FINAL_SHA
    assert previous["restored_store"] == RESTORE and previous["rehearsal_tag"] == TAG
    assert previous["restored_server_ready"]
    archive = previous["off_cluster_backup"]
    assert archive["full_object_reread_verified"] and archive["overwrite_prevented"]
    assert archive["bytes"] > 0 and re.fullmatch(r"[0-9a-f]{64}", archive["sha256"])


def resume_final_witness(recreate_cold=False):
    # Explicit recovery of the final disposable-server witness only. Never
    # repeat import/archive/extraction or infer a pass from the failed status.
    previous = json.loads((OUT / "status.json").read_text())
    check_final_witness_recovery(previous)
    assert remote(["sha256sum", FINAL_BINARY]).decode().split()[0] == FINAL_SHA
    assert remote(["sha256sum", SIGNING_HELPER]).decode().split()[0] == SIGNING_HELPER_SHA
    assert not importer_alive(), "scratch server and all importers must have exited"
    archive = previous["off_cluster_backup"]
    assert int(remote(["stat", "-c", "%s", REMOTE_ARCHIVE]).decode()) == archive["bytes"]
    assert remote(["sha256sum", REMOTE_ARCHIVE]).decode().split()[0] == archive["sha256"]
    # Recheck the strict saved native readbacks before allowing a new probe.
    gate(OUT / "report-validated.json")
    gate(OUT / "report-restored.json")
    key = (OUT / "synapse-signing.key").read_bytes()
    assert hashlib.sha256(key).hexdigest() == previous["source_signing_key_sha256"]
    shell(f"cp {shlex.quote(SERVER_LOG)} {shlex.quote(SERVER_LOG + '.before-recovery')}; "
          f"cp {shlex.quote(SERVER_PID)} {shlex.quote(SERVER_PID + '.before-recovery')}")
    state.clear()
    state.update(previous)
    state.update(complete=False, passed=False, recovery_started_at=time.time(),
                 recovered_from="scratch shutdown exceeded ten-second guard")
    for field in ("error", "error_type", "scratch_cleanup_error"):
        state.pop(field, None)
    save("recovering_final_witness")
    if recreate_cold:
        preserved = RESTORE + ".push-scan-preserved"
        old_extract = EXTRACTED + ".push-scan-preserved"
        shell(f"test ! -e {shlex.quote(preserved)}; test ! -e {shlex.quote(old_extract)}; "
              f"test -d {shlex.quote(RESTORE)}; test -d {shlex.quote(EXTRACTED)}; "
              f"mv {shlex.quote(RESTORE)} {shlex.quote(preserved)}; "
              f"mv {shlex.quote(EXTRACTED)} {shlex.quote(old_extract)}; mkdir {shlex.quote(EXTRACTED)}")
        command(["tar", "-xzf", REMOTE_ARCHIVE, "-C", EXTRACTED], "pristine-recovery-extract.log")
        shell(f"mv {shlex.quote(EXTRACTED + '/spindle-data')} {shlex.quote(RESTORE)}; "
              f"cp {shlex.quote(EXTRACTED + '/report.json')} {shlex.quote(RESTORE_REPORT)}")
        config = remote(["cat", CONFIG]).decode().replace("/work/spindle-data", RESTORE)
        config = inert_scratch_config(config)
        shell(f"umask 077; cat > {shlex.quote(RESTORE_CONFIG)}", config.encode())
        command([FINAL_BINARY, "import-synapse", RESTORE_CONFIG, CONN,
                 "--media", "/source/synapse-media", "--checkpoint", RESTORE_REPORT,
                 "--validate-only"], "pristine-recovery-validate.log")
        restored = json.loads(remote(["cat", RESTORE_REPORT]))
        (OUT / "report-restored.json").write_text(json.dumps(restored, indent=2) + "\n")
        gate(OUT / "report-restored.json")
        state.update(pristine_cold_restore_recreated=True,
                     preserved_push_scan_store=preserved)
    witness_server(key)
    save("rehearsal_store_verified", complete=True, passed=True,
         remaining_gates=["client E2EE witness", "historical PDU continuity",
                          "fresh frozen production backup, restore and import"])


def run(check_only=False):
    report = fetch_report("report-observed.json")
    assert report["server_name"] == "reilly.asia"
    remote(["test", "-x", BINARY])
    assert remote(["sha256sum", FINAL_BINARY]).decode().split()[0] == FINAL_SHA
    assert remote(["sha256sum", SIGNING_HELPER]).decode().split()[0] == SIGNING_HELPER_SHA
    assert (ROOT / "target/debug/migrator-cli").is_file()
    if check_only:
        print(json.dumps({"server_name": report["server_name"],
                          "rooms": len(report["rooms"]), "importer_alive": importer_alive(),
                          "candidate_sha256": FINAL_SHA,
                          "prerequisites_checked": True}))
        return
    state["candidate_sha256"] = FINAL_SHA
    state["rehearsal_tag"] = TAG
    state["restored_store"] = RESTORE
    save("waiting_for_retry")
    while importer_alive():
        wait_for_importer()
    report = fetch_report("report-after-retry.json")
    assert ROOM in report["rooms"], "retry did not import the excluded room"
    assert len(report["rooms"]) == 116 and not report["excluded_rooms"], "room coverage failed"
    # Pin the patched executable and preserve the source change for recovery.
    state["binary_sha256"] = remote(["sha256sum", BINARY]).decode().split()[0]
    save("refreshing_dependent_phases")
    secret = json.loads(subprocess.check_output(K + ["get", "secret", "dark-synapse-secrets", "-o", "json"]))
    key = base64.b64decode(secret["data"]["signing.key"])
    state["source_signing_key_sha256"] = hashlib.sha256(key).hexdigest()
    (OUT / "synapse-signing.key").write_bytes(key)
    shell("umask 077; cat > /work/synapse-signing.key", key)
    # Receipts and directory entries previously skipped the excluded room.
    # The original checkpoint is retained, and all existing rooms stay intact.
    report["phases_done"] = [p for p in report["phases_done"]
                             if p not in ("signing_key", "receipts", "directory")]
    report["domains"].pop("signing_key", None)
    report["validation"] = None
    write_refreshed_report(report)
    invocation = ["env", "SPINDLE_SYNAPSE_SIGNING_KEY_FILE=/work/synapse-signing.key",
                  BINARY, "import-synapse", CONFIG, CONN, "--media", "/source/synapse-media",
                  "--checkpoint", REPORT]
    command(invocation + ["--no-validate"], "domain-refresh.log")
    fetch_report("report-after-refresh.json")
    save("validating_full_store")
    command(invocation + ["--validate-only"], "validate.log")
    fetch_report("report-validated.json")
    gate(OUT / "report-validated.json")
    save("backing_up_real_store")
    assert not importer_alive(), "store must be stopped for the cold backup"
    # The native backup implementation materializes every row in memory.
    # A cold archive streams the real Fjall store and its media with bounded
    # memory. Every writer has exited and validation synced the store.
    artifact_members = [str(Path(path).relative_to("/work"))
                        for path in dict.fromkeys([BINARY, FINAL_BINARY, SIGNING_HELPER])]
    command(["tar", "-C", "/work", "-czf", REMOTE_ARCHIVE,
             "spindle-data", "spindle.toml", "report.json", "synapse-signing.key",
             *artifact_members], "archive.log")
    state["off_cluster_backup"] = transfer(REMOTE_ARCHIVE, OUT / f"reilly-{TAG}.tar.gz")
    save("restoring_scratch_store")
    config = remote(["cat", CONFIG]).decode().replace("/work/spindle-data", RESTORE)
    shell(f"umask 077; cat > {shlex.quote(RESTORE_CONFIG)}", config.encode())
    extracted = EXTRACTED
    shell(f"test ! -e {shlex.quote(RESTORE)}; test ! -e {extracted}; mkdir {extracted}")
    command(["tar", "-xzf", REMOTE_ARCHIVE, "-C", extracted], "restore.log")
    shell(f"mv {extracted}/spindle-data {shlex.quote(RESTORE)}; "
          f"cp {extracted}/report.json {shlex.quote(RESTORE_REPORT)}")
    restored_invocation = [FINAL_BINARY, "import-synapse", RESTORE_CONFIG, CONN,
                          "--media", "/source/synapse-media", "--checkpoint",
                          RESTORE_REPORT, "--validate-only"]
    command(restored_invocation, "restore-validate.log")
    restored = remote(["cat", RESTORE_REPORT])
    (OUT / "report-restored.json").write_bytes(restored)
    gate(OUT / "report-restored.json")
    state["restored_validation_binary_sha256"] = FINAL_SHA
    save("checking_restored_server")
    witness_server(key)
    save("rehearsal_store_verified", complete=True, passed=True,
         remaining_gates=["client E2EE witness", "historical PDU continuity",
                          "fresh frozen production backup, restore and import"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check prerequisites without starting follow-up work")
    parser.add_argument("--resume-final-witness", action="store_true",
                        help="recover only failed final scratch shutdown after all cold readbacks passed")
    parser.add_argument("--recreate-pristine-cold", action="store_true",
                        help="with final-witness recovery, preserve prior scratch and revalidate a new archive extraction")
    options = parser.parse_args()
    assert not (options.check and options.resume_final_witness)
    assert not options.recreate_pristine_cold or options.resume_final_witness
    os.umask(0o077)
    OUT.mkdir(parents=True, exist_ok=True)
    if options.check:
        # Read-only prerequisites are useful while the continuation holds its
        # exclusive mutation lock. A check must never rewrite worker status.
        run(check_only=True)
        raise SystemExit(0)
    lock = (OUT / "finish.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        if options.resume_final_witness:
            resume_final_witness(recreate_cold=options.recreate_pristine_cold)
        else:
            run(options.check)
    except Exception as error:
        save("failed", complete=True, error_type=type(error).__name__, error=str(error))
        raise

#!/usr/bin/env python3
"""Root-operated fresh S3 source restore into unused rehearsal DBs; no full spool."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import subprocess
import time
import uuid

from markers import Collector
from exclusive import check as check_ess_exclusive

GIB = 1024 ** 3
SCRIPT = Path(__file__).with_name("s3-download.py").read_text()
REQUIRED_OBJECTS = {"synapse.dump", "mas.dump", "media.tar", "signing.key", "source-db.password",
                    "mas-homeserver.secret", "mas-secrets-private.json", "configs-private.json",
                    "source-high-water.json", "source-restore-markers.json"}
OPTIONAL_OBJECTS = {"source-control.tar.gz"}


def capacity_plan(source_bytes, free_bytes, wal_bytes=6 * GIB, concurrent_bytes=8 * GIB, floor_bytes=8 * GIB):
    predicted_restore = math.ceil(source_bytes * 1.15)
    required_free = predicted_restore + wal_bytes + concurrent_bytes + floor_bytes
    if free_bytes < required_free:
        raise RuntimeError("insufficient node filesystem margin for fresh restore, WAL and concurrent import")
    return {"source_database_bytes": source_bytes, "predicted_restore_bytes": predicted_restore,
            "wal_allowance_bytes": wal_bytes, "concurrent_import_allowance_bytes": concurrent_bytes,
            "unallocated_floor_bytes": floor_bytes, "required_initial_free_bytes": required_free,
            "observed_initial_free_bytes": free_bytes, "abort_restore_free_bytes": concurrent_bytes + floor_bytes,
            "reservation_scope": "Coordinated headroom claim; no disk-filling preallocation"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kubeconfig", required=True)
    p.add_argument("--context", required=True)
    p.add_argument("--prefix", required=True)
    p.add_argument("--capture-reports", required=True, type=Path,
                   help="Root's local objects.json from capture, including CAPTURE-COMPLETE hash")
    p.add_argument("--output", required=True, type=Path, help="New private evidence directory")
    p.add_argument("--uploader", default="spindle-cutover-stream-upload-v2")
    p.add_argument("--pg-pod", default="source-backup-restore-pg16-node12")
    p.add_argument("--expected-pg-node", default="ip-10-20-1-12")
    p.add_argument("--concurrent-reserve-bytes", type=int, default=8 * GIB)
    p.add_argument("--floor-bytes", type=int, default=24 * GIB)
    p.add_argument("--exclusive-restore", action="store_true", help="Reserve0 only when no importer active, root coordinates exclusive node growth")
    p.add_argument("--importer-pod", default="migrator-import")
    p.add_argument("--suffix", default=None)
    p.add_argument("--capacity-check-only", action="store_true")
    args = p.parse_args()
    if not args.prefix.startswith("postgres/spindle-cutover/") or ".." in args.prefix.split("/"):
        raise SystemExit("fresh allowed backup prefix required")
    if args.floor_bytes < 24 * GIB or (args.concurrent_reserve_bytes < 8 * GIB and not (args.concurrent_reserve_bytes == 0 and args.exclusive_restore)):
        raise SystemExit("minimum node capacity reserves required")
    suffix = args.suffix or uuid.uuid4().hex[:16]
    if not re.fullmatch(r"[a-z0-9_]{8,24}", suffix):
        raise SystemExit("fresh database suffix required")
    os.umask(0o077)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    k = ["kubectl", "--kubeconfig", args.kubeconfig, "--context", args.context]
    state = {"complete": False, "passed": False, "started_at": time.time(), "prefix": args.prefix,
             "full_local_spool": False, "production_changed": False,
             "synapse_database": "spindle_restore_synapse_" + suffix,
             "mas_database": "spindle_restore_mas_" + suffix}
    plan = None
    reservation = None
    download_counter = 0
    control_counter = 0

    def save(phase, **details):
        state.update(phase=phase, updated_at=time.time(), **details)
        temporary = args.output / "status.tmp"
        temporary.write_text(json.dumps(state, indent=2))
        temporary.replace(args.output / "status.json")

    def pg(args_pg, data=None):
        nonlocal control_counter
        control_counter += 1
        with (args.output / f"pg-control-{control_counter:04d}.stderr.log").open("xb") as log:
            command = k + ["-n", "spindle-rehearsal", "exec"] + (["-i"] if data is not None else []) + [args.pg_pod, "--"] + args_pg
            result = subprocess.run(command, input=data, stdout=subprocess.PIPE, stderr=log, timeout=21600,
                                    **({"stdin": subprocess.DEVNULL} if data is None else {}))
        if result.returncode:
            raise RuntimeError("rehearsal database operation failed; details suppressed")
        return result.stdout

    def free():
        # Target PGDATA is on the shared node filesystem, so this detects all
        # concurrent store/restore growth, not only this database's files.
        if args.exclusive_restore:
            state["exclusive_ess_workloads"] = check_ess_exclusive(k)
            # Inspect only spindle executables; never print process arguments,
            # which can contain private database connection material.
            check = "for proc in /proc/[0-9]*; do exe=$(readlink \"$proc/exe\" 2>/dev/null) || continue; case \"${exe##*/}\" in spindle*) if tr '\\0' '\\n' < \"$proc/cmdline\" | grep -qx import-synapse; then exit 1; fi;; esac; done"
            result = subprocess.run(k + ["-n", "spindle-rehearsal", "exec", args.importer_pod, "--", "sh", "-c", check], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode:
                raise RuntimeError("exclusive source restore requires no active importer")
        value = pg(["df", "-P", "-B1", "/var/lib/postgresql/data"]).decode().splitlines()[-1].split()
        total = int(value[1])
        if args.floor_bytes < math.ceil(total * 0.15) + GIB:
            raise RuntimeError("configured floor is below measured imagefs eviction threshold plus margin")
        state["pg_filesystem_total_bytes"] = total
        return int(value[3])

    def download_command(report):
        return k + ["-n", "postgres", "exec", args.uploader, "--", "python3", "-c", SCRIPT,
                    "--key", report["key"], "--sha256", report["sha256"], "--bytes", str(report["bytes"])]

    def download_process(report):
        nonlocal download_counter
        download_counter += 1
        with (args.output / f"download-{download_counter:02d}.stderr.log").open("xb") as log:
            return subprocess.Popen(download_command(report), stdout=subprocess.PIPE, stderr=log)

    def small(report, limit=8 * 1024 * 1024):
        if report["bytes"] > limit:
            raise RuntimeError("small source metadata exceeded bounded limit")
        process = download_process(report)
        value = bytearray()
        try:
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                if len(value) + len(chunk) > limit:
                    raise RuntimeError("small source metadata exceeded bounded limit")
                value.extend(chunk)
            if process.wait():
                raise RuntimeError("source metadata object hash verification failed")
            return bytes(value)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
            process.stdout.close()

    def verify_discard(report):
        process = download_process(report)
        next_capacity_check = 0
        while process.stdout.read(1024 * 1024):
            if plan is not None and time.monotonic() >= next_capacity_check:
                next_capacity_check = time.monotonic() + 15
                if free() < plan["abort_restore_free_bytes"]:
                    process.terminate()
                    process.wait(timeout=15)
                    raise RuntimeError("node filesystem margin fell below reserved floor")
        process.stdout.close()
        if process.wait():
            raise RuntimeError("source object stream verification failed")

    def restore(database, report):
        producer = download_process(report)
        with (args.output / (database + "-restore.log")).open("wb") as log:
            receiver = subprocess.Popen(k + ["-n", "spindle-rehearsal", "exec", "-i", args.pg_pod, "--",
                                             "pg_restore", "--username=postgres", "--dbname=" + database,
                                             "--no-owner", "--no-privileges", "--single-transaction", "--exit-on-error"],
                                        stdin=producer.stdout, stdout=log, stderr=log)
            producer.stdout.close()
            try:
                deadline = time.monotonic() + 21600
                while receiver.poll() is None or producer.poll() is None:
                    if receiver.poll() not in [None, 0] or producer.poll() not in [None, 0]:
                        raise RuntimeError("bounded fresh database restore failed")
                    if free() < plan["abort_restore_free_bytes"]:
                        raise RuntimeError("restore cancelled before reserved filesystem margin exhausted")
                    if time.monotonic() > deadline:
                        raise RuntimeError("bounded fresh database restore timed out")
                    time.sleep(15)
                if producer.returncode or receiver.returncode:
                    raise RuntimeError("dump download hash verification or fresh database restore failed")
            finally:
                for process in [producer, receiver]:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=15)
                # kubectl termination alone is not proof the remote restore
                # query exited. Cancel only connections to this new own DB.
                if receiver.returncode:
                    pg(["psql", "-X", "-U", "postgres", "-d", "postgres", "-Atq", "-v", "ON_ERROR_STOP=1"],
                       ("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='" + database + "' AND pid<>pg_backend_pid();").encode())

    save("checking_capture_marker")
    try:
        node = subprocess.run(k + ["-n", "spindle-rehearsal", "get", "pod", args.pg_pod, "-o", "jsonpath={.spec.nodeName}"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout.decode()
        if node != args.expected_pg_node:
            raise RuntimeError("independent source restore target node mismatch")
        state.update(pg_pod=args.pg_pod, pg_node=node)
        root_reports = json.loads(args.capture_reports.read_text())
        marker_key = args.prefix.rstrip("/") + "/source/CAPTURE-COMPLETE.json"
        matches = [report for report in root_reports if report["key"] == marker_key]
        if len(matches) != 1:
            raise RuntimeError("root-anchored CAPTURE-COMPLETE checksum missing")
        if matches[0].get("full_object_reread_verified") is not True or matches[0].get("overwrite_prevented") is not True:
            raise RuntimeError("root capture completion verification flags absent")
        manifest = json.loads(small(matches[0]))
        assert manifest["status"] == "capture_complete_restore_pending" and manifest["full_local_spool"] is False
        objects = {}
        for report in manifest["objects"]:
            key = report["key"]
            if not key.startswith(args.prefix.rstrip("/") + "/source/"):
                raise RuntimeError("capture object escaped immutable prefix")
            name = key.rsplit("/", 1)[1]
            if name in objects or report.get("full_object_reread_verified") is not True or report.get("overwrite_prevented") is not True:
                raise RuntimeError("capture object metadata gate failed")
            objects[name] = report
        if not REQUIRED_OBJECTS <= set(objects) or not set(objects) <= REQUIRED_OBJECTS | OPTIONAL_OBJECTS:
            raise RuntimeError("complete capture object topology mismatch")
        if manifest.get("source_control_included", False) is not ("source-control.tar.gz" in objects):
            raise RuntimeError("source control snapshot completion flag mismatch")
        expected = json.loads(small(objects["source-restore-markers.json"]))
        high = json.loads(small(objects["source-high-water.json"]))
        semantic = expected["synapse"]["semantic"]
        if high != manifest["source_high_water"] or any(high[key] != semantic[key] for key in ["event_rows", "stream_high_water"]):
            raise RuntimeError("frozen source high-water marker mismatch")
        assert expected["schema_version"] == 1 and semantic["exact_retained_room_scope"]
        # A cooperative node restore reservation is held by a live rehearsal PG
        # session, independent of controller output paths. Production import's
        # forecast headroom is reserved in the capacity calculation below.
        with (args.output / "reservation-session.stderr.log").open("xb") as log:
            reservation = subprocess.Popen(k + ["-n", "spindle-rehearsal", "exec", "-i", args.pg_pod, "--",
                                                "psql", "-X", "-U", "postgres", "-d", "postgres", "-Atq", "-v", "ON_ERROR_STOP=1"],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        reservation.stdin.write(b"SELECT pg_try_advisory_lock(202610065113::bigint);\n")
        reservation.stdin.flush()
        if not select.select([reservation.stdout], [], [], 30)[0] or reservation.stdout.readline().strip() != b"t":
            raise RuntimeError("another source restore owns node capacity reservation or reservation unavailable")
        source_bytes = sum(expected[name]["metadata"]["database_bytes"] for name in ["synapse", "mas"])
        plan = capacity_plan(source_bytes, free(), concurrent_bytes=args.concurrent_reserve_bytes, floor_bytes=args.floor_bytes)
        args.output.joinpath("capacity-reservation.json").write_text(json.dumps(plan, indent=2))
        save("capacity_reserved", capacity=plan)
        if args.capacity_check_only:
            save("capacity_checked_restore_not_executed", complete=True, passed=False, capacity_check_pass=True)
            return
        existing = pg(["psql", "-X", "-U", "postgres", "-d", "postgres", "-Atq", "-v", "ON_ERROR_STOP=1"],
                      ("SELECT count(*) FROM pg_database WHERE datname IN ('" + state["synapse_database"] + "','" + state["mas_database"] + "');").encode())
        if existing.strip() != b"0":
            raise RuntimeError("fresh restore database name already exists; no replacement allowed")
        for name in ["synapse", "mas"]:
            metadata = expected[name]["metadata"]
            if metadata["postgres_version_num"] // 10000 != 16 or metadata["locale_provider"] != "c":
                raise RuntimeError("unreviewed PostgreSQL major version or ICU locale restore")
            quote = lambda text: "'" + text.replace("'", "''") + "'"
            database = state[name + "_database"]
            create = ("CREATE DATABASE " + database + " WITH TEMPLATE template0 ENCODING " + quote(metadata["encoding"])
                      + " LC_COLLATE " + quote(metadata["lc_collate"]) + " LC_CTYPE " + quote(metadata["lc_ctype"]) + ";")
            pg(["psql", "-X", "-U", "postgres", "-d", "postgres", "-Atq", "-v", "ON_ERROR_STOP=1"], create.encode())
            save("restoring_" + name)
            restore(database, objects[name + ".dump"])
        save("verifying_source_configuration_objects")
        protected = {}
        for name in ["signing.key", "source-db.password", "mas-homeserver.secret", "mas-secrets-private.json", "configs-private.json"]:
            payload = small(objects[name])
            if name == "mas-secrets-private.json":
                private = json.loads(payload)
                assert private["kind"] == "List" and private["items"]
                protected["mas_secret_data_sha256"] = {item["metadata"]["name"]: hashlib.sha256(
                    json.dumps(item["data"], sort_keys=True, separators=(",", ":")).encode()).hexdigest() for item in private["items"]}
            if name == "configs-private.json":
                private = json.loads(payload)
                assert any(item["metadata"]["name"] == "ess-matrix-authentication-service" for item in private["items"])
            protected[name] = {"sha256": objects[name]["sha256"], "original_captured_bytes_verified": True}
        verify_discard(objects["media.tar"])
        if "source-control.tar.gz" in objects:
            verify_discard(objects["source-control.tar.gz"])
            protected["source-control.tar.gz"] = {"sha256": objects["source-control.tar.gz"]["sha256"],
                                                "original_control_archive_bytes_verified": True}
        save("comparing_restored_semantics")
        collector = Collector(args.kubeconfig, args.context, "spindle-rehearsal", args.pg_pod,
                              args.output / "marker-private-logs")
        actual = {"synapse": collector.collect(state["synapse_database"], synapse=True,
                    server_name=semantic.get("scope_server_name", "reilly.asia"),
                    preserve_local_history=semantic.get("preserve_local_history", False)),
                  "mas": collector.collect(state["mas_database"])}
        if any(actual[name]["semantic"] != expected[name]["semantic"] for name in ["synapse", "mas"]):
            raise RuntimeError("restored source identity/key/domain/room/high-water markers diverged")
        (args.output / "restored-markers.json").write_text(json.dumps(actual, indent=2))
        (args.output / "protected-source-config-verification.json").write_text(json.dumps(protected, indent=2))
        save("fresh_source_backup_restore_verified", complete=True, passed=True,
             retained_rooms=len(semantic["exact_retained_room_scope"]), source_high_water=high,
             protected_source_configs_original=True, observed_final_free_bytes=free(),
             restored_application_started=False, production_existing_owner_login_proven=False)
    except Exception as error:
        save("failed", complete=True, passed=False, error_type=type(error).__name__,
             error="Source restore gate failed; raw diagnostic details are private", private_logs_directory=str(args.output), restored_application_started=False,
             cleanup_policy="New uniquely named rehearsal DBs retained; no DROP/replacement/production mutation")
        raise SystemExit("Fresh source restore verifier failed; inspect private status; credentials suppressed") from None
    finally:
        if reservation is not None:
            if reservation.stdin is not None:
                reservation.stdin.close()
            try:
                reservation.wait(timeout=15)
            except subprocess.TimeoutExpired:
                reservation.terminate()
                try:
                    reservation.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    reservation.kill()
                    reservation.wait(timeout=15)
            reservation.stdout.close()


if __name__ == "__main__":
    main()

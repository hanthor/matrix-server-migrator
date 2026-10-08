#!/usr/bin/env python3
"""Generate an exact root-reviewable restore command from a completed capture."""
import argparse
import json
import os
from pathlib import Path
import shlex
import uuid


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture-directory", required=True, type=Path)
    p.add_argument("--output-script", required=True, type=Path)
    p.add_argument("--restore-output", type=Path)
    p.add_argument("--kubeconfig", default="/home/ubuntu/.kube/config-aws-migration")
    p.add_argument("--context", default="admin@aws-migration")
    p.add_argument("--pg-pod", default="source-backup-restore-pg16-node12")
    p.add_argument("--expected-pg-node", default="ip-10-20-1-12")
    p.add_argument("--concurrent-reserve-bytes", type=int, default=8589934592)
    p.add_argument("--floor-bytes", type=int, default=24 * 1024**3)
    p.add_argument("--exclusive-restore", action="store_true")
    p.add_argument("--importer-pod", default="migrator-import")
    args = p.parse_args()
    os.umask(0o077)
    try:
        directory = args.capture_directory.resolve(strict=True)
        reports_path = directory / "objects.json"
        reports = json.loads(reports_path.read_text())
        complete = [report for report in reports if report["key"].endswith("/source/CAPTURE-COMPLETE.json")]
        assert len(complete) == 1
        marker = complete[0]
        assert marker["full_object_reread_verified"] is True and marker["overwrite_prevented"] is True
        prefix = marker["key"].removesuffix("/source/CAPTURE-COMPLETE.json")
        assert prefix.startswith("postgres/spindle-cutover/") and ".." not in prefix.split("/")
        local_manifest = json.loads((directory / "manifest.json").read_text())
        assert local_manifest["status"] == "capture_complete_restore_pending"
        assert local_manifest["full_local_spool"] is False
        assert any(report["key"] == prefix + "/source/source-restore-markers.json" for report in local_manifest["objects"])
        suffix = uuid.uuid4().hex[:16]
        restore_output = (args.restore_output or directory.parent / ("source-restore-proof-" + suffix)).absolute()
        assert not restore_output.exists(), "restore evidence directory already exists"
        command = ["/usr/bin/env", "PATH=/home/linuxbrew/.linuxbrew/bin:/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin",
                   "/usr/bin/python3", str(Path(__file__).resolve().with_name("verify.py")),
                   "--kubeconfig", args.kubeconfig, "--context", args.context, "--prefix", prefix,
                   "--capture-reports", str(reports_path), "--uploader", "spindle-cutover-stream-upload-v2",
                   "--pg-pod", args.pg_pod, "--expected-pg-node", args.expected_pg_node,
                   "--concurrent-reserve-bytes", str(args.concurrent_reserve_bytes), "--floor-bytes", str(args.floor_bytes), "--suffix", suffix, "--output", str(restore_output)]
        if args.exclusive_restore:
            command += ["--exclusive-restore", "--importer-pod", args.importer_pod]
        args.output_script.parent.mkdir(parents=True, exist_ok=True)
        with args.output_script.open("x") as script:
            script.write("#!/bin/sh\nset -eu\numask 077\n# Generated from completed source capture; root owns execution.\n# Exclusive mode requires ess/job/ess-spindle-import-fresh absent, ess/deployment/ess-spindle fully0, and no nonterminal ess-spindle-data PVC user.\nexec " + shlex.join(command) + "\n")
        args.output_script.chmod(0o700)
        print(json.dumps({"prepared": True, "executed": False, "prefix": prefix,
                          "script": str(args.output_script.absolute()), "restore_output": str(restore_output),
                          "synapse_database": "spindle_restore_synapse_" + suffix,
                          "mas_database": "spindle_restore_mas_" + suffix}, indent=2))
    except Exception as error:
        raise SystemExit("Restore invocation preparation failed (" + type(error).__name__ + "); no cluster action executed") from None


if __name__ == "__main__":
    main()

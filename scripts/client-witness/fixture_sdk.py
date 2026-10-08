#!/usr/bin/env python3
"""Reuse retained fixture accounts on an already prepared disposable target.

Does not import fixture rooms, start servers, read secret values, or touch source.
Fresh login/recovery creates temporary device state on the target.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import urllib.parse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--kubeconfig", default="/home/ubuntu/.kube/config-aws-migration")
    parser.add_argument("--context", default="admin@aws-migration")
    args = parser.parse_args()
    os.umask(0o077)
    target = urllib.parse.urlparse(args.target_url)
    if target.scheme != "http" or not (target.hostname or "").endswith(".spindle-rehearsal.svc.cluster.local"):
        raise SystemExit("Use the separate disposable witness ClusterIP service DNS name")
    if target.username or target.password or target.query or target.fragment:
        raise SystemExit("Unexpected target URL components")
    output = Path(args.output)
    output.mkdir(mode=0o700)  # Never overwrite previous evidence.
    kube = ["kubectl", "--kubeconfig", args.kubeconfig, "--context", args.context,
            "-n", "spindle-rehearsal", "exec", "mig-rig-toolbox", "--"]
    reports = []
    for user in "abc":
        command = kube + ["/work/bin/mig-rig", "verify", "--homeserver", args.target_url,
                          "--user", f"@spindle-mig-{user}:reilly.asia",
                          "--manifest", "/work/out/manifest.json",
                          "--password-file", f"/secrets/rig/password-{user}",
                          "--recovery-key-file", f"/secrets/rig/recovery-key-{user}"]
        try:
            result = subprocess.run(command, capture_output=True, timeout=900)
            # Store SDK report privately; do not print stderr/HTTP bodies.
            (output / f"sdk-{user}.json").write_bytes(result.stdout)
            report = json.loads(result.stdout)
            pass_user = (result.returncode == 0 and report.get("pass") is True
                         and report.get("recovery_ok") is True
                         and report.get("own_device_cross_signed") is True
                         and report.get("summary", {}).get("failures") == 0
                         and report.get("summary", {}).get("expected_readable", 0) > 0
                         and report.get("summary", {}).get("readable_ok") == report.get("summary", {}).get("expected_readable"))
            reports.append({"user": user, "pass": pass_user,
                            "device": report.get("device"), "summary": report.get("summary")})
        except (subprocess.TimeoutExpired, ValueError):
            reports.append({"user": user, "pass": False, "error": "timeout or invalid SDK report"})
    passed = all(report["pass"] for report in reports)
    (output / "summary.json").write_text(json.dumps({
        "fixture_pass": passed, "scope": "Preserved synthetic pre-migration synapse_dark fixture",
        "production_116_room_history_decryption_pass": False,
        "target_url": args.target_url, "reports": reports}, indent=2))
    print("Preserved fixture witness " + ("passed" if passed else "failed"))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

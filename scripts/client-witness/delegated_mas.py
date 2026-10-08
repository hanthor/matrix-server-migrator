#!/usr/bin/env python3
"""Isolated preserved-MAS existing-session proof; never connects to production."""
import argparse
import base64
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519

K = ["kubectl", "--kubeconfig", "/home/ubuntu/.kube/config-aws-migration",
     "--context", "admin@aws-migration", "-n", "spindle-rehearsal"]
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/reilly-2026-10-06"
ISSUER = "http://dark-mas.spindle-rehearsal.svc.cluster.local:18080/"
HS = "http://127.0.0.1:18708"
MAS = "http://127.0.0.1:18780"


def kube(args, data=None):
    result = subprocess.run(K + args, input=data, capture_output=True, timeout=240)
    if result.returncode:
        raise RuntimeError("isolated Kubernetes action failed; details suppressed")
    return result.stdout


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def call(base, method, path, token=None, body=None, form=None, basic=None):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = "Bearer " + token
    if basic:
        headers["Authorization"] = "Basic " + base64.b64encode(":".join(basic).encode()).decode()
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-binary", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output-root", type=Path, default=OUT)
    args = parser.parse_args()
    assert re.fullmatch(r"/work/[A-Za-z0-9_./-]+", args.target_binary) and ".." not in Path(args.target_binary).parts
    assert re.fullmatch(r"[0-9a-f]{64}", args.expected_sha256)
    os.umask(0o077)
    args.output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = str(int(time.time()))
    work = "/work/client-delegated-mas-" + stamp
    out = args.output_root / ("delegated-mas-" + stamp)
    out.mkdir(mode=0o700)
    state = {"complete": False, "passed": False, "target_binary": args.target_binary,
             "candidate_sha256": args.expected_sha256,
             "scope": "Preserved synthetic fixture MAS sessions on an isolated copied fixture store",
             "production_changed": False, "started_at": time.time()}

    def save(phase, **details):
        state.update(phase=phase, updated_at=time.time(), **details)
        temp = args.output_root / "delegated-mas-status.tmp"
        temp.write_text(json.dumps(state, indent=2))
        temp.replace(args.output_root / "delegated-mas-status.json")
        (out / "status.json").write_text(json.dumps(state, indent=2))

    original = {}
    forwards = []
    sessions = []
    admin = None
    changed = False
    cleanup_errors = []
    save("preparing")
    try:
        staged_sha = kube(["exec", "mig-rig-toolbox", "--", "sha256sum", args.target_binary]).decode().split()[0]
        assert staged_sha == args.expected_sha256, "isolated target candidate checksum mismatch"
        for name in ["dark-mas", "dark-spindle-mas"]:
            original[name] = json.loads(kube(["get", "deployment", name, "-o", "json"]))
            assert original[name]["spec"]["replicas"] == 0, "isolated rig is already in use"
            (out / (name + "-before.json")).write_text(json.dumps(original[name], indent=2))
        conf = next(volume for volume in original["dark-spindle-mas"]["spec"]["template"]["spec"]["volumes"] if volume["name"] == "conf")
        raw = json.loads(kube(["get", "secret", conf["secret"]["secretName"], "-o", "json"]))
        config = base64.b64decode(raw["data"]["spindle.toml"]).decode()
        source_store = tomllib.loads(config)["storage"]["path"]
        assert source_store.startswith('/work/masgate/store') and ISSUER in config
        assert tomllib.loads(config).get("federation", {}).get("enabled") is False, "isolated fixture federation must be disabled"
        config = config.replace(source_store, work + '/store')
        kube(["exec", "-i", "mig-rig-toolbox", "--", "sh", "-eu", "-c",
              f"umask 077; mkdir {work}; cp -a --reflink=auto {source_store} {work}/store; cat > {work}/spindle.toml; chown -R 10092:10092 {work}"], config.encode())
        secret = json.loads(kube(["get", "secret", "dark-mas-secrets", "-o", "json"]))["data"]
        admin_secret = base64.b64decode(secret["admin_client_secret"]).decode()
        matrix_secret = base64.b64decode(secret["matrix_secret"]).decode()
        image = original["dark-spindle-mas"]["spec"]["template"]["spec"]["containers"][0]["image"]

        def runtime(binary, replicas=1):
            patch = {"spec": {"replicas": replicas, "template": {"spec": {"containers": [{"name": "spindle", "image": image,
                     "command": [binary, work + "/spindle.toml"]}]}}}}
            kube(["patch", "deployment", "dark-spindle-mas", "--type", "strategic", "-p", json.dumps(patch)])
            kube(["rollout", "status", "deployment/dark-spindle-mas", "--timeout=180s"])

        changed = True
        kube(["scale", "deployment/dark-mas", "--replicas=1"])
        runtime("/work/masgate/bin/spindle-5eb4452")
        state["source_binary_sha256"] = kube(["exec", "deployment/dark-spindle-mas", "-c", "spindle", "--", "sha256sum", "/work/masgate/bin/spindle-5eb4452"]).decode().split()[0]
        kube(["rollout", "status", "deployment/dark-mas", "--timeout=180s"])
        for service, ports in [("dark-mas", "18780:8080"), ("dark-spindle-mas", "18708:8008")]:
            with (out / (service + "-forward.log")).open("wb") as log:
                forwards.append(subprocess.Popen(K + ["port-forward", "svc/" + service, ports, "--address=127.0.0.1"], stdout=log, stderr=subprocess.STDOUT))
        deadline = time.monotonic() + 60
        while True:
            assert all(p.poll() is None for p in forwards)
            try:
                if call(HS, "GET", "/_matrix/client/versions")[0] == 200 and call(MAS, "GET", "/.well-known/openid-configuration")[0] == 200:
                    break
            except OSError:
                pass
            assert time.monotonic() < deadline, "isolated listener timeout"
            time.sleep(1)
        code, body = call(MAS, "POST", "/oauth2/token", basic=("0000000000000000000MASADMN", admin_secret),
                          form={"grant_type": "client_credentials", "scope": "urn:mas:admin"})
        assert code == 200
        admin = body["access_token"]
        spec = importlib.util.spec_from_file_location("native_session", Path(__file__).with_name("native_session.py"))
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        save("minting_isolated_fixture_sessions")
        baselines = []
        for user, scope, device in [("a", "urn:matrix:client:api:*", "DELEGATEDSTABLE" + stamp),
                                     ("b", "urn:matrix:org.matrix.msc2967.client:api:*", "DELEGATEDUNSTABLE" + stamp)]:
            code, actor = call(MAS, "GET", "/api/admin/v1/users/by-username/spindle-mig-" + user, token=admin)
            assert code == 200, "preserved fixture MAS identity absent"
            device_scope = "urn:matrix:client:device:" if user == "a" else "urn:matrix:org.matrix.msc2967.client:device:"
            code, minted = call(MAS, "POST", "/api/admin/v1/personal-sessions", token=admin,
                                 body={"actor_user_id": actor["data"]["id"], "human_name": "isolated delegated continuity " + stamp,
                                       "scope": scope + " " + device_scope + device, "expires_in": 3600})
            assert code in [200, 201]
            sid = minted["data"]["id"]
            token = minted["data"]["attributes"]["access_token"]
            sessions.append((sid, token))
            # Only this owned fixture device gets disposable cryptographic keys.
            # A real signed upload verifies device-key mapping survives restart,
            # rather than merely observing an empty newly granted device.
            ed = ed25519.Ed25519PrivateKey.generate()
            curve = x25519.X25519PrivateKey.generate()
            encode = lambda raw: base64.b64encode(raw).decode().rstrip("=")
            signed = {"user_id": f"@spindle-mig-{user}:reilly.asia", "device_id": device,
                      "algorithms": ["m.olm.v1.curve25519-aes-sha2", "m.megolm.v1.aes-sha2"],
                      "keys": {"ed25519:" + device: encode(ed.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
                               "curve25519:" + device: encode(curve.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))}}
            canonical = json.dumps(signed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            signed["signatures"] = {signed["user_id"]: {"ed25519:" + device: encode(ed.sign(canonical))}}
            code, _ = call(HS, "POST", "/_matrix/client/v3/keys/upload", token=token, body={"device_keys": signed})
            assert code == 200, "owned fixture device key upload failed"
            baseline = native.capture(HS, token, ISSUER)
            assert baseline["user_id"] == f"@spindle-mig-{user}:reilly.asia" and baseline["device_id"] == device
            code, account = call(HS, "GET", "/_synapse/mas/query_user?localpart=spindle-mig-" + user, token=matrix_secret)
            assert code == 200 and account["user_id"] == baseline["user_id"]
            (out / ("baseline-" + user + ".json")).write_text(json.dumps(baseline, indent=2))
            baselines.append((user, token, baseline))
        save("restarting_target_with_existing_mas_sessions")
        # Same target still gets a real cold restart; another chosen binary changes
        # the command. Scale zero first so both cases release the fixture store.
        kube(["scale", "deployment/dark-spindle-mas", "--replicas=0"])
        kube(["wait", "--for=delete", "pod", "-l", "app=dark-spindle-mas", "--timeout=120s"])
        runtime(args.target_binary)
        state["target_binary_sha256"] = kube(["exec", "deployment/dark-spindle-mas", "-c", "spindle", "--", "sha256sum", args.target_binary]).decode().split()[0]
        assert state["target_binary_sha256"] == args.expected_sha256, "running isolated candidate checksum mismatch"
        # Service forwards attached to the old pod must be reopened after restart.
        forwards[-1].terminate()
        forwards[-1].wait(timeout=15)
        with (out / "target-forward.log").open("wb") as log:
            forwards[-1] = subprocess.Popen(K + ["port-forward", "svc/dark-spindle-mas", "18708:8008", "--address=127.0.0.1"], stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while True:
            try:
                if call(HS, "GET", "/ready")[0] == 200:
                    break
            except OSError:
                pass
            assert time.monotonic() < deadline
            time.sleep(1)
        save("verifying_existing_sessions")
        proofs = []
        for user, token, baseline in baselines:
            result = native.capture(HS, token, ISSUER)
            assert result == baseline, "identity/device/key/backup/joined-room continuity failed"
            assert result["has_cross_signing"], "fixture cross-signing public keys missing"
            assert result["has_device_keys"], "owned fixture device keys missing"
            (out / ("target-" + user + ".json")).write_text(json.dumps(result, indent=2))
            proofs.append({"user": result["user_id"], "device_id": result["device_id"],
                           "existing_session_accepted": True, "scope": "stable" if user == "a" else "unstable",
                           "identity_device_keys_backup_joined_rooms_unchanged": True})
        state["proofs"] = proofs
        state["passed"] = True
    except Exception as error:
        state.update(passed=False, error_type=type(error).__name__, error=str(error))
    finally:
        if admin:
            for sid, token in sessions:
                try:
                    code, _ = call(MAS, "POST", "/api/admin/v1/personal-sessions/" + sid + "/revoke", token=admin)
                    assert code in [200, 204]
                except Exception:
                    cleanup_errors.append("owned fixture session revocation failed")
        for process in reversed(forwards):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
        if changed:
            try:
                before = original["dark-spindle-mas"]["spec"]["template"]["spec"]["containers"][0]
                patch = {"spec": {"replicas": 0, "template": {"spec": {"containers": [{"name": "spindle", "image": before["image"], "command": before["command"]}]}}}}
                kube(["patch", "deployment", "dark-spindle-mas", "--type", "strategic", "-p", json.dumps(patch)])
                kube(["scale", "deployment/dark-mas", "--replicas=0"])
                kube(["wait", "--for=delete", "pod", "-l", "app=dark-spindle-mas", "--timeout=120s"])
                kube(["wait", "--for=delete", "pod", "-l", "app=dark-mas", "--timeout=120s"])
            except Exception:
                cleanup_errors.append("isolated deployment restoration failed")
        save("delegated_fixture_sessions_verified" if state["passed"] and not cleanup_errors else "failed",
             complete=True, passed=state["passed"] and not cleanup_errors, cleanup_errors=cleanup_errors,
             evidence_directory=str(out), isolated_owned_sessions=len(sessions),
             production_existing_owner_session_proven=False, history_decryption_pass=False)
    print("isolated delegated MAS proof " + ("passed" if state["passed"] else "failed") + "; credentials suppressed")
    raise SystemExit(0 if state["passed"] else 1)


if __name__ == "__main__":
    main()

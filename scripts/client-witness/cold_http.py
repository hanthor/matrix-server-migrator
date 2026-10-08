#!/usr/bin/env python3
"""Bounded first real-room read on an isolated, freshly started fixture server."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def witness(base, user, password, room, max_seconds, opener=None, warm_fixture_first=False):
    url = urllib.parse.urlsplit(base)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.port != 18608 or url.path or url.query or url.username:
        raise RuntimeError("isolated listener identity rejected")
    if user != "@spindle-mig-a:reilly.asia" or not room.startswith("!") or not 1 <= max_seconds <= 180:
        raise RuntimeError("isolated cold witness input rejected")
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    token = None
    progress = {}
    primary_error = None
    measurement_start = time.monotonic()

    def request(method, path, body=None, timeout=30, label="unlabelled"):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        start = time.monotonic()
        try:
            try:
                response = opener.open(req, timeout=timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise RuntimeError("cold response bound exceeded")
                elapsed = time.monotonic() - start
                progress[label] = {"status": response.status, "seconds": round(elapsed, 6),
                                   "started_offset_seconds": round(start - measurement_start, 6),
                                   "completed_offset_seconds": round(time.monotonic() - measurement_start, 6)}
                return response.status, json.loads(raw) if raw else {}, elapsed
        except Exception as error:
            progress[label] = {"error_type": type(error).__name__, "seconds": round(time.monotonic() - start, 6)}
            raise

    try:
        device = "COLDWITNESS" + secrets.token_hex(8).upper()
        code, login, _ = request("POST", "/_matrix/client/v3/login", {
            "type": "m.login.password", "identifier": {"type": "m.id.user", "user": user},
            "password": password, "device_id": device,
        }, label="fixture_login")
        if code != 200 or login.get("user_id") != user or login.get("device_id") != device:
            raise RuntimeError("owned fixture login failed")
        token = login["access_token"]
        code, joined, _ = request("GET", "/_matrix/client/v3/joined_rooms", label="joined_index")
        fixture_rooms = joined.get("joined_rooms", [])
        if code != 200 or not fixture_rooms or room in fixture_rooms:
            raise RuntimeError("fixture unexpectedly joined real room")
        route = "/_matrix/client/v3/rooms/" + urllib.parse.quote(room, safe="") + "/state/m.room.create"
        other_route = "/_matrix/client/v3/rooms/" + urllib.parse.quote(fixture_rooms[0], safe="") + "/state/m.room.create"
        fixture_warm = None
        if warm_fixture_first:
            code, _, elapsed = request("GET", other_route, label="fixture_state_pre_warm")
            if code != 200:
                raise RuntimeError("concurrent fixture prewarm failed")
            fixture_warm = {"status": code, "seconds": round(elapsed, 6)}
        checks = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            cold_started = time.monotonic()
            cold = pool.submit(request, "GET", route, timeout=max_seconds + 5, label="cold")
            time.sleep(0.05)
            other_started = time.monotonic()
            other = pool.submit(request, "GET", other_route, timeout=max_seconds + 5, label="fixture_state_concurrent")
            ready = pool.submit(request, "GET", "/ready", timeout=max_seconds + 5, label="readiness_concurrent")
            cold_reply = cold.result()
            other_code, other_body, other_elapsed = other.result()
            ready_code, _, ready_elapsed = ready.result()
        if other_code != 200 or ready_code != 200:
            raise RuntimeError("concurrent fixture/readiness probe failed")
        for kind in ["cold", "hot"]:
            code, body, elapsed = cold_reply if kind == "cold" else request("GET", route, timeout=max_seconds + 5, label="hot")
            if not (code == 200 or (code == 403 and body.get("errcode") == "M_FORBIDDEN")):
                raise RuntimeError("unexpected real-room read verdict")
            if elapsed > max_seconds:
                raise RuntimeError("cold room latency gate exceeded")
            checks.append({"kind": kind, "seconds": round(elapsed, 6), "status": code,
                           "errcode": body.get("errcode"), "response_sha256": hashlib.sha256(
                               json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
        if checks[0]["status"] != checks[1]["status"] or checks[0]["response_sha256"] != checks[1]["response_sha256"]:
            raise RuntimeError("real-room read verdict changed after warming")
        objective = 30
        result = {"passed": True, "diagnostic_only": True,
                  "production_readiness_objective_seconds": objective,
                  "production_readiness_objective_met": all(t <= objective for t in [cold_reply[2], other_elapsed, ready_elapsed]),
                  "production_latency_accepted": False,
                  "fixture_pre_warm": fixture_warm, "independent_tcp_connections": True,
                  "request_timings": progress,
                  "concurrent_fixture_state": {"status": other_code, "seconds": round(other_elapsed, 6),
                      "overlapped_cold_request": other_started < cold_started + cold_reply[2],
                      "response_sha256": hashlib.sha256(json.dumps(other_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()},
                  "concurrent_readiness": {"status": ready_code, "seconds": round(ready_elapsed, 6)},
                  "room_sha256": hashlib.sha256(room.encode()).hexdigest(),
                  "max_seconds": max_seconds, "requests": checks, "real_room_writes": False,
                  "real_room_membership_granted": False, "real_head_history_content_proven": False,
                  "cold_process_order_required": "First huge-room read in a fresh process; optionally warm only the independent fixture room"}
    except Exception as error:
        primary_error = error
        error.witness_diagnostics = progress
        raise
    finally:
        if token:
            try:
                code, _, _ = request("POST", "/_matrix/client/v3/logout", {}, label="fixture_logout")
                if code != 200:
                    raise RuntimeError("owned fixture device logout failed")
            except Exception as error:
                if primary_error is None:
                    error.witness_diagnostics = progress
                    raise
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--room", required=True)
    p.add_argument("--password-file", type=Path, required=True)
    p.add_argument("--max-seconds", type=float, default=120)
    p.add_argument("--warm-fixture-first", action="store_true")
    args = p.parse_args()
    try:
        result = witness("http://127.0.0.1:18608", "@spindle-mig-a:reilly.asia",
                         args.password_file.read_text().rstrip("\n"), args.room, args.max_seconds,
                         warm_fixture_first=args.warm_fixture_first)
    except Exception as error:
        gates = {"isolated listener identity rejected", "isolated cold witness input rejected",
                 "cold response bound exceeded", "owned fixture login failed",
                 "fixture unexpectedly joined real room", "concurrent fixture prewarm failed", "concurrent fixture/readiness probe failed",
                 "unexpected real-room read verdict", "cold room latency gate exceeded",
                 "real-room read verdict changed after warming", "owned fixture device logout failed"}
        message = str(error)
        print(json.dumps({"passed": False, "error_type": type(error).__name__,
                          "failure_gate": message.replace(" ", "_") if message in gates else "transport_or_decode",
                          "request_diagnostics": getattr(error, "witness_diagnostics", {}),
                          "details_suppressed": True}))
        raise SystemExit(1)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

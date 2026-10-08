#!/usr/bin/env python3
"""Read-only bounded semantic fingerprints of frozen Synapse and MAS databases."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

SYNAPSE_DOMAINS = ["events", "event_json", "rooms", "current_state_events", "room_memberships",
                   "users", "devices", "access_tokens", "e2e_device_keys_json", "e2e_one_time_keys_json",
                   "e2e_fallback_keys_json", "e2e_cross_signing_keys", "e2e_cross_signing_signatures",
                   "e2e_room_keys_versions", "e2e_room_keys", "account_data", "room_account_data",
                   "room_tags", "receipts_linearized", "receipts_graph", "user_push_rules", "pushers",
                   "profiles", "room_aliases", "blocked_rooms", "local_media_repository"]
REQUIRED = {"events", "event_json", "rooms", "current_state_events", "room_memberships",
            "users", "devices", "e2e_cross_signing_keys", "e2e_room_keys", "account_data"}


def quote_ident(value):
    return '"' + value.replace('"', '""') + '"'


def scope_sql(server_name="reilly.asia", preserve_local_history=False):
    # Quote the value independently of SQL identifiers. No source payload is read.
    server = "'" + server_name.replace("'", "''") + "'"
    ledger = (f" UNION SELECT room_id FROM local_current_membership WHERE "
              f"right(user_id,length({server})+1)=':' || {server}") if preserve_local_history else ""
    policy = (f", 'preserve_local_history',true, 'scope_server_name',{server}"
              if preserve_local_history else
              (f", 'scope_server_name',{server}" if server_name != "reilly.asia" else ""))
    return f"""WITH retained AS (
      SELECT DISTINCT s.room_id FROM current_state_events s JOIN room_memberships m USING(event_id)
      WHERE s.type='m.room.member' AND right(s.state_key,length({server})+1)=':' || {server} AND m.membership='join'
      {ledger})
      SELECT json_build_object(
        'event_rows',(SELECT count(*) FROM events),
        'stream_high_water',(SELECT max(stream_ordering) FROM events),
        'exact_retained_room_scope',(SELECT coalesce(json_agg(json_build_object('room_id',r.room_id,'room_version',coalesce(r.room_version,'1')) ORDER BY r.room_id),'[]'::json) FROM rooms r JOIN retained USING(room_id)),
        'retained_event_rows',(SELECT count(*) FROM events JOIN retained USING(room_id)),
        'retained_room_event_rows',(SELECT coalesce(json_object_agg(room_id,event_rows ORDER BY room_id),'{{}}'::json) FROM (SELECT retained.room_id,count(events.event_id) AS event_rows FROM retained LEFT JOIN events USING(room_id) GROUP BY retained.room_id) counted),
        'local_user_rows',(SELECT count(*) FROM users WHERE right(name,length({server})+1)=':' || {server})
        {policy});"""


class Collector:
    def __init__(self, kubeconfig, context, namespace, pod, log_directory=None):
        self.prefix = ["kubectl", "--kubeconfig", kubeconfig, "--context", context,
                       "-n", namespace, "exec", "-i", pod, "--", "psql", "-X", "-U", "postgres"]
        self.log_directory = Path(log_directory) if log_directory is not None else None
        self.log_counter = 0
        if self.log_directory is not None:
            self.log_directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def private_log(self, text):
        self.log_counter += 1
        if self.log_directory is None:
            return tempfile.TemporaryFile()
        target = self.log_directory / f"query-{self.log_counter:04d}-{hashlib.sha256(text.encode()).hexdigest()[:12]}.stderr.log"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        return os.fdopen(fd, "wb")

    def sql(self, database, text):
        # Exec websocket failures can truncate even a successful remote query.
        # Every attempt is a fresh read-only query with private stderr; never
        # combine partial output from different attempts.
        for attempt in range(3):
            try:
                return self._sql_once(database, text)
            except (RuntimeError, json.JSONDecodeError, UnicodeDecodeError, BrokenPipeError):
                if attempt == 2:
                    raise

    def _sql_once(self, database, text):
        log = self.private_log(text)
        process = subprocess.Popen(self.prefix + ["-d", database, "-Atq", "-v", "ON_ERROR_STOP=1"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        log.close()
        process.stdin.write(("SET timezone='UTC'; SET datestyle='ISO,YMD'; SET default_transaction_read_only=on; " + text).encode())
        process.stdin.close()
        value = bytearray()
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            if len(value) + len(chunk) > 8 * 1024 * 1024:
                process.terminate()
                process.wait(timeout=15)
                raise RuntimeError("database marker metadata exceeded bounded limit")
            value.extend(chunk)
        process.stdout.close()
        if process.wait():
            raise RuntimeError("database marker query failed; details suppressed")
        if not value.endswith(b"\n"):
            raise RuntimeError("database marker result ended before its complete output line")
        return json.loads(value)

    def digest_table(self, database, table, expected_rows=None):
        for attempt in range(3):
            try:
                result = self._digest_table_once(database, table)
                # A websocket may end at a complete line boundary with exit0.
                # The independently collected frozen count detects that case.
                if expected_rows is not None and result["rows"] != expected_rows:
                    raise RuntimeError("domain digest row count differs from frozen table count")
                return result
            except (RuntimeError, BrokenPipeError):
                if attempt == 2:
                    raise

    def _digest_table_once(self, database, table):
        # COPY only fixed-size row hashes, never source credentials or plaintext.
        # The server can spill sorting to its normal temp files; local memory is
        # fixed at 64KiB regardless of the event corpus or encrypted MAS rows.
        sql = ("SET timezone='UTC'; SET datestyle='ISO,YMD'; SET default_transaction_read_only=on; "
               f"COPY (SELECT row_digest FROM (SELECT encode(sha256(convert_to(to_jsonb(row_value)::text,'UTF8')),'hex') row_digest FROM public.{quote_ident(table)} row_value) hashed_rows ORDER BY row_digest COLLATE \"C\") TO STDOUT;")
        log = self.private_log(sql)
        process = subprocess.Popen(self.prefix + ["-d", database, "-Atq", "-v", "ON_ERROR_STOP=1"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        log.close()
        process.stdin.write(sql.encode())
        process.stdin.close()
        digest, count, size = hashlib.sha256(), 0, 0
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            digest.update(chunk)
            count += chunk.count(b"\n")
            size += len(chunk)
        process.stdout.close()
        if process.wait() or size != count * 65:
            raise RuntimeError("bounded domain digest failed")
        return {"rows": count, "sorted_row_sha256_stream_sha256": digest.hexdigest()}

    def collect(self, database, synapse=False, *, server_name="reilly.asia", preserve_local_history=False):
        metadata = self.sql(database, """SELECT json_build_object(
          'database_bytes',pg_database_size(current_database()),
          'postgres_version_num',current_setting('server_version_num')::int,
          'encoding',pg_encoding_to_char(encoding),'lc_collate',datcollate,'lc_ctype',datctype,
          'locale_provider',datlocprovider,'icu_locale',daticulocale)
          FROM pg_database WHERE datname=current_database();""")
        tables = self.sql(database, "SELECT coalesce(json_agg(tablename ORDER BY tablename),'[]'::json) FROM pg_tables WHERE schemaname='public';")
        schema = self.sql(database, """SELECT json_build_object(
          'columns',(SELECT json_agg(row_to_json(c) ORDER BY c.table_name,c.ordinal_position)
            FROM (SELECT table_name,ordinal_position,column_name,udt_schema,udt_name,is_nullable,column_default
              FROM information_schema.columns WHERE table_schema='public') c),
          'constraints',(SELECT json_agg(row_to_json(c) ORDER BY c.table_name,c.name)
            FROM (SELECT cls.relname table_name,con.conname name,pg_get_constraintdef(con.oid) definition
              FROM pg_constraint con JOIN pg_class cls ON cls.oid=con.conrelid
              JOIN pg_namespace ns ON ns.oid=cls.relnamespace WHERE ns.nspname='public') c),
          'indexes',(SELECT json_agg(row_to_json(i) ORDER BY i.tablename,i.indexname)
            FROM (SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public') i));""")
        if synapse and not REQUIRED <= set(tables):
            raise RuntimeError("required Synapse source domains absent")
        counts = {}
        for table in tables:
            counts[table] = self.sql(database, f"SELECT to_json(count(*)) FROM public.{quote_ident(table)};")
        domains = list(SYNAPSE_DOMAINS) if synapse else tables
        if synapse and preserve_local_history:
            if "local_current_membership" not in tables:
                raise RuntimeError("preservation membership ledger absent")
            domains.append("local_current_membership")
        digests = {table: self.digest_table(database, table, counts[table])
                   for table in domains if table in tables}
        semantic = {"table_counts": counts, "domains": digests,
                    "schema_sha256": hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        semantic["sequences"] = self.sql(database, """SELECT coalesce(json_agg(row_to_json(s) ORDER BY s.sequencename),'[]'::json)
          FROM (SELECT sequencename,start_value,min_value,max_value,increment_by,cycle,cache_size,last_value
            FROM pg_sequences WHERE schemaname='public') s;""")
        if synapse:
            semantic.update(self.sql(database, scope_sql(server_name, preserve_local_history)))
        return {"metadata": metadata, "semantic": semantic}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kubeconfig", required=True)
    p.add_argument("--context", required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("--pod", required=True)
    p.add_argument("--synapse-db", required=True)
    p.add_argument("--mas-db", required=True)
    p.add_argument("--private-log-dir", required=True, type=Path)
    p.add_argument("--server-name", default="reilly.asia")
    p.add_argument("--preserve-local-history", action="store_true")
    args = p.parse_args()
    os.umask(0o077)
    try:
        collector = Collector(args.kubeconfig, args.context, args.namespace, args.pod, args.private_log_dir)
        result = {"schema_version": 1, "captured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "synapse": collector.collect(args.synapse_db, synapse=True, server_name=args.server_name,
                                               preserve_local_history=args.preserve_local_history),
                  "mas": collector.collect(args.mas_db), "hash_method": "sha256(sorted C-collation sha256(UTF8 to_jsonb(row)::text) lines); UTC ISO dates",
                  "domain_stream_buffer_bytes": 65536, "source_payload_exposed": False}
        print(json.dumps(result, sort_keys=True, indent=2))
    except Exception:
        raise SystemExit("Source marker collection failed; credentials and payloads suppressed") from None


if __name__ == "__main__":
    main()

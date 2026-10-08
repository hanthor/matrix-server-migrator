# Matrix homeserver migrator

A shared migration core, terminal UI, web dashboard, and operator CLI.
`reilly.asia` is the prototype; JSON plans configure other servers without
changing either UI. The current connector implements **Synapse PostgreSQL
to Spindle**. Additional homeserver connectors extend the `Source`/`Target`
traits, configuration enums, and executor.

Inventory runs in a read-only, repeatable-read transaction. Rooms without
local joined members are outside the retained scope and listed with reasons.
The importer owns room replay, media, checkpoints, and resumability.

## Configure a migration

Start with [examples/synapse-to-spindle.json](examples/synapse-to-spindle.json).
The Spindle config must name the same server as the source and point at the
intended target store. Keep database credentials in the environment.

```sh
export MIGRATOR_PLAN="$PWD/examples/synapse-to-spindle.json"
export MIGRATOR_PG_CONN='host=127.0.0.1 port=5432 user=postgres dbname=synapse'
export MIGRATOR_BINARY=/usr/local/bin/spindle
export MIGRATOR_CONFIG=/etc/spindle.toml
export MIGRATOR_CHECKPOINT=/var/lib/migrator/report.json
export SPINDLE_SYNAPSE_SIGNING_KEY_FILE=/secure/synapse-signing.key
```

`MIGRATOR_SERVER_NAME` and `MIGRATOR_MEDIA` override the plan's source name
and media directory. Without `MIGRATOR_PLAN`, the reilly.asia skeleton is
loaded. Plan displays and argv previews redact the database connection.
Use a separate checkpoint for dry runs: the importer refuses to mix writing
and dry-run checkpoints. Logs append to `report.run.log` beside `report.json`.

## Operator CLI

```sh
cargo run -p migrator-cli -- plan
cargo run -p migrator-cli -- inventory
cargo run -p migrator-cli -- preview
cargo run -p migrator-cli -- dry-run
cargo run -p migrator-cli -- import
cargo run -p migrator-cli -- validate
cargo run -p migrator-cli -- report /var/lib/migrator/report.json 116
```

`import` writes the target. `validate` performs fresh read-back and checks
the exact room IDs, server, and per-room source event counts against live
plan inventory captured before and after execution. A changed count rejects a stale checkpoint even when the
new messages leave room state unchanged. Counts do not prove all source
values are unchanged: freeze the source across import, resume, and validation. `report` checks
saved evidence with a supplied expected count; it does not establish that
the source or target has remained unchanged since the report was written.

Use a passwordless `MIGRATOR_PG_CONN` or plan connection string and provide the database password through `SPINDLE_SYNAPSE_PASSWORD`. Inventory applies this environment override and backend programs inherit it. Executable argument construction rejects inline passwords in keyword strings and PostgreSQL URLs with a static actionable error; connection parse errors never echo the connection or secret.

Set `"preserve_local_history": true` in a plan, or `MIGRATOR_PRESERVE_LOCAL_HISTORY=true`, to include current joins and every room with a latest local membership record, including invites and departed history. The environment override accepts only `true`, `false`, `1` or `0`; old plans default to joined-only selection. This opt-in passes `--preserve-local-history` to a compatible backend. Use a separate checkpoint: its mode must exactly match the plan for execution, fresh validation and saved CLI/Web report checks. Preservation also requires complete, mismatch-free `local_memberships` and `forgotten_rooms` import/read-back domains; zero-row domains are valid. The backend fails unsupported membership cases rather than silently dropping them. Live read-only verification on 2026-10-06 returned 116 default rooms versus 249 preservation rooms, with zero scope differences from the joined-or-latest-local-membership rule.

Inventory exclusion reasons include raw source event counts, latest local invites/leaves, and historical locally sent message/encrypted event counts, using batched metadata queries without event-body loads. Default selection requires a local joined member. A read-only repeatable-read comparison on 2026-10-06 found zero differences from the previous discovery query in room IDs, versions, event counts or joined-member counts (116 selected rooms, 1,411,251 source events). The 133 excluded rooms held 453,620 raw events, 2 latest local invites, 199 latest local leaves, 514 local message events and 5,553 local encrypted events; 60 had locally sent history. These counts describe excluded history without adding it to the plan.

The shared gates reject failed processes, dry runs, missing validation,
missing/excluded rooms, state divergence, short logs, event sample or domain
mismatches, missing domain checks, rejection policies other than v3, and an
unimported source signing key. Media read-back must check at least as many
source files as were imported; an absent or incomplete source media mount
cannot certify those imported files. Zero-row source domains are valid when their
read-back check is present. These are import gates; cutover also needs a
backup restore, actual signing-key continuity, a client E2EE witness, and
federation checks.

## Terminal and web UI

```sh
cargo run -p migrator-tui
cargo run -p migrator-web
```

The TUI uses `1/2/3` for plans/inventory/run, `x` for a dry run, `v` for fresh
validation, and `q` to quit after the current operation finishes. Inventory
and execution run in the background so screens remain responsive.

The web dashboard is at `http://127.0.0.1:8471`. Its buttons read inventory,
start dry runs or fresh validation, and inspect checkpoints. Set
`MIGRATOR_EXPECTED_ROOMS` to enable saved-checkpoint inspection. The API uses
the same configured plan and environment; explicit request fields may
override execution settings. It allows one job at a time, reports importer
failure accurately, and returns 409 for a concurrent job.

- `GET /api/plan`: redacted plan.
- `GET /api/inventory`: live read-only inventory.
- `POST /api/inventory/run`: same inventory, retained for compatibility.
- `POST /api/run`: `{"dry_run":true}` or `{"validate_only":true}`.
- `GET /api/run/{id}`: current job result.
- `GET /api/report`: saved checkpoint evidence and gate failures.

Run IDs are in-memory dashboard state. Checkpoints and logs remain on disk;
after restarting the dashboard, inspect the checkpoint rather than expecting
old run IDs to survive. CLI/TUI/web execution paths and the database must be
accessible from the host running the migrator.

## Recovery after cutover

The current `56db7d0` production candidate imports joined rooms. The isolated
preservation candidate supports departed history only for a fresh target; it
has not rehearsed the actual departed-room corpus. Do not run the full importer
against a live server to recover old rooms: even a room filter still imports
global account, device and encryption data. `--allow-nonempty` is not a safe
merge mode. Room-only recovery and coalescing are not implemented; see the
[departed-room recovery design](docs/departed-room-recovery-design.md).

## reilly.asia rehearsal continuation

[scripts/finish-reilly-rehearsal.py](scripts/finish-reilly-rehearsal.py)
continues the handed-off Kubernetes import. It waits for the retry to exit,
requires all 116 rooms, imports the source signing key, refreshes the receipts
and directory phases that skipped the excluded room, and validates twice.
It streams a cold archive of the stopped store, media, checkpoint, config,
key, and patched binary off-cluster, checks its SHA-256, extracts a scratch
restore, validates that restore, and checks server readiness and the original
public signing key with the final candidate's offline key loader. Federation
stays disabled on the imported clone. Restore validation uses the exact final
production executable; source refresh uses the preserved retry executable.
The off-cluster transfer uses verified S3 streaming by default. An explicit
local transfer requires sufficient host disk headroom. Native backup currently materializes every row in memory;
the cold archive avoids that allocation for this large corpus.

The running user service is `reilly-rehearsal-finish-head-56db7d0.service`:

```sh
systemctl --user status reilly-rehearsal-finish-head-56db7d0
cat artifacts/reilly-2026-10-06/status.json
```

Completion uses the tested Linux `pidfd` helper in `scripts/wait-import.c`,
so follow-up starts when the importer exits, including an unreaped zombie.
Hosts without the helper retain a 15-minute fallback. Domain refresh skips
validation; one explicit original-store validation follows it. Private artifacts
are excluded from version control. A failure records its phase and reason;
it never waives a gate or changes production routing. See
[docs/cutover-reilly-asia.md](docs/cutover-reilly-asia.md) for the remaining
client, federation, and production cutover work.

Parallel gate work is documented in [docs/client-witness.md](docs/client-witness.md)
and [docs/federation-drill.md](docs/federation-drill.md). Retained test accounts
and recovery material belong to the separate `synapse_dark` fixture database;
the client witness adds only its four rooms and three users to a second
disposable copy after the base 116-room restore passes. Synthetic federation
checks passed for both the retry and final production binaries in fresh isolated stores.

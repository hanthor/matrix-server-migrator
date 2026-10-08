# Existing-history client witness

The rehearsal must demonstrate a real imported account can log in on the
scratch restore, retain its cross-signing identity, recover keys, and decrypt
selected **pre-migration events to the original plaintext hashes**. A GET of
`m.room.encrypted`, matching database counts, and the four-room synthetic
fixture prove different properties; none establishes this history gate.

Preparation is complete; no witness against the 116-room corpus is claimed.
Coordinate with the migration worker before opening the restored store at
`/work/reilly-restored-20261006`. Use a separate witness copy of the restore:
login, sync, key recovery, and device verification can write account/device
state, so even browser checks must use that disposable copy. Do not restart
or mutate `/work/spindle-data`, switch routing, reset a real identity, or
modify the source MAS user/password.

## Inputs and reference

Supply an actual imported user ID and a recovery route: its recovery key,
or an existing verified client/crypto store. This browser runner implements
fresh password login plus recovery key; an existing-session witness needs
the preserved device store, and source MAS/OIDC accounts need the existing
dark MAS integration rather than an invented local password. Keep secrets
in private files; do not paste them in chat or put them in command arguments.

Choose several ordinary unedited encrypted text events already readable by
that user on the original client, ideally across old/recent sessions and
multiple rooms. Record original event IDs, room IDs, exact decrypted `body`,
and provenance (client, time, selection method). The trusted source reference
must predate the target read; deriving expected hashes from the restored
copy would make the check circular. Obtain original public cross-signing keys
from the source's `/keys/query` response for the same user. Querying public
keys is read-only. No source login, backup creation, or password mutation is
needed if the owner's existing client session is available.

The private reference JSON has this structure (use real original values):

```json
{
  "user_id": "@user:reilly.asia",
  "provenance": "Original verified client; source timestamp; selected old and recent messages",
  "source_keys_query": {
    "master_keys": {"@user:reilly.asia": {"keys": {"ed25519:ID": "PUBLIC_KEY"}}},
    "self_signing_keys": {"@user:reilly.asia": {"keys": {"ed25519:ID": "PUBLIC_KEY"}}},
    "user_signing_keys": {"@user:reilly.asia": {"keys": {"ed25519:ID": "PUBLIC_KEY"}}}
  },
  "decrypted_events": [{
    "room_id": "!room:server", "event_id": "$event",
    "original_type": "m.room.encrypted", "type": "m.room.message",
    "content": {"msgtype": "m.text", "body": "EXACT TRUSTED BODY"}
  }]
}
```

Use the trusted client to export this data; server API reads return ciphertext
and cannot fill decrypted `body`. For formatted text, edits, replies, or media,
use an SDK event-content witness instead of assuming Element's rendered body
equals the JSON body.

```sh
umask 077
python3 scripts/client-witness/make_manifest.py \
  --reference /private/client-witness/source-reference.json \
  --output /private/client-witness/manifest.json
python3 scripts/client-witness/preflight.py \
  --manifest /private/client-witness/manifest.json --validate-only
```

## Disposable restore and Element

After the worker's restore evidence passes, root/operator starts a separate
client witness server against a second disposable copy and forwards its
client listener to `127.0.0.1:18508`. Pin Element to that URL and server name
`reilly.asia`, disable custom URLs/guests/registration, and remove external
integration/telemetry endpoints. Serve that separate Element directory on
`127.0.0.1:18509`. Do not reuse or overwrite the running synthetic lab's
config. The preserved Element Web v1.12.28 assets are at
`/tmp/spindle-live-element-rig/element-v1.12.28`; Playwright is installed at
`/home/ubuntu/.cache/ui-smoke/node_modules/playwright-core`. Both are ephemeral
local prerequisites and should be copied/installed explicitly elsewhere.

Copy `scripts/client-witness/config.example.json` into a private directory and
set the manifest/password/recovery-key paths. Set `login_mode` to `mas` only
with a properly isolated existing dark MAS; add its
`*.spindle-rehearsal.svc.cluster.local` hostname to `allowed_lab_hosts`. The
browser blocks every other network hostname and uses a fresh empty profile.
The MAS runner signs in using the supplied password; it never resets one.

```sh
node scripts/client-witness/element.cjs /private/client-witness/config.json --check-preparation
node scripts/client-witness/element.cjs /private/client-witness/config.json
```

The runner requires successful recovery UI completion and every selected
event's rendered body SHA-256 to equal the original trusted hash. It writes
only event IDs/outcomes; no screenshots, response bodies, traces, passwords,
recovery keys, or decrypted bodies. A hash mismatch fails even if the event
renders without an unable-to-decrypt error. Unsupported UI selectors fail
closed and may need adapting for another Element release.

## Client-surface and identity preflight

Obtain the **witness client's** access token into a private file from its
session. Run this against the disposable target on loopback (never a public
production URL):

```sh
python3 scripts/client-witness/preflight.py \
  --base-url http://127.0.0.1:18508 \
  --token-file /private/client-witness/witness-token \
  --manifest /private/client-witness/manifest.json \
  --output /private/client-witness/preflight.json
```

It checks `whoami`, joined sample rooms, unchanged public master/self-signing/
user-signing keys, and exact event-ID reads of Megolm encrypted events. Its
output intentionally sets `history_decryption_pass: false`: combine it with
the browser results, matching account/device/session evidence and the
operator's verification that this is the stopped-store-derived witness copy.
The browser alone intentionally sets `full_cutover_gate_pass: false` too.
Successful login plus recovery UI is a device-grant/recovery witness; record
the granted device ID and verify device trust in Element before calling the
full cross-signing/device gate complete. Browser recovery can initialize a
new identity on a misconfigured server, which is why the independent source
public-key continuity check is required.

## Existing rigs and limits

### Production MAS and existing-session continuity

Production delegates login to MAS. Preserve its database, public issuer
`https://auth.reilly.asia/`, existing signing/encryption secrets and
`ess-generated/MAS_SYNAPSE_SHARED_SECRET`. The runtime config needs
`[auth.delegated]` with that issuer, internal introspection URL
`http://ess-matrix-authentication-service.ess.svc.cluster.local:8080/oauth2/introspect`,
and the mounted shared secret. Keep legacy `/login`, `/logout`, and `/refresh`
on MAS; do not route those to the local-password fixture listener. Existing
stable and unstable MAS session scopes, device IDs (including `+` and `/`),
and device grant/provisioning must continue working. The previous dark MAS
rig proved this for synthetic users; it did not retain a live production user
access token. A fresh production read-only user inventory found no accounts
matching `spindle-mig`, `spindle-mas`, `witness`, or `drill`.

The preserved production base config and memory-backed Secret renderer are
at `/tmp/spindle-final-production-config/{runtime-base.toml,render-config.py}`;
they are prior drafts, so root must compare them with current production
configuration and selected binary before use. Original session preservation
depends on keeping the source MAS identity, not restoring a synthetic dark
MAS over production.

If an existing session token is available privately from an owner's client,
`native_session.py` provides a read-only before/after comparison through
loopback forwards. No password, refresh, logout or new session is needed:

```sh
python3 scripts/client-witness/native_session.py capture \
  --base-url http://127.0.0.1:SOURCE_FORWARD \
  --token-file /private/existing-mas-token \
  --output /private/source-session-baseline.json
python3 scripts/client-witness/native_session.py check \
  --base-url http://127.0.0.1:TARGET_FORWARD \
  --token-file /private/existing-mas-token \
  --baseline /private/source-session-baseline.json \
  --output /private/target-session-continuity.json
```

It requires the same user/device to authenticate, the granted device to exist,
and identical device keys, public cross-signing identities, joined-room IDs,
and key-backup metadata. Collect the baseline at the final frozen source to
avoid normal ongoing room/backup activity producing a comparison mismatch.
It remains separate from decryption proof. Current production MAS token
database hashes cannot reconstruct an existing client token. If root chooses
to create a production canary as part of authorized cutover work, label its
proof as a new canary session, not an existing owner's session.

The isolated delegated-auth proof completed on 2026-10-06 against final
`8e40719` (SHA-256 `4036a6073191eea49a8bfc58af4b1dbef096774c89cbabf6569ef158b3bba092`).
`scripts/client-witness/delegated_mas.py` used a new copy of the preserved
candidate fixture store and the existing isolated `mas_dark`/dark MAS secrets.
It issued exactly two owned fixture personal sessions with stable/unstable
scopes, uploaded valid signed keys to those two new fixture devices, captured
identity/device/public-key/backup/room evidence under preserved `5eb4452`,
cold-switched to final `8e40719`, and required exactly the same tokens and
snapshots to pass. Both owned sessions were revoked, both isolated deployments
returned to their original commands/images with replicas zero, and cleanup
passed. Status/evidence: `artifacts/reilly-2026-10-06/delegated-mas-status.json`
and `delegated-mas-1791292383/`.

This demonstrates the final server accepts already-issued MAS sessions and
preserves the user/device/key mapping through its delegated introspection
path. The accounts are synthetic fixtures in a restored dark MAS; this does
not claim an existing production owner's token was obtained, nor that actual
production encrypted history decrypted. The separate SDK/Element witness
remains queued on the verified base116 restore.

The supervised continuation script `scripts/client-witness/finish_witness.py`
waits once per minute for the migration status to be complete/passed with phase
`rehearsal_store_verified`. It checks the base116 scratch server has exited,
cold-copies that store to its separately named `--work` directory, imports
only the four fixture rooms/three users with its own checkpoint, starts its
own loopback-only server, and runs all three retained SDK accounts plus the
strict Element fixture-b witness. Required `--binary`, `--expected-sha256`,
`--output`, and `--work` arguments pin the executable and isolate evidence.
Every status poll checks the base candidate SHA, including successful statuses;
the remote executable and completed cold-copy marker must match that SHA.
Its private status is `client-witness-status.json` beneath `--output`.
It stops on any failure, preserves
evidence and shuts down its own listener/forwards. It never modifies the
original store, original checkpoint, or validated base116 scratch copy.

Cold copying has a completion marker, the additive import has a separate
checkpoint, and each Element retry uses a fresh evidence directory. On a
failed run, inspect the status and private import/SDK reports before restarting
the unit. An unexpectedly live previous witness server blocks restart instead
of opening its store concurrently. This continuation can complete while the
operator is away; it does not authorize production routing or cutover.

The joined-room head repair is queued as
`reilly-client-witness-head-56db7d0.service`, using
`/work/bin/spindle-head-56db7d0` with SHA256
`0316f922957ae968e5a65e28f59cdc233841e6b764d42aae54187b8749921a18`.
Its new clone is `/work/reilly-client-witness-head-56db7d0/store` and evidence
directory is `artifacts/reilly-client-witness-head-56db7d0`. It reuses the
preserved private fixture material and SDK executable, with no compilation.

Exact joined-candidate delegated MAS proof passed on 2026-10-06 for
`56db7d0`, SHA256 `0316f922957ae968e5a65e28f59cdc233841e6b764d42aae54187b8749921a18`.
Evidence is `artifacts/delegated-mas-head-56db7d0/delegated-mas-status.json`.
Two owned personal sessions used stable and unstable Matrix API/device scopes;
the same tokens survived the baseline-to-candidate cold restart with unchanged
user/device mapping, signed device keys, public cross-signing, backup metadata
and joined-room identities. The owned sessions were revoked; both isolated
deployment specs were compared with their originals and restored exactly at
replicas zero (`restoration-verification.json`). The source fixture store was
copied separately, federation disabled, and production unchanged. This proves
the selected binary's isolated delegated-session handling, not an actual
production owner's existing token or encrypted-history decryption.

Cold large-room runtime preparation (not executed): the next exact candidate
can select a newly tagged validated base via `--base-status`, `--base-store`
and `--base-pidfile`, with a new output/clone and explicit binary SHA.
`--cold-room '!iMZEhwCvbfeAYUxAjZ:t2l.io' --cold-max-seconds 120` runs
`cold_http.py` before SDK/Element or any other room reads on a fresh process.
It logs in only fixture-a on an owned disposable device and checks that this
real room is absent from its joined index. A GET of the room's create state
forces room reconstruction through the authorization summary before a stranger
403 (or reads public create content if world-readable). A repeat measures hot
latency. Concurrent reads of an owned fixture room's create state and `/ready`
measure other-room contention and readiness; overlap is reported explicitly.
Only timings, status, error code and response hashes are recorded. The owned
device logs out; there are no real-room writes or membership grants.

120 seconds is a diagnostic ceiling. The separate production readiness
objective is 30 seconds for cold response and concurrent probes; the report
always leaves `production_latency_accepted=false` for the coordinator's
decision. A denied HTTP probe does not prove real head/membership/history
contents: new full native original/cold-store validation and the exact historical
PDU witness must establish those independently. Existing SDK/Element/cursor
checks still follow the diagnostic. Mocked protocol/security/cleanup checks
and Python compilation passed; no next-candidate proof is claimed yet.

Exact seeded-cold candidate `2044cb5d2e368d74d26d911d3915ae31fd3403a3`
delegated MAS proof passed on 2026-10-07, binary SHA256
`c3485f89eef2a015faa8745d56db6d838cf74da8f9eb4ce3b26a23a1619d80ff`.
Evidence: `artifacts/delegated-mas-seeded-2044cb5-20261007/delegated-mas-status.json`.
Both stable and unstable owned sessions retained exact identity/device/key/
cross-signing/backup/joined-room mappings after baseline-to-candidate restart.
Owned sessions were revoked, cleanup passed and both isolated deployment specs
were independently verified restored exactly at replicas zero. This does not
claim actual production-owner session or history recovery. New full original/
cold-store and SDK/Element/cold-HTTP proofs remain separate pending gates.

The current queued client unit is
`reilly-client-witness-seeded-2044cb5-20261007.service`, using exact2044 SHA above,
root status `artifacts/reilly-rehearsal-seeded-2044cb5-20261007/status.json`,
base store `/work/reilly-restored-seeded-2044cb5-20261007` and base PID file
`/work/reilly-restore-server-seeded-2044cb5-20261007.pid`. Its own clone/output
use `reilly-client-witness-seeded-2044cb5-20261007`. The cold huge-room/concurrent
probe is requested. It remains waiting for verified root cold restore; no
client/cold-HTTP pass is claimed. The older56 worker failed on the superseded
root wait gate before any cold clone or server started; its status is preserved.

Actual2044 fixture diagnostics passed separately on 2026-10-07 under
`reilly-fixture-sdk-element-2044cb5-20261007.service` (exit0, runtime stopped).
SDK a/b/c recovered and verified89/89,89/89 and59/59 readable event hashes,
with own devices cross-signed. Element b verified73 original plaintext hashes.
Actual precursor checks rebuilt4joined rooms/38state events from foreign
Synapse sync tokens, rejected2foreign sliding positions with400M_UNKNOWN_POS,
and matched9source pagination anchors. Evidence:
`artifacts/reilly-fixture-sdk-element-2044cb5-20261007/`.

The actual real-huge-room cold request on corrected R3 timed out; concurrent
timings were not retained by that earlier helper. This is a blocking runtime
performance failure, not waived by the separate fixture crypto pass. Its
owned inert process required root-authorized PID/exe/start-time guarded kill
after TERM45; private proc/log/config/checkpoint metadata and hashes remain
under `artifacts/reilly-client-witness-seeded-2044cb5-20261007-r3/`. No clone
was deleted and the source/base stayed unchanged. Future helper failures retain
all labeled cold/concurrent/ready/logout timings and the primary failure.

Two initial harness failures are preserved privately: missing known-fixture
passwords during additive import, and missing system public CA certificates in
the importer image. Fresh fixture imports now supply only owned fixture
passwords through `SPINDLE_REHEARSAL_PASSWORD_DIR` with private localpart files;
production credentials are unaffected. SDK launches explicitly use a SHA-verified
public CA bundle through `SSL_CERT_FILE`. The approved production runtime base
independently has150public trust roots; no external TLS handshake is claimed
by that availability check. The successful fixture run reused the corrected
stopped R3 via `--reuse-imported-fixture`, without another clone/import or any
real-room read. Its `full_cutover_gate_pass` remains false. Successor runtime
acceleration and exact-candidate cold/crypto/MAS proofs are pending.

Final combined runtime26496c9 delegated MAS proof passed for SHA256
`83e59309f913b16b682f78bdc66d122664daa0e514a4ac30af1111bb3e1b8d86`.
Evidence: `artifacts/delegated-mas-runtime-26496c9-20261007/`. Stable/unstable
owned sessions retained all compared identity/device/key/backup/room mappings;
they were revoked and isolated deployment specs restored exactly at zero.
The client unit `reilly-client-witness-runtime-26496c9-20261007.service` waits
for exact tagged root native/cold proof before making its unique clone and
running mandatory cold/concurrent diagnostics plus SDK/Element/cursors.
Actual final cold HTTP diagnostic passed: first large-room create-state read
returned 200 in 24.589102s; warm read returned the identical response hash
in 0.388241s. Concurrent fixture state and readiness returned 200 in 24.541934s
and 24.539268s, both delayed almost the full cold duration. All meet the 30s
objective, but `production_latency_accepted=false` remains for coordinator
assessment. SDK passed a 89/89, b 89/89 and c 59/59 expected readable hashes,
recovery and own-device cross-signing. The first Element attempt timed out
opening a history view; its failure remains recorded. A separate fresh-process
Element-only retry on the same clone passed 73/73 hashes plus predecessor
cursors (foreign sync rebuilt 4 rooms/38 state events, two foreign sliding
positions rejected 400 M_UNKNOWN_POS, nine source pagination anchors matched).
No source import, cold diagnostic or SDK checks were repeated for that retry.
Evidence: `element-only-retry-status.json` and
`client-witness-element-retry-1791364431/` under the final client artifact folder.
Native/historical proofs independently establish real head/history content.

A separate approved `TOKIO_WORKER_THREADS=4` proof reused the same stopped
final clone, with no recopy/source import. It warmed only an unrelated fixture
room before measuring: huge-room fresh-process read 200 in 1.975182s, warm read
200 in 0.375513s with unchanged response hash. Concurrent warmed fixture state
and readiness returned 200 in 1.927942s and 1.923721s on separate TCP connections.
Four Tokio worker threads and 2 CPU quota were observed. They still completed
almost alongside the huge-room request. This is a restart on a previously read
store: OS cache or durable runtime materialization may also explain improvement,
so it does not isolate the effect of the worker override. The same env4 run
passed SDK a 89/89, b 89/89, c 59/59, Element 73/73 and predecessor cursors, then
exited 0 with guarded runtime shutdown. Acceptance remains false for coordinator
assessment. Evidence is under
`artifacts/reilly-client-witness-runtime-26496c9-env4-20261007/`.

The failed stopped2044 R2 clone was retired under explicit authorization after
fresh full S3 archive reread, retained metadata hashes, canonical inode, stable
pod UID, exact zombie PID/start tick, and no live argv/FD/cwd references passed.
Evidence and private metadata remain under
`artifacts/reilly-client-witness-seeded-2044cb5-20261007-r2/`. Actual filesystem
free space increased 101,318,656 bytes; apparent clone size was 2,095,165,440 bytes.
Passed R3, all original/cold root stores and source backups remain retained.

- `spindle-rehearsal-final/scripts/migration-mas-rig` and
  `/tmp/spindle-live-element-rig` automate Element Web + MAS against a synthetic
  four-room fixture. Existing browser evidence applies to that fixture.
- `spindle-rehearsal-final/scripts/federation-drill/e2ee` has a matrix-sdk
  0.18 `check --hs ... --relogin` command that verifies event plaintext hashes
  after backup recovery. It needs its own matching original `meta.json`,
  `recovery.key`, device store and JSONL manifest. Do not run `prepare
  --recovery` on an imported user: that enables/changes recovery and creates
  new reference state. A trusted preserved state may be copied privately
  before using `check` against a disposable restore.
- `/tmp/spindle-final-client-witness-after-import.py` schedules a different
  synthetic client API witness; it supplies no imported-history plaintext.

### Preserved test accounts confirmed on 2026-10-06

Read-only PostgreSQL checks confirmed `@spindle-mig-a/b/c:reilly.asia` exist in
`synapse_dark`, and none of these users exist in the current `synapse` snapshot
being imported into `/work/spindle-data`. Secret `spindle-mig-rig` retains all
three passwords and recovery keys. ConfigMap `spindle-mig-rig-manifest` has
four rooms with 88 encrypted events and their original hashes/readability.
The toolbox retains `/work/bin/mig-rig`, `/work/out/manifest.json`, verified
reports and original crypto stores at `/work/out/stores/{a,b,c}`. This is enough
to repeat a real client key-recovery test for the **preserved synthetic
fixture**, without asking the owner for their personal recovery key.

To reuse it with the current imported store, first make a **second disposable
copy** after the worker completes backup/restore validation. Into that second
copy only, import `synapse_dark` with `--allow-nonempty`, a separate checkpoint,
`--users @spindle-mig-a:reilly.asia,@spindle-mig-b:reilly.asia,@spindle-mig-c:reilly.asia`,
and `--rooms` restricted to the four IDs in the ConfigMap. The historical
`fi-rig-import` Job used exactly this additive approach. Its four room IDs are
`!Dob38JgXu0oXeH1bFP-fVwa578glny_u5Drl-KRQxlg`,
`!gANCZAEzFswJvEzHbY:reilly.asia`, `!fJgacLfsDbkATqEtqO:reilly.asia`, and
`!YWbCmnOrmRNpLNKtft:reilly.asia` (reconfirm against the ConfigMap). Do not merge
fixture data into the validated original or use the 120-room fixture copy as
evidence that the 116-room backup was unchanged.

After the fixture copy starts on a separate ClusterIP service, run:

```sh
python3 scripts/client-witness/fixture_sdk.py \
  --target-url http://DISPOSABLE-WITNESS.spindle-rehearsal.svc.cluster.local:8008 \
  --output /private/client-witness/fixture-run-1
```

For the stricter Element runner, export user b's retained password/recovery
key and trusted message-only manifest into a private directory without any
login or source write:

```sh
python3 scripts/client-witness/fixture_material.py \
  --user b --output /private/client-witness/fixture-material
```

Use those private files in the Element config. The source public cross-signing
digest comes from `synapse_dark`; original plaintext hashes come from the
preserved pre-migration manifest. Recovering on the disposable fixture target
can then be checked against both, with no secrets printed.

This uses mounted secret file paths inside the existing toolbox, captures
reports privately, and requires recovery, own-device cross-signing, and zero
hash-check failures for all three accounts. SDK logout normally cleans up the
temporary device; failed logins/recovery can leave disposable target devices.
It performs no import or server startup and never modifies the source DB.
The witness demonstrates old synthetic encrypted history survived an import
using the current binary alongside the real corpus. It does not independently
demonstrate decryption of the production users' 116-room history.

For that stronger production-history claim, the exact missing inputs are the chosen imported account, its
existing verified session or recovery method, trusted source sample hashes,
trusted source public cross-signing keys, and the disposable restore endpoint
after migration-worker completion. Scripts and example configuration are
ready; no imported-user login or decryption has been executed yet.


Predecessor cursor continuity

The queued final witness now reuses its Element fixture login session in memory for actual HTTP cursor tests, without another account/device login. `prepare_cursor.py` reads retained synapse_dark stream/event positions and serializes them using the isolated Synapse1.156 implementation. These are source-derived genuine Synapse-format cursors, not a captured owner client token; absent/ephemeral stream components are explicitly zeroed in private preparation material. No production or source database write occurs.

`cursor.cjs` requires a foreign legacy /sync since token to return the same joined-room and full initial state-key sets as an initial request. It requires foreign sliding positions (stream-shaped and connection-prefixed) to return400/M_UNKNOWN_POS. For three readable fixture rooms it requires bare topological tokens, old multi-stream prev_batch tokens and stream-shaped prev_batch tokens to paginate back to the exact source anchor event, with identical one-event identity digests. The report contains only counts, hashes and HTTP/error codes; tokens and decrypted event content are not written. The witness uses its existing Element session token only in memory. These HTTP cursor results do not claim an owner's persisted Element cache was restored or their production history decrypted.

Worker updates/restarts are allowed only while client-witness-status.json remains waiting_for_verified_restore and base status is waiting_for_retry. Cursor checks are mandatory in the updated queued gate, and any failure closes the fixture witness gate.

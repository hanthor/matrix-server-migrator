# Federation checks for the patched reilly.asia retry

The candidate is `/home/ubuntu/dev/spindle-artifacts/retry-2026-10-06/spindle`,
SHA-256 `e4fd7d5141f7b97e39910d1fb1f305db355b9c301366650a1f7674d73773db5a`.
Evidence from older drill binaries does not establish this candidate's behavior.

## Completed independent smoke

`scripts/federation-drill/loopback.py` launches two copies of that exact
executable with fresh stores under a newly created private output directory.
Both servers bind to loopback, have `.invalid` synthetic names and fresh keys,
and communicate through explicit loopback peer URLs. No source credentials,
production signing keys, imported stores, DNS changes or cluster changes are
used. Processes are stopped after the run; stores and logs remain for review.
Existing output directories are refused.

The read-only source query found versions `1`, `6`, `9`, `10`, `12`:

```sql
SELECT DISTINCT room_version FROM rooms
WHERE room_id IN (
  SELECT DISTINCT room_id FROM current_state_events
  WHERE type='m.room.member' AND state_key LIKE '%:reilly.asia'
) ORDER BY room_version;
```

This query is a conservative version inventory of rooms with local membership
state; final inventory must be tied to the completed checkpoint's 116 room IDs.
For each version, the smoke creates a room, remotely joins the peer, delivers
one message each way, and observes topic changes from both sides. The fixture
explicitly permits either member to set its topic. V12 has a serverless room ID,
so the join supplies `server_name`.

Run from the migrator directory:

```bash
python3 scripts/federation-drill/loopback.py \
  --binary /home/ubuntu/dev/spindle-artifacts/retry-2026-10-06/spindle \
  --output artifacts/federation-NEW-RUN --tls
```

The TLS mode makes a private CA and leaf certificate with the loopback IP SAN,
uses Spindle's native HTTPS federation listener, and sets `SSL_CERT_FILE` for
each child process. It deletes the CA signing key after issuing the leaf.
HTTP is also available for isolating protocol failures from TLS failures.
Output contains synthetic store keys and TLS leaf keys, so keep the whole
directory private. `result.json` contains no passwords or access tokens.

On 2026-10-06, both runs passed all five versions:

| Run | Evidence | Result |
| --- | --- | --- |
| Explicit HTTP peers | `artifacts/federation-20261006-loopback-r2/result.json` | 5/5 remote joins, bidirectional messages and state |
| Private-CA HTTPS peers | `artifacts/federation-20261006-tls/result.json` | 5/5 remote joins, bidirectional messages and state |

The initial HTTP run in `artifacts/federation-20261006-loopback/` failed due
to fixture permissions and a missing V12 join hint. It is retained separately;
the binary did not change between runs.

## Remaining cutover evidence

This synthetic smoke checks the exact candidate's federation flow across every
source room version. It does not establish Synapse interoperability, public
delegation/routing, continuity of imported history, encrypted client recovery,
or rollback. The cutover gate remains pending those checks.

The existing Synapse witness rig is in
`/home/ubuntu/dev/spindle-drill-refresh/scripts/federation-drill/`; an older
copy is in `spindle-integration`. Its current deployments `drill-a`, `drill-b`
and `drill-resolver` are stopped. The old plan at
`/tmp/spindle-final-drill-plan.json` targets SHA
`c0dfae27ccfbb239c06652a5fd06fc76a2006688051dba0de14c7c7dd2005bb6`, so
its verdict cannot be attributed to this retry. The old driver has destructive
store/database reset and rollback commands. Preserve its previous seals and
results; use fresh resource names, databases, stores and report destinations.

After the root worker confirms full import, backup and restore:

1. Take a second cold copy of the validated scratch restore. Do not federate
   the original imported store or validation scratch in place. Record exact
   candidate SHA, checkpoint SHA, all 116 room IDs and their version inventory.
2. Configure this copy behind a private TLS front and a resolver that answers
   only the witness and lab copies of `reilly.asia` / `matrix.reilly.asia`.
   Restrict peer addresses and monitor outbound destinations. Do not point
   public DNS, ingress, or clients at it.
3. Verify the witness sees the original `ed25519:a_qMqD` public key and the
   key document's signature. Have Synapse verify old-PDU hashes and signatures
   from a sample in each source room version. Record the room IDs and checked
   event IDs, avoiding publication of event contents.
4. Use synthetic accounts and fresh shared rooms on that disposable copy for
   remote joins and both directions of message and state delivery, all five
   versions. Include at least one Synapse-created room and one Spindle-created
   room for each version. Preserve an actual pre/post imported-history witness
   separately from these new fixtures.
5. Require each expected room's result, compare state and newest historical
   events before/after, and inspect queue drain and outbound monitors. Missing
   rooms, skipped versions or absent signature checks fail the gate.
6. Rehearse rollback on the independent synthetic Synapse-to-Spindle rig,
   preserving the sealed database, media and key. Existing scripts can supply
   clients and witness verification, but must use the exact retry executable
   and a newly isolated store; successful old runs do not waive a fresh run.

No real-store federation was started during this independent preparation.

## Fresh Synapse interoperability harness

`scripts/federation-drill/run-synapse.sh` creates one newly named disposable
pod with a fresh shared `emptyDir`. It runs Synapse v1.156.0 using sqlite and
the exact candidate using a pinned cached Rust runtime image on node12.
Both image digests are fixed in the harness, and a deny-all NetworkPolicy
contains traffic to the pod loopback. Each container is capped at
500m CPU; memory caps are 768Mi for Synapse and 512Mi for Spindle. It creates
fresh signing keys and a private CA, uses native HTTPS federation listeners,
and permits only the loopback peer. Cluster DNS is disabled and all client
and federation listeners bind to loopback. Existing pods/output directories
are refused. It preserves a private archive before deleting its own pod.

```bash
bash scripts/federation-drill/run-synapse.sh \
  /home/ubuntu/dev/spindle-artifacts/head-repair-2026-10-06/spindle-head-56db7d0 \
  artifacts/federation-synapse-NEW-RUN reilly-fed-synthetic-NEW-RUN \
  0316f922957ae968e5a65e28f59cdc233841e6b764d42aae54187b8749921a18
```

The witness script tests ten distinct rooms: a Synapse-created and a
Spindle-created room for every source version, remote joins, and messages
and state in both directions. Numeric loopback server names with explicit
federation ports avoid public name resolution. This exercises TLS and
Synapse interoperability; it leaves well-known delegation untested.

On 2026-10-06, the fresh Synapse v1.156.0 run passed **10/10 rooms**, every
remote join and both directions of message/topic delivery, using candidate
SHA `e4fd7d5141f7b97e39910d1fb1f305db355b9c301366650a1f7674d73773db5a`.
Evidence is `artifacts/federation-20261006-synapse-r3/result.json`; the full
private sqlite/store/config/log archive is `pod-artifacts.tar` beside it.
The pod ran on `ip-10-20-1-12`, separate from the original importer node.

Earlier attempts are retained separately. The first harness filename shadowed
the installed `synapse` package and was renamed. The second reached federation
but Synapse's literal-IP path rejected the peer because it directly evaluated
the blocklist. The final fixture computes a blocklist containing all IPv4
addresses except `127.0.0.1/32`, plus all IPv6; this keeps nonpeer addresses
blocked without relying on the separate whitelist exception. No candidate
binary changes were made. These attempts do not count as passing evidence.

Historical imported-PDU continuity preparation (2026-10-06)

`historical-pdu.py select` reads a caller-supplied completed-import room scope
(JSON array of room IDs) in one read-only repeatable-read source transaction.
It selects at most five accepted, nonredacted, locally sent historical events
per room version: oldest two, middle, newest two, with source signatures under
advertised current/old public keys. Selection data and logs are private. Supply
the exact independent source snapshot database and completed checkpoint scope;
do not claim a live-source selection proves frozen import coverage.

`pdu-point-lookup.rs` retrieves only those body keys from an independent cold
restored copy. It never scans or exports the full store. Fjall open can recover
and change clone metadata, so the restored copy must be disposable and closed,
with no server/helper concurrently opening it. Never use the original import
store. The helper compiled against the existing final candidate store/core
rlibs with Rust 1.90.0. All 67 store/core files and both Cargo manifests/lockfile
match candidate commit `8e40719`; provenance is recorded in
`artifacts/historical-pdu-tools/build-provenance.json`. Helper SHA256 is
`ca2c4c23ff5dc50ca593fa744a01dfcb72c8230c0293874944d2f0460551c50c`.
The point-lookup smoke passed five PDUs across versions 1/6/9/10/12 on a tiny
copy of the stopped final-candidate synthetic TLS fixture. This is not real
corpus evidence.

Example root-operated sequence for repaired minimal candidate0316 (private
paths; replace scope/source/restored copy with reviewed exact inputs). Both
selection and verification require the same explicit candidate SHA:

```sh
python3 scripts/federation-drill/historical-pdu.py select \
  --kubeconfig /home/ubuntu/.kube/config-aws-migration \
  --context admin@aws-migration --namespace spindle-rehearsal \
  --pod deploy/rehearsal-pg --database synapse \
  --rooms /PRIVATE/completed-room-scope.json --output /PRIVATE/pdu-selection \
  --binary-sha 0316f922957ae968e5a65e28f59cdc233841e6b764d42aae54187b8749921a18
umask 077
artifacts/historical-pdu-tools-head-56db7d0/pdu-point-lookup-static-head-56db7d0 \
  --independent-cold-copy /PRIVATE/cold-restored-copy \
  /PRIVATE/pdu-selection/source-samples.json > /PRIVATE/target-pdus.json
# In an isolated Synapse v1.156.0 Python environment with signedjson/canonicaljson:
python3 scripts/federation-drill/historical-pdu.py verify \
  --source /PRIVATE/pdu-selection/source-samples.json \
  --target /PRIVATE/target-pdus.json \
  --keys /PRIVATE/pdu-selection/source-public-key.json \
  --selection /PRIVATE/pdu-selection/selection.json \
  --binary-sha 0316f922957ae968e5a65e28f59cdc233841e6b764d42aae54187b8749921a18 \
  --output /PRIVATE/historical-pdu-result.json
```

The offline verifier compares canonical PDUs after removing only unsigned age
metadata, checks the original content hash, prunes using Synapse's exact room
version rules, and verifies retained source Ed25519 signatures under the
unchanged original public signing document. It verifies that document's own
signature too. Result files contain IDs, hashes and statuses, never event
bodies or private signing keys. Missing source-version samples fail the default
all-five-version gate; they must be explained rather than silently waived.
Final binary SHA is a required anchor, not a substitute for independently
verifying the actual runtime binary against that SHA. Offline continuity does
not prove delegated federation routing or authentication by a real remote peer.
No real-corpus execution has been claimed yet.

Offline persisted signing-key proof

When federation is disabled, the federation gate intentionally returns 404 for
`/_matrix/key/*`. Keep it disabled on imported copies, including runtimes with
an imported outbox. After stopping the scratch runtime, root may run:

```sh
artifacts/historical-pdu-tools/signing-public \
  --independent-cold-copy /PRIVATE/cold-restored-copy
```

`signing-public.rs` links the final candidate's existing server/store rlibs.
It holds the exclusive Fjall lock and requires exactly one existing ServerKey
row before invoking `ServerKey::load_or_create`. Missing/multiple keys fail
before any key generation. Output is only key ID, public key base64 and existing
row count. Never invoke it on an original or active store; opening a cold clone
can recover its storage metadata. Helper SHA256:
`3b729a40ffb7005fee7561b247e881b1743e59564b40b4107666c6a7a2c77da9`.
Synthetic existing-key load and missing-key refusal passed, with sanitized
report `artifacts/historical-pdu-tools/signing-public-check.json`.
Compare the output key ID/public bytes with the unchanged original Synapse
public key document (or the public half independently derived with OpenSSL).
This proves persisted signing-key continuity without exposing a seed or
activating imported federation/outbox traffic.

Static PIE alternatives are available for scratch images with older glibc:
`artifacts/historical-pdu-tools/signing-public-static` SHA256
`67837c54075c386b20397fdc68e24524a591a83e10d737334ebcc4321c906021`,
and `pdu-point-lookup-static` (SHA recorded in `static-helper-check.json`).
Both link the same final rlibs with Rust1.90 and `-C target-feature=+crt-static`.
Static signing existing/missing-key checks and static point lookup of five
synthetic PDUs passed. Prefer these variants when the image's libc is unproven.

Queued real-corpus proof

`run-historical.py` requires explicit `--output`, `--root-artifacts`,
`--candidate-sha256`, `--candidate-binary`, `--cold-base`, `--cold-clone`,
`--run-tag`, `--point-helper` and `--point-helper-sha256`. It waits for root's
`rehearsal_store_verified` status with `passed=true`, requires the restored
checkpoint's exact 116 retained rooms and no exclusions, and checks the actual
remote final-binary SHA. It then runs the read-only source selector, checks node
capacity with a floor of15% filesystem capacity plus1GiB, and creates the
explicit new cold-copy path from the independently restored base. Helper/sample
pushes use a unique run tag; helper hash validation precedes source operations. It refuses an existing target rather than
reusing another agent's store. It retrieves only selected PDU keys with the
static helper and verifies them in an isolated pinned Synapse 1.156.0 container.
The container has no source/secret mounts, no service account, a read-only root
filesystem and deny-all ingress/egress. It never starts a homeserver.

The original queued output `artifacts/historical-pdu-actual116-20261006` exited
fail-closed when root reported the retry did not import the excluded room. It
never cloned or proved imported continuity. Keep it as failed-run evidence; a
new candidate needs a fresh private output directory and explicit binary,
helper and verified cold-base provenance. New worker failure status explicitly
records its failed phase. This
proof adds approximately1.6GiB cold-copy growth on the node; production restore
capacity must account for it. Source-only preflight against the earlier115-room
report passed25/25 actual local signatures and content hashes, plus source key
document self-signature (`artifacts/historical-pdu-preflight-20261006/selection/
source-only-crypto.json`). That source-only preflight is not imported corpus
continuity and does not meet the queued gate.

Conditional head-source diagnostic

`head-source-regression.rs` links the exact existing final release rlibs and
runs a four-event synthetic contested-topic fork entirely in memory. No
candidate files, database, store or imported runtime are changed. The source
current state additionally contains a membership slot omitted by the
resolver-derived state. Both natural replay and `head_from_source=true`
finish after two fixed-point passes but remain one slot divergent. A control
that makes the last event's continuity Unknown, enabling full comparison with
the cached source-current state, finishes clean in one pass. Evidence and build
provenance are in `artifacts/head-source-regression-20261006/`.

This confirms that the head flag alone is conditional; it also confirms that
the cached-current comparison path can repair the fixture. It does not identify
the real room's final control path or replace its verdict. If the actual retry
fails specifically on its head divergence, a future repair would need an
explicit last-step source-current seed after head resolution, with tests for
both Derived and Unknown continuity and unchanged signed parent IDs. Such a
repair would require a new reviewed candidate and full real validation; no
repair/build was performed by this diagnostic, and acceptance/authentication
policy must remain unchanged.

Reviewed isolated head repair

Commit `0f63f4b303d11676d2dad1ca6497dc222872da8a` on
`fix/import-authoritative-head`, worktree
`/home/ubuntu/dev/spindle-head-repair-candidate`, contains the explicit
non-first-seed final head override and removes the cached-current historical
getter injection. Its proper unit suite verifies immutable historical last
state and resolver inputs, a current-only outlier remaining outside the
four-event timeline, retained historical supplied body plus new current-only
body deltas, unchanged signed parent lists, and an unsupported singleton first
seed remaining divergent. All26 targeted import tests pass, with format and
whitespace checks passing. Review patch/provenance:
`artifacts/head-source-repair-proposal-20261006/worktree-review.patch` and
`worktree-verification.json`. The earlier scratch proposal is diagnostic
history; the review commit additionally guards the singleton first-seed path.

No release executable was built, no pinned/retry binary was changed, and no
candidate/preservation worktree was edited. This repair retains the initial
natural replay followed by a conditional authoritative-head replay; it does not
claim to remove that second invocation or to prove real-corpus migration success.


Archived remote rejoin follow-up (isolated)

Commit `7b77286` in `/home/ubuntu/dev/spindle-preservation-candidate` adds a
history-preserving stock remote rejoin and new integration tests. It appends
only the fresh local self-join through the shared persistence spine; validated
state/auth context is retained without replaying those bodies as new timeline
entries. Original accepted bodies, event IDs, linear positions and pagination
cursors remain intact. Only the rejoining user gains a new membership-history
position; other departed local users retain their authentic cutoff/history.

Three targeted tests pass, including real HTTP and a separately trusted
private-CA TLS subprocess across versions1/6/9/10/12, peer advanced state,
bidirectional subsequent messages, old body bytes/positions/cursors and a
second departed user's exact read scope. The negative matrix proves no writes
for malformed or mismatched state/create/join bindings, repeated state slots,
nonstate auth entries, and attempted revival of another local user. Evidence:
`artifacts/archived-rejoin-followup-20261006/result.json`.

State-DAG rejoin and changed/new invitations for another local user remain
explicitly refused before custody; the latter needs a separately tested
standalone invitation projection. This is an isolated follow-up, with no
release build or production promotion. The joined-rooms-first production
candidate and actual imported-corpus verdict are independent gates.


Historical harness SHA repair (2026-10-06)

The first exact-minimal waiter was stopped while still waiting, before any
source selection or cold clone: the secondary selector/verifier still used the
old4036 constant. It is preserved as superseded-before-selection evidence.
Selection and verification now require an explicit consistent SHA; the waiter
also checks exact root candidate/import/restored-validation identities and the
remote executable hash, and records candidate/helper pins in every phase.
All four historical/signing/point helper sources were audited; no old4036/e4
functional identity remains. Historical diagnostic/drill provenance remains
unchanged. Offline SHA-pin smoke accepted0316 and rejected mismatched4036 on
25 retained source-only prerecorded PDUs without querying any database; this
is harness evidence, not imported-corpus proof.

Fresh waiter output is
`artifacts/historical-pdu-actual116-head-56db7d0-20261006-r2`; its clone, remote
helper/sample files and offline-container run directory all have a unique r2
suffix. Evidence: `artifacts/historical-sha-pin-repair-20261006/result.json`.

## Exact minimal head-repair synthetic TLS witness

On 2026-10-06, candidate 56db7d0 (SHA
`0316f922957ae968e5a65e28f59cdc233841e6b764d42aae54187b8749921a18`)
passed a fresh Synapse 1.156.0 private-CA TLS drill: 10/10 rooms, versions
1/6/9/10/12 created by each implementation, remote joins and messages/topic
state in both directions. Both federation listeners rejected an untrusted CA.
Evidence: `artifacts/federation-head-56db7d0-synapse-tls/result.json` and
`identity.json` (pinned images, node, pod UID, harness hashes and cleanup).
The new pod and its NetworkPolicy were deleted after private evidence capture.
This synthetic result does not establish imported PDU continuity or delegation.
The harness now requires an explicit SHA argument; the witness validates that
pin directly rather than importing the loopback harness's old diagnostic pin.

Historical R3 (`historical-pdu-actual116-head-56db7d0-20261007-r3`) preserves
R2, which stopped before selection when the finish coordinator's long exec
transport failed. R3 still requires the exact0316 passing restored116 proof.
Its transport hardening omits stdin for read-only execs, stages bounded helper
and sample uploads with size/SHA checks before rename, runs point lookups once
into a private remote file, and verifies remote JSON size/SHA before parsing.
Only immutable read fetches and read-only SQL selection retry, at most3 times;
clone/helper/write operations are never automatically repeated. Offline tar
inputs are hashed after transfer. A mock truncated-success fetch was retried;
persistent truncation failed closed (`historical-transport-smoke-20261007`).

## Actual imported historical continuity on2044cb5

The passing exact candidate SHA is
`c3485f89eef2a015faa8745d56db6d838cf74da8f9eb4ce3b26a23a1619d80ff`.
After the pristine inert restored116-room report and persisted public-key
proof passed, R2 used a separate cold clone and the exact-release bounded
point helper SHA
`9774096a1d7479d1ff4770fb2f1be02c3896c9138e1873370d3d5bbf2c634828`.
All 25 representative accepted local PDUs (five each from versions1/6/9/10/12)
were found, canonically equal excluding unsigned/age_ts, content-hash valid,
and valid under historical local Ed25519 signatures with exact Synapse
room-version pruning. The source key document self-signature passed and its
public key matched the persisted restored key. Scope116/excluded 0 matches the
actual restored report, SHA
`2cb392a7658c6a44afe6c35ca4796d8b9c930ef0f980d00644f4f1636802c421`.
Evidence: `artifacts/historical-pdu-actual116-cache-2044cb5-20261007-r2/`
`status.json`, `result.json`, `review.json`; private body/selection logs remain
restricted. This establishes sampled offline imported PDU/signature continuity,
not delegated federation transport or new peer authentication. Prior R1 stopped
before selection when scratch shutdown cleanup caused the coordinator failure;
it remains preserved. Original stores were not opened by this witness.

## Final runtime candidate 26496c9 witnesses

The final source commit is 26496c9a64f40ead6d5e8b44a6ecdc3c2c15b8d7,
SHA 83e59309f913b16b682f78bdc66d122664daa0e514a4ac30af1111bb3e1b8d86.
Its separate runtime restore checks ordered metadata, full chain/counters and
last 512+all forward-tip roots, retaining recorded historical root addresses
for verified on-demand access. Explicit native validation remains exhaustive
across all persisted roots; rolling 512 snapshot owners retain weak-node cache
reuse during that check. Registry loading/resolution occurs outside the map
write lock; publication keeps the canonical room Arc. Existing resolver-error
handling remains unchanged. Core/store/runtime registry tests and clippy/fmt
passed in pipeline's exact remote build; no host build was performed.

The fresh exact Synapse 1.156 private-CA TLS witness passed 10/10 rooms across
versions1/6/9/10/12, each implementation creating a room, remote joins and
bidirectional message/topic state; both untrusted-CA probes failed as expected.
Its disposable pod/policy were deleted. Evidence:
`artifacts/federation-runtime-26496c9-synapse-tls/{result,identity}.json`.
Coordinated delegated MAS fixture evidence is separately retained by the
client witness in `artifacts/delegated-mas-runtime-26496c9-20261007`.

After exact inert original/cold 116 validation and public-key proof passed,
the historical witness used a new independent cold clone and exact-release
point helper 2879cdf0b7044a00b249e267bf8eaa431004d0d2e7615d5bd24f7ce37c54d43e.
All 25 accepted locally signed source PDUs across 1/6/9/10/12 were found,
canonically equal, content-hash valid and historically signature valid with
exact room-version pruning. Public-key document self-signature and persisted
key equality passed. Retained scope 116/excluded 0 matches the restored report,
SHA 2cb392a7658c6a44afe6c35ca4796d8b9c930ef0f980d00644f4f1636802c421.
Evidence: `artifacts/historical-pdu-actual116-runtime-26496c9-20261007-r1/`
`status.json`, `result.json`, `review.json`. This is sampled offline historical
continuity; huge-room cold HTTP latency and delegated peer authentication are
separate gates.

The redundant completed 2044 historical clone was retired only with root's
explicit authorization, stable pod UID and immediate exact path/inode,
non-symlink, no argv/FD/cwd-reference guards. All 11 private proof files were
rehash-verified and retained; pristine S3 full-object reread proof and 10KiB
small clone metadata archive were retained. Although du showed 1.859GiB, df
increased only 2416KiB (shared extents), so this is not 1.859GiB capacity recovery.
Evidence: `artifacts/historical-clone-retirement-2044cb5-20261007/result.json`.

Optional owned production canary preparation

Status: prepared, Python syntax/CLI checked and Rust formatted. The adapted SDK
has NOT been compiled or exercised against an isolated MAS server. Do not use
this as a production gate until that isolated test passes. No production OAuth
grant, account, session, room or backup was created during preparation.

Production read-only evidence: MAS 1.23.0 has one static client-credentials
client (0000000000000000000SYNAPSE) listed in the admin-client policy, and the
corresponding OIDC client credential exists in ess-generated. These support
an automated administrative flow; acceptance of an admin OAuth grant remains
unverified. No matching spindle migration/canary production users were found.

The adapter uses exactly two new random-prefix owned users, four expiring
personal sessions (seed and fresh recovery device for each), one private
version-12 encrypted room and per-user server-side key backup. It never uses
real user passwords, sessions or recovery material. Fresh recovery devices
are issued before freeze but have no local crypto store or downloaded keys.
Verification checks the original issued seed sessions retain their Matrix
identity/device and private room membership, then restores secrets and
historical plaintext through server-side backup on the recovery sessions.
The preserved fixture flow remains the required rehearsal; this is optional.

Build the standalone SDK only in an isolated builder with sufficient disk:

    cargo build --locked --release -j1 --manifest-path scripts/production-canary/sdk/Cargo.toml

Root should first exercise seed/verify/revoke on an isolated MAS-backed server
using correspondingly adapted endpoint constants and test credentials. The
current constants deliberately name production, so this command is NOT an
isolated test by default. Python inspect performs metadata reads only:

    python3 scripts/production-canary/canary.py inspect

After isolated proof and all existing rehearsal gates pass, root may produce
an aggregate gate JSON whose passed, import_store, client_e2ee and federation
fields are all true, then execute before production freeze:

    python3 scripts/production-canary/canary.py seed --execute --sdk /ABS/sdk-binary --rehearsal-gates /ABS/gates.json --output /ABS/NEW-private-canary-dir

Refresh the frozen inventory after seed: two users and one room are added.
Do not reuse the earlier 116-room/1371-user source counts. Keep the complete
private directory through migration; it contains tokens, recovery keys,
crypto stores and event hashes, and must not be published or attached to PRs.
The script enforces umask 077 and records owned IDs incrementally for cleanup.

After import, verify through a root-owned private target endpoint, then the
final public endpoint if needed. The SDK logs out recovery sessions at the
end, so a second recovery verification requires new owned recovery sessions;
original seed sessions remain usable until explicit revocation.

    python3 scripts/production-canary/canary.py verify --execute --sdk /ABS/same-sdk-binary --output /ABS/private-canary-dir --target-url https://matrix.reilly.asia
    python3 scripts/production-canary/canary.py revoke --execute --output /ABS/private-canary-dir

Revoke handles only IDs recorded by this run. It revokes sessions rather than
deleting accounts or the historical room, preserving migration evidence.
Admin API reference: https://element-hq.github.io/matrix-authentication-service/topics/admin-api.html
Authorization reference: https://element-hq.github.io/matrix-authentication-service/topics/authorization.html

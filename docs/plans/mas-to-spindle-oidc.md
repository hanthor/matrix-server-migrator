# Plan: MAS → Spindle built-in OIDC migration (and the migrator as its own project)

Status: draft, 2026-10-08. Nothing is implemented. Run this only after reilly.asia is live on Spindle with delegated MAS.

## Goals

1. Make the migrator a standalone, general-purpose Matrix migration project, with Synapse → Spindle as the first connector.
2. Add a second migration type: **auth provider migration**, from MAS (delegated, MSC3861) to Spindle's built-in OIDC provider (`spindle-server/src/oidc.rs`).
3. Prove it on fixtures before touching reilly.asia.

## Part A: the migrator as its own project

| Step | Detail |
|---|---|
| A1 | The repo is under git (`2bf550a`, 2026-10-08). Push it to its own GitHub repository. **Name to be decided**; a neutral `matrix-migrator` fits the general-purpose goal. |
| A2 | **Do not rename or move `~/dev/spindle-migrator` until the reilly.asia cutover is finished.** About 100 prepared gate scripts hardcode that absolute path. |
| A3 | Move the reilly-specific operational knowledge out of `artifacts/` (gitignored, full of secrets) into versioned, parameterized code: phases, gates and evidence records. `artifacts/` stays as private per-run output only. |
| A4 | Core model: a `Migration` is an ordered list of **phases** (plan → inventory → pre-sync → freeze → capture → import → validate → backup/restore → activate → verify → rollback). Each phase has explicit gates and a durable state file, so a run can resume after a crash. The 2026-10-07 outage lasted about 20 extra hours because a host reboot killed host-side units mid-chain. |
| A5 | Connector traits: `Source` (Synapse-PG, MAS-PG), `Target` (Spindle store), `Deployment` (ESS Helm/k8s first, then plain systemd). The `reilly_prototype()` preset becomes just an example plan. |
| A6 | Speed work, in this order (see `docs/import-speedup-options.md`): pass-2 cache reuse, `pg_dump` alongside the import, media pre-copy, then cache pre-warm and catch-up. |

## Part B: MAS → Spindle OIDC

### What has to move

Facts from a read-only query of production MAS on 2026-10-08:

| MAS data | Count | Plan |
|---|---|---|
| `users` (active) | 5 | Must already exist in Spindle (imported from Synapse). Cross-check localparts and the deactivated, locked and admin flags. |
| `user_passwords` | 5, all `$argon2id$`, scheme version 1 | Copy the PHC string as-is via `Accounts::register_hashed`, or a new `set_password_hash`. **Gate:** MAS config must have no password `secret` (pepper) and only scheme v1 = argon2id. Otherwise refuse, or fall back to a forced reset. |
| `user_emails` | ? | Spindle has no email flows; record them in the report and do not import. |
| `upstream_oauth_providers` / `_links` | 0 / 0 | **Gate:** must be 0. Spindle has no upstream IdP or SSO support. |
| `oauth2_sessions` (active) | 24 | Cannot migrate: tokens are MAS-signed or opaque. Users re-login once. Report the per-user count. |
| `compat_sessions` (active) | 2 | Same. Their devices remain in Spindle, so they can be cleaned up or kept for key verification. |
| `oauth2_clients` | 31 | Do not migrate. Clients re-register dynamically against the new issuer. |

### Behaviour change for users

- Every user signs in once more.
- E2EE history stays readable through key backup with the recovery key, or by verifying from another device; cross-signing and backup were imported with 0 mismatches.
- The issuer moves from `https://auth.reilly.asia/` to Spindle's issuer. Clients rediscover it via `/.well-known/matrix/client` and `/_matrix/client/v1/auth_metadata`.
- `auth.reilly.asia` can then redirect to the new issuer or return 410.

### New migrator pieces

1. **`MasSource`** (read-only, repeatable-read transaction): inventory of the tables above, plus the password-scheme config read from the MAS config Secret.
2. **Pre-flight gates:**
   - no upstream providers;
   - no peppered or unknown hash schemes;
   - every active MAS user maps to an existing, non-deactivated Spindle account;
   - the Spindle build exposes `[auth.oidc]`.
3. **Spindle side:** an import entry point (CLI subcommand or `import-mas`) that writes the password hashes. It needs either a small Spindle change (an `Accounts::set_password_hash` that validates PHC and argon2 parameters) or reuse of `register_hashed` if it can update in place. **Check before building.**
4. **Config switch:** render the Spindle config with `[auth.oidc]` enabled and `[auth.delegated]` removed, plus updated well-known values.
5. **Validation:**
   - for each user, Spindle `verify_password` with a fixture password succeeds (fixtures only);
   - each hash read back equals the MAS row's SHA;
   - discovery returns the new issuer;
   - `/_synapse/mas/*` returns 404;
   - `/login` flows are as expected.
6. **Rollback:** before the switch, keep the MAS Deployment and DB untouched, scaled to 0 rather than deleted. Rollback restores `[auth.delegated]` and the routes. Sessions minted by Spindle's OIDC in the meantime are dropped, and users log in again via MAS.

### Test plan

| Stage | Environment | Pass criteria |
|---|---|---|
| T1 unit | migrator-core | Inventory parsing; gate refusal for a pepper, an upstream provider, a bcrypt-only user, a user missing from Spindle, and a deactivated mismatch. |
| T2 Spindle unit | spindle-server | An MAS-produced argon2id PHC (real `mas-cli` output) verifies with `verify_password`. The `set_password_hash` round-trip is checked, and malformed PHC is refused. |
| T3 fixture rig | Isolated namespace, existing `mas_dark` / `synapse_dark` fixtures (`@spindle-mas-d`, `@spindle-mig-a/b/c`), empty Spindle store, delegation **off**, OIDC **on** | Element Web (MSC3861 flag) and Element X sign in with the **original MAS password**. Sessions refresh. Key backup restores with the stored recovery key, and the plaintext hashes of the fixture rooms match. |
| T4 negative | Same rig | A peppered-MAS fixture is refused before any write. A wrong password fails. Old MAS tokens get 401 `M_UNKNOWN_TOKEN`. |
| T5 rollback | Same rig | Re-enable delegation and scale MAS back to 1: the original MAS password logs in, and no Spindle-side account data is lost. |
| T6 production dry run | reilly.asia, read-only | `MasSource` inventory and gates only. Expect 5 users, 0 upstream, scheme v1 argon2id, no pepper. No writes. |
| T7 production | reilly.asia, short window | Announce the change to the 5 users. Switch config and routes. One owner account signs in first as a canary, then the rest. MAS stays at 0 replicas for 7 days, then is retired along with its DB, after a backup. |

### Open questions

- Repo name and GitHub org for the standalone project.
- Whether to keep `auth.reilly.asia` as the issuer hostname by routing it to Spindle, which would avoid issuer drift for clients that cache it. This needs Spindle to support a custom issuer URL.
- Whether Spindle's OIDC authorization page is good enough for real users: it has no account-management UI and no password reset by email.

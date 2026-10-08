//! Plan execution: run a [`Plan`](crate::Plan) through an `import-synapse`
//! binary and read back the checkpoint report.
//!
//! The binary does the heavy lifting; this module owns argv construction,
//! process supervision, and report parsing so both UIs share one behavior.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

use crate::{Plan, SourceConfig, TargetConfig};

/// How to run a plan.
#[derive(Clone, Debug, Default)]
pub struct RunOptions {
    /// Pass `--dry-run`: replay and check everything, write nothing.
    pub dry_run: bool,
    /// Pass `--validate-only`: read back an existing imported store.
    pub validate_only: bool,
    /// Pass `--no-validate`: skip the read-back phase.
    pub no_validate: bool,
}

/// What a finished run produced, from the checkpoint report.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Execution {
    pub server_name: String,
    pub room_ids: Vec<String>,
    pub source_events: std::collections::BTreeMap<String, u64>,
    pub rooms: usize,
    pub excluded_rooms: usize,
    pub rooms_checked: usize,
    pub divergent: usize,
    pub short: usize,
    pub sample_mismatches: usize,
    pub domain_mismatches: usize,
    pub missing_domains: Vec<String>,
    pub policy_versions: Vec<u32>,
    pub validation_present: bool,
    pub signing_key_imported: u64,
    pub media_imported: u64,
    pub media_checked: u64,
    pub dry_run: bool,
    #[serde(default)]
    pub preserve_local_history: bool,
    pub returncode: i32,
    pub report_path: PathBuf,
    pub log_path: Option<PathBuf>,
}

/// Build the exact argv for a plan. Pure, so both UIs and tests share it.
pub fn argv(
    plan: &Plan,
    spindle_config: &Path,
    pg_conn: &str,
    checkpoint: &Path,
    options: &RunOptions,
) -> Result<Vec<String>> {
    crate::synapse::reject_inline_password(pg_conn)?;
    if options.validate_only && (options.dry_run || options.no_validate) {
        anyhow::bail!("validate-only cannot be combined with dry-run or no-validate");
    }
    let (binary, media) = match (&plan.source, &plan.target) {
        (
            SourceConfig::SynapsePostgres { media_dir, .. },
            TargetConfig::SpindleImport { binary, .. },
        ) => (binary, media_dir),
    };
    if binary.is_empty() {
        anyhow::bail!("plan target has no binary set");
    }
    let mut args = vec![
        binary.clone(),
        "import-synapse".to_owned(),
        spindle_config.display().to_string(),
        pg_conn.to_owned(),
        "--media".to_owned(),
        media.clone(),
        "--checkpoint".to_owned(),
        checkpoint.display().to_string(),
    ];
    if plan.preserve_local_history {
        args.push("--preserve-local-history".to_owned());
    }
    if !plan.only_rooms.is_empty() {
        args.push("--rooms".to_owned());
        args.push(plan.only_rooms.join(","));
    }
    if options.dry_run {
        args.push("--dry-run".to_owned());
    }
    if options.validate_only {
        args.push("--validate-only".to_owned());
    }
    if options.no_validate {
        args.push("--no-validate".to_owned());
    }
    Ok(args)
}

/// Run a plan to completion and read back its checkpoint report.
pub async fn execute(
    plan: &Plan,
    spindle_config: &Path,
    pg_conn: &str,
    checkpoint: &Path,
    options: &RunOptions,
) -> Result<Execution> {
    let args = argv(plan, spindle_config, pg_conn, checkpoint, options)?;
    let (program, rest) = args
        .split_first()
        .context("argv is never empty by construction")?;
    let log_path = checkpoint.with_extension("run.log");
    let mut log_options = std::fs::OpenOptions::new();
    log_options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        log_options.mode(0o600);
    }
    let log = log_options
        .open(&log_path)
        .with_context(|| format!("opening run log {}", log_path.display()))?;
    let status = tokio::process::Command::new(program)
        .args(rest)
        .stdout(log.try_clone()?)
        .stderr(log)
        .kill_on_drop(true)
        .status()
        .await
        .with_context(|| format!("spawning {program}"))?;
    let mut execution = read_execution(checkpoint, status.code().unwrap_or(-1))?;
    execution.log_path = Some(log_path);
    // A resumed checkpoint may contain validation from a previous invocation.
    // Skipping validation cannot produce new read-back evidence.
    if options.no_validate || options.dry_run {
        execution.validation_present = false;
    }
    if execution.dry_run != options.dry_run {
        anyhow::bail!("checkpoint mode does not match the requested run");
    }
    check_execution_mode(&execution, plan)?;
    Ok(execution)
}

/// Required read-back checks emitted by the Synapse importer. Empty source
/// domains are valid: presence of their check matters, not a nonzero row count.
const REQUIRED_DOMAINS: &[&str] = &[
    "users",
    "profiles",
    "devices",
    "device_keys",
    "cross_signing_keys",
    "cross_signing_signatures",
    "account_data",
    "push_rules",
    "pushers",
    "key_backup_sessions",
    "receipts",
    "directory",
    "media",
    "auth_context",
    "historical_rejections",
];

const PRESERVATION_DOMAINS: &[&str] = &["local_memberships", "forgotten_rooms"];

#[derive(Deserialize)]
struct RoomReport {
    source_events: u64,
    #[serde(default)]
    rejection_policy_version: u32,
}

#[derive(Deserialize)]
struct DomainReport {
    imported: u64,
}

#[derive(Deserialize)]
struct ValidationReport {
    rooms_checked: usize,
    rooms_divergent: std::collections::BTreeMap<String, serde_json::Value>,
    rooms_short: std::collections::BTreeMap<String, serde_json::Value>,
    sample_mismatches: Vec<String>,
    domains: std::collections::BTreeMap<String, (u64, Vec<String>)>,
}

#[derive(Deserialize)]
struct CheckpointReport {
    #[serde(default)]
    preserve_local_history: bool,
    server_name: String,
    dry_run: bool,
    rooms: std::collections::BTreeMap<String, RoomReport>,
    excluded_rooms: std::collections::BTreeMap<String, serde_json::Value>,
    domains: std::collections::BTreeMap<String, DomainReport>,
    validation: Option<ValidationReport>,
}

/// Inspect a checkpoint using the importer's real report schema. Missing or
/// malformed evidence cannot silently become zero failures.
pub fn read_execution(checkpoint: &Path, returncode: i32) -> Result<Execution> {
    let report: CheckpointReport = serde_json::from_slice(
        &std::fs::read(checkpoint)
            .with_context(|| format!("reading checkpoint {}", checkpoint.display()))?,
    )
    .context("parsing checkpoint report")?;
    let validation = report.validation.as_ref();
    let policy_versions = report
        .rooms
        .values()
        .map(|room| room.rejection_policy_version)
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    Ok(Execution {
        server_name: report.server_name,
        room_ids: report.rooms.keys().cloned().collect(),
        source_events: report
            .rooms
            .iter()
            .map(|(id, room)| (id.clone(), room.source_events))
            .collect(),
        rooms: report.rooms.len(),
        excluded_rooms: report.excluded_rooms.len(),
        rooms_checked: validation.map_or(0, |value| value.rooms_checked),
        divergent: validation.map_or(0, |value| value.rooms_divergent.len()),
        short: validation.map_or(0, |value| value.rooms_short.len()),
        sample_mismatches: validation.map_or(0, |value| value.sample_mismatches.len()),
        domain_mismatches: validation.map_or(0, |value| {
            value
                .domains
                .values()
                .map(|(_, mismatches)| mismatches.len())
                .sum()
        }),
        missing_domains: REQUIRED_DOMAINS
            .iter()
            .chain(
                PRESERVATION_DOMAINS
                    .iter()
                    .filter(|_| report.preserve_local_history),
            )
            .filter(|domain| {
                validation.is_none_or(|value| !value.domains.contains_key(**domain))
                    || (report.preserve_local_history
                        && PRESERVATION_DOMAINS.contains(domain)
                        && (report.domains.get(**domain).is_none()
                            || validation
                                .and_then(|value| value.domains.get(**domain))
                                .is_none_or(|(checked, _)| {
                                    *checked
                                        < report
                                            .domains
                                            .get(**domain)
                                            .map_or(0, |value| value.imported)
                                })))
            })
            .map(|domain| (*domain).to_owned())
            .collect(),
        policy_versions,
        validation_present: validation.is_some(),
        signing_key_imported: report
            .domains
            .get("signing_key")
            .map_or(0, |domain| domain.imported),
        media_imported: report
            .domains
            .get("media")
            .map_or(0, |domain| domain.imported),
        media_checked: validation
            .and_then(|value| value.domains.get("media"))
            .map_or(0, |(rows, _)| *rows),
        dry_run: report.dry_run,
        preserve_local_history: report.preserve_local_history,
        returncode,
        report_path: checkpoint.to_owned(),
        log_path: None,
    })
}

/// The cutover gates from the runbook: a migration counts only when every
/// room is present, checked, and in agreement with the source.
pub fn check_gates(execution: &Execution, expected_rooms: usize) -> Result<()> {
    let mut failed = Vec::new();
    if execution.returncode != 0 {
        failed.push(format!("returncode={}", execution.returncode));
    }
    if execution.dry_run {
        failed.push("dry run proves nothing about a real migration".to_owned());
    }
    if execution.rooms != expected_rooms {
        failed.push(format!(
            "rooms={} expected={}",
            execution.rooms, expected_rooms
        ));
    }
    if execution.excluded_rooms != 0 {
        failed.push(format!("excluded_rooms={}", execution.excluded_rooms));
    }
    if execution.rooms_checked != expected_rooms {
        failed.push(format!("rooms_checked={}", execution.rooms_checked));
    }
    if execution.divergent != 0 {
        failed.push(format!("divergent={}", execution.divergent));
    }
    if !execution.validation_present {
        failed.push("read-back validation is missing".to_owned());
    }
    for (name, count) in [
        ("short", execution.short),
        ("sample_mismatches", execution.sample_mismatches),
        ("domain_mismatches", execution.domain_mismatches),
    ] {
        if count != 0 {
            failed.push(format!("{name}={count}"));
        }
    }
    if !execution.missing_domains.is_empty() {
        failed.push(format!(
            "missing_domains={}",
            execution.missing_domains.join(",")
        ));
    }
    if execution.policy_versions != [3] {
        failed.push(format!(
            "policy_versions={:?} expected=[3]",
            execution.policy_versions
        ));
    }
    if execution.signing_key_imported != 1 {
        failed.push("Synapse signing key has not been imported".to_owned());
    }
    if execution.media_checked < execution.media_imported {
        failed.push(format!(
            "media_checked={} imported={}: source media read-back is incomplete",
            execution.media_checked, execution.media_imported
        ));
    }
    if expected_rooms == 0 {
        failed.push("expected scope is empty".to_owned());
    }
    if failed.is_empty() {
        Ok(())
    } else {
        anyhow::bail!("migration gates failed: {}", failed.join("; "))
    }
}

/// A report from joined-only scope cannot certify an opted-in history plan.
pub fn check_execution_mode(execution: &Execution, plan: &Plan) -> Result<()> {
    anyhow::ensure!(
        execution.preserve_local_history == plan.preserve_local_history,
        "checkpoint preservation mode does not match the requested plan"
    );
    Ok(())
}

/// Fresh validation must cover the plan's room identities and server, as
/// well as its totals. Equal counts from another plan are not sufficient.
pub fn check_plan_gates(
    execution: &Execution,
    plan: &Plan,
    inventory: &crate::Inventory,
) -> Result<()> {
    check_execution_mode(execution, plan)?;
    let rooms = plan.planned_rooms(inventory);
    check_gates(execution, rooms.len())?;
    let expected: std::collections::BTreeSet<_> =
        rooms.iter().map(|room| room.room_id.as_str()).collect();
    let actual: std::collections::BTreeSet<_> =
        execution.room_ids.iter().map(String::as_str).collect();
    anyhow::ensure!(
        expected == actual,
        "validated room IDs differ from the plan scope"
    );
    anyhow::ensure!(
        execution.server_name == inventory.server_name,
        "validated server {} differs from source {}",
        execution.server_name,
        inventory.server_name
    );
    for room in rooms {
        anyhow::ensure!(
            execution.source_events.get(&room.room_id) == Some(&room.source_events),
            "source event count changed for {}: checkpoint {:?}, current {}; resume requires the same frozen source",
            room.room_id, execution.source_events.get(&room.room_id), room.source_events
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reilly_prototype;

    fn test_plan() -> Plan {
        let mut plan = reilly_prototype();
        let TargetConfig::SpindleImport { binary, .. } = &mut plan.target;
        *binary = "/bin/spindle".to_owned();
        plan
    }

    #[test]
    fn argv_carries_scope_and_flags() {
        let mut plan = test_plan();
        plan.only_rooms = vec!["!abc:reilly.asia".to_owned()];
        let args = argv(
            &plan,
            Path::new("/etc/spindle.toml"),
            "host=db dbname=synapse",
            Path::new("/work/report.json"),
            &RunOptions {
                dry_run: true,
                ..RunOptions::default()
            },
        )
        .unwrap();
        assert_eq!(args[1], "import-synapse");
        assert!(args.contains(&"--rooms".to_owned()));
        assert!(args.contains(&"--dry-run".to_owned()));
        assert!(!args.contains(&"--validate-only".to_owned()));
        assert!(!args.contains(&"--preserve-local-history".to_owned()));
        plan.preserve_local_history = true;
        let preserving = argv(
            &plan,
            Path::new("/etc/spindle.toml"),
            "host=db",
            Path::new("/work/report.json"),
            &RunOptions {
                validate_only: true,
                ..RunOptions::default()
            },
        )
        .unwrap();
        assert_eq!(
            preserving
                .iter()
                .filter(|arg| *arg == "--preserve-local-history")
                .count(),
            1
        );
        assert!(preserving.contains(&"--validate-only".to_owned()));
    }

    #[test]
    fn argv_rejects_keyword_and_url_credentials_before_constructing_a_command() {
        for conn in [
            "host=db password=credential-secret",
            "postgresql://test:credential-secret@db/synapse",
        ] {
            let error = argv(
                &test_plan(),
                Path::new("/etc/spindle.toml"),
                conn,
                Path::new("/work/report.json"),
                &RunOptions::default(),
            )
            .unwrap_err()
            .to_string();
            assert!(error.contains("SPINDLE_SYNAPSE_PASSWORD"));
            assert!(!error.contains("credential-secret"));
        }
    }

    #[test]
    fn argv_rejects_plan_without_binary() {
        let plan = reilly_prototype();
        assert!(argv(
            &plan,
            Path::new("/etc/spindle.toml"),
            "host=db",
            Path::new("/work/report.json"),
            &RunOptions::default(),
        )
        .is_err());
    }

    #[tokio::test]
    async fn execute_reads_a_stub_report() {
        let dir = tempfile_dir();
        let checkpoint = dir.join("report.json");
        let stub = dir.join("spindle-stub.sh");
        std::fs::write(
            &stub,
            format!(
                "#!/bin/sh\necho '{{\"server_name\":\"reilly.asia\",\"rooms\":{{\"!a:x\":{{\"source_events\":10}}}},\"excluded_rooms\":{{}},\"dry_run\":true,\"domains\":{{}},\"validation\":{{\"rooms_checked\":1,\"rooms_divergent\":{{}},\"rooms_short\":{{}},\"sample_mismatches\":[],\"domains\":{{}}}}}}' > {}",
                checkpoint.display()
            ),
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&stub, std::fs::Permissions::from_mode(0o700)).unwrap();
        }
        let mut plan = test_plan();
        let TargetConfig::SpindleImport { binary, .. } = &mut plan.target;
        *binary = stub.display().to_string();
        // Stub ignores argv shape; point media/config at anything.
        let execution = execute(
            &plan,
            Path::new("/nonexistent.toml"),
            "host=db",
            &checkpoint,
            &RunOptions {
                dry_run: true,
                ..RunOptions::default()
            },
        )
        .await
        .unwrap();
        assert_eq!(execution.rooms, 1);
        assert_eq!(execution.excluded_rooms, 0);
        assert!(execution.dry_run);
        assert!(!execution.validation_present);
        assert_eq!(execution.returncode, 0);
        plan.preserve_local_history = true;
        let mismatch = execute(
            &plan,
            Path::new("/nonexistent.toml"),
            "host=db",
            &checkpoint,
            &RunOptions {
                dry_run: true,
                ..RunOptions::default()
            },
        )
        .await
        .unwrap_err();
        assert!(mismatch.to_string().contains("preservation mode"));
        std::fs::remove_dir_all(&dir).ok();
    }

    fn passing_execution() -> Execution {
        Execution {
            server_name: "reilly.asia".to_owned(),
            room_ids: Vec::new(),
            source_events: std::collections::BTreeMap::new(),
            rooms: 116,
            excluded_rooms: 0,
            rooms_checked: 116,
            divergent: 0,
            short: 0,
            sample_mismatches: 0,
            domain_mismatches: 0,
            missing_domains: Vec::new(),
            policy_versions: vec![3],
            validation_present: true,
            signing_key_imported: 1,
            media_imported: 0,
            media_checked: 0,
            dry_run: false,
            preserve_local_history: false,
            returncode: 0,
            report_path: PathBuf::from("/work/report.json"),
            log_path: None,
        }
    }

    #[test]
    fn gates_pass_on_a_clean_migration() {
        assert!(check_gates(&passing_execution(), 116).is_ok());
    }

    #[test]
    fn live_gates_reject_another_scope_with_the_same_counts() {
        let plan = test_plan();
        let inventory = crate::Inventory {
            server_name: "reilly.asia".to_owned(),
            rooms: vec![crate::RoomScope {
                room_id: "!a:x".to_owned(),
                version: "6".to_owned(),
                local_members: 1,
                source_events: 10,
            }],
            ..crate::Inventory::default()
        };
        let mut execution = passing_execution();
        execution.rooms = 1;
        execution.rooms_checked = 1;
        execution.room_ids = vec!["!a:x".to_owned()];
        execution.source_events.insert("!a:x".to_owned(), 10);
        assert!(check_plan_gates(&execution, &plan, &inventory).is_ok());
        execution.room_ids = vec!["!other:x".to_owned()];
        assert!(check_plan_gates(&execution, &plan, &inventory).is_err());
        execution.room_ids = vec!["!a:x".to_owned()];
        execution.server_name = "other.example".to_owned();
        assert!(check_plan_gates(&execution, &plan, &inventory).is_err());
    }

    #[test]
    fn live_gates_reject_new_messages_in_the_same_room_scope() {
        let plan = test_plan();
        let inventory = crate::Inventory {
            server_name: "reilly.asia".to_owned(),
            rooms: vec![crate::RoomScope {
                room_id: "!a:x".to_owned(),
                version: "6".to_owned(),
                local_members: 1,
                source_events: 11,
            }],
            ..crate::Inventory::default()
        };
        let mut execution = passing_execution();
        execution.rooms = 1;
        execution.rooms_checked = 1;
        execution.room_ids = vec!["!a:x".to_owned()];
        execution.source_events.insert("!a:x".to_owned(), 10);
        assert!(check_plan_gates(&execution, &plan, &inventory)
            .unwrap_err()
            .to_string()
            .contains("source event count changed"));
    }

    #[test]
    fn gates_fail_on_every_shortfall() {
        let mut execution = passing_execution();
        execution.excluded_rooms = 1;
        execution.rooms = 115;
        let error = check_gates(&execution, 116).unwrap_err().to_string();
        assert!(error.contains("excluded_rooms=1"), "{error}");
        assert!(error.contains("rooms=115 expected=116"), "{error}");

        let mut execution = passing_execution();
        execution.dry_run = true;
        assert!(check_gates(&execution, 116).is_err());

        let mut execution = passing_execution();
        execution.divergent = 2;
        assert!(check_gates(&execution, 116).is_err());

        for mutate in [
            |e: &mut Execution| e.short = 1,
            |e: &mut Execution| e.sample_mismatches = 1,
            |e: &mut Execution| e.domain_mismatches = 1,
            |e: &mut Execution| e.missing_domains.push("media".to_owned()),
            |e: &mut Execution| e.policy_versions = vec![2, 3],
            |e: &mut Execution| e.validation_present = false,
            |e: &mut Execution| e.signing_key_imported = 0,
            |e: &mut Execution| e.media_imported = 1,
        ] {
            let mut execution = passing_execution();
            mutate(&mut execution);
            assert!(check_gates(&execution, 116).is_err());
        }
    }

    #[test]
    fn parses_real_schema_and_rejects_missing_evidence() {
        let dir = tempfile_dir();
        let checkpoint = dir.join("schema-report.json");
        let domains: serde_json::Map<String, serde_json::Value> = REQUIRED_DOMAINS
            .iter()
            .map(|name| ((*name).to_owned(), serde_json::json!([0, []])))
            .collect();
        let mut report = serde_json::json!({
            "server_name": "reilly.asia",
            "dry_run": false,
            "rooms": {"!a:x": {"source_events": 10, "rejection_policy_version": 3}},
            "excluded_rooms": {},
            "domains": {"signing_key": {"source": 1, "imported": 1},
                        "media": {"source": 0, "imported": 0}},
            "validation": {"rooms_checked": 1, "rooms_divergent": {},
                "rooms_short": {}, "sample_mismatches": [], "domains": domains}
        });
        std::fs::write(&checkpoint, report.to_string()).unwrap();
        let legacy = read_execution(&checkpoint, 0).unwrap();
        assert!(!legacy.preserve_local_history);
        assert!(check_gates(&legacy, 1).is_ok());
        // The importer skips missing source files during validation. A wrong
        // media root could therefore report zero mismatches without checking
        // files that were imported. Count that as incomplete evidence.
        report["domains"]["media"]["imported"] = serde_json::json!(2);
        report["validation"]["domains"]["media"] = serde_json::json!([1, []]);
        std::fs::write(&checkpoint, report.to_string()).unwrap();
        let error = check_gates(&read_execution(&checkpoint, 0).unwrap(), 1)
            .unwrap_err()
            .to_string();
        assert!(error.contains("media_checked=1 imported=2"), "{error}");
        report["domains"]["media"]["imported"] = serde_json::json!(0);
        report["validation"]["domains"]["media"] = serde_json::json!([1, ["missing blob"]]);
        std::fs::write(&checkpoint, report.to_string()).unwrap();
        let execution = read_execution(&checkpoint, 0).unwrap();
        assert_eq!(execution.domain_mismatches, 1);
        assert!(check_gates(&execution, 1).is_err());
        report["validation"]
            .as_object_mut()
            .unwrap()
            .remove("rooms_short");
        std::fs::write(&checkpoint, report.to_string()).unwrap();
        assert!(read_execution(&checkpoint, 0).is_err());
        report["validation"] = serde_json::Value::Null;
        std::fs::write(&checkpoint, report.to_string()).unwrap();
        assert!(check_gates(&read_execution(&checkpoint, 0).unwrap(), 1).is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn preservation_requires_exact_mode_and_complete_metadata_evidence() {
        let dir = tempfile_dir();
        let checkpoint = dir.join("preserve-report.json");
        let domains: serde_json::Map<String, serde_json::Value> = REQUIRED_DOMAINS
            .iter()
            .map(|name| ((*name).to_owned(), serde_json::json!([0, []])))
            .collect();
        let mut report = serde_json::json!({
            "server_name": "reilly.asia", "preserve_local_history": true, "dry_run": false,
            "rooms": {"!departed:x": {"source_events": 10, "rejection_policy_version": 3}},
            "excluded_rooms": {}, "domains": {"signing_key": {"imported": 1}, "media": {"imported": 0}},
            "validation": {"rooms_checked": 1, "rooms_divergent": {}, "rooms_short": {}, "sample_mismatches": [], "domains": domains}
        });
        std::fs::write(&checkpoint, report.to_string()).unwrap();
        let missing = read_execution(&checkpoint, 0).unwrap();
        assert_eq!(
            missing.missing_domains,
            ["local_memberships", "forgotten_rooms"]
        );
        assert!(check_gates(&missing, 1).is_err());
        for name in PRESERVATION_DOMAINS {
            report["domains"][name] = serde_json::json!({"imported": 0});
            report["validation"]["domains"][name] = serde_json::json!([0, []]);
        }
        std::fs::write(&checkpoint, report.to_string()).unwrap();
        let execution = read_execution(&checkpoint, 0).unwrap();
        assert!(check_gates(&execution, 1).is_ok());
        report["domains"]
            .as_object_mut()
            .unwrap()
            .remove("forgotten_rooms");
        std::fs::write(&checkpoint, report.to_string()).unwrap();
        assert!(read_execution(&checkpoint, 0)
            .unwrap()
            .missing_domains
            .contains(&"forgotten_rooms".to_owned()));
        report["domains"]["forgotten_rooms"] = serde_json::json!({"imported": 0});
        let mut plan = test_plan();
        assert!(check_execution_mode(&execution, &plan).is_err());
        plan.preserve_local_history = true;
        let inventory = crate::Inventory {
            server_name: "reilly.asia".to_owned(),
            rooms: vec![crate::RoomScope {
                room_id: "!departed:x".to_owned(),
                version: "10".to_owned(),
                local_members: 0,
                source_events: 10,
            }],
            ..crate::Inventory::default()
        };
        assert!(check_plan_gates(&execution, &plan, &inventory).is_ok());
        let mut legacy = execution.clone();
        legacy.preserve_local_history = false;
        assert!(check_execution_mode(&legacy, &plan).is_err());
        report["domains"]["local_memberships"]["imported"] = serde_json::json!(1);
        std::fs::write(&checkpoint, report.to_string()).unwrap();
        assert!(read_execution(&checkpoint, 0)
            .unwrap()
            .missing_domains
            .contains(&"local_memberships".to_owned()));
        report["validation"]["domains"]["local_memberships"] =
            serde_json::json!([1, ["membership event mismatch"]]);
        std::fs::write(&checkpoint, report.to_string()).unwrap();
        assert!(check_gates(&read_execution(&checkpoint, 0).unwrap(), 1).is_err());
        std::fs::remove_dir_all(dir).unwrap();
    }

    fn tempfile_dir() -> std::path::PathBuf {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let dir = std::env::temp_dir().join(format!(
            "migrator-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }
}

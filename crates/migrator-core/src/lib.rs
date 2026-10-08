//! Pluggable migration core: sources, targets, and plans.
//!
//! A [`Source`] inventories migratable scope (users, rooms, media) from a
//! live system. A [`Target`] receives it. [`Plan`] is the serializable
//! contract the TUI and WebUI both build, preview, and execute. Nothing here
//! touches a network; connectors implement the traits.

mod execute;
mod preset;
mod synapse;

pub use execute::{
    argv, check_execution_mode, check_gates, check_plan_gates, execute, read_execution, Execution,
    RunOptions,
};
pub use preset::reilly_prototype;
pub use synapse::SynapseSource;

use anyhow::Result;
use serde::{Deserialize, Serialize};

/// One migratable room as inventoried from a source.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RoomScope {
    pub room_id: String,
    pub version: String,
    pub local_members: u64,
    pub source_events: u64,
}

/// What the source holds, before anything moves.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Inventory {
    pub server_name: String,
    pub rooms: Vec<RoomScope>,
    pub users: Vec<String>,
    pub excluded: Vec<ExcludedScope>,
}

/// Scope the migrator will not carry, with the reason why.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ExcludedScope {
    pub id: String,
    pub reason: String,
}

/// A frozen migration plan both UIs execute.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Plan {
    pub name: String,
    pub source: SourceConfig,
    pub target: TargetConfig,
    pub only_rooms: Vec<String>,
    /// Carry rooms with any latest local membership, including departed history.
    #[serde(default)]
    pub preserve_local_history: bool,
}

/// Connection config for a migration source.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind")]
pub enum SourceConfig {
    SynapsePostgres {
        /// Postgres connect string, e.g. `host=... port=5432 user=... dbname=synapse`.
        conn: String,
        server_name: String,
        media_dir: String,
    },
}

/// Connection config for a migration target.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind")]
pub enum TargetConfig {
    SpindleImport {
        /// Path to the `spindle` binary carrying `import-synapse`.
        binary: String,
        checkpoint_dir: String,
    },
}

/// Inventory half of a connector.
pub trait Source {
    fn config(&self) -> SourceConfig;
    fn inventory(&self) -> impl std::future::Future<Output = Result<Inventory>> + Send;
}

/// Receive half of a connector.
pub trait Target {
    fn config(&self) -> TargetConfig;
}

impl Plan {
    /// Load an operator-supplied plan, with the prototype as a convenient default.
    /// Runtime overrides are shared by the CLI, TUI, and web server.
    pub fn from_env() -> Result<Self> {
        let mut plan = match std::env::var_os("MIGRATOR_PLAN") {
            Some(path) => serde_json::from_slice::<Self>(&std::fs::read(path)?)?,
            None => reilly_prototype(),
        };
        let SourceConfig::SynapsePostgres {
            conn,
            server_name,
            media_dir,
        } = &mut plan.source;
        if let Ok(value) = std::env::var("MIGRATOR_PG_CONN") {
            *conn = value;
        }
        if let Ok(value) = std::env::var("MIGRATOR_SERVER_NAME") {
            *server_name = value;
        }
        if let Ok(value) = std::env::var("MIGRATOR_MEDIA") {
            *media_dir = value;
        }
        let TargetConfig::SpindleImport { binary, .. } = &mut plan.target;
        if let Ok(value) = std::env::var("MIGRATOR_BINARY") {
            *binary = value;
        }
        match std::env::var("MIGRATOR_PRESERVE_LOCAL_HISTORY") {
            Ok(value) => plan.preserve_local_history = parse_preserve_local_history(&value)?,
            Err(std::env::VarError::NotPresent) => {}
            Err(_) => anyhow::bail!("MIGRATOR_PRESERVE_LOCAL_HISTORY must be UTF-8 true/false/1/0"),
        }
        Ok(plan)
    }

    pub async fn inventory(&self) -> Result<Inventory> {
        let SourceConfig::SynapsePostgres {
            conn,
            server_name,
            media_dir,
        } = &self.source;
        SynapseSource::new(conn.clone(), server_name.clone(), media_dir.clone())
            .with_preserve_local_history(self.preserve_local_history)
            .inventory()
            .await
    }

    /// A display copy never contains database credentials.
    #[must_use]
    pub fn redacted(&self) -> Self {
        let mut plan = self.clone();
        let SourceConfig::SynapsePostgres { conn, .. } = &mut plan.source;
        *conn = "<configured privately>".to_owned();
        plan
    }

    /// Rooms the plan will attempt, after `only_rooms` filtering.
    #[must_use]
    pub fn planned_rooms<'a>(&self, inventory: &'a Inventory) -> Vec<&'a RoomScope> {
        inventory
            .rooms
            .iter()
            .filter(|room| {
                self.only_rooms.is_empty() || self.only_rooms.iter().any(|id| id == &room.room_id)
            })
            .collect()
    }
}

fn parse_preserve_local_history(value: &str) -> Result<bool> {
    match value {
        "true" | "1" => Ok(true),
        "false" | "0" => Ok(false),
        _ => anyhow::bail!("MIGRATOR_PRESERVE_LOCAL_HISTORY must be exactly true/false/1/0"),
    }
}

#[cfg(test)]
mod plan_tests {
    use super::*;

    #[test]
    fn old_plan_defaults_to_joined_scope_and_override_is_strict() {
        let mut value = serde_json::to_value(reilly_prototype()).unwrap();
        value
            .as_object_mut()
            .unwrap()
            .remove("preserve_local_history");
        assert!(
            !serde_json::from_value::<Plan>(value)
                .unwrap()
                .preserve_local_history
        );
        for value in ["true", "1"] {
            assert!(parse_preserve_local_history(value).unwrap());
        }
        for value in ["false", "0"] {
            assert!(!parse_preserve_local_history(value).unwrap());
        }
        for value in ["", "yes", "TRUE", " true", "false ", "2"] {
            assert!(parse_preserve_local_history(value).is_err());
        }
    }
}

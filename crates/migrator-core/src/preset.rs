//! The reilly.asia prototype preset: the first migration this tool ran.

use crate::{Plan, SourceConfig, TargetConfig};

/// Plan skeleton for the reilly.asia prototype.
///
/// Connection secrets stay out: fill `conn` and `binary` at runtime.
#[must_use]
pub fn reilly_prototype() -> Plan {
    Plan {
        name: "reilly.asia prototype".to_owned(),
        source: SourceConfig::SynapsePostgres {
            conn: "host=rehearsal-pg port=5432 user=postgres dbname=synapse".to_owned(),
            server_name: "reilly.asia".to_owned(),
            media_dir: "/source/synapse-media".to_owned(),
        },
        target: TargetConfig::SpindleImport {
            binary: String::new(),
            checkpoint_dir: String::new(),
        },
        only_rooms: Vec::new(),
        preserve_local_history: false,
    }
}

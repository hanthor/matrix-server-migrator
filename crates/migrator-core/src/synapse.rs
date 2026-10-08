//! Synapse Postgres source connector.
//!
//! Discovery mirrors the importer's own query: rooms ordered by id, with the
//! count of locally-joined members from current state. Rooms without local
//! members are reported as excluded, never silently dropped.

use anyhow::{anyhow, Result};

use crate::{ExcludedScope, Inventory, RoomScope, Source, SourceConfig};

pub struct SynapseSource {
    conn: String,
    server_name: String,
    media_dir: String,
    preserve_local_history: bool,
}

impl SynapseSource {
    #[must_use]
    pub fn new(conn: String, server_name: String, media_dir: String) -> Self {
        Self {
            conn,
            server_name,
            media_dir,
            preserve_local_history: false,
        }
    }

    #[must_use]
    pub fn with_preserve_local_history(mut self, enabled: bool) -> Self {
        self.preserve_local_history = enabled;
        self
    }

    /// The discovery query, kept as a value so the TUI/WebUI can show the
    /// exact SQL a plan will run before it runs.
    #[must_use]
    pub fn discovery_sql(server_name: &str) -> (String, String) {
        (
            "WITH event_counts AS ( \
               SELECT room_id, count(*) AS source_events, \
                      count(*) FILTER (WHERE type = 'm.room.message' \
                        AND right(sender, length($1) + 1) = ':' || $1) AS local_messages, \
                      count(*) FILTER (WHERE type = 'm.room.encrypted' \
                        AND right(sender, length($1) + 1) = ':' || $1) AS local_encrypted \
                 FROM events GROUP BY room_id \
             ), joined_counts AS ( \
               SELECT state.room_id, count(*) AS local_members \
                 FROM current_state_events AS state \
                 INNER JOIN room_memberships AS member ON member.event_id = state.event_id \
                WHERE state.type = 'm.room.member' \
                  AND right(state.state_key, length($1) + 1) = ':' || $1 \
                  AND member.membership = 'join' GROUP BY state.room_id \
             ), latest_local AS ( \
               SELECT room_id, count(*) AS memberships, \
                      count(*) FILTER (WHERE membership = 'invite') AS invites, \
                      count(*) FILTER (WHERE membership = 'leave') AS leaves \
                 FROM local_current_membership \
                WHERE right(user_id, length($1) + 1) = ':' || $1 GROUP BY room_id \
             ) \
             SELECT room.room_id, COALESCE(room.room_version, '1'), \
                    COALESCE(event_counts.source_events, 0), \
                    COALESCE(joined_counts.local_members, 0), \
                    COALESCE(latest_local.invites, 0), COALESCE(latest_local.leaves, 0), \
                    COALESCE(event_counts.local_messages, 0), \
                    COALESCE(event_counts.local_encrypted, 0), \
                    COALESCE(latest_local.memberships, 0) \
               FROM rooms AS room \
               LEFT JOIN event_counts ON event_counts.room_id = room.room_id \
               LEFT JOIN joined_counts ON joined_counts.room_id = room.room_id \
               LEFT JOIN latest_local ON latest_local.room_id = room.room_id \
               ORDER BY room.room_id"
                .to_owned(),
            server_name.to_owned(),
        )
    }
}

impl Source for SynapseSource {
    fn config(&self) -> SourceConfig {
        SourceConfig::SynapsePostgres {
            conn: self.conn.clone(),
            server_name: self.server_name.clone(),
            media_dir: self.media_dir.clone(),
        }
    }

    async fn inventory(&self) -> Result<Inventory> {
        let password = match std::env::var("SPINDLE_SYNAPSE_PASSWORD") {
            Ok(value) => Some(value),
            Err(std::env::VarError::NotPresent) => None,
            Err(_) => return Err(anyhow!("SPINDLE_SYNAPSE_PASSWORD must be UTF-8")),
        };
        let config = source_connection_config(&self.conn, password.as_deref())?;
        let (mut client, connection) =
            config.connect(tokio_postgres::NoTls).await.map_err(|_| {
                anyhow!("Synapse source connection failed; connection details suppressed")
            })?;
        tokio::spawn(async move {
            if connection.await.is_err() {
                eprintln!("Synapse source connection closed; connection details suppressed");
            }
        });
        let snapshot = client
            .build_transaction()
            .read_only(true)
            .isolation_level(tokio_postgres::IsolationLevel::RepeatableRead)
            .start()
            .await?;
        let (sql, server) = Self::discovery_sql(&self.server_name);
        let mut rooms = Vec::new();
        let mut users = Vec::new();
        let mut excluded = Vec::new();
        for row in snapshot.query(&sql, &[&server]).await? {
            let room_id: String = row.get(0);
            let version: String = row.get(1);
            let source_events: i64 = row.get(2);
            let local_members: i64 = row.get(3);
            if local_members == 0 && !(self.preserve_local_history && row.get::<_, i64>(8) > 0) {
                excluded.push(ExcludedScope {
                    id: room_id,
                    reason: format!(
                        "no local joined members; outside the retained scope; \
                         source events: {source_events}; latest local invites: {}; \
                         latest local leaves: {}; historical local message events: {}; \
                         historical local encrypted events: {}",
                        row.get::<_, i64>(4),
                        row.get::<_, i64>(5),
                        row.get::<_, i64>(6),
                        row.get::<_, i64>(7),
                    ),
                });
                continue;
            }
            rooms.push(RoomScope {
                room_id,
                version,
                local_members: local_members as u64,
                source_events: source_events as u64,
            });
        }
        for row in snapshot
            .query("SELECT name FROM users ORDER BY name", &[])
            .await?
        {
            let user_id: String = row.get(0);
            if user_id.ends_with(&format!(":{}", self.server_name)) {
                users.push(user_id);
            }
        }
        snapshot.commit().await?;
        Ok(Inventory {
            server_name: self.server_name.clone(),
            rooms,
            users,
            excluded,
        })
    }
}

/// Parse privately: Config's parser errors can contain the connection string.
pub(crate) fn source_connection_config(
    conn: &str,
    password_override: Option<&str>,
) -> Result<tokio_postgres::Config> {
    let mut config: tokio_postgres::Config = conn
        .parse()
        .map_err(|_| anyhow!("Invalid Synapse connection configuration; details suppressed"))?;
    if let Some(password) = password_override {
        config.password(password);
    }
    Ok(config)
}

/// An executable's argv must never carry a connection-string password.
pub(crate) fn reject_inline_password(conn: &str) -> Result<()> {
    let config = source_connection_config(conn, None)?;
    if config.get_password().is_some() {
        return Err(anyhow!(
            "Remove the inline connection password and set SPINDLE_SYNAPSE_PASSWORD instead"
        ));
    }
    Ok(())
}

#[cfg(test)]
mod credential_tests {
    use super::*;

    #[test]
    fn keyword_and_uri_passwords_never_enter_executable_arguments() {
        for conn in [
            "host=db user=test password=private-keyword-secret dbname=synapse",
            "postgresql://test:private-uri-secret@db/synapse",
            "postgres://test:percent%2Dencoded%2Dsecret@db/synapse",
            "postgresql://test@db/synapse?password=private-query-secret",
            "host=db password=''",
        ] {
            let error = reject_inline_password(conn).unwrap_err().to_string();
            assert!(error.contains("SPINDLE_SYNAPSE_PASSWORD"));
            assert!(!error.contains("private"));
            assert!(!error.contains(conn));
        }
        for conn in [
            "host=db user=test dbname=synapse",
            "postgresql://test@db/synapse",
        ] {
            assert!(reject_inline_password(conn).is_ok());
        }
    }

    #[test]
    fn environment_password_override_is_applied_without_global_env_mutation() {
        for conn in [
            "host=db password=old-password",
            "postgresql://test:old-password@db/synapse",
            "host=db",
        ] {
            let config = source_connection_config(conn, Some("override-password")).unwrap();
            assert_eq!(config.get_password(), Some(&b"override-password"[..]));
        }
        let config = source_connection_config("host=db password=old-password", None).unwrap();
        assert_eq!(config.get_password(), Some(&b"old-password"[..]));
        let error = source_connection_config("password=private-secret unknown_option=value", None)
            .unwrap_err()
            .to_string();
        assert_eq!(
            error,
            "Invalid Synapse connection configuration; details suppressed"
        );
    }
}

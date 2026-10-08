//! Read-only inventory probe: prints room/user counts, nothing else.

use migrator_core::{Source, SynapseSource};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let conn = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "host=127.0.0.1 port=5432 user=postgres dbname=synapse".to_owned());
    let source = SynapseSource::new(conn, "reilly.asia".to_owned(), String::new());
    let inventory = source.inventory().await?;
    let events: u64 = inventory.rooms.iter().map(|room| room.source_events).sum();
    println!(
        "server={} rooms={} users={} source_events={}",
        inventory.server_name,
        inventory.rooms.len(),
        inventory.users.len(),
        events
    );
    Ok(())
}

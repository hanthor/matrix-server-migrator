//! Root-operated public signing key check on an independent cold restored copy.
//! No seed/document output. Existing exactly-one key checked before loader.
use std::{error::Error, path::Path};
use spindle_store::{FjallStore, ReadView};
use spindle_server::signing::ServerKey;
fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len()!=3 || args[1]!="--independent-cold-copy" {
        return Err("usage: signing-public --independent-cold-copy STORE".into());
    }
    let root=Path::new(&args[2]);
    if !root.is_dir() {return Err("existing cold restored copy required".into());}
    let store=FjallStore::open(root)?;
    // Same schema1/ServerKey prefix as the final candidate. The store holds
    // its exclusive Fjall lock throughout; no other process can race loader.
    let count=store.scan_prefix(&[1u8,0x0au8])?.len();
    if count!=1 {return Err("exactly one EXISTING server key required; refusing key generation".into());}
    let key=ServerKey::load_or_create(&store)?;
    println!("{}",serde_json::json!({"key_id":key.key_id(),"public_key_base64":key.public_key_base64(),"existing_key_rows":count}));
    Ok(())
}

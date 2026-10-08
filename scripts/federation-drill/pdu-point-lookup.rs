//! Compile against final candidate's already-built spindle_store/serde_json rlibs.
//! Opening a Fjall store may recover its metadata: use ONLY the cold restored
//! disposable copy, never an active/original store. No scan or write API used.
use std::{error::Error, fs, io::{self, Write}, path::Path};
use spindle_store::{FjallStore, ReadView};
use serde_json::{Value, json};
fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 4 || args[1] != "--independent-cold-copy" {
        return Err("usage: helper --independent-cold-copy STORE SOURCE-SAMPLES.json".into());
    }
    let root = Path::new(&args[2]);
    if !root.is_dir() { return Err("existing cold copy directory required".into()); }
    let raw = fs::read(&args[3])?;
    if raw.len() > 16*1024*1024 { return Err("bounded sample file exceeded".into()); }
    let samples: Vec<Value> = serde_json::from_slice(&raw)?;
    if samples.is_empty() || samples.len() > 30 { return Err("sample count out of bounds".into()); }
    let store = FjallStore::open(root)?;
    let mut out = io::BufWriter::new(io::stdout().lock());
    out.write_all(b"[")?;
    for (i, sample) in samples.iter().enumerate() {
        let room = sample["room_id"].as_str().ok_or("room missing")?;
        let event = sample["event_id"].as_str().ok_or("event missing")?;
        let len = u16::try_from(room.len())?;
        // spindle_core::keys::room_prefix(EventIndex,room)+event_id.
        let mut key = vec![1u8,2u8]; key.extend_from_slice(&len.to_be_bytes());
        key.extend_from_slice(room.as_bytes()); key.extend_from_slice(event.as_bytes());
        let pdu = match store.get(&key)? {
            Some(body) => {
                if body.len()>16*1024*1024 { return Err("bounded body exceeded".into()); }
                serde_json::from_slice::<Value>(&body)?
            }, None => Value::Null
        };
        if i>0 {out.write_all(b",")?;}
        serde_json::to_writer(&mut out,&json!({"room_id":room,"event_id":event,"pdu":pdu}))?;
    }
    out.write_all(b"]\n")?; out.flush()?;
    Ok(())
}

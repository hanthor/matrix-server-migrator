//! Diagnostic only: exact final release rlibs, no server/store/DB opened.
use spindle_server::import::{SourceEvent,SourceRoom,SourceState,StateMap,Resolution,Continuity,replay_resolving};
use spindle_core::{EventId,StateKey,StateSnapshot};
use serde_json::json;

struct FixtureSource {current:StateMap,full_compare_last:bool,resolver_calls:usize,last_state_reads:usize}
impl SourceState for FixtureSource {
    fn state_after(&mut self,event:&str)->Result<StateMap,String>{
        if event!="$merge" {return Err("fixture only stores merged state".into());}
        self.last_state_reads+=1;Ok(self.current.clone())
    }
    fn state_after_keys(&mut self,event:&str,keys:&[(String,String)])->Result<StateMap,String>{
        let mut state=self.state_after(event)?;state.retain(|key,_|keys.contains(key));Ok(state)
    }
    fn resolve(&mut self,_sets:&[&StateSnapshot])->Option<Result<Resolution,String>>{
        self.resolver_calls+=1;
        Some(Ok(Resolution{contested:vec![StateKey::new("m.room.topic","")],
            slots:vec![(StateKey::new("m.room.topic",""),Some("$topic_a".to_owned()))]}))
    }
    fn continuity(&mut self,event:&str,_parents:&[EventId],_is_state:bool)->Continuity{
        if self.full_compare_last && event=="$merge" {Continuity::Unknown}else{Continuity::Derived}
    }
}
fn event(id:&str,kind:&str,state_key:Option<&str>,parents:&[&str],depth:u64)->SourceEvent{
    SourceEvent{event_id:id.into(),event_type:kind.into(),state_key:state_key.map(str::to_owned),
        prev_events:parents.iter().map(|s|s.to_string()).collect(),depth,stream_ordering:depth as i64,
        outlier:false,rejected:false}
}
fn main()->Result<(),Box<dyn std::error::Error>>{
    let current=StateMap::from([
        (("m.room.create".into(),"".into()),"$create".into()),
        (("m.room.topic".into(),"".into()),"$topic_a".into()),
        (("m.room.member".into(),"@synthetic:example.invalid".into()),"$member_from_source".into())]);
    let room=SourceRoom{room_id:"!head-regression:example.invalid".into(),events:vec![
        event("$create","m.room.create",Some(""),&[],1),
        event("$topic_a","m.room.topic",Some(""),&["$create"],2),
        event("$topic_b","m.room.topic",Some(""),&["$create"],3),
        event("$merge","m.room.message",None,&["$topic_a","$topic_b"],4)],
        current_state:current.clone(),state_after_root:None,forward_extremities:vec!["$merge".into()]};
    let mut rows=Vec::new();
    for (name,head,full_compare) in [("natural_resolver_state",false,false),
        ("head_flag_with_resolver_state",true,false),("head_flag_and_cached_current_full_compare",true,true)]{
        let mut source=FixtureSource{current:current.clone(),full_compare_last:full_compare,resolver_calls:0,last_state_reads:0};
        let result=replay_resolving(&room,&mut source,head)?;
        let missing=result.outcome.divergence.iter().any(|d|d.key.event_type().as_str()=="m.room.member" && d.spindle.is_none() && d.synapse.as_deref()==Some("$member_from_source"));
        rows.push(json!({"case":name,"head_from_source":head,"last_continuity_unknown":full_compare,
          "clean":result.outcome.clean(),"passes":result.passes,"divergence_slots":result.outcome.divergence.len(),
          "missing_source_current_membership":missing,"resolver_calls":source.resolver_calls,
          "last_state_reads":source.last_state_reads,"supplied_states":result.from_source.len()}));
    }
    assert_eq!(rows[0]["clean"],false);assert_eq!(rows[1]["clean"],false);
    assert_eq!(rows[1]["missing_source_current_membership"],true);
    assert_eq!(rows[2]["clean"],true);
    println!("{}",json!({"diagnostic_passed":true,"candidate_sha256":"4036a6073191eea49a8bfc58af4b1dbef096774c89cbabf6569ef158b3bba092", "cases":rows,
      "scope":"Four synthetic events; derivation-layer diagnostic only, not real corpus/auth/signature replay.",
      "conclusion":"head_from_source flag alone does not force current state when resolver-derived Want.state is Some. The cached-current full-comparison path repairs this fixture."}));
    Ok(())
}

//! Local dashboard over shared plan loading, execution, and read-back gates.
use axum::{
    extract::State,
    http::StatusCode,
    response::Html,
    routing::{get, post},
    Json, Router,
};
use migrator_core::{Execution, Plan, RunOptions, SourceConfig};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    path::PathBuf,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
};
use tokio::sync::Mutex;

#[derive(Clone)]
struct AppState {
    runs: Arc<Mutex<HashMap<u64, RunRecord>>>,
    next_id: Arc<AtomicU64>,
}

#[derive(Clone)]
struct RunRecord {
    status: String,
    execution: Option<Execution>,
    gate_error: Option<String>,
}

fn escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

async fn dashboard() -> Html<String> {
    let name = match Plan::from_env() {
        Ok(plan) => plan.name,
        Err(error) => error.to_string(),
    };
    Html(r#"<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Matrix migrator</title>
<style>body{font-family:system-ui,sans-serif;max-width:64rem;margin:3rem auto;padding:0 1rem;color:#202c35;background:#f7f8fa}h1{font-size:2rem}section{padding:1.5rem;background:white;border:1px solid #d5dde3;border-radius:8px;margin:1rem 0}button{background:#215b82;color:white;border:0;border-radius:5px;padding:.7rem 1rem;margin:.3rem;cursor:pointer}button:disabled{opacity:.5}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f5;padding:1rem;border-radius:5px}a{color:#215b82}p{line-height:1.5}</style></head><body>
<h1>Matrix homeserver migrator</h1><p id="plan-name">__PLAN__</p>
<section><h2>Source inventory</h2><p>Preview the retained rooms, local users, and every scope exclusion.</p>
<button id="inventory">Read inventory</button><a href="/api/plan">View plan</a><pre id="inventory-result">Inventory has not been read.</pre></section>
<section><h2>Rehearsal and validation</h2><p>A dry run replays source history. Validation checks an existing imported store. Real imports run through the operator CLI.</p>
<button id="dry">Start dry run</button><button id="validate">Validate imported store</button><pre id="run-result">No run started.</pre></section>
<section><h2>Saved checkpoint</h2><p>Inspect the last saved read-back evidence. A passing report still needs a tested backup, client witness, and federation drill before cutover.</p>
<button id="report">Inspect checkpoint</button><pre id="report-result">No checkpoint inspected.</pre></section>
<script>
const show=(id,value)=>document.getElementById(id).textContent=JSON.stringify(value,null,2);
async function request(path,options){const response=await fetch(path,options);return await response.json()}
document.getElementById('inventory').onclick=async()=>{try{show('inventory-result',await request('/api/inventory'))}catch(e){show('inventory-result',{error:String(e)})}};
document.getElementById('report').onclick=async()=>{try{show('report-result',await request('/api/report'))}catch(e){show('report-result',{error:String(e)})}};
async function run(validate){const buttons=[document.getElementById('dry'),document.getElementById('validate')];buttons.forEach(b=>b.disabled=true);try{let result=await request('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dry_run:!validate,validate_only:validate})});show('run-result',result);while(result.run_id&&result.status==='running'){await new Promise(r=>setTimeout(r,3000));result=await request('/api/run/'+result.run_id);show('run-result',result)}}catch(e){show('run-result',{error:String(e)})}finally{buttons.forEach(b=>b.disabled=false)}}
document.getElementById('dry').onclick=()=>run(false);document.getElementById('validate').onclick=()=>run(true);
</script></body></html>"#.replace("__PLAN__", &escape(&name)))
}

async fn plan() -> Json<Value> {
    Json(match Plan::from_env() {
        Ok(plan) => serde_json::to_value(plan.redacted()).unwrap_or_default(),
        Err(error) => json!({"error": format!("{error:#}")}),
    })
}

async fn inventory() -> Json<Value> {
    let result = match Plan::from_env() {
        Ok(plan) => plan.inventory().await,
        Err(error) => Err(error),
    };
    Json(match result {
        Ok(inventory) => serde_json::to_value(inventory).unwrap_or_default(),
        Err(error) => json!({"error": format!("{error:#}")}),
    })
}

async fn report() -> Json<Value> {
    let result = (|| -> anyhow::Result<Value> {
        let checkpoint = PathBuf::from(std::env::var("MIGRATOR_CHECKPOINT")?);
        let expected = std::env::var("MIGRATOR_EXPECTED_ROOMS")?.parse::<usize>()?;
        let execution = migrator_core::read_execution(&checkpoint, 0)?;
        let plan = Plan::from_env()?;
        let gates = migrator_core::check_execution_mode(&execution, &plan)
            .and_then(|()| migrator_core::check_gates(&execution, expected));
        Ok(
            json!({"execution": execution, "saved_report_gates_passed": gates.is_ok(),
            "gate_error": gates.err().map(|error| error.to_string()),
            "note": "saved evidence; use Validate imported store for a fresh read-back"}),
        )
    })();
    Json(result.unwrap_or_else(|error| json!({"error": format!("set MIGRATOR_CHECKPOINT and MIGRATOR_EXPECTED_ROOMS: {error}")})))
}

#[derive(serde::Deserialize)]
struct RunRequest {
    #[serde(default)]
    plan: Option<Plan>,
    #[serde(default)]
    dry_run: bool,
    #[serde(default)]
    validate_only: bool,
    #[serde(default)]
    no_validate: bool,
    #[serde(default)]
    spindle_config: Option<PathBuf>,
    #[serde(default)]
    checkpoint: Option<PathBuf>,
    #[serde(default)]
    pg_conn: Option<String>,
}

fn error(status: StatusCode, message: impl ToString) -> (StatusCode, Json<Value>) {
    (status, Json(json!({"error": message.to_string()})))
}

async fn run_start(
    State(state): State<AppState>,
    Json(request): Json<RunRequest>,
) -> (StatusCode, Json<Value>) {
    if !request.dry_run && !request.validate_only {
        return error(
            StatusCode::BAD_REQUEST,
            "use the CLI for real imports; the API starts dry runs or validation",
        );
    }
    if request.validate_only && (request.dry_run || request.no_validate) {
        return error(
            StatusCode::BAD_REQUEST,
            "validate-only cannot be combined with dry-run or no-validate",
        );
    }
    let mut plan = match request.plan.map(Ok).unwrap_or_else(Plan::from_env) {
        Ok(plan) => plan,
        Err(err) => return error(StatusCode::BAD_REQUEST, err),
    };
    let config = match request
        .spindle_config
        .or_else(|| std::env::var_os("MIGRATOR_CONFIG").map(PathBuf::from))
    {
        Some(path) => path,
        None => return error(StatusCode::BAD_REQUEST, "set MIGRATOR_CONFIG"),
    };
    let checkpoint = match request
        .checkpoint
        .or_else(|| std::env::var_os("MIGRATOR_CHECKPOINT").map(PathBuf::from))
    {
        Some(path) => path,
        None => return error(StatusCode::BAD_REQUEST, "set MIGRATOR_CHECKPOINT"),
    };
    let SourceConfig::SynapsePostgres { conn, .. } = &plan.source;
    let conn = request.pg_conn.unwrap_or_else(|| conn.clone());
    let SourceConfig::SynapsePostgres {
        conn: plan_conn, ..
    } = &mut plan.source;
    *plan_conn = conn.clone();
    let options = RunOptions {
        dry_run: request.dry_run,
        validate_only: request.validate_only,
        no_validate: request.no_validate,
    };
    if let Err(err) = migrator_core::argv(&plan, &config, &conn, &checkpoint, &options) {
        return error(StatusCode::BAD_REQUEST, err);
    }
    let mut records = state.runs.lock().await;
    if records.values().any(|record| record.status == "running") {
        return error(StatusCode::CONFLICT, "an operation is already running");
    }
    let id = state.next_id.fetch_add(1, Ordering::SeqCst);
    records.insert(
        id,
        RunRecord {
            status: "running".to_owned(),
            execution: None,
            gate_error: None,
        },
    );
    drop(records);
    let runs = state.runs.clone();
    tokio::spawn(async move {
        let result = async {
            let expected = if options.validate_only {
                Some(plan.inventory().await?)
            } else {
                None
            };
            let execution =
                migrator_core::execute(&plan, &config, &conn, &checkpoint, &options).await?;
            let current = if options.validate_only {
                Some(plan.inventory().await?)
            } else {
                None
            };
            let gate_error = expected.into_iter().chain(current).find_map(|inventory| {
                migrator_core::check_plan_gates(&execution, &plan, &inventory)
                    .err()
                    .map(|error| error.to_string())
            });
            Ok::<_, anyhow::Error>((execution, gate_error))
        }
        .await;
        let record = match result {
            Ok((execution, gate_error)) => RunRecord {
                status: if execution.returncode == 0 && gate_error.is_none() {
                    "complete"
                } else {
                    "failed"
                }
                .to_owned(),
                execution: Some(execution),
                gate_error,
            },
            Err(err) => RunRecord {
                status: "failed".to_owned(),
                execution: None,
                gate_error: Some(err.to_string()),
            },
        };
        runs.lock().await.insert(id, record);
    });
    (
        StatusCode::ACCEPTED,
        Json(json!({"run_id": id, "status": "running"})),
    )
}

async fn run_status(
    State(state): State<AppState>,
    axum::extract::Path(id): axum::extract::Path<u64>,
) -> (StatusCode, Json<Value>) {
    let guard = state.runs.lock().await;
    match guard.get(&id) {
        Some(record) => (
            StatusCode::OK,
            Json(json!({"run_id": id, "status": record.status,
            "execution": record.execution, "gate_error": record.gate_error})),
        ),
        None => error(
            StatusCode::NOT_FOUND,
            "unknown run id; saved checkpoint evidence is at /api/report",
        ),
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    Plan::from_env()?;
    let state = AppState {
        runs: Arc::new(Mutex::new(HashMap::new())),
        next_id: Arc::new(AtomicU64::new(1)),
    };
    let app = Router::new()
        .route("/", get(dashboard))
        .route("/api/plan", get(plan))
        .route("/api/inventory", get(inventory))
        .route("/api/inventory/run", post(inventory))
        .route("/api/report", get(report))
        .route("/api/run", post(run_start))
        .route("/api/run/{id}", get(run_status))
        .with_state(state);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:8471").await?;
    println!("migrator-web on http://127.0.0.1:8471");
    axum::serve(listener, app).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state() -> AppState {
        AppState {
            runs: Arc::new(Mutex::new(HashMap::new())),
            next_id: Arc::new(AtomicU64::new(1)),
        }
    }

    fn request() -> RunRequest {
        let mut plan = migrator_core::reilly_prototype();
        let migrator_core::TargetConfig::SpindleImport { binary, .. } = &mut plan.target;
        *binary = "/bin/true".to_owned();
        RunRequest {
            plan: Some(plan),
            dry_run: true,
            validate_only: false,
            no_validate: false,
            spindle_config: Some(PathBuf::from("/unused.toml")),
            checkpoint: Some(PathBuf::from("/unused.json")),
            pg_conn: None,
        }
    }

    #[tokio::test]
    async fn rejects_concurrent_jobs_before_touching_the_store() {
        let state = state();
        state.runs.lock().await.insert(
            1,
            RunRecord {
                status: "running".to_owned(),
                execution: None,
                gate_error: None,
            },
        );
        let (code, _) = run_start(State(state), Json(request())).await;
        assert_eq!(code, StatusCode::CONFLICT);
    }

    #[tokio::test]
    async fn rejects_writes_and_conflicting_validation_modes() {
        let mut invalid = request();
        invalid.dry_run = false;
        let (code, _) = run_start(State(state()), Json(invalid)).await;
        assert_eq!(code, StatusCode::BAD_REQUEST);
        let mut invalid = request();
        invalid.validate_only = true;
        let (code, _) = run_start(State(state()), Json(invalid)).await;
        assert_eq!(code, StatusCode::BAD_REQUEST);
    }
}

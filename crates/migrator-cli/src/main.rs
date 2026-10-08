use std::path::PathBuf;

use anyhow::{Context, Result};
use migrator_core::{
    check_execution_mode, check_gates, check_plan_gates, execute, read_execution, Plan, RunOptions,
    SourceConfig,
};

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let command = args.first().map(String::as_str).unwrap_or("help");
    if command == "help" || command == "--help" {
        println!(
            "migrator-cli plan|inventory|preview|dry-run|import|validate\n\
            migrator-cli report CHECKPOINT EXPECTED_ROOMS\n\
            Set MIGRATOR_PLAN (optional JSON plan), MIGRATOR_BINARY, MIGRATOR_CONFIG,\n\
            MIGRATOR_CHECKPOINT, and MIGRATOR_PG_CONN. Import writes the target store.\n\
            Report checks saved evidence; validate performs a fresh read-back."
        );
        return Ok(());
    }
    if command == "report" {
        anyhow::ensure!(args.len() == 3, "usage: report CHECKPOINT EXPECTED_ROOMS");
        let expected = args[2].parse::<usize>().context("invalid room count")?;
        let execution = read_execution(&PathBuf::from(&args[1]), 0)?;
        println!("{}", serde_json::to_string_pretty(&execution)?);
        check_execution_mode(&execution, &Plan::from_env()?)?;
        return check_gates(&execution, expected);
    }
    anyhow::ensure!(
        args.len() == 1,
        "unexpected arguments; use environment configuration"
    );
    let plan = Plan::from_env()?;
    match command {
        "plan" => println!("{}", serde_json::to_string_pretty(&plan.redacted())?),
        "inventory" => println!(
            "{}",
            serde_json::to_string_pretty(&plan.inventory().await?)?
        ),
        "preview" | "dry-run" | "import" | "validate" => {
            let config =
                PathBuf::from(std::env::var("MIGRATOR_CONFIG").context("set MIGRATOR_CONFIG")?);
            let checkpoint = PathBuf::from(
                std::env::var("MIGRATOR_CHECKPOINT").context("set MIGRATOR_CHECKPOINT")?,
            );
            let SourceConfig::SynapsePostgres { conn, .. } = &plan.source;
            let options = RunOptions {
                dry_run: command == "dry-run" || command == "preview",
                validate_only: command == "validate",
                ..RunOptions::default()
            };
            if command == "preview" {
                let mut args = migrator_core::argv(&plan, &config, conn, &checkpoint, &options)?;
                args[3] = "<database connection>".to_owned();
                println!("{}", serde_json::to_string_pretty(&args)?);
                return Ok(());
            }
            // Freeze expected scope before execution. The importer owns its
            // source snapshot; cutover still requires a quiescent source.
            let inventory = plan.inventory().await?;
            let expected = plan.planned_rooms(&inventory).len();
            anyhow::ensure!(expected != 0, "the plan selects no rooms");
            let execution = execute(&plan, &config, conn, &checkpoint, &options).await?;
            println!("{}", serde_json::to_string_pretty(&execution)?);
            anyhow::ensure!(
                execution.returncode == 0,
                "importer exited with {}",
                execution.returncode
            );
            if !options.dry_run {
                check_plan_gates(&execution, &plan, &inventory)?;
                check_plan_gates(&execution, &plan, &plan.inventory().await?)?;
            }
        }
        _ => anyhow::bail!("unknown command {command}; use --help"),
    }
    Ok(())
}

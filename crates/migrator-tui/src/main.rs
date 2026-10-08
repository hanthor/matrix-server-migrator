//! Responsive terminal views over the shared migration plan and executor.
use std::{path::PathBuf, sync::mpsc, time::Duration};

use anyhow::Result;
use migrator_core::{Execution, Inventory, Plan, RunOptions, SourceConfig};
use ratatui::{
    layout::{Constraint, Direction, Layout},
    widgets::{Block, Borders, List, ListItem, Paragraph},
    Frame,
};

#[derive(Clone, Copy, PartialEq, Eq)]
enum Screen {
    Plans,
    Inventory,
    Run,
}

enum Update {
    Inventory(Result<Inventory>),
    Run(Result<(Execution, Option<String>)>),
}

struct App {
    screen: Screen,
    plan: Plan,
    inventory: Inventory,
    log: Vec<String>,
    pending: Option<mpsc::Receiver<Update>>,
}

impl App {
    fn new() -> Result<Self> {
        let plan = Plan::from_env()?;
        let SourceConfig::SynapsePostgres { server_name, .. } = &plan.source;
        Ok(Self {
            screen: Screen::Plans,
            inventory: Inventory {
                server_name: server_name.clone(),
                ..Inventory::default()
            },
            plan,
            log: vec!["plan loaded; 2 inventories, 3 previews, x runs dry, v validates".to_owned()],
            pending: None,
        })
    }

    fn settings(&self) -> Result<(String, PathBuf, PathBuf)> {
        let SourceConfig::SynapsePostgres { conn, .. } = &self.plan.source;
        Ok((
            conn.clone(),
            std::env::var("MIGRATOR_CONFIG").map(PathBuf::from)?,
            std::env::var("MIGRATOR_CHECKPOINT").map(PathBuf::from)?,
        ))
    }

    fn start(&mut self, task: impl FnOnce() -> Update + Send + 'static) {
        if self.pending.is_some() {
            self.log.push("an operation is already running".to_owned());
            return;
        }
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let _ = sender.send(task());
        });
        self.pending = Some(receiver);
    }

    fn fill_inventory(&mut self) {
        if self.pending.is_some() {
            return;
        }
        let plan = self.plan.clone();
        self.log.push("reading live inventory...".to_owned());
        self.start(move || {
            Update::Inventory(runtime().and_then(|rt| rt.block_on(plan.inventory())))
        });
    }

    fn show_argv(&mut self) {
        let result = self.settings().and_then(|(conn, config, checkpoint)| {
            migrator_core::argv(
                &self.plan,
                &config,
                &conn,
                &checkpoint,
                &RunOptions {
                    dry_run: true,
                    ..RunOptions::default()
                },
            )
        });
        match result {
            Ok(mut args) => {
                args[3] = "<database connection>".to_owned();
                self.log.push(format!("dry-run argv: {args:?}"));
            }
            Err(error) => self.log.push(format!("preview: {error}")),
        }
    }

    fn run(&mut self, validate_only: bool) {
        if self.pending.is_some() {
            self.log.push("an operation is already running".to_owned());
            return;
        }
        let (conn, config, checkpoint) = match self.settings() {
            Ok(settings) => settings,
            Err(error) => {
                self.log.push(format!("configuration: {error}"));
                return;
            }
        };
        let plan = self.plan.clone();
        self.log.push(format!(
            "{} started; log: {}",
            if validate_only {
                "validation"
            } else {
                "dry run"
            },
            checkpoint.with_extension("run.log").display()
        ));
        self.screen = Screen::Run;
        self.start(move || {
            Update::Run(runtime().and_then(|rt| {
                rt.block_on(async {
                    let inventory = plan.inventory().await?;

                    let execution = migrator_core::execute(
                        &plan,
                        &config,
                        &conn,
                        &checkpoint,
                        &RunOptions {
                            dry_run: !validate_only,
                            validate_only,
                            ..RunOptions::default()
                        },
                    )
                    .await?;
                    let gates = if validate_only {
                        let current = plan.inventory().await?;
                        Some(match migrator_core::check_plan_gates(&execution, &plan, &inventory)
                            .and_then(|()| migrator_core::check_plan_gates(&execution, &plan, &current)) {
                            Ok(()) => {
                                "read-back gates passed; backup, client and federation gates remain"
                                    .to_owned()
                            }
                            Err(error) => error.to_string(),
                        })
                    } else {
                        None
                    };
                    Ok((execution, gates))
                })
            }))
        });
    }

    fn poll(&mut self) {
        let Some(receiver) = &self.pending else {
            return;
        };
        let update = match receiver.try_recv() {
            Ok(update) => update,
            Err(mpsc::TryRecvError::Empty) => return,
            Err(mpsc::TryRecvError::Disconnected) => {
                self.pending = None;
                self.log
                    .push("background operation stopped without a result".to_owned());
                return;
            }
        };
        self.pending = None;
        match update {
            Update::Inventory(Ok(inventory)) => {
                self.log.push(format!(
                    "inventory: {} rooms, {} users, {} outside scope",
                    inventory.rooms.len(),
                    inventory.users.len(),
                    inventory.excluded.len()
                ));
                self.inventory = inventory;
            }
            Update::Run(Ok((execution, gates))) => {
                self.log.push(format!(
                    "run finished: rooms={} excluded={} checked={} returncode={}",
                    execution.rooms,
                    execution.excluded_rooms,
                    execution.rooms_checked,
                    execution.returncode
                ));
                if let Some(gates) = gates {
                    self.log.push(gates);
                }
            }
            Update::Inventory(Err(error)) | Update::Run(Err(error)) => {
                self.log.push(format!("failed: {error}"))
            }
        }
    }
}

fn runtime() -> Result<tokio::runtime::Runtime> {
    Ok(tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?)
}

fn draw(frame: &mut Frame, app: &App) {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Min(0)])
        .split(frame.area());
    frame.render_widget(
        Paragraph::new(format!(
            "{}  1 plans | 2 inventory | 3 run | x dry | v validate | q quit{}",
            app.plan.name,
            if app.pending.is_some() {
                "  [running]"
            } else {
                ""
            }
        ))
        .block(Block::default().borders(Borders::ALL)),
        rows[0],
    );
    let body = match app.screen {
        Screen::Plans => List::new([ListItem::new(app.plan.name.as_str())])
            .block(Block::default().title("Plans").borders(Borders::ALL)),
        Screen::Inventory => List::new(app.inventory.rooms.iter().map(|room| {
            ListItem::new(format!(
                "{}  v{}  {} members  {} events",
                room.room_id, room.version, room.local_members, room.source_events
            ))
        }))
        .block(
            Block::default()
                .title(format!("Inventory ({})", app.inventory.rooms.len()))
                .borders(Borders::ALL),
        ),
        Screen::Run => List::new(
            app.log
                .iter()
                .skip(
                    app.log
                        .len()
                        .saturating_sub(rows[1].height.saturating_sub(2) as usize),
                )
                .map(|line| ListItem::new(line.as_str())),
        )
        .block(Block::default().title("Run").borders(Borders::ALL)),
    };
    frame.render_widget(body, rows[1]);
}

fn main() -> Result<()> {
    use crossterm::{
        event::{self, Event, KeyCode, KeyEventKind},
        execute,
        terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
    };
    use ratatui::{backend::CrosstermBackend, Terminal};
    let mut app = App::new()?;
    enable_raw_mode()?;
    let mut out = std::io::stdout();
    execute!(out, EnterAlternateScreen)?;
    let mut term = Terminal::new(CrosstermBackend::new(out))?;
    let result = (|| -> Result<()> {
        loop {
            app.poll();
            term.draw(|frame| draw(frame, &app))?;
            if event::poll(Duration::from_millis(100))? {
                if let Event::Key(key) = event::read()? {
                    if key.kind != KeyEventKind::Press {
                        continue;
                    }
                    match key.code {
                        KeyCode::Char('q') if app.pending.is_none() => break,
                        KeyCode::Char('q') => app
                            .log
                            .push("wait for the current operation before quitting".to_owned()),
                        KeyCode::Char('1') => app.screen = Screen::Plans,
                        KeyCode::Char('2') => {
                            app.screen = Screen::Inventory;
                            app.fill_inventory();
                        }
                        KeyCode::Char('3') => {
                            app.screen = Screen::Run;
                            app.show_argv();
                        }
                        KeyCode::Char('x') => app.run(false),
                        KeyCode::Char('v') => app.run(true),
                        _ => {}
                    }
                }
            }
        }
        Ok(())
    })();
    disable_raw_mode()?;
    execute!(term.backend_mut(), LeaveAlternateScreen)?;
    term.show_cursor()?;
    result
}

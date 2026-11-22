//! StruggleTime Layer-1 daemon library.
use anyhow::{Context, Result};
use chrono::Utc;
use clap::Parser;
use serde_json::json;
use st_core::config::{
    daemon_log_path, linked_paths, load_settings, pause_flag_path, runtime_home, Settings,
};
use st_core::ledger::Ledger;
use st_core::privacy::anonymized_file_id;
use st_core::verify::FORMULA_VERSION;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime};
use walkdir::WalkDir;

#[derive(Parser, Debug)]
#[command(name = "struggletime-daemon", about = "StruggleTime desktop tracker")]
pub struct DaemonArgs {
    /// Project root to watch (repeatable). Also reads linked projects from settings.
    #[arg(long)]
    repo: Vec<PathBuf>,
    #[arg(long, default_value = "")]
    user: String,
    #[arg(long, default_value = "")]
    server: String,
    #[arg(long, default_value = "")]
    token: String,
    #[arg(long, default_value = "")]
    project: String,
    #[arg(long, default_value_t = 300)]
    bucket_secs: u64,
    /// Map path=project_id (repeatable)
    #[arg(long = "project-map")]
    project_map: Vec<String>,
    /// Flush pending ledger uploads and exit
    #[arg(long)]
    flush_only: bool,
    /// Seal one bucket and exit
    #[arg(long)]
    once: bool,
    /// Allowlisted debug logs (also STRUGGLETIME_DEBUG=1)
    #[arg(long)]
    debug: bool,
}

#[derive(Default, Clone)]
struct FileSnap {
    mtime: u64,
    len: u64,
}

struct ProjectState {
    id: String,
    root: PathBuf,
    snaps: HashMap<String, FileSnap>,
    human_edits: i64,
    ai_writes: i64,
    prompts: i64,
    executions: i64,
    exec_failures: i64,
    active_secs: i64,
    idle_secs: i64,
    last_activity: Instant,
    ledger: Ledger,
    ledger_path: PathBuf,
}

const IGNORE_DIRS: &[&str] = &[
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "dist",
    "build",
    ".aitime",
    ".godot",
    "Library",
    "Temp",
    "Obj",
    "Intermediate",
    "Saved",
    "Binaries",
    ".idea",
    "DerivedData",
    "xcuserdata",
];

pub fn run(args: DaemonArgs) -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let rt = tokio::runtime::Runtime::new()?;
    rt.block_on(async_main(args))
}

async fn async_main(args: DaemonArgs) -> Result<()> {
    if args.debug
        || std::env::var_os("STRUGGLETIME_DEBUG").is_some()
        || std::env::var_os("AITIME_DEBUG").is_some()
    {
        tracing::info!("debug mode on (labels/counts only — never paths, prompts, or argv)");
    }
    fs::create_dir_all(runtime_home())?;
    let mut settings = load_settings().unwrap_or_default();
    if !args.server.is_empty() {
        settings.server = st_core::normalize_server(&args.server);
    }
    if !args.token.is_empty() {
        settings.token = args.token.clone();
    }
    if !args.user.is_empty() {
        settings.handle = args.user.clone();
    }

    let mut projects = collect_projects(&args, &settings)?;
    if projects.is_empty() {
        anyhow::bail!("no project folders to watch — pass --repo or link via `struggletime start`");
    }

    if args.flush_only {
        for p in &mut projects {
            flush_pending(p, &settings).await?;
        }
        return Ok(());
    }

    tracing::info!(
        count = projects.len(),
        server = %settings.server,
        "daemon watching projects (metadata only; no paths/prompts leave the machine)"
    );

    for p in &mut projects {
        p.snaps = scan_tree(&p.root);
        p.last_activity = Instant::now();
    }

    let bucket = Duration::from_secs(args.bucket_secs.max(30));
    let poll = Duration::from_secs(1);
    let mut since_seal = Instant::now();

    loop {
        if pause_flag_path().exists() {
            tokio::time::sleep(poll).await;
            continue;
        }
        drain_agent_events(&mut projects);
        for p in &mut projects {
            tick_project(p);
        }
        if since_seal.elapsed() >= bucket || args.once {
            for p in &mut projects {
                if let Err(e) = seal_and_sync(p, &settings).await {
                    tracing::warn!(error = %e, "seal/sync failed");
                    let _ = append_daemon_log(&format!("seal/sync error: {e}"));
                }
            }
            since_seal = Instant::now();
            if args.once {
                break;
            }
        }
        tokio::time::sleep(poll).await;
    }
    Ok(())
}

fn collect_projects(args: &DaemonArgs, settings: &Settings) -> Result<Vec<ProjectState>> {
    let mut out = Vec::new();
    let mut maps: HashMap<PathBuf, String> = HashMap::new();
    for m in &args.project_map {
        if let Some((path, id)) = m.split_once('=') {
            maps.insert(PathBuf::from(path), id.to_string());
        }
    }
    for (id, path) in linked_paths(settings) {
        maps.entry(path).or_insert(id);
    }
    for repo in &args.repo {
        let path = repo.canonicalize().unwrap_or_else(|_| repo.clone());
        maps.entry(path).or_insert_with(|| args.project.clone());
    }
    for (root, id) in maps {
        if !root.is_dir() {
            continue;
        }
        let ledger_path = Ledger::path_for_repo(&root);
        let ledger = Ledger::load(&ledger_path)?;
        out.push(ProjectState {
            id,
            root,
            snaps: HashMap::new(),
            human_edits: 0,
            ai_writes: 0,
            prompts: 0,
            executions: 0,
            exec_failures: 0,
            active_secs: 0,
            idle_secs: 0,
            last_activity: Instant::now(),
            ledger,
            ledger_path,
        });
    }
    Ok(out)
}

fn scan_tree(root: &Path) -> HashMap<String, FileSnap> {
    let mut out = HashMap::new();
    for entry in WalkDir::new(root)
        .into_iter()
        .filter_entry(|e| {
            let name = e.file_name().to_string_lossy();
            !(e.file_type().is_dir() && IGNORE_DIRS.iter().any(|d| *d == name))
        })
        .flatten()
    {
        if !entry.file_type().is_file() {
            continue;
        }
        let Ok(meta) = entry.metadata() else { continue };
        let rel = entry
            .path()
            .strip_prefix(root)
            .unwrap_or(entry.path())
            .to_string_lossy()
            .replace('\\', "/");
        let mtime = meta
            .modified()
            .ok()
            .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
            .map(|d| d.as_secs())
            .unwrap_or(0);
        out.insert(
            rel,
            FileSnap {
                mtime,
                len: meta.len(),
            },
        );
    }
    out
}

fn tick_project(p: &mut ProjectState) {
    let now = scan_tree(&p.root);
    let mut changed = 0i64;
    let mut burst = 0i64;
    for (rel, snap) in &now {
        match p.snaps.get(rel) {
            Some(old) if old.mtime != snap.mtime || old.len != snap.len => {
                changed += 1;
                let delta = snap.len.abs_diff(old.len);
                if delta > 8_000 {
                    burst += 1;
                }
            }
            None => {
                changed += 1;
                if snap.len > 8_000 {
                    burst += 1;
                }
            }
            _ => {}
        }
    }
    p.snaps = now;
    if changed > 0 {
        // Burst of many large writes → agent-ish; 1–2 small → human-ish.
        if changed >= 5 || burst >= 3 {
            p.ai_writes += changed;
        } else {
            p.human_edits += changed;
        }
        p.active_secs += 1;
        p.last_activity = Instant::now();
        p.idle_secs = 0;
    } else if p.last_activity.elapsed() > Duration::from_secs(2) {
        p.idle_secs += 1;
    }
}

async fn seal_and_sync(p: &mut ProjectState, settings: &Settings) -> Result<()> {
    if p.human_edits == 0 && p.ai_writes == 0 && p.active_secs == 0 {
        p.idle_secs = 0;
        return Ok(());
    }
    let salt = p.ledger.salt.clone();
    let mut files = Vec::new();
    for (rel, snap) in p.snaps.iter().take(40) {
        // Only include recently touched files loosely via size class — never raw path.
        let _ = snap;
        files.push(json!({
            "file_id": anonymized_file_id(rel, &salt),
            "op": "modified",
            "source": "layer1",
        }));
        if files.len() >= 20 {
            break;
        }
    }
    // Prefer empty files list over path leakage if too noisy — keep count-only when idle-heavy.
    if p.human_edits + p.ai_writes < 1 {
        files.clear();
    }

    let bucket_secs = (p.active_secs + p.idle_secs).max(1);
    let bucket = json!({
        "v": 1,
        "bucket_id": uuid::Uuid::new_v4().to_string(),
        "install_id": "rust-daemon",
        "project_id": p.id,
        "bucket_start_wall": Utc::now().timestamp(),
        "bucket_secs": bucket_secs,
        "mono_delta_ms": bucket_secs * 1000,
            "counts": {
            "human_edits": p.human_edits,
            "ai_writes": p.ai_writes,
            "active_secs": p.active_secs,
            "idle_secs": p.idle_secs,
            "prompts": p.prompts,
            "executions": p.executions,
            "exec_failures": p.exec_failures,
            "commits": 0,
        },
        "files": files,
        "formula_version": FORMULA_VERSION,
        "os": std::env::consts::OS,
    });

    p.ledger.append_sealed(bucket)?;
    p.ledger.save(&p.ledger_path)?;
    p.human_edits = 0;
    p.ai_writes = 0;
    p.prompts = 0;
    p.executions = 0;
    p.exec_failures = 0;
    p.active_secs = 0;
    p.idle_secs = 0;

    flush_pending(p, settings).await?;
    Ok(())
}

async fn flush_pending(p: &mut ProjectState, settings: &Settings) -> Result<()> {
    if p.ledger.pending.is_empty() {
        return Ok(());
    }
    let token = settings.token.trim();
    if token.is_empty() {
        tracing::debug!("no API key — pending stays local");
        return Ok(());
    }
    let url = format!("{}/v1/buckets/batch", settings.server.trim_end_matches('/'));
    let body = json!({ "buckets": p.ledger.pending });
    let client = reqwest::Client::new();
    let res = client
        .post(&url)
        .header("Authorization", format!("Bearer {token}"))
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await
        .context("POST /v1/buckets/batch")?;
    if res.status().is_success() {
        let hashes: Vec<String> = p
            .ledger
            .pending
            .iter()
            .filter_map(|b| {
                b.get("hash")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string())
            })
            .collect();
        p.ledger.ack_pending(&hashes);
        p.ledger.save(&p.ledger_path)?;
        tracing::info!(n = hashes.len(), "flushed buckets");
    } else {
        let status = res.status();
        tracing::warn!(%status, "batch upload rejected — keeping pending locally");
    }
    Ok(())
}

/// Drain ~/.aitime/agent_events.jsonl (+ legacy exec.jsonl). Metadata only. never paths/argv.
///
/// Subagent fan-out / retry storms increment per-event *counts*, but `active_secs`
/// gains at most one tick per drain so billable `bucket_secs` cannot be inflated
/// by thousands of micro-events in a single second.
fn drain_agent_events(projects: &mut [ProjectState]) {
    let home = runtime_home();
    for name in ["agent_events.jsonl", "exec.jsonl"] {
        let path = home.join(name);
        let Ok(raw) = fs::read_to_string(&path) else {
            continue;
        };
        if raw.trim().is_empty() {
            continue;
        }
        let _ = fs::write(&path, ""); // truncate after read
        if projects.is_empty() {
            continue;
        }
        let (deltas, stats) = st_core::drain_agent_events_jsonl(&raw);
        if stats.rejected_forbidden > 0 {
            tracing::debug!(
                rejected = stats.rejected_forbidden,
                "dropped adapter lines with forbidden keys"
            );
        }
        let mut touched: HashSet<usize> = HashSet::new();
        for d in &deltas {
            let idx = d
                .project_id
                .as_ref()
                .and_then(|pid| projects.iter().position(|p| p.id == *pid))
                .unwrap_or(0);
            let p = &mut projects[idx];
            p.human_edits += d.human_edits;
            p.ai_writes += d.ai_writes;
            p.prompts += d.prompts;
            p.executions += d.executions;
            p.exec_failures += d.exec_failures;
            touched.insert(idx);
        }
        if stats.activity_tick > 0 {
            if touched.is_empty() {
                touched.insert(0);
            }
            // At most one active_secs tick per touched project per drain (not per event).
            for idx in touched {
                let p = &mut projects[idx];
                p.active_secs += 1;
                p.last_activity = Instant::now();
                p.idle_secs = 0;
            }
        }
    }
}

fn append_daemon_log(line: &str) -> Result<()> {
    use std::io::Write;
    let path = daemon_log_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    writeln!(f, "{} {line}", Utc::now().to_rfc3339())?;
    Ok(())
}

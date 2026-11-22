use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};

pub const DEFAULT_SERVER: &str = "https://62-83-116-143.sslip.io";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct LinkedProject {
    #[serde(default)]
    pub id: String,
    pub path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    #[serde(default)]
    pub handle: String,
    #[serde(default)]
    pub repo: String,
    #[serde(default = "default_server")]
    pub server: String,
    #[serde(default)]
    pub token: String,
    #[serde(default = "default_bucket_mins")]
    pub bucket_mins: u32,
    #[serde(default)]
    pub project: String,
    #[serde(default)]
    pub projects: Vec<LinkedProject>,
}

fn default_server() -> String {
    DEFAULT_SERVER.to_string()
}
fn default_bucket_mins() -> u32 {
    5
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            handle: String::new(),
            repo: String::new(),
            server: DEFAULT_SERVER.to_string(),
            token: String::new(),
            bucket_mins: 5,
            project: String::new(),
            projects: vec![],
        }
    }
}

pub fn runtime_home() -> PathBuf {
    if let Ok(v) = std::env::var("STRUGGLETIME_HOME").or_else(|_| std::env::var("AITIME_HOME")) {
        return PathBuf::from(v);
    }
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".aitime")
}

pub fn config_home() -> PathBuf {
    if let Ok(v) = std::env::var("STRUGGLETIME_CONFIG").or_else(|_| std::env::var("AITIME_CONFIG"))
    {
        return PathBuf::from(v);
    }
    #[cfg(windows)]
    {
        if let Ok(appdata) = std::env::var("APPDATA") {
            return PathBuf::from(appdata).join("struggletime");
        }
    }
    dirs::config_dir()
        .unwrap_or_else(|| {
            dirs::home_dir()
                .unwrap_or_else(|| PathBuf::from("."))
                .join(".config")
        })
        .join("struggletime")
}

pub fn settings_path() -> PathBuf {
    config_home().join("settings.json")
}

pub fn daemon_log_path() -> PathBuf {
    runtime_home().join("daemon.log")
}

pub fn daemon_pid_path() -> PathBuf {
    runtime_home().join("daemon.pid")
}

pub fn pause_flag_path() -> PathBuf {
    runtime_home().join("paused")
}

pub fn normalize_server(url: &str) -> String {
    let u = url.trim().trim_end_matches('/');
    if u.is_empty() {
        return DEFAULT_SERVER.to_string();
    }
    // Drop ephemeral tunnel hosts — same policy as the settings normalizer.
    let lower = u.to_lowercase();
    if lower.contains("trycloudflare.com")
        || lower.contains("ngrok")
        || lower.contains("loca.lt")
        || lower.contains("cloudflared")
    {
        return DEFAULT_SERVER.to_string();
    }
    u.to_string()
}

pub fn load_settings() -> Result<Settings> {
    let path = settings_path();
    if !path.exists() {
        let legacy = runtime_home().join("gui.json");
        if legacy.exists() {
            let raw = fs::read_to_string(&legacy)?;
            let mut s: Settings = serde_json::from_str(&raw).unwrap_or_default();
            s.server = normalize_server(&s.server);
            let _ = save_settings(&s);
            return Ok(s);
        }
        return Ok(Settings::default());
    }
    let raw = fs::read_to_string(&path).with_context(|| format!("read {}", path.display()))?;
    let mut s: Settings = serde_json::from_str(&raw).unwrap_or_default();
    s.server = normalize_server(&s.server);
    Ok(s)
}

pub fn save_settings(s: &Settings) -> Result<()> {
    let dir = config_home();
    fs::create_dir_all(&dir)?;
    let mut out = s.clone();
    out.server = normalize_server(&out.server);
    let raw = serde_json::to_string_pretty(&out)?;
    fs::write(settings_path(), raw)?;
    Ok(())
}

pub fn linked_paths(s: &Settings) -> Vec<(String, PathBuf)> {
    let mut out = Vec::new();
    for p in &s.projects {
        let path = PathBuf::from(shellexpand_home(&p.path));
        if path.is_dir() {
            out.push((p.id.clone(), path));
        }
    }
    if out.is_empty() && !s.repo.is_empty() {
        let path = PathBuf::from(shellexpand_home(&s.repo));
        if path.is_dir() {
            out.push((s.project.clone(), path));
        }
    }
    out
}

fn shellexpand_home(p: &str) -> String {
    if let Some(rest) = p.strip_prefix("~/") {
        if let Some(home) = dirs::home_dir() {
            return home.join(rest).to_string_lossy().into_owned();
        }
    }
    if p == "~" {
        if let Some(home) = dirs::home_dir() {
            return home.to_string_lossy().into_owned();
        }
    }
    p.to_string()
}

pub fn upsert_linked_project(s: &mut Settings, project_id: &str, local_path: &Path) {
    let path = local_path.to_string_lossy().to_string();
    if let Some(row) = s
        .projects
        .iter_mut()
        .find(|p| p.path == path || (!project_id.is_empty() && p.id == project_id))
    {
        if !project_id.is_empty() {
            row.id = project_id.to_string();
        }
        row.path = path.clone();
    } else {
        s.projects.push(LinkedProject {
            id: project_id.to_string(),
            path: path.clone(),
        });
    }
    s.repo = path;
    if !project_id.is_empty() {
        s.project = project_id.to_string();
    }
}

pub fn merge_json(base: &mut Value, patch: Value) {
    if let (Some(b), Some(p)) = (base.as_object_mut(), patch.as_object()) {
        for (k, v) in p {
            b.insert(k.clone(), v.clone());
        }
    }
}

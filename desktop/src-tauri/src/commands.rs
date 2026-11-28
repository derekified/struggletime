use crate::{cli, navigation};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use tauri::{AppHandle, Manager, WebviewWindow};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DashboardConfig {
    server: String,
    cli_available: bool,
    has_api_key: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TrackerAction {
    Start,
    Pause,
    Resume,
    Stop,
    Sync,
}

impl TrackerAction {
    fn command(&self) -> &'static str {
        match self {
            Self::Start => "start",
            Self::Pause => "pause",
            Self::Resume => "resume",
            Self::Stop => "stop",
            Self::Sync => "sync",
        }
    }
}

#[tauri::command]
pub fn dashboard_config() -> DashboardConfig {
    let settings = st_core::load_settings().unwrap_or_default();
    DashboardConfig {
        server: navigation::dashboard_base().to_string(),
        cli_available: cli::resolve_cli()
            .map(|p| p.is_file() || !p.is_absolute())
            .unwrap_or(false),
        has_api_key: !settings.token.trim().is_empty(),
    }
}

#[tauri::command]
pub async fn tracker_status() -> Result<String, String> {
    cli::run_owned(vec!["status".into()]).await
}

#[tauri::command]
pub async fn tracker_action(action: TrackerAction) -> Result<String, String> {
    cli::run_owned(vec![action.command().into()]).await
}

fn validate_api_key(key: &str) -> Result<(), String> {
    if key.starts_with("st_") && key.len() >= 8 && !key.chars().any(char::is_whitespace) {
        Ok(())
    } else {
        Err("Enter a valid StruggleTime API key beginning with st_".into())
    }
}

pub fn inject_dashboard_token(window: &WebviewWindow, token: &str) {
    let base = navigation::dashboard_base();
    if let Ok(current) = window.url() {
        if let Some(script) = navigation::token_bootstrap_script(&current, &base, token) {
            let _ = window.eval(&script);
        }
    }
}

fn clear_dashboard_token(window: &WebviewWindow) {
    let base = navigation::dashboard_base();
    if let Ok(current) = window.url() {
        if let Some(script) = navigation::clear_token_script(&current, &base) {
            let _ = window.eval(script);
        }
    }
}

#[tauri::command]
pub async fn set_api_key(app: AppHandle, key: String) -> Result<String, String> {
    let key = key.trim().to_string();
    validate_api_key(&key)?;
    let result = cli::run_owned(vec!["apikey".into(), key.clone()]).await?;
    if let Some(window) = app.get_webview_window("main") {
        inject_dashboard_token(&window, &key);
    }
    Ok(result)
}

#[tauri::command]
pub async fn clear_api_key(app: AppHandle) -> Result<String, String> {
    let result = cli::run_owned(vec!["clear-key".into()]).await?;
    if let Some(window) = app.get_webview_window("main") {
        clear_dashboard_token(&window);
    }
    Ok(result)
}

#[tauri::command]
pub async fn choose_project_folder() -> Option<String> {
    tauri::async_runtime::spawn_blocking(|| {
        rfd::FileDialog::new()
            .set_title("Link a project folder")
            .pick_folder()
            .map(|p| p.to_string_lossy().into_owned())
    })
    .await
    .ok()
    .flatten()
}

#[tauri::command]
pub async fn link_project(path: String, project: Option<String>) -> Result<String, String> {
    let path = PathBuf::from(path.trim());
    if !path.is_dir() {
        return Err("Choose an existing project folder".into());
    }
    let path = path
        .canonicalize()
        .map_err(|e| format!("Could not resolve the project folder: {e}"))?;
    let mut args = vec![
        "link".into(),
        "--repo".into(),
        path.to_string_lossy().into_owned(),
    ];
    if let Some(project) = project.filter(|p| !p.trim().is_empty()) {
        args.push("--project".into());
        args.push(project.trim().into());
    }
    cli::run_owned(args).await
}

#[derive(Serialize)]
pub struct LinkedProjectInfo {
    id: String,
    path: String,
    active: bool,
}

#[tauri::command]
pub fn list_linked_projects() -> Vec<LinkedProjectInfo> {
    let settings = st_core::load_settings().unwrap_or_default();
    let mut projects: Vec<LinkedProjectInfo> = settings
        .projects
        .iter()
        .map(|p| LinkedProjectInfo {
            id: p.id.clone(),
            path: p.path.clone(),
            active: !settings.project.is_empty() && p.id == settings.project,
        })
        .collect();
    // Legacy single-link settings (repo/project fields with no projects entry).
    if projects.is_empty() && !settings.repo.is_empty() {
        projects.push(LinkedProjectInfo {
            id: settings.project.clone(),
            path: settings.repo.clone(),
            active: true,
        });
    }
    projects.sort_by(|a, b| a.id.cmp(&b.id));
    projects
}

/// Remove a linked project by id. Pure so it is unit-testable without touching
/// the real settings file. Clears the default repo/project when the active link
/// is the one being removed. Never logs the removed path.
fn remove_project_by_id(settings: &mut st_core::Settings, id: &str) -> bool {
    let before = settings.projects.len();
    settings.projects.retain(|p| p.id != id);
    let removed = settings.projects.len() != before;
    if removed && settings.project == id {
        settings.project.clear();
        settings.repo.clear();
    }
    removed
}

#[tauri::command]
pub fn unlink_project(id: String) -> Result<String, String> {
    let id = id.trim().to_string();
    if id.is_empty() {
        return Err("Missing project id".into());
    }
    let mut settings =
        st_core::load_settings().map_err(|e| format!("Could not load settings: {e}"))?;
    if !remove_project_by_id(&mut settings, &id) {
        return Err(format!("No linked project with id '{id}'"));
    }
    st_core::save_settings(&settings).map_err(|e| format!("Could not save settings: {e}"))?;
    Ok(format!("Unlinked project '{id}'"))
}

/// Adapter names become CLI argv entries; keep them inside a fixed
/// lowercase-name shape so the command surface stays predictable.
fn valid_adapter_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 32
        && name
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
}

#[tauri::command]
pub async fn adapters_list() -> Result<String, String> {
    cli::run_owned(vec!["adapters".into(), "list".into()]).await
}

#[tauri::command]
pub async fn adapters_install(names: Vec<String>) -> Result<String, String> {
    for name in &names {
        if !valid_adapter_name(name) {
            return Err(format!("Invalid adapter name: {name}"));
        }
    }
    let mut args = vec!["adapters".into(), "install".into()];
    args.extend(names); // empty list = install all known adapters
    cli::run_owned(args).await
}

pub fn open_settings(app: &AppHandle) -> tauri::Result<()> {
    if let Some(window) = app.get_webview_window("settings") {
        window.show()?;
        window.set_focus()?;
        return Ok(());
    }
    WebviewWindow::builder(
        app,
        "settings",
        tauri::WebviewUrl::App("settings.html".into()),
    )
    .title("StruggleTime — Tracker Settings")
    .inner_size(720.0, 820.0)
    .min_inner_size(560.0, 620.0)
    .resizable(true)
    .build()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn api_keys_are_checked_without_logging_them() {
        assert!(validate_api_key("st_valid_key").is_ok());
        for invalid in ["", "token", "st_short key", "st_"] {
            assert!(validate_api_key(invalid).is_err());
        }
    }

    #[test]
    fn tracker_actions_have_fixed_commands() {
        assert_eq!(TrackerAction::Start.command(), "start");
        assert_eq!(TrackerAction::Pause.command(), "pause");
        assert_eq!(TrackerAction::Resume.command(), "resume");
        assert_eq!(TrackerAction::Stop.command(), "stop");
        assert_eq!(TrackerAction::Sync.command(), "sync");
    }

    #[test]
    fn missing_project_paths_are_rejected() {
        assert!(!std::path::Path::new("definitely-not-a-struggletime-project").is_dir());
    }

    #[test]
    fn removing_projects_keeps_settings_consistent() {
        let mut settings = st_core::Settings {
            repo: "/tmp/a".into(),
            project: "a".into(),
            projects: vec![
                st_core::config::LinkedProject {
                    id: "a".into(),
                    path: "/tmp/a".into(),
                },
                st_core::config::LinkedProject {
                    id: "b".into(),
                    path: "/tmp/b".into(),
                },
            ],
            ..Default::default()
        };
        assert!(remove_project_by_id(&mut settings, "a"));
        assert_eq!(settings.projects.len(), 1);
        assert!(settings.project.is_empty());
        assert!(settings.repo.is_empty());
        assert!(!remove_project_by_id(&mut settings, "a"));
        assert!(remove_project_by_id(&mut settings, "b"));
        assert!(settings.projects.is_empty());
    }

    #[test]
    fn adapter_names_keep_the_fixed_argv_shape() {
        for valid in ["vscode", "claude-code", "hooks", "neovim2"] {
            assert!(valid_adapter_name(valid), "{valid}");
        }
        let long = "x".repeat(33);
        for invalid in ["", "VS Code", "../etc", "a b", long.as_str()] {
            assert!(!valid_adapter_name(invalid), "{invalid}");
        }
    }
}

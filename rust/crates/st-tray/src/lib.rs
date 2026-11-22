#![cfg_attr(not(any(target_os = "macos", target_os = "windows")), allow(dead_code))]
//! StruggleTime tray — menu-bar matching the classic Python tray.
use anyhow::Result;
use clap::Parser;
use st_core::config::{
    daemon_pid_path, load_settings, normalize_server, pause_flag_path, runtime_home, save_settings,
    settings_path, upsert_linked_project, Settings,
};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

mod dialogs;
mod icon;
mod theme;

#[derive(Parser, Debug)]
#[command(name = "struggletime-tray")]
pub struct TrayArgs {
    #[arg(long, default_value_t = false)]
    headless: bool,
    #[arg(long, default_value_t = 15)]
    interval: u64,
}

#[derive(Clone, Default)]
struct TrayState {
    hours: f64,
}

pub fn run(args: TrayArgs) -> Result<()> {
    fs::create_dir_all(runtime_home())?;
    let state = Arc::new(Mutex::new(TrayState::default()));
    ensure_daemon_if_configured()?;

    if !args.headless {
        #[cfg(any(target_os = "macos", target_os = "windows"))]
        {
            match native::run_native_tray(args.interval.max(5), Arc::clone(&state)) {
                Ok(()) => return Ok(()),
                Err(e) => eprintln!("graphical tray unavailable ({e}); using headless supervisor"),
            }
        }
        #[cfg(not(any(target_os = "macos", target_os = "windows")))]
        {
            eprintln!("StruggleTime tray: headless supervisor on this OS (menu-bar icon is macOS/Windows)");
        }
    }
    run_headless(args.interval.max(5), state)
}

fn run_headless(interval: u64, state: Arc<Mutex<TrayState>>) -> Result<()> {
    println!("StruggleTime tray (headless supervisor)");
    println!("keeps the daemon running; Ctrl-C stops the supervisor (daemon stays up)");
    loop {
        refresh_hours(&state);
        println!(
            "[{}] {}",
            chrono::Local::now().format("%H:%M:%S"),
            status_line(&state)
        );
        thread::sleep(Duration::from_secs(interval));
        let _ = ensure_daemon_if_configured();
    }
}

fn cli_bin() -> PathBuf {
    if let Ok(p) = std::env::var("STRUGGLETIME_HELPER") {
        return PathBuf::from(p);
    }
    if let Ok(exe) = std::env::current_exe() {
        let name = exe.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if name == "struggletime" || name == "struggletime.exe" {
            return exe;
        }
        if let Some(dir) = exe.parent() {
            for name in [
                "struggletime",
                "struggletime.exe",
                "struggletime-helper",
                "struggletime-helper.exe",
            ] {
                let cand = dir.join(name);
                if cand.exists() {
                    return cand;
                }
            }
        }
        return exe;
    }
    PathBuf::from("struggletime")
}

fn process_alive(pid: u32) -> bool {
    #[cfg(unix)]
    {
        Command::new("kill")
            .args(["-0", &pid.to_string()])
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    }
    #[cfg(windows)]
    {
        Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/NH"])
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).contains(&pid.to_string()))
            .unwrap_or(false)
    }
}

fn tracking() -> bool {
    fs::read_to_string(daemon_pid_path())
        .ok()
        .and_then(|p| p.trim().parse::<u32>().ok())
        .map(process_alive)
        .unwrap_or(false)
}

fn paused() -> bool {
    pause_flag_path().exists()
}

fn ensure_daemon_if_configured() -> Result<()> {
    let s = load_settings().unwrap_or_default();
    if s.token.is_empty() && s.repo.is_empty() && s.projects.is_empty() {
        return Ok(());
    }
    if !tracking() {
        run_cli(&["start"]);
    }
    Ok(())
}

fn run_cli(args: &[&str]) {
    let bin = cli_bin();
    match Command::new(&bin).args(args).status() {
        Ok(st) if st.success() => {}
        Ok(st) => eprintln!("{} {:?} -> {st}", bin.display(), args),
        Err(e) => eprintln!("failed {} {:?}: {e}", bin.display(), args),
    }
}

fn format_duration(hours: f64) -> String {
    let mins = (hours * 60.0).round() as i64;
    let h = mins / 60;
    let m = mins % 60;
    if h > 0 && m > 0 {
        format!("{h}h {m:02}m")
    } else if h > 0 {
        format!("{h}h")
    } else {
        format!("{m}m")
    }
}

fn pending_count(repo: &str) -> usize {
    let path = PathBuf::from(repo).join(".aitime-ledger.json");
    let Ok(raw) = fs::read_to_string(path) else {
        return 0;
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&raw) else {
        return 0;
    };
    v.get("pending")
        .and_then(|p| p.as_array())
        .map(|a| a.len())
        .unwrap_or(0)
}

fn project_label(s: &Settings) -> String {
    if !s.project.is_empty() {
        return s.project.clone();
    }
    if !s.projects.is_empty() {
        return s.projects[0].id.clone();
    }
    Path::new(&s.repo)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("-")
        .to_string()
}

fn auth_label(s: &Settings) -> String {
    if s.token.is_empty() {
        "no API key".into()
    } else if !s.handle.is_empty() {
        format!("@{}", s.handle)
    } else {
        "API key set".into()
    }
}

fn status_line(state: &Mutex<TrayState>) -> String {
    let s = load_settings().unwrap_or_default();
    let hours = state.lock().map(|g| g.hours).unwrap_or(0.0);
    let proj = project_label(&s);
    let auth = auth_label(&s);
    let pending = if s.repo.is_empty() {
        0
    } else {
        pending_count(&s.repo)
    };
    if tracking() {
        let state_s = if paused() { "Paused" } else { "Active" };
        format!(
            "Status: {state_s} — {proj} · {auth} · {} today · {pending} pending",
            format_duration(hours)
        )
    } else {
        format!(
            "Status: No active session · {auth} · {} today",
            format_duration(hours)
        )
    }
}

fn today_line(state: &Mutex<TrayState>) -> String {
    let hours = state.lock().map(|g| g.hours).unwrap_or(0.0);
    format!("Today: {}", format_duration(hours))
}

fn refresh_hours(state: &Mutex<TrayState>) {
    let s = load_settings().unwrap_or_default();
    if s.token.is_empty() {
        return;
    }
    let url = format!(
        "{}/v1/me/overview",
        normalize_server(&s.server).trim_end_matches('/')
    );
    let Ok(client) = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(6))
        .build()
    else {
        return;
    };
    let Ok(res) = client
        .get(&url)
        .header("Authorization", format!("Bearer {}", s.token))
        .send()
    else {
        return;
    };
    if !res.status().is_success() {
        return;
    }
    let Ok(v) = res.json::<serde_json::Value>() else {
        return;
    };
    if let Some(h) = v.get("weighted_hours").and_then(|x| x.as_f64()) {
        if let Ok(mut g) = state.lock() {
            g.hours = h;
        }
    }
}

fn browser_base(s: &Settings) -> String {
    normalize_server(&s.server)
}

fn open_url(url: &str) {
    if let Err(e) = open::that(url) {
        eprintln!("could not open browser: {e}");
        let _ = dialogs::alert("StruggleTime", &format!("Open this URL:\n{url}"));
    }
}

fn do_set_api_key() {
    let Some(val) = dialogs::prompt(
        "StruggleTime API key",
        "Paste the API key from Settings -> StruggleTime API key:",
        true,
    ) else {
        return;
    };
    let key = val.trim().to_string();
    if key.is_empty() {
        return;
    }
    if !key.starts_with("st_") {
        let _ = dialogs::alert(
            "StruggleTime",
            "That does not look like a StruggleTime key (expected st_...).",
        );
        return;
    }
    run_cli(&["apikey", &key]);
}

fn do_clear_api_key() {
    if !dialogs::confirm(
        "StruggleTime",
        "Clear the StruggleTime API key from this machine?",
    ) {
        return;
    }
    if tracking() {
        run_cli(&["stop"]);
    }
    run_cli(&["clear-key"]);
}

fn do_link_project(create: bool) {
    let s = load_settings().unwrap_or_default();
    if s.token.is_empty() {
        let _ = dialogs::alert("StruggleTime", "Set an API key first.");
        return;
    }
    let start = if s.repo.is_empty() {
        std::env::var("HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("."))
    } else {
        PathBuf::from(&s.repo)
    };
    let title = if create {
        "Create project — choose local directory"
    } else {
        "Link project — choose local directory"
    };
    let Some(folder) = dialogs::choose_folder(title, &start) else {
        return;
    };
    let id = folder
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("project")
        .to_string();
    let mut s = load_settings().unwrap_or_default();
    upsert_linked_project(&mut s, &id, &folder);
    let _ = save_settings(&s);
    if tracking() {
        run_cli(&["stop"]);
    }
    run_cli(&[
        "start",
        "--repo",
        folder.to_str().unwrap_or("."),
        "--project",
        &id,
    ]);
    let _ = dialogs::alert(
        "StruggleTime",
        &format!("linked {id} -> {}", folder.display()),
    );
}

fn do_inspect() {
    let s = load_settings().unwrap_or_default();
    let repo = if !s.repo.is_empty() {
        PathBuf::from(&s.repo)
    } else if let Some(p) = s.projects.first() {
        PathBuf::from(&p.path)
    } else {
        let _ = dialogs::alert("StruggleTime", "Pick a repo in settings first.");
        return;
    };
    let ledger = repo.join(".aitime-ledger.json");
    if !ledger.is_file() {
        let _ = dialogs::alert(
            "Inspect — what would be sent",
            "No .aitime-ledger.json yet. Start tracking and seal a bucket first.",
        );
        return;
    }
    let bin = cli_bin();
    let out = Command::new(&bin)
        .args(["verify", "--ledger"])
        .arg(&ledger)
        .output();
    let body = match out {
        Ok(o) => {
            let mut t = String::from_utf8_lossy(&o.stdout).to_string();
            if t.trim().is_empty() {
                t = String::from_utf8_lossy(&o.stderr).to_string();
            }
            if t.trim().is_empty() {
                t = fs::read_to_string(&ledger).unwrap_or_else(|_| "empty ledger".into());
                if t.len() > 4000 {
                    t = format!(
                        "{}\n... (truncated; open {} for full allowlisted ledger)",
                        &t[..4000],
                        ledger.display()
                    );
                }
            }
            t
        }
        Err(e) => format!("verify failed: {e}"),
    };
    let pending = pending_count(repo.to_str().unwrap_or(""));
    let _ = dialogs::alert(
        "Inspect — what would be sent",
        &format!("pending buckets: {pending}\n\n{body}"),
    );
}

fn do_settings() {
    let s = load_settings().unwrap_or_default();
    open_url(&format!("{}/settings", browser_base(&s)));
    let _ = dialogs::alert(
        "StruggleTime",
        &format!(
            "Opened Settings in the browser.\nLocal config:\n{}",
            settings_path().display()
        ),
    );
}

fn do_dashboard() {
    let s = load_settings().unwrap_or_default();
    let path = if s.token.is_empty() {
        "/login"
    } else {
        "/settings"
    };
    open_url(&format!("{}{path}", browser_base(&s)));
}

fn do_open_api_key_page() {
    let s = load_settings().unwrap_or_default();
    open_url(&format!("{}/settings", browser_base(&s)));
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
mod native {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};
    use tray_icon::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
    use tray_icon::{TrayIconBuilder, TrayIconEvent};
    use winit::application::ApplicationHandler;
    use winit::event::WindowEvent;
    use winit::event_loop::{ActiveEventLoop, EventLoop};
    use winit::window::WindowId;

    #[derive(Debug)]
    enum UserEvent {
        Menu(tray_icon::menu::MenuId),
        Tick,
    }

    struct MenuIds {
        set_key: tray_icon::menu::MenuId,
        get_key: tray_icon::menu::MenuId,
        clear_key: tray_icon::menu::MenuId,
        link: tray_icon::menu::MenuId,
        create: tray_icon::menu::MenuId,
        start: tray_icon::menu::MenuId,
        stop: tray_icon::menu::MenuId,
        dash: tray_icon::menu::MenuId,
        inspect: tray_icon::menu::MenuId,
        settings: tray_icon::menu::MenuId,
        quit: tray_icon::menu::MenuId,
    }

    struct BuiltMenu {
        menu: Menu,
        ids: MenuIds,
        item_status: MenuItem,
        item_today: MenuItem,
        item_clear: MenuItem,
        item_link: MenuItem,
        item_create: MenuItem,
        item_start: MenuItem,
        item_stop: MenuItem,
    }

    fn build_menu(state: &Mutex<TrayState>) -> Result<BuiltMenu> {
        let s = load_settings().unwrap_or_default();
        let has_token = !s.token.is_empty();
        let is_tracking = tracking();

        let item_status = MenuItem::new(status_line(state), false, None);
        let item_today = MenuItem::new(today_line(state), false, None);
        let item_set_key = MenuItem::new("set / link API key…", true, None);
        let item_get_key = MenuItem::new("get API key in browser…", true, None);
        let item_clear = MenuItem::new("clear API key", has_token, None);
        let item_link = MenuItem::new("link / create project…", has_token, None);
        let item_create = MenuItem::new("create project…", has_token, None);
        let item_start = MenuItem::new("start tracking", !is_tracking, None);
        let item_stop = MenuItem::new("stop tracking", is_tracking, None);
        let item_dash = MenuItem::new("open dashboard", true, None);
        let item_inspect = MenuItem::new("inspect what would be sent", true, None);
        let item_settings = MenuItem::new("settings…", true, None);
        let item_quit = MenuItem::new("quit", true, None);

        let ids = MenuIds {
            set_key: item_set_key.id().clone(),
            get_key: item_get_key.id().clone(),
            clear_key: item_clear.id().clone(),
            link: item_link.id().clone(),
            create: item_create.id().clone(),
            start: item_start.id().clone(),
            stop: item_stop.id().clone(),
            dash: item_dash.id().clone(),
            inspect: item_inspect.id().clone(),
            settings: item_settings.id().clone(),
            quit: item_quit.id().clone(),
        };

        let menu = Menu::new();
        menu.append(&item_status)?;
        menu.append(&item_today)?;
        menu.append(&PredefinedMenuItem::separator())?;
        menu.append(&item_set_key)?;
        menu.append(&item_get_key)?;
        menu.append(&item_clear)?;
        menu.append(&item_link)?;
        menu.append(&item_create)?;
        menu.append(&PredefinedMenuItem::separator())?;
        menu.append(&item_start)?;
        menu.append(&item_stop)?;
        menu.append(&PredefinedMenuItem::separator())?;
        menu.append(&item_dash)?;
        menu.append(&item_inspect)?;
        menu.append(&item_settings)?;
        menu.append(&PredefinedMenuItem::separator())?;
        menu.append(&item_quit)?;

        Ok(BuiltMenu {
            menu,
            ids,
            item_status,
            item_today,
            item_clear,
            item_link,
            item_create,
            item_start,
            item_stop,
        })
    }

    struct App {
        state: Arc<Mutex<TrayState>>,
        stop: Arc<AtomicBool>,
        tray: Option<tray_icon::TrayIcon>,
        ids: MenuIds,
        item_status: MenuItem,
        item_today: MenuItem,
        item_clear: MenuItem,
        item_link: MenuItem,
        item_create: MenuItem,
        item_start: MenuItem,
        item_stop: MenuItem,
        icon_key: (bool, bool, String),
    }

    impl App {
        fn refresh_chrome(&mut self) {
            refresh_hours(&self.state);
            let s = load_settings().unwrap_or_default();
            let th = theme::resolve_theme(&s.theme);
            let track = tracking();
            let pause = paused();
            let key = (track, pause, th.id.to_string());
            if key != self.icon_key {
                self.icon_key = key;
                if let Ok(ic) = icon::make_icon(th, track, pause) {
                    if let Some(tray) = &self.tray {
                        let _ = tray.set_icon(Some(ic));
                    }
                }
            }
            let tip = format_duration(self.state.lock().map(|g| g.hours).unwrap_or(0.0));
            if let Some(tray) = &self.tray {
                let _ = tray.set_tooltip(Some(tip.clone()));
                let _ = tray.set_title(Some(tip));
            }
            self.item_status.set_text(status_line(&self.state));
            self.item_today.set_text(today_line(&self.state));
            let has_token = !s.token.is_empty();
            self.item_clear.set_enabled(has_token);
            self.item_link.set_enabled(has_token);
            self.item_create.set_enabled(has_token);
            self.item_start.set_enabled(!track);
            self.item_stop.set_enabled(track);
        }

        fn handle_menu(&mut self, id: &tray_icon::menu::MenuId, event_loop: &ActiveEventLoop) {
            if id == &self.ids.set_key {
                do_set_api_key();
            } else if id == &self.ids.get_key {
                do_open_api_key_page();
            } else if id == &self.ids.clear_key {
                do_clear_api_key();
            } else if id == &self.ids.link {
                do_link_project(false);
            } else if id == &self.ids.create {
                do_link_project(true);
            } else if id == &self.ids.start {
                run_cli(&["start"]);
            } else if id == &self.ids.stop {
                run_cli(&["stop"]);
            } else if id == &self.ids.dash {
                do_dashboard();
            } else if id == &self.ids.inspect {
                do_inspect();
            } else if id == &self.ids.settings {
                do_settings();
            } else if id == &self.ids.quit {
                self.stop.store(true, Ordering::SeqCst);
                run_cli(&["stop"]);
                event_loop.exit();
                return;
            }
            self.refresh_chrome();
        }
    }

    impl ApplicationHandler<UserEvent> for App {
        fn resumed(&mut self, _event_loop: &ActiveEventLoop) {}
        fn user_event(&mut self, event_loop: &ActiveEventLoop, event: UserEvent) {
            match event {
                UserEvent::Tick => {
                    let _ = ensure_daemon_if_configured();
                    self.refresh_chrome();
                }
                UserEvent::Menu(id) => self.handle_menu(&id, event_loop),
            }
        }
        fn window_event(
            &mut self,
            _event_loop: &ActiveEventLoop,
            _window_id: WindowId,
            _event: WindowEvent,
        ) {
        }
    }

    pub fn run_native_tray(interval: u64, state: Arc<Mutex<TrayState>>) -> Result<()> {
        refresh_hours(&state);
        let event_loop = EventLoop::<UserEvent>::with_user_event().build()?;
        let proxy = event_loop.create_proxy();
        let built = build_menu(&state)?;
        let s = load_settings().unwrap_or_default();
        let th = theme::resolve_theme(&s.theme);
        let track = tracking();
        let pause = paused();
        let ic = icon::make_icon(th, track, pause)?;
        let tip = format_duration(state.lock().map(|g| g.hours).unwrap_or(0.0));
        let tray = TrayIconBuilder::new()
            .with_menu(Box::new(built.menu))
            .with_tooltip(tip.clone())
            .with_icon(ic)
            .with_title(tip)
            .build()
            .map_err(|e| anyhow::anyhow!("tray icon: {e}"))?;

        let stop = Arc::new(AtomicBool::new(false));
        {
            let proxy = proxy.clone();
            let stop = Arc::clone(&stop);
            thread::spawn(move || {
                while !stop.load(Ordering::SeqCst) {
                    thread::sleep(Duration::from_secs(interval));
                    if proxy.send_event(UserEvent::Tick).is_err() {
                        break;
                    }
                }
            });
        }
        let menu_proxy = proxy.clone();
        MenuEvent::set_event_handler(Some(move |event: MenuEvent| {
            let _ = menu_proxy.send_event(UserEvent::Menu(event.id));
        }));
        TrayIconEvent::set_event_handler(Some(|_| {}));

        let s = load_settings().unwrap_or_default();
        if !s.token.is_empty() && (!s.repo.is_empty() || !s.projects.is_empty()) && !tracking() {
            run_cli(&["start"]);
        } else if s.token.is_empty() {
            eprintln!("set your StruggleTime API key from Settings -> API key");
        }

        let mut app = App {
            state,
            stop,
            tray: Some(tray),
            ids: built.ids,
            item_status: built.item_status,
            item_today: built.item_today,
            item_clear: built.item_clear,
            item_link: built.item_link,
            item_create: built.item_create,
            item_start: built.item_start,
            item_stop: built.item_stop,
            icon_key: (track, pause, th.id.to_string()),
        };
        app.refresh_chrome();
        println!("StruggleTime tray (menu bar)");
        event_loop.run_app(&mut app)?;
        Ok(())
    }
}

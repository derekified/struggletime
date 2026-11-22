//! StruggleTime tray — keeps the daemon alive.
//!
//! On macOS and Windows, opens a menu-bar / system-tray icon. On Linux (and
//! whenever the graphical tray cannot start), runs a headless supervisor that
//! still respawns the daemon and prints status.
use anyhow::Result;
use clap::Parser;
use st_core::config::{
    daemon_pid_path, load_settings, normalize_server, pause_flag_path, runtime_home,
};
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::thread;
use std::time::Duration;

#[derive(Parser, Debug)]
#[command(name = "struggletime-tray")]
pub struct TrayArgs {
    /// Skip the graphical tray and only run the headless supervisor.
    #[arg(long, default_value_t = false)]
    headless: bool,
    /// Status refresh interval seconds
    #[arg(long, default_value_t = 15)]
    interval: u64,
}

pub fn run(args: TrayArgs) -> Result<()> {
    fs::create_dir_all(runtime_home())?;
    ensure_daemon()?;

    if !args.headless {
        #[cfg(any(target_os = "macos", target_os = "windows"))]
        {
            match native::run_native_tray(args.interval.max(5)) {
                Ok(()) => return Ok(()),
                Err(e) => {
                    eprintln!("graphical tray unavailable ({e}); using headless supervisor");
                }
            }
        }
        #[cfg(not(any(target_os = "macos", target_os = "windows")))]
        {
            eprintln!("StruggleTime tray: headless supervisor on this OS (menu-bar icon is macOS/Windows)");
        }
    }

    run_headless(args.interval.max(5))
}

fn run_headless(interval: u64) -> Result<()> {
    println!("StruggleTime tray (headless supervisor)");
    println!("keeps the daemon running; Ctrl-C stops the supervisor (daemon stays up)");
    loop {
        print_status_line();
        thread::sleep(Duration::from_secs(interval));
        let _ = ensure_daemon();
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
            .map(|o| {
                let out = String::from_utf8_lossy(&o.stdout);
                out.contains(&pid.to_string())
            })
            .unwrap_or(false)
    }
}

fn ensure_daemon() -> Result<()> {
    let s = load_settings().unwrap_or_default();
    if s.token.is_empty() && s.repo.is_empty() && s.projects.is_empty() {
        eprintln!("link an API key / project first:");
        eprintln!("  struggletime apikey <st_…>");
        eprintln!("  struggletime start --repo ~/my-project");
        return Ok(());
    }
    let alive = fs::read_to_string(daemon_pid_path())
        .ok()
        .and_then(|p| p.trim().parse::<u32>().ok())
        .map(process_alive)
        .unwrap_or(false);
    if !alive {
        let bin = cli_bin();
        match Command::new(&bin).arg("start").status() {
            Ok(st) if st.success() => {}
            Ok(st) => eprintln!("struggletime start exited with {st}"),
            Err(e) => eprintln!("failed to run {} start: {e}", bin.display()),
        }
    }
    Ok(())
}

fn status_tooltip() -> String {
    let s = load_settings().unwrap_or_default();
    let paused = pause_flag_path().exists();
    let pid = fs::read_to_string(daemon_pid_path())
        .ok()
        .map(|p| p.trim().to_string())
        .unwrap_or_else(|| "-".into());
    let alive = pid.parse::<u32>().ok().map(process_alive).unwrap_or(false);
    let daemon = if alive { pid } else { "down".to_string() };
    format!(
        "StruggleTime | {} | handle={} | daemon={} | paused={}",
        normalize_server(&s.server),
        if s.handle.is_empty() { "-" } else { &s.handle },
        daemon,
        paused
    )
}

fn print_status_line() {
    println!(
        "[{}] {}",
        chrono::Local::now().format("%H:%M:%S"),
        status_tooltip()
    );
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn run_cli(args: &[&str]) {
    let bin = cli_bin();
    match Command::new(&bin).args(args).status() {
        Ok(st) if st.success() => {}
        Ok(st) => eprintln!("{} {:?} -> {st}", bin.display(), args),
        Err(e) => eprintln!("failed {} {:?}: {e}", bin.display(), args),
    }
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
mod native {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use tray_icon::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
    use tray_icon::{Icon, TrayIconBuilder, TrayIconEvent};
    use winit::application::ApplicationHandler;
    use winit::event::WindowEvent;
    use winit::event_loop::{ActiveEventLoop, EventLoop, EventLoopProxy};
    use winit::window::WindowId;

    #[derive(Debug)]
    enum UserEvent {
        Menu(tray_icon::menu::MenuId),
        Tick,
    }

    struct App {
        proxy: EventLoopProxy<UserEvent>,
        stop: Arc<AtomicBool>,
        tray: Option<tray_icon::TrayIcon>,
        item_status: MenuItem,
        item_start: MenuItem,
        item_pause: MenuItem,
        item_resume: MenuItem,
        item_sync: MenuItem,
        item_quit: MenuItem,
    }

    impl ApplicationHandler<UserEvent> for App {
        fn resumed(&mut self, _event_loop: &ActiveEventLoop) {}

        fn user_event(&mut self, event_loop: &ActiveEventLoop, event: UserEvent) {
            match event {
                UserEvent::Tick => {
                    let _ = ensure_daemon();
                    let tip = status_tooltip();
                    if let Some(tray) = &self.tray {
                        let _ = tray.set_tooltip(Some(tip.clone()));
                    }
                    self.item_status.set_text(truncate_label(&tip, 48));
                }
                UserEvent::Menu(id) => {
                    if id == self.item_start.id() {
                        run_cli(&["start"]);
                    } else if id == self.item_pause.id() {
                        run_cli(&["pause"]);
                    } else if id == self.item_resume.id() {
                        run_cli(&["resume"]);
                    } else if id == self.item_sync.id() {
                        run_cli(&["sync"]);
                    } else if id == self.item_quit.id() {
                        self.stop.store(true, Ordering::SeqCst);
                        event_loop.exit();
                    }
                    let tip = status_tooltip();
                    if let Some(tray) = &self.tray {
                        let _ = tray.set_tooltip(Some(tip.clone()));
                    }
                    self.item_status.set_text(truncate_label(&tip, 48));
                }
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

    fn truncate_label(s: &str, max: usize) -> String {
        if s.chars().count() <= max {
            s.to_string()
        } else {
            format!(
                "{}…",
                s.chars().take(max.saturating_sub(1)).collect::<String>()
            )
        }
    }

    fn load_icon() -> Result<Icon> {
        let bytes = include_bytes!("../assets/tray-icon.png");
        let img = image::load_from_memory(bytes)?.into_rgba8();
        let (w, h) = img.dimensions();
        Icon::from_rgba(img.into_raw(), w, h).map_err(|e| anyhow::anyhow!(e))
    }

    pub fn run_native_tray(interval: u64) -> Result<()> {
        let event_loop = EventLoop::<UserEvent>::with_user_event().build()?;
        let proxy = event_loop.create_proxy();

        let menu = Menu::new();
        let item_status = MenuItem::new(truncate_label(&status_tooltip(), 48), false, None);
        let item_start = MenuItem::new("Start daemon", true, None);
        let item_pause = MenuItem::new("Pause", true, None);
        let item_resume = MenuItem::new("Resume", true, None);
        let item_sync = MenuItem::new("Sync now", true, None);
        let item_quit = MenuItem::new("Quit tray", true, None);
        menu.append(&item_status)?;
        menu.append(&PredefinedMenuItem::separator())?;
        menu.append(&item_start)?;
        menu.append(&item_pause)?;
        menu.append(&item_resume)?;
        menu.append(&item_sync)?;
        menu.append(&PredefinedMenuItem::separator())?;
        menu.append(&item_quit)?;

        let icon = load_icon()?;
        let tip = status_tooltip();
        let tray = TrayIconBuilder::new()
            .with_menu(Box::new(menu))
            .with_tooltip(tip)
            .with_icon(icon)
            .with_title("ST")
            .build()
            .map_err(|e| anyhow::anyhow!("tray icon: {e}"))?;

        let stop = Arc::new(AtomicBool::new(false));
        {
            let proxy = proxy.clone();
            let stop = Arc::clone(&stop);
            let secs = interval;
            thread::spawn(move || {
                while !stop.load(Ordering::SeqCst) {
                    thread::sleep(Duration::from_secs(secs));
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

        let mut app = App {
            proxy,
            stop,
            tray: Some(tray),
            item_status,
            item_start,
            item_pause,
            item_resume,
            item_sync,
            item_quit,
        };

        println!("StruggleTime tray (menu bar / system tray)");
        event_loop.run_app(&mut app)?;
        Ok(())
    }
}

//! StruggleTime tray — native binary.
//!
//! Default build is a headless supervisor (works everywhere): keeps the daemon
//! running and prints status. Full OS tray icons ship on Windows/macOS release
//! builds; until then this binary is the supported native entrypoint alongside
//! `struggletime-helper`.
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
struct Args {
    /// Do not try a graphical tray (always on for this build).
    #[arg(long, default_value_t = true)]
    headless: bool,
    /// Refresh interval seconds
    #[arg(long, default_value_t = 15)]
    interval: u64,
}

fn main() -> Result<()> {
    let args = Args::parse();
    fs::create_dir_all(runtime_home())?;
    println!("StruggleTime tray (native)");
    println!("settings + helper drive the daemon — native binaries.");
    ensure_daemon()?;
    if args.headless {
        loop {
            print_status_line();
            thread::sleep(Duration::from_secs(args.interval.max(5)));
            let _ = ensure_daemon();
        }
    }
    Ok(())
}

fn helper_bin() -> PathBuf {
    if let Ok(p) = std::env::var("STRUGGLETIME_HELPER") {
        return PathBuf::from(p);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for name in ["struggletime-helper", "struggletime-helper.exe"] {
                let cand = dir.join(name);
                if cand.exists() {
                    return cand;
                }
            }
        }
    }
    PathBuf::from("struggletime-helper")
}

fn ensure_daemon() -> Result<()> {
    let s = load_settings().unwrap_or_default();
    if s.token.is_empty() && s.repo.is_empty() && s.projects.is_empty() {
        eprintln!("link an API key / project: struggletime-helper apikey <st_…>");
        return Ok(());
    }
    let alive = fs::read_to_string(daemon_pid_path())
        .ok()
        .and_then(|p| p.trim().parse::<u32>().ok())
        .map(|pid| {
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
                let _ = pid;
                true
            }
        })
        .unwrap_or(false);
    if !alive {
        let _ = Command::new(helper_bin()).arg("start").status();
    }
    Ok(())
}

fn print_status_line() {
    let s = load_settings().unwrap_or_default();
    let paused = pause_flag_path().exists();
    let pid = fs::read_to_string(daemon_pid_path())
        .ok()
        .map(|p| p.trim().to_string())
        .unwrap_or_else(|| "-".into());
    println!(
        "[{}] server={} handle={} paused={paused} daemon_pid={pid}",
        chrono::Local::now().format("%H:%M:%S"),
        normalize_server(&s.server),
        if s.handle.is_empty() {
            "-"
        } else {
            &s.handle
        },
    );
}

//! Install optional editor / agent adapters into user locations.
//!
//! Adapters emit allowlisted labels only (never prompts, paths, argv, or file contents).
use anyhow::{bail, Context, Result};
use st_core::config::config_home;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

pub const ADAPTER_NAMES: &[&str] = &[
    "vscode",
    "neovim",
    "claude-code",
    "codex",
    "hooks",
    "openclaw",
    "godot",
    "unity",
    "jetbrains",
];

pub fn adapters_vendor_dir() -> PathBuf {
    config_home().join("adapters")
}

/// Locate the adapters source tree (repo checkout, release package, or vendored copy).
pub fn find_adapters_root() -> Result<PathBuf> {
    if let Ok(p) = std::env::var("STRUGGLETIME_ADAPTERS") {
        let p = PathBuf::from(p);
        if looks_like_adapters(&p) {
            return Ok(p);
        }
    }
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("adapters"));
            candidates.push(dir.join("share").join("struggletime").join("adapters"));
            candidates.push(dir.join("..").join("adapters"));
            candidates.push(
                dir.join("..")
                    .join("share")
                    .join("struggletime")
                    .join("adapters"),
            );
            candidates.push(dir.join("..").join("Resources").join("adapters"));
            candidates.push(dir.join("..").join("..").join("..").join("adapters"));
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd.join("adapters"));
        candidates.push(cwd.join("..").join("adapters"));
    }
    candidates.push(adapters_vendor_dir());

    for c in &candidates {
        let resolved = c.canonicalize().unwrap_or_else(|_| c.clone());
        if looks_like_adapters(&resolved) {
            return Ok(resolved);
        }
    }

    fetch_adapters_from_github(&adapters_vendor_dir()).context(
        "could not find adapters/ (set STRUGGLETIME_ADAPTERS or run from a checkout / release package)",
    )?;
    Ok(adapters_vendor_dir())
}

fn looks_like_adapters(p: &Path) -> bool {
    p.join("vscode").is_dir() || p.join("neovim").is_dir() || p.join("claude-code").is_dir()
}

fn fetch_adapters_from_github(dest: &Path) -> Result<()> {
    let url = "https://codeload.github.com/derekified/struggletime/tar.gz/refs/heads/main";
    eprintln!("downloading adapters from github.com/derekified/struggletime …");
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()?;
    let bytes = client.get(url).send()?.error_for_status()?.bytes()?;
    let parent = dest.parent().unwrap_or(Path::new("."));
    let tmp = parent.join(".adapters-download.tgz");
    let extract = parent.join(".adapters-extract");
    fs::write(&tmp, &bytes)?;
    let _ = fs::remove_dir_all(&extract);
    fs::create_dir_all(&extract)?;
    let status = std::process::Command::new("tar")
        .args(["-xzf"])
        .arg(&tmp)
        .arg("-C")
        .arg(&extract)
        .status()
        .context("tar extract failed — install tar or clone the repo")?;
    let _ = fs::remove_file(&tmp);
    if !status.success() {
        bail!("tar extract failed");
    }
    let mut found = None;
    if let Ok(rd) = fs::read_dir(&extract) {
        for ent in rd.flatten() {
            let p = ent.path().join("adapters");
            if looks_like_adapters(&p) {
                found = Some(p);
                break;
            }
        }
    }
    let src = found.context("adapters/ missing from downloaded archive")?;
    let _ = fs::remove_dir_all(dest);
    copy_dir_recursive(&src, dest)?;
    let _ = fs::remove_dir_all(&extract);
    Ok(())
}

fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<()> {
    fs::create_dir_all(dst)?;
    for ent in fs::read_dir(src)? {
        let ent = ent?;
        let ty = ent.file_type()?;
        let to = dst.join(ent.file_name());
        if ty.is_dir() {
            copy_dir_recursive(&ent.path(), &to)?;
        } else if ty.is_file() {
            if let Some(parent) = to.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::copy(ent.path(), &to)?;
        }
    }
    Ok(())
}

fn home_dir() -> PathBuf {
    dirs::home_dir().unwrap_or_else(|| PathBuf::from("."))
}

pub fn list_adapters() -> Result<()> {
    let root = match find_adapters_root() {
        Ok(p) => p,
        Err(e) => {
            println!("adapters source: (not found — {e})");
            println!("known names: {}", ADAPTER_NAMES.join(", "));
            return Ok(());
        }
    };
    println!("adapters source: {}", root.display());
    println!("vendor copy:     {}", adapters_vendor_dir().display());
    println!();
    for name in ADAPTER_NAMES {
        let present = adapter_source_present(&root, name);
        let status = if installed_hint(name) {
            "installed"
        } else if present {
            "available"
        } else {
            "missing"
        };
        println!("  {name:<12} {status}");
    }
    println!();
    println!("install: struggletime adapters install");
    println!("         struggletime adapters install vscode neovim claude-code");
    Ok(())
}

fn adapter_source_present(root: &Path, name: &str) -> bool {
    if name == "hooks" {
        return root
            .parent()
            .map(|p| p.join("daemon").join("hooks.sh").is_file())
            .unwrap_or(false)
            || root.join("hooks").join("hooks.sh").is_file()
            || root.join("hooks.sh").is_file();
    }
    root.join(name).is_dir()
}

fn installed_hint(name: &str) -> bool {
    match name {
        "vscode" => vscode_extension_dirs().into_iter().any(|d| {
            d.join("struggletime.struggletime-0.4.0").is_dir()
                || d.join("struggletime.struggletime").is_dir()
        }),
        "neovim" => {
            let pack = neovim_pack_dir();
            pack.join("lua")
                .join("struggletime")
                .join("init.lua")
                .is_file()
        }
        "claude-code" => claude_settings_path()
            .ok()
            .and_then(|p| fs::read_to_string(p).ok())
            .map(|t| t.contains("agent_events.jsonl"))
            .unwrap_or(false),
        "codex" => codex_wrap_path().is_file(),
        "hooks" => {
            config_home().join("hooks.sh").is_file() || config_home().join("hooks.ps1").is_file()
        }
        "openclaw" => adapters_vendor_dir()
            .join("openclaw")
            .join("run.sh")
            .is_file(),
        "godot" | "unity" | "jetbrains" => adapters_vendor_dir().join(name).is_dir(),
        _ => false,
    }
}

fn codex_wrap_path() -> PathBuf {
    #[cfg(windows)]
    {
        return home_dir()
            .join("AppData")
            .join("Local")
            .join("StruggleTime")
            .join("struggletime-wrap.cmd");
    }
    #[cfg(not(windows))]
    {
        home_dir()
            .join(".local")
            .join("bin")
            .join("struggletime-wrap")
    }
}

fn vscode_extension_dirs() -> Vec<PathBuf> {
    let home = home_dir();
    vec![
        home.join(".vscode").join("extensions"),
        home.join(".vscode-oss").join("extensions"),
        home.join(".cursor").join("extensions"),
        home.join(".windsurf").join("extensions"),
        home.join(".trae").join("extensions"),
        home.join(".kiro").join("extensions"),
        home.join(".vscodium").join("extensions"),
    ]
}

fn neovim_pack_dir() -> PathBuf {
    #[cfg(windows)]
    {
        if let Ok(local) = std::env::var("LOCALAPPDATA") {
            return PathBuf::from(local)
                .join("nvim")
                .join("site")
                .join("pack")
                .join("struggletime")
                .join("start")
                .join("struggletime");
        }
    }
    home_dir()
        .join(".local")
        .join("share")
        .join("nvim")
        .join("site")
        .join("pack")
        .join("struggletime")
        .join("start")
        .join("struggletime")
}

fn claude_settings_path() -> Result<PathBuf> {
    let home = home_dir();
    let candidates = [
        home.join(".claude").join("settings.json"),
        home.join(".config").join("claude").join("settings.json"),
    ];
    for c in &candidates {
        if c.is_file() {
            return Ok(c.clone());
        }
    }
    Ok(candidates[0].clone())
}

/// Install named adapters (empty = all known). Returns a human-readable summary.
pub fn install_adapters(names: &[String]) -> Result<String> {
    let root = find_adapters_root()?;
    let vendor = adapters_vendor_dir();
    if root != vendor {
        let _ = copy_dir_recursive(&root, &vendor);
        // Also try to pull daemon hooks into vendor parent awareness via copy of hooks files
        if let Some(parent) = root.parent() {
            let hooks_sh = parent.join("daemon").join("hooks.sh");
            let hooks_ps = parent.join("daemon").join("hooks.ps1");
            if hooks_sh.is_file() {
                let hdir = vendor.join("hooks");
                fs::create_dir_all(&hdir)?;
                let _ = fs::copy(&hooks_sh, hdir.join("hooks.sh"));
            }
            if hooks_ps.is_file() {
                let hdir = vendor.join("hooks");
                fs::create_dir_all(&hdir)?;
                let _ = fs::copy(&hooks_ps, hdir.join("hooks.ps1"));
            }
        }
    }
    let src_root = if looks_like_adapters(&vendor) {
        vendor
    } else {
        root
    };

    let targets: Vec<String> = if names.is_empty() {
        ADAPTER_NAMES.iter().map(|s| (*s).to_string()).collect()
    } else {
        names
            .iter()
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty())
            .collect()
    };

    let mut lines: Vec<String> = Vec::new();
    for name in &targets {
        if !ADAPTER_NAMES.contains(&name.as_str()) {
            lines.push(format!("{name}: unknown (skip)"));
            continue;
        }
        match install_one(&src_root, name) {
            Ok(msg) => lines.push(format!("{name}: {msg}")),
            Err(e) => lines.push(format!("{name}: error — {e:#}")),
        }
    }
    Ok(lines.join("\n"))
}

fn install_one(root: &Path, name: &str) -> Result<String> {
    match name {
        "vscode" => install_vscode(root),
        "neovim" => install_neovim(root),
        "claude-code" => install_claude(root),
        "codex" => install_codex(root),
        "hooks" => install_hooks(root),
        "openclaw" => install_openclaw(root),
        "godot" | "unity" | "jetbrains" => install_project_adapter(root, name),
        _ => bail!("unknown adapter"),
    }
}

fn install_vscode(root: &Path) -> Result<String> {
    let src = root.join("vscode");
    if !src.join("extension.js").is_file() {
        bail!("adapters/vscode missing");
    }
    let mut installed = Vec::new();
    for dir in vscode_extension_dirs() {
        let parent_ok = dir.parent().map(|p| p.is_dir()).unwrap_or(false);
        if !parent_ok && !dir.is_dir() {
            continue;
        }
        fs::create_dir_all(&dir)?;
        let dest = dir.join("struggletime.struggletime-0.4.0");
        let _ = fs::remove_dir_all(&dest);
        copy_dir_recursive(&src, &dest)?;
        installed.push(dest.display().to_string());
    }
    if installed.is_empty() {
        let fallback = home_dir().join(".vscode").join("extensions");
        fs::create_dir_all(&fallback)?;
        let dest = fallback.join("struggletime.struggletime-0.4.0");
        let _ = fs::remove_dir_all(&dest);
        copy_dir_recursive(&src, &dest)?;
        installed.push(dest.display().to_string());
    }
    Ok(format!(
        "VS Code family extension → {} (reload window)",
        installed.join(", ")
    ))
}

fn install_neovim(root: &Path) -> Result<String> {
    let src = root.join("neovim");
    if !src
        .join("lua")
        .join("struggletime")
        .join("init.lua")
        .is_file()
    {
        bail!("adapters/neovim missing");
    }
    let dest = neovim_pack_dir();
    let _ = fs::remove_dir_all(&dest);
    copy_dir_recursive(&src, &dest)?;
    Ok(format!(
        "Neovim packpath → {} (restart nvim)",
        dest.display()
    ))
}

fn install_claude(root: &Path) -> Result<String> {
    let hooks_path = root.join("claude-code").join("hooks.json");
    let raw = fs::read_to_string(&hooks_path).context("adapters/claude-code/hooks.json")?;
    let hooks_val: serde_json::Value = serde_json::from_str(&raw)?;
    let new_hooks = hooks_val
        .get("hooks")
        .cloned()
        .context("hooks.json missing hooks")?;

    let settings_path = claude_settings_path()?;
    if let Some(parent) = settings_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut settings: serde_json::Value = if settings_path.is_file() {
        serde_json::from_str(&fs::read_to_string(&settings_path)?)?
    } else {
        serde_json::json!({})
    };
    if !settings.is_object() {
        settings = serde_json::json!({});
    }
    let obj = settings.as_object_mut().unwrap();
    match obj.get_mut("hooks") {
        Some(existing) if existing.is_object() => {
            if let (Some(dst), Some(src)) = (existing.as_object_mut(), new_hooks.as_object()) {
                for (k, v) in src {
                    dst.insert(k.clone(), v.clone());
                }
            }
        }
        _ => {
            obj.insert("hooks".into(), new_hooks);
        }
    }
    let mut f = fs::File::create(&settings_path)?;
    f.write_all(serde_json::to_string_pretty(&settings)?.as_bytes())?;
    f.write_all(b"\n")?;
    Ok(format!(
        "merged Claude Code hooks → {}",
        settings_path.display()
    ))
}

fn install_codex(root: &Path) -> Result<String> {
    let wrap = root.join("codex").join("wrap.sh");
    if !wrap.is_file() {
        bail!("adapters/codex/wrap.sh missing");
    }
    #[cfg(windows)]
    {
        let dest_dir = home_dir()
            .join("AppData")
            .join("Local")
            .join("StruggleTime");
        fs::create_dir_all(&dest_dir)?;
        let dest = dest_dir.join("wrap.sh");
        fs::copy(&wrap, &dest)?;
        let cmd = dest_dir.join("struggletime-wrap.cmd");
        fs::write(
            &cmd,
            format!("@echo off\r\nbash \"{}\" %*\r\n", dest.display()),
        )?;
        return Ok(format!(
            "Codex wrap → {} (run: struggletime-wrap.cmd <cli>)",
            dest.display()
        ));
    }
    #[cfg(not(windows))]
    {
        let bin = home_dir().join(".local").join("bin");
        fs::create_dir_all(&bin)?;
        let dest = bin.join("struggletime-wrap");
        fs::copy(&wrap, &dest)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&dest)?.permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&dest, perms)?;
        }
        Ok(format!(
            "Codex wrap → {}  (alias: alias codex='struggletime-wrap codex')",
            dest.display()
        ))
    }
}

fn install_hooks(root: &Path) -> Result<String> {
    let sh = [
        root.parent().map(|p| p.join("daemon").join("hooks.sh")),
        Some(root.join("hooks").join("hooks.sh")),
        Some(root.join("hooks.sh")),
    ]
    .into_iter()
    .flatten()
    .find(|p| p.is_file());
    let ps = [
        root.parent().map(|p| p.join("daemon").join("hooks.ps1")),
        Some(root.join("hooks").join("hooks.ps1")),
        Some(root.join("hooks.ps1")),
    ]
    .into_iter()
    .flatten()
    .find(|p| p.is_file());

    fs::create_dir_all(config_home())?;
    let mut msgs = Vec::new();
    if let Some(sh) = sh {
        let dest = config_home().join("hooks.sh");
        fs::copy(&sh, &dest)?;
        msgs.push(format!(
            "shell hooks → {} (add: source \"{}\")",
            dest.display(),
            dest.display()
        ));
    }
    if let Some(ps) = ps {
        let dest = config_home().join("hooks.ps1");
        fs::copy(&ps, &dest)?;
        msgs.push(format!(
            "PowerShell hooks → {} (dot-source in your profile)",
            dest.display()
        ));
    }
    if msgs.is_empty() {
        bail!("daemon/hooks.sh not found next to adapters");
    }
    Ok(msgs.join("; "))
}

fn install_openclaw(root: &Path) -> Result<String> {
    let src = root.join("openclaw");
    if !src.is_dir() {
        bail!("adapters/openclaw missing");
    }
    let dest = adapters_vendor_dir().join("openclaw");
    let _ = fs::remove_dir_all(&dest);
    copy_dir_recursive(&src, &dest)?;
    Ok(format!(
        "OpenClaw helper → {} (run: sh {}/run.sh)",
        dest.display(),
        dest.display()
    ))
}

fn install_project_adapter(root: &Path, name: &str) -> Result<String> {
    let src = root.join(name);
    if !src.is_dir() {
        bail!("adapters/{name} missing");
    }
    let dest = adapters_vendor_dir().join(name);
    let _ = fs::remove_dir_all(&dest);
    copy_dir_recursive(&src, &dest)?;
    let hint = match name {
        "godot" => format!(
            "copied to {} — symlink addons/struggletime into your Godot project, then enable the plugin",
            dest.display()
        ),
        "unity" => format!(
            "copied to {} — put Editor/StruggleTimeTracker.cs under Assets/.../Editor/",
            dest.display()
        ),
        "jetbrains" => format!(
            "copied to {} — build/load as a JetBrains plugin module",
            dest.display()
        ),
        _ => dest.display().to_string(),
    };
    Ok(hint)
}

/// Used by tray: install everything and return summary text.
pub fn install_all_adapters() -> Result<String> {
    install_adapters(&[])
}

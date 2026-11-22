//! Native OS prompts (osascript / PowerShell) for tray actions.
use std::path::{Path, PathBuf};
#[cfg(any(target_os = "macos", target_os = "windows"))]
use std::process::Command;

fn escape_as(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}

pub fn prompt(title: &str, message: &str, secret: bool) -> Option<String> {
    #[cfg(target_os = "macos")]
    {
        let hidden = if secret { " with hidden answer" } else { "" };
        let script = format!(
            "set r to display dialog \"{}\" with title \"{}\" default answer \"\"{} buttons {{\"Cancel\", \"OK\"}} default button \"OK\"\nreturn text returned of r",
            escape_as(message),
            escape_as(title),
            hidden
        );
        let out = Command::new("osascript")
            .arg("-e")
            .arg(script)
            .output()
            .ok()?;
        if !out.status.success() {
            return None;
        }
        let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
        return if s.is_empty() { None } else { Some(s) };
    }
    #[cfg(target_os = "windows")]
    {
        let _ = secret;
        let ps = format!(
            "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.Interaction]::InputBox('{}','{}','')",
            message.replace('\'', "''"),
            title.replace('\'', "''")
        );
        let out = Command::new("powershell")
            .args(["-NoProfile", "-Command", &ps])
            .output()
            .ok()?;
        if !out.status.success() {
            return None;
        }
        let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
        return if s.is_empty() { None } else { Some(s) };
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = (title, message, secret);
        None
    }
}

pub fn alert(title: &str, message: &str) -> bool {
    #[cfg(target_os = "macos")]
    {
        let script = format!(
            "display dialog \"{}\" with title \"{}\" buttons {{\"OK\"}} default button \"OK\"",
            escape_as(message),
            escape_as(title)
        );
        return Command::new("osascript")
            .arg("-e")
            .arg(script)
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
    }
    #[cfg(target_os = "windows")]
    {
        let ps = format!(
            "[System.Windows.Forms.MessageBox]::Show('{}','{}') | Out-Null; Add-Type -AssemblyName System.Windows.Forms",
            message.replace('\'', "''"),
            title.replace('\'', "''")
        );
        // Load assembly first
        let ps = format!(
            "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('{}','{}') | Out-Null",
            message.replace('\'', "''"),
            title.replace('\'', "''")
        );
        return Command::new("powershell")
            .args(["-NoProfile", "-Command", &ps])
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        eprintln!("[{title}] {message}");
        true
    }
}

pub fn confirm(title: &str, message: &str) -> bool {
    #[cfg(target_os = "macos")]
    {
        let script = format!(
            "set r to display dialog \"{}\" with title \"{}\" buttons {{\"Cancel\", \"OK\"}} default button \"OK\"\nreturn button returned of r",
            escape_as(message),
            escape_as(title)
        );
        let out = Command::new("osascript").arg("-e").arg(script).output();
        match out {
            Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).trim() == "OK",
            _ => false,
        }
    }
    #[cfg(target_os = "windows")]
    {
        let ps = format!(
            "Add-Type -AssemblyName System.Windows.Forms; $r=[System.Windows.Forms.MessageBox]::Show('{}','{}','OKCancel'); if($r -eq 'OK'){{exit 0}} else {{exit 1}}",
            message.replace('\'', "''"),
            title.replace('\'', "''")
        );
        Command::new("powershell")
            .args(["-NoProfile", "-Command", &ps])
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = (title, message);
        false
    }
}

pub fn choose_folder(title: &str, start: &Path) -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        let start_s = start.to_string_lossy();
        let script = format!(
            "set r to choose folder with prompt \"{}\" default location POSIX file \"{}\"\nreturn POSIX path of r",
            escape_as(title),
            escape_as(&start_s)
        );
        let out = Command::new("osascript")
            .arg("-e")
            .arg(script)
            .output()
            .ok()?;
        if !out.status.success() {
            return None;
        }
        let s = String::from_utf8_lossy(&out.stdout)
            .trim()
            .trim_end_matches('/')
            .to_string();
        if s.is_empty() {
            None
        } else {
            Some(PathBuf::from(s))
        }
    }
    #[cfg(target_os = "windows")]
    {
        let ps = r#"
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.FolderBrowserDialog
$f.Description = 'TITLE_PLACEHOLDER'
$f.SelectedPath = 'START_PLACEHOLDER'
if ($f.ShowDialog() -eq 'OK') { Write-Output $f.SelectedPath; exit 0 } else { exit 1 }
"#
        .replace("TITLE_PLACEHOLDER", &title.replace('\'', "''"))
        .replace(
            "START_PLACEHOLDER",
            &start.to_string_lossy().replace('\'', "''"),
        );
        let out = Command::new("powershell")
            .args(["-NoProfile", "-Command", &ps])
            .output()
            .ok()?;
        if !out.status.success() {
            return None;
        }
        let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if s.is_empty() {
            None
        } else {
            Some(PathBuf::from(s))
        }
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = (title, start);
        None
    }
}

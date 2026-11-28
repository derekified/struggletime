#[cfg(windows)]
use std::path::Path;
use std::path::PathBuf;
#[cfg(windows)]
use std::process::Stdio;
use std::process::{Command, ExitStatus};

fn executable_name() -> &'static str {
    if cfg!(windows) {
        "struggletime.exe"
    } else {
        "struggletime"
    }
}

pub fn resolve_cli() -> Result<PathBuf, String> {
    if let Some(path) = std::env::var_os("STRUGGLETIME_CLI") {
        let path = PathBuf::from(path);
        if path.is_file() {
            return Ok(path);
        }
        return Err(format!(
            "STRUGGLETIME_CLI does not point to an executable: {}",
            path.display()
        ));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let candidate = dir.join(executable_name());
            if candidate.is_file() {
                return Ok(candidate);
            }
        }
    }
    let workspace_cli = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../rust/target/debug")
        .join(executable_name());
    if workspace_cli.is_file() {
        return Ok(workspace_cli);
    }
    Ok(PathBuf::from(executable_name()))
}

/// The desktop shell is a GUI-subsystem app on Windows while the CLI sidecar is
/// a console app. Without CREATE_NO_WINDOW every tracker action would flash a
/// console window (CLAUDE.md: background helpers must set CREATE_NO_WINDOW).
#[cfg(windows)]
fn configure_command(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
}

#[cfg(not(windows))]
fn configure_command(_command: &mut Command) {}

pub fn run(args: &[String]) -> Result<String, String> {
    let executable = resolve_cli()?;

    // On Windows the daemon inherits the start command's stdout pipe. Waiting
    // for that pipe to close would block until the daemon stops, so capture the
    // short-lived CLI's output in a file instead.
    #[cfg(windows)]
    if args.first().map(String::as_str) == Some("start") {
        return run_windows_start(&executable, args);
    }

    let mut command = Command::new(&executable);
    configure_command(&mut command);
    let output = command.args(args).output().map_err(|e| {
        format!(
            "Could not run the StruggleTime CLI ({}): {e}",
            executable.display()
        )
    })?;
    command_output(output.status, &output.stdout, &output.stderr)
}

#[cfg(windows)]
fn run_windows_start(executable: &Path, args: &[String]) -> Result<String, String> {
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| format!("Could not create a CLI output name: {e}"))?
        .as_nanos();
    let path = std::env::temp_dir().join(format!(
        "struggletime-desktop-start-{}-{nonce}.log",
        std::process::id()
    ));
    let file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .map_err(|e| format!("Could not create temporary CLI output: {e}"))?;
    let stdout = file
        .try_clone()
        .map_err(|e| format!("Could not prepare temporary CLI output: {e}"))?;
    let mut command = Command::new(executable);
    configure_command(&mut command);
    let output = command
        .args(args)
        .stdin(Stdio::null())
        .stdout(stdout)
        .stderr(file)
        .output();
    let captured = std::fs::read(&path);
    let _ = std::fs::remove_file(&path);
    let output = output.map_err(|e| {
        format!(
            "Could not run the StruggleTime CLI ({}): {e}",
            executable.display()
        )
    })?;
    let captured = captured.map_err(|e| format!("Could not read temporary CLI output: {e}"))?;
    command_output(output.status, &captured, &[])
}

fn command_output(status: ExitStatus, stdout: &[u8], stderr: &[u8]) -> Result<String, String> {
    let stdout = String::from_utf8_lossy(stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(stderr).trim().to_string();
    if status.success() {
        return Ok(if stdout.is_empty() { stderr } else { stdout });
    }
    let detail = match (stdout.is_empty(), stderr.is_empty()) {
        (false, false) => format!("{stdout}\n{stderr}"),
        (false, true) => stdout,
        _ => stderr,
    };
    Err(if detail.is_empty() {
        format!("CLI exited with {status}")
    } else {
        detail
    })
}

pub async fn run_owned(args: Vec<String>) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run(&args))
        .await
        .map_err(|e| format!("CLI task failed: {e}"))?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explicit_cli_override_must_exist() {
        let old = std::env::var_os("STRUGGLETIME_CLI");
        std::env::set_var("STRUGGLETIME_CLI", "definitely-not-a-struggletime-binary");
        assert!(resolve_cli().is_err());
        match old {
            Some(value) => std::env::set_var("STRUGGLETIME_CLI", value),
            None => std::env::remove_var("STRUGGLETIME_CLI"),
        }
    }
}

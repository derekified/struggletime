#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Native OS dialogs for tray/GUI — no tkinter.

  macOS:  osascript
  Windows: PowerShell + MessageBoxW (ctypes)
  Linux:  zenity / kdialog / yad, else stdin
"""
from __future__ import annotations

import os, shutil, subprocess, sys


def has_display():
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _run(cmd, *, input_text=None, timeout=600, env=None):
    try:
        return subprocess.run(
            cmd, input=input_text, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")


def _stdin_prompt(prompt, secret=False):
    if not sys.stdin.isatty():
        return None
    try:
        if secret:
            import getpass
            return getpass.getpass(prompt + " ").strip() or None
        return input(prompt + " ").strip() or None
    except (EOFError, KeyboardInterrupt):
        return None


def _stdin_confirm(message):
    if not sys.stdin.isatty():
        return False
    try:
        ans = input(f"{message} [y/N] ").strip().lower()
        return ans in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ---- macOS -----------------------------------------------------------------

def _mac_escape(s):
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


def _mac_prompt(title, prompt, secret=False, default=""):
    # AppleScript: display dialog … default answer / hidden answer
    if secret:
        script = (
            f'set r to display dialog "{_mac_escape(prompt)}" with title "{_mac_escape(title)}" '
            f'default answer "" with hidden answer buttons {{"Cancel", "OK"}} default button "OK"\n'
            f'return text returned of r'
        )
    else:
        script = (
            f'set r to display dialog "{_mac_escape(prompt)}" with title "{_mac_escape(title)}" '
            f'default answer "{_mac_escape(default)}" buttons {{"Cancel", "OK"}} default button "OK"\n'
            f'return text returned of r'
        )
    r = _run(["osascript", "-e", script])
    if r is None or r.returncode != 0:
        return None
    return (r.stdout or "").rstrip("\n")


def _mac_alert(title, message):
    script = (
        f'display dialog "{_mac_escape(message)}" with title "{_mac_escape(title)}" '
        f'buttons {{"OK"}} default button "OK"'
    )
    _run(["osascript", "-e", script], timeout=120)


def _mac_confirm(title, message):
    script = (
        f'set r to display dialog "{_mac_escape(message)}" with title "{_mac_escape(title)}" '
        f'buttons {{"Cancel", "OK"}} default button "OK"\n'
        f'return button returned of r'
    )
    r = _run(["osascript", "-e", script], timeout=120)
    return bool(r and r.returncode == 0 and (r.stdout or "").strip() == "OK")


def _mac_folder(title, start=""):
    start = start or str(os.path.expanduser("~"))
    script = (
        f'set r to choose folder with prompt "{_mac_escape(title)}" '
        f'default location POSIX file "{_mac_escape(start)}"\n'
        f'return POSIX path of r'
    )
    r = _run(["osascript", "-e", script], timeout=600)
    if r is None or r.returncode != 0:
        return None
    path = (r.stdout or "").rstrip("\n")
    return path.rstrip("/") if path else None


# ---- Windows ---------------------------------------------------------------

def _win_ps(script):
    # Avoid hanging console: -WindowStyle Hidden, CREATE_NO_WINDOW when available
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        return subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-Command", script,
            ],
            capture_output=True, text=True, timeout=600,
            creationflags=flags,
        )
    except Exception:
        return None


def _win_prompt(title, prompt, secret=False, default=""):
    # PowerShell InputBox via Microsoft.VisualBasic
    safe_title = (title or "").replace("'", "''")
    safe_prompt = (prompt or "").replace("'", "''")
    safe_default = (default or "").replace("'", "''")
    if secret:
        # Password-style: Read-Host -AsSecureString then convert (console) — prefer VB InputBox
        # VB InputBox can't mask; use a simple form via Windows.Forms if available
        script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = '{safe_title}'
$form.Width = 420; $form.Height = 160
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false; $form.MinimizeBox = $false
$label = New-Object System.Windows.Forms.Label
$label.Text = '{safe_prompt}'; $label.Left = 12; $label.Top = 12; $label.Width = 380
$box = New-Object System.Windows.Forms.TextBox
$box.Left = 12; $box.Top = 40; $box.Width = 380; $box.UseSystemPasswordChar = $true
$ok = New-Object System.Windows.Forms.Button
$ok.Text = 'OK'; $ok.Left = 220; $ok.Top = 80; $ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = 'Cancel'; $cancel.Left = 310; $cancel.Top = 80; $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.AddRange(@($label,$box,$ok,$cancel))
$form.AcceptButton = $ok; $form.CancelButton = $cancel
if ($form.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{ Write-Output $box.Text }} else {{ exit 1 }}
"""
    else:
        script = f"""
Add-Type -AssemblyName Microsoft.VisualBasic
$r = [Microsoft.VisualBasic.Interaction]::InputBox('{safe_prompt}', '{safe_title}', '{safe_default}')
if ([string]::IsNullOrEmpty($r)) {{ exit 1 }} else {{ Write-Output $r }}
"""
    r = _win_ps(script)
    if r is None or r.returncode != 0:
        return None
    out = (r.stdout or "").strip()
    return out or None


def _win_alert(title, message):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 0x40)  # MB_ICONINFORMATION
        return
    except Exception:
        pass
    safe_title = (title or "").replace("'", "''")
    safe_msg = (message or "").replace("'", "''")
    _win_ps(f"[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; [System.Windows.Forms.MessageBox]::Show('{safe_msg}','{safe_title}')")


def _win_confirm(title, message):
    try:
        import ctypes
        # MB_OKCANCEL | MB_ICONQUESTION → IDOK=1
        return ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 0x21) == 1
    except Exception:
        safe_title = (title or "").replace("'", "''")
        safe_msg = (message or "").replace("'", "''")
        r = _win_ps(
            f"[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
            f"$r = [System.Windows.Forms.MessageBox]::Show('{safe_msg}','{safe_title}','OKCancel','Question'); "
            f"if ($r -eq 'OK') {{ exit 0 }} else {{ exit 1 }}"
        )
        return bool(r and r.returncode == 0)


def _win_folder(title, start=""):
    safe_title = (title or "").replace("'", "''")
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = '{safe_title}'
"""
    if start:
        script += f"$d.SelectedPath = '{start.replace(chr(39), chr(39)+chr(39))}'\n"
    script += "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath } else { exit 1 }"
    r = _win_ps(script)
    if r is None or r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


# ---- Linux -----------------------------------------------------------------

def _linux_env():
    return {k: v for k, v in os.environ.items() if k not in ("LD_LIBRARY_PATH", "PYTHONHOME")}


def _linux_prompt(title, prompt, secret=False, default=""):
    env = _linux_env()
    zenity = _which("zenity")
    if zenity:
        cmd = [zenity, "--entry", f"--title={title}", f"--text={prompt}"]
        if default and not secret:
            cmd.append(f"--entry-text={default}")
        if secret:
            cmd.append("--hide-text")
        r = _run(cmd, env=env)
        if r and r.returncode == 0:
            return (r.stdout or "").rstrip("\n")
        if r and r.returncode in (1, 5):  # cancel
            return None
    kdialog = _which("kdialog")
    if kdialog:
        cmd = [kdialog, "--title", title, "--password" if secret else "--inputbox", prompt]
        if not secret and default:
            cmd.append(default)
        r = _run(cmd, env=env)
        if r and r.returncode == 0:
            return (r.stdout or "").rstrip("\n")
        return None
    yad = _which("yad")
    if yad:
        cmd = [yad, "--entry", f"--title={title}", f"--text={prompt}"]
        if secret:
            cmd.append("--hide-text")
        if default and not secret:
            cmd.append(f"--entry-text={default}")
        r = _run(cmd, env=env)
        if r and r.returncode == 0:
            return (r.stdout or "").rstrip("\n")
        return None
    print(f"[native_ui] no zenity/kdialog/yad — falling back to terminal ({title})", flush=True)
    return _stdin_prompt(f"{title}: {prompt}", secret=secret)


def _linux_alert(title, message):
    env = _linux_env()
    for cmd in (
        ["zenity", "--info", f"--title={title}", f"--text={message}", "--width=420"],
        ["kdialog", "--title", title, "--msgbox", message],
        ["yad", "--info", f"--title={title}", f"--text={message}"],
        ["notify-send", title, message],
    ):
        if not _which(cmd[0]):
            continue
        r = _run(cmd, env=env, timeout=120)
        if r is not None:
            return
    print(f"[{title}] {message}", flush=True)


def _linux_confirm(title, message):
    env = _linux_env()
    zenity = _which("zenity")
    if zenity:
        r = _run(["zenity", "--question", f"--title={title}", f"--text={message}"], env=env, timeout=120)
        return bool(r and r.returncode == 0)
    kdialog = _which("kdialog")
    if kdialog:
        r = _run(["kdialog", "--title", title, "--yesno", message], env=env, timeout=120)
        return bool(r and r.returncode == 0)
    yad = _which("yad")
    if yad:
        r = _run(["yad", "--question", f"--title={title}", f"--text={message}"], env=env, timeout=120)
        return bool(r and r.returncode == 0)
    return _stdin_confirm(f"{title}: {message}")


def _linux_folder(title, start=""):
    env = _linux_env()
    start = start or os.path.expanduser("~")
    zenity = _which("zenity")
    if zenity:
        r = _run(["zenity", "--file-selection", "--directory", f"--title={title}", f"--filename={start}/"], env=env)
        if r and r.returncode == 0:
            return (r.stdout or "").rstrip("\n") or None
        return None
    kdialog = _which("kdialog")
    if kdialog:
        r = _run(["kdialog", "--title", title, "--getexistingdirectory", start], env=env)
        if r and r.returncode == 0:
            return (r.stdout or "").rstrip("\n") or None
        return None
    return _stdin_prompt(f"{title} (folder path)", secret=False)


# ---- public API ------------------------------------------------------------

def prompt(title, message, *, secret=False, default=""):
    """Ask for a string. Returns stripped text, or None if cancelled/unavailable."""
    try:
        if sys.platform == "darwin":
            return _mac_prompt(title, message, secret=secret, default=default)
        if sys.platform.startswith("win"):
            return _win_prompt(title, message, secret=secret, default=default)
        if has_display():
            return _linux_prompt(title, message, secret=secret, default=default)
        return _stdin_prompt(f"{title}: {message}", secret=secret)
    except Exception as e:
        print(f"[native_ui] prompt error: {e}", flush=True)
        return _stdin_prompt(f"{title}: {message}", secret=secret)


def alert(title, message):
    """Show an informational alert (Inspect / errors)."""
    message = str(message or "")
    if len(message) > 3500:
        message = message[:3500] + "\n…"
    try:
        if sys.platform == "darwin":
            _mac_alert(title, message); return
        if sys.platform.startswith("win"):
            _win_alert(title, message); return
        if has_display():
            _linux_alert(title, message); return
    except Exception as e:
        print(f"[native_ui] alert error: {e}", flush=True)
    print(f"[{title}] {message}", flush=True)


def confirm(title, message):
    """OK/Cancel. Returns True if confirmed."""
    try:
        if sys.platform == "darwin":
            return _mac_confirm(title, message)
        if sys.platform.startswith("win"):
            return _win_confirm(title, message)
        if has_display():
            return _linux_confirm(title, message)
        return _stdin_confirm(f"{title}: {message}")
    except Exception as e:
        print(f"[native_ui] confirm error: {e}", flush=True)
        return _stdin_confirm(f"{title}: {message}")


def choose_folder(title="Choose folder", start=""):
    """Native folder picker. Returns path or None."""
    try:
        if sys.platform == "darwin":
            return _mac_folder(title, start)
        if sys.platform.startswith("win"):
            return _win_folder(title, start)
        if has_display():
            return _linux_folder(title, start)
        return _stdin_prompt(f"{title} (path)", secret=False)
    except Exception as e:
        print(f"[native_ui] folder error: {e}", flush=True)
        return _stdin_prompt(f"{title} (path)", secret=False)


def _stdin_choose(title, message, options, default=0):
    if not options:
        return None
    print(f"\n{title}", flush=True)
    if message:
        print(message, flush=True)
    for i, opt in enumerate(options):
        mark = "*" if i == default else " "
        print(f"  {mark}{i + 1}) {opt}", flush=True)
    if not sys.stdin.isatty():
        return default if 0 <= default < len(options) else None
    try:
        raw = input(f"choice [{default + 1}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return default if 0 <= default < len(options) else None
    try:
        n = int(raw)
    except ValueError:
        return None
    if 1 <= n <= len(options):
        return n - 1
    return None


def _mac_choose(title, message, options, default=0):
    # AppleScript choose from list
    items = ", ".join(f'"{_mac_escape(o)}"' for o in options)
    default_item = _mac_escape(options[default]) if 0 <= default < len(options) else _mac_escape(options[0])
    script = (
        f'set opts to {{{items}}}\n'
        f'set r to choose from list opts with prompt "{_mac_escape(message or title)}" '
        f'default items {{"{default_item}"}} OK button name "OK" cancel button name "Cancel"\n'
        f'if r is false then return ""\n'
        f'return item 1 of r'
    )
    r = _run(["osascript", "-e", script], timeout=600)
    if r is None or r.returncode != 0:
        return None
    picked = (r.stdout or "").rstrip("\n")
    if not picked:
        return None
    try:
        return options.index(picked)
    except ValueError:
        return None


def _win_choose(title, message, options, default=0):
    safe_title = (title or "").replace("'", "''")
    safe_msg = (message or "").replace("'", "''")
    # Build a simple WinForms list dialog
    items_ps = "@(" + ",".join("'" + str(o).replace("'", "''") + "'" for o in options) + ")"
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = '{safe_title}'
$form.Width = 480; $form.Height = 360
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false; $form.MinimizeBox = $false
$label = New-Object System.Windows.Forms.Label
$label.Text = '{safe_msg}'; $label.Left = 12; $label.Top = 12; $label.Width = 440; $label.Height = 40
$list = New-Object System.Windows.Forms.ListBox
$list.Left = 12; $list.Top = 56; $list.Width = 440; $list.Height = 220
$items = {items_ps}
$list.Items.AddRange($items)
if ($list.Items.Count -gt 0) {{ $list.SelectedIndex = {int(default)} }}
$ok = New-Object System.Windows.Forms.Button
$ok.Text = 'OK'; $ok.Left = 280; $ok.Top = 288; $ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = 'Cancel'; $cancel.Left = 360; $cancel.Top = 288; $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.AddRange(@($label,$list,$ok,$cancel))
$form.AcceptButton = $ok; $form.CancelButton = $cancel
if ($form.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK -and $list.SelectedIndex -ge 0) {{
  Write-Output $list.SelectedIndex
}} else {{ exit 1 }}
"""
    r = _win_ps(script)
    if r is None or r.returncode != 0:
        return None
    try:
        return int((r.stdout or "").strip())
    except ValueError:
        return None


def _linux_choose(title, message, options, default=0):
    env = _linux_env()
    zenity = _which("zenity")
    if zenity:
        cmd = [
            zenity, "--list", f"--title={title}", f"--text={message or title}",
            "--column=Option", "--hide-header", "--width=480", "--height=360",
        ]
        if 0 <= default < len(options):
            # zenity has no great default; prepend marker
            pass
        cmd.extend(options)
        r = _run(cmd, env=env, timeout=600)
        if r and r.returncode == 0:
            picked = (r.stdout or "").rstrip("\n")
            try:
                return options.index(picked)
            except ValueError:
                return None
        return None
    kdialog = _which("kdialog")
    if kdialog:
        # menu returns selected tag
        cmd = [kdialog, "--title", title, "--menu", message or title]
        for i, opt in enumerate(options):
            cmd.extend([str(i), opt])
        r = _run(cmd, env=env, timeout=600)
        if r and r.returncode == 0:
            try:
                return int((r.stdout or "").strip())
            except ValueError:
                return None
        return None
    return _stdin_choose(title, message, options, default=default)


def choose(title, message, options, *, default=0):
    """Pick one item from a list. Returns 0-based index, or None if cancelled."""
    options = [str(o) for o in (options or [])]
    if not options:
        return None
    if default < 0 or default >= len(options):
        default = 0
    try:
        if sys.platform == "darwin":
            return _mac_choose(title, message, options, default=default)
        if sys.platform.startswith("win"):
            return _win_choose(title, message, options, default=default)
        if has_display():
            return _linux_choose(title, message, options, default=default)
        return _stdin_choose(title, message, options, default=default)
    except Exception as e:
        print(f"[native_ui] choose error: {e}", flush=True)
        return _stdin_choose(title, message, options, default=default)


def backend_name():
    if sys.platform == "darwin":
        return "osascript"
    if sys.platform.startswith("win"):
        return "win32"
    if _which("zenity"):
        return "zenity"
    if _which("kdialog"):
        return "kdialog"
    if _which("yad"):
        return "yad"
    return "stdin"

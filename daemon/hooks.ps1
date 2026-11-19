# StruggleTime shell hook (PowerShell) — add to $PROFILE
# Emits ONLY: timestamp, command category, exit code. Never arguments.
$script:AitimeLog = if ($env:AITIME_EXEC_LOG) { $env:AITIME_EXEC_LOG } else { "$HOME/.aitime/exec.jsonl" }
New-Item -ItemType Directory -Force (Split-Path $script:AitimeLog) | Out-Null
function Global:prompt {
  $ec = if ($?) { 0 } else { 1 }
  $cmd = (Get-History -Count 1).CommandLine
  if ($cmd) {
    $name = ($cmd -split ' ')[0]
    $cat = switch -Regex ($name) { '^git$' {'git'} '^(claude|codex|aider|opencode|openclaw|gemini|goose|amp|crush|copilot|cursor-agent|qwen|pi|cline|roo)$' {'ai_cli'} '^(pytest|npm|cargo|go|make|node|python)$' {'test_runner'} default {'other'} }
    "{`"ts`":$([DateTimeOffset]::Now.ToUnixTimeSeconds()),`"category`":`"$cat`",`"verb`":`"`",`"exit_code`":$ec}" | Add-Content $script:AitimeLog
  }
  "PS $($executionContext.SessionState.Path.CurrentLocation)> "
}

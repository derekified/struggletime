# install the struggletime tray on windows (per-user scheduled task at logon, no admin)
$Root = Split-Path -Parent $PSScriptRoot
$Tray = Join-Path $PSScriptRoot "tray.py"
$Helper = Join-Path $PSScriptRoot "helper.py"
$Config = Join-Path $env:APPDATA "struggletime"
$Data = if ($env:STRUGGLETIME_HOME) { $env:STRUGGLETIME_HOME } else { Join-Path $HOME ".aitime" }
New-Item -ItemType Directory -Force -Path $Config, $Data | Out-Null

# Migrate legacy gui.json if present
$Legacy = Join-Path $Data "gui.json"
$Settings = Join-Path $Config "settings.json"
if ((Test-Path $Legacy) -and -not (Test-Path $Settings)) {
  Copy-Item $Legacy $Settings
  Write-Host "migrated settings → $Settings"
}

$Action = New-ScheduledTaskAction -Execute "python" -Argument "`"$Tray`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "StruggleTimeTray" -Action $Action -Trigger $Trigger -Force | Out-Null
Write-Host "scheduled task 'StruggleTimeTray' registered (starts tray on login)."
Write-Host "settings: $Settings"
Write-Host "gui:  python `"$PSScriptRoot\gui.py`""
Write-Host "start it now:  python `"$Tray`""
Write-Host "daemon helper:  python `"$Helper`" start --repo ~/your-project --user you"
Write-Host "shell hook: add to your `$PROFILE ->  . `"$PSScriptRoot\hooks.ps1`""

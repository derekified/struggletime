# install the struggletime helper on windows (per-user scheduled task, no admin)
$Root = Split-Path -Parent $PSScriptRoot
$Helper = Join-Path $PSScriptRoot "helper.py"
$Repo = if ($env:REPO) { $env:REPO } else { $HOME }
$Action = New-ScheduledTaskAction -Execute "python" -Argument "`"$Helper`" start --repo `"$Repo`" --user `"$env:USERNAME`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "StruggleTimeHelper" -Action $Action -Trigger $Trigger -Force | Out-Null
Write-Host "scheduled task 'StruggleTimeHelper' registered (repo: $Repo). edit the task to change the repo."
Write-Host "gui:  python `"$PSScriptRoot\gui.py`""
Write-Host "start it now:  python `"$Helper`" start --repo ~/your-project --user you"
Write-Host "shell hook: add to your `$PROFILE ->  . `"$PSScriptRoot\hooks.ps1`""

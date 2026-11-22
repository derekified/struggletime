# Install StruggleTime as a Start Menu app + CLI on Windows.
# Usage (from an extracted release or source tree):
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Binary .\struggletime.exe
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Autostart
param(
  [string]$Binary = "",
  [switch]$Autostart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Dest = Join-Path $env:LOCALAPPDATA "Programs\StruggleTime"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

function Resolve-Binary {
  param([string]$Hint)
  if ($Hint -and (Test-Path $Hint)) { return (Resolve-Path $Hint).Path }
  $release = Join-Path $Root "rust\target\release\struggletime.exe"
  if (Test-Path $release) { return $release }
  $here = Join-Path (Get-Location) "struggletime.exe"
  if (Test-Path $here) { return $here }
  $named = Join-Path (Get-Location) "struggletime-windows-x64.exe"
  if (Test-Path $named) { return $named }
  Push-Location (Join-Path $Root "rust")
  try {
    cargo build --release -p struggletime
  } finally {
    Pop-Location
  }
  if (-not (Test-Path $release)) { throw "could not build struggletime.exe" }
  return $release
}

$Src = Resolve-Binary -Hint $Binary
$Exe = Join-Path $Dest "struggletime.exe"
Copy-Item $Src $Exe -Force
Write-Host "installed $Exe"

# User PATH
$binDir = $Dest
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
$parts = $userPath.Split(';') | Where-Object { $_ -and $_.Trim() -ne "" }
if ($parts -notcontains $binDir) {
  $newPath = ($parts + $binDir) -join ';'
  [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
  $env:Path = "$binDir;$env:Path"
  Write-Host "added $binDir to user PATH"
} else {
  Write-Host "PATH already includes $binDir"
}

# Start Menu shortcut → tray mode
$programs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Item -ItemType Directory -Force -Path $programs | Out-Null
$lnkPath = Join-Path $programs "StruggleTime.lnk"
$w = New-Object -ComObject WScript.Shell
$sc = $w.CreateShortcut($lnkPath)
$sc.TargetPath = $Exe
$sc.Arguments = "--tray"
$sc.WorkingDirectory = $Dest
$sc.WindowStyle = 7
$sc.Description = "StruggleTime tray"
$sc.Save()
Write-Host "Start Menu: $lnkPath"

if ($Autostart) {
  $startup = [Environment]::GetFolderPath("Startup")
  $auto = Join-Path $startup "StruggleTime.lnk"
  $sc2 = $w.CreateShortcut($auto)
  $sc2.TargetPath = $Exe
  $sc2.Arguments = "--tray"
  $sc2.WorkingDirectory = $Dest
  $sc2.WindowStyle = 7
  $sc2.Description = "StruggleTime tray (login)"
  $sc2.Save()
  Write-Host "Startup: $auto"
}

Write-Host ""
Write-Host "Done. Open StruggleTime from the Start Menu, or in a new terminal:"
Write-Host "  struggletime --version"
Write-Host "  struggletime apikey <st_…>"
Write-Host "  struggletime status"

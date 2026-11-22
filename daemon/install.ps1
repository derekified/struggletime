# Install StruggleTime native Windows binaries into %LOCALAPPDATA%\StruggleTime\bin
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Out = Join-Path $env:LOCALAPPDATA "StruggleTime\bin"
New-Item -ItemType Directory -Force -Path $Out | Out-Null

Push-Location (Join-Path $Root "rust")
try {
  cargo build --release
} finally {
  Pop-Location
}

foreach ($name in @("struggletime-helper","struggletime-daemon","struggletime-server","struggletime-tray")) {
  $src = Join-Path $Root "rust\target\release\$name.exe"
  if (-not (Test-Path $src)) { throw "missing $src" }
  Copy-Item $src (Join-Path $Out "$name.exe") -Force
  Write-Host "installed $name.exe -> $Out"
}
Write-Host "Add $Out to your PATH, then run: struggletime-helper status"

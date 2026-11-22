# Install StruggleTime as a Start Menu app + CLI (Windows).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
& (Join-Path $Root "scripts\install.ps1") @args

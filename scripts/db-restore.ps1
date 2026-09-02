# Restore a SQLite backup taken by db-backup.ps1 onto this machine.
#
#   ./scripts/db-restore.ps1 backups\trading_bot_data_20260902-101500.sqlite
#
# REPLACES the current trading_bot_data.sqlite. The one it replaces is moved aside
# to trading_bot_data.sqlite.bak-<stamp> first, never deleted. Run with the bot
# and the dashboard STOPPED.

param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dst  = Join-Path $root "trading_bot_data.sqlite"

if (-not (Test-Path $BackupFile)) { throw "Backup file not found: $BackupFile" }

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*run_live_auto_bot*" -or $_.CommandLine -like "*streamlit*app.py*" -or $_.CommandLine -like "*streamlit*streamlit_app*" }
if ($running) {
    throw "Stop the bot/dashboard first (PID $($running.ProcessId -join ', ')) — restoring under a live process corrupts state."
}

if (Test-Path $dst) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $aside = "$dst.bak-$stamp"
    Move-Item -Path $dst -Destination $aside
    Write-Host "Existing DB moved aside -> $aside"
}

Copy-Item -Path $BackupFile -Destination $dst
Write-Host "Restored $BackupFile -> $dst"
Write-Host "Start the bot: `$env:PYTHONUTF8='1'; ./venv/Scripts/python trading_bot/run_live_auto_bot.py"

# Snapshot the bot's SQLite database to a timestamped copy in backups/.
#
# This bot's entire persistence is ONE file: trading_bot_data.sqlite at the repo
# root (trades, settings, logs). Moving it to another machine is just a file copy —
# no pg_dump / pg_restore, no service. That's the whole point of SQLite here.
#
#   ./scripts/db-backup.ps1                     -> backups/trading_bot_data_<stamp>.sqlite
#   ./scripts/db-restore.ps1 <that file>        (on the other machine)
#
# Run this with the bot STOPPED so the file isn't mid-write.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src  = Join-Path $root "trading_bot_data.sqlite"

if (-not (Test-Path $src)) { throw "trading_bot_data.sqlite not found at $src" }

# Warn if the headless trader looks like it's running (open file = possible mid-write).
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*run_live_auto_bot*" }
if ($running) {
    Write-Warning "run_live_auto_bot.py appears to be running (PID $($running.ProcessId)). Stop it first for a clean copy."
}

$backups = Join-Path $root "backups"
New-Item -ItemType Directory -Force -Path $backups | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $backups "trading_bot_data_$stamp.sqlite"

Copy-Item -Path $src -Destination $out
Write-Host "Backed up -> $out"
Write-Host "Copy that file to the other machine and run: ./scripts/db-restore.ps1 <file>"

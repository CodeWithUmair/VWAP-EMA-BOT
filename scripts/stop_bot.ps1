<#
.SYNOPSIS
    Stop the dashboard and the trading engine started by start_bot.ps1.

.DESCRIPTION
    Targets exactly the PIDs start_bot.ps1 recorded in .run/*.pid, so it never
    guesses at "any python.exe" and never touches processes it didn't start.
    MT5 itself is left running by default - it's the broker connection, not
    part of this bot's process tree, and other tools may depend on it too.

.PARAMETER Mt5Too
    Also close the MT5 terminal.
#>
param(
    [switch]$Mt5Too
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $RepoRoot ".run"
$Python = Join-Path $RepoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }
$env:PYTHONPATH = $RepoRoot

function Stop-ByPidFile($name, $pidFile) {
    if (-not (Test-Path $pidFile)) {
        Write-Host "    $name : no PID file, nothing to stop" -ForegroundColor DarkGray
        return $false
    }
    $procId = Get-Content $pidFile -ErrorAction SilentlyContinue
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    $stopped = $false
    if ($proc) {
        Stop-Process -Id $procId -Force
        Write-Host "    $name : stopped (PID $procId)" -ForegroundColor Green
        $stopped = $true
    } else {
        Write-Host "    $name : PID $procId not running (already stopped)" -ForegroundColor DarkGray
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    return $stopped
}

Write-Host "==> Stopping bot processes" -ForegroundColor Cyan
$engineStopped = Stop-ByPidFile "Engine   " (Join-Path $RunDir "engine.pid")
Stop-ByPidFile "Dashboard" (Join-Path $RunDir "dashboard.pid") | Out-Null

# The heartbeat can otherwise linger "fresh" (< 20s old) for a few seconds after
# the process is gone, which fooled start_bot.ps1's liveness check into thinking
# a restart wasn't needed. Clear it so the next start_bot run isn't fooled.
if ($engineStopped) {
    $clearScript = Join-Path $RunDir "_clear_heartbeat.py"
    @'
from trading_bot.storage import BotStorage
BotStorage().set_setting("engine_heartbeat", None)
'@ | Set-Content -Encoding ascii $clearScript
    & $Python $clearScript 2>$null
}

if ($Mt5Too) {
    $mt5 = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue
    if ($mt5) { Stop-Process -Id $mt5.Id -Force; Write-Host "    MT5      : stopped" -ForegroundColor Green }
    else { Write-Host "    MT5      : not running" -ForegroundColor DarkGray }
} else {
    Write-Host "    MT5      : left running (pass -Mt5Too to close it too)" -ForegroundColor DarkGray
}

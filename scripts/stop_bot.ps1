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

function Stop-ByPidFile($name, $pidFile) {
    if (-not (Test-Path $pidFile)) {
        Write-Host "    $name : no PID file, nothing to stop" -ForegroundColor DarkGray
        return
    }
    $procId = Get-Content $pidFile -ErrorAction SilentlyContinue
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $procId -Force
        Write-Host "    $name : stopped (PID $procId)" -ForegroundColor Green
    } else {
        Write-Host "    $name : PID $procId not running (already stopped)" -ForegroundColor DarkGray
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

Write-Host "==> Stopping bot processes" -ForegroundColor Cyan
Stop-ByPidFile "Engine   " (Join-Path $RunDir "engine.pid")
Stop-ByPidFile "Dashboard" (Join-Path $RunDir "dashboard.pid")

if ($Mt5Too) {
    $mt5 = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue
    if ($mt5) { Stop-Process -Id $mt5.Id -Force; Write-Host "    MT5      : stopped" -ForegroundColor Green }
    else { Write-Host "    MT5      : not running" -ForegroundColor DarkGray }
} else {
    Write-Host "    MT5      : left running (pass -Mt5Too to close it too)" -ForegroundColor DarkGray
}

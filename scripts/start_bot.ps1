<#
.SYNOPSIS
    Bring the whole bot up in one command: MT5 terminal, the headless trading
    engine, and the Streamlit dashboard.

.DESCRIPTION
    "Start the bot" means three separate programs are running and talking to
    each other through the shared SQLite settings table and the MT5 terminal:

      1. MetaTrader 5 (terminal64.exe)   - the broker connection everything else needs
      2. The dashboard (Streamlit, :8501) - the sidebar, the live checklist, manual controls
      3. The headless engine              - the process that actually places orders

    This script is idempotent: each stage checks whether it's already up (MT5
    by process name, the dashboard by its port, the engine by its SQLite
    heartbeat) and only starts what's missing. Running it twice in a row is
    safe and does not spawn duplicates.

    PIDs and logs land in .run/, gitignored, so stop_bot.ps1 can target exactly
    the processes this script started rather than guessing from a process name.

.PARAMETER DashboardPort
    Port for the Streamlit dashboard. Default 8501.

.PARAMETER Strategy
    Pin the engine to one strategy key (e.g. crt_body_soup). Omit to have the
    engine follow whatever is selected in the dashboard's sidebar.

.PARAMETER Mt5Path
    Path to terminal64.exe, if it isn't already running and isn't at the
    default Exness install location.

.EXAMPLE
    ./scripts/start_bot.ps1
    Starts (or confirms already running) all three, engine follows the sidebar.

.EXAMPLE
    ./scripts/start_bot.ps1 -Strategy crt_body_soup -DashboardPort 8502
#>
param(
    [int]$DashboardPort = 8501,
    [string]$Strategy = "",
    [string]$Mt5Path = "C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$RunDir = Join-Path $RepoRoot ".run"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$Python = Join-Path $RepoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

# The helper check-scripts below live under .run/, outside the package tree, so
# python needs telling where trading_bot/ actually is.
$env:PYTHONPATH = $RepoRoot

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK   $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    ..   $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# 1. MT5 terminal
# ---------------------------------------------------------------------------
Write-Step "MetaTrader 5 terminal"
$mt5proc = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue
if ($mt5proc) {
    Write-Ok "already running (PID $($mt5proc.Id))"
} else {
    if (-not (Test-Path $Mt5Path)) {
        Write-Host "    MT5 not found at '$Mt5Path'." -ForegroundColor Red
        Write-Host "    Pass -Mt5Path <path to terminal64.exe> and re-run." -ForegroundColor Red
        exit 1
    }
    Start-Process -FilePath $Mt5Path | Out-Null
    Write-Warn2 "launched $Mt5Path - waiting for it to come up..."
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if (Get-Process -Name "terminal64" -ErrorAction SilentlyContinue) { $ready = $true; break }
    }
    if ($ready) { Write-Ok "MT5 process is up" }
    else {
        Write-Host "    MT5 did not appear within 60s - check it manually, then re-run this script." -ForegroundColor Red
        exit 1
    }
    # Give it a further moment to finish logging in before anything queries it.
    Start-Sleep -Seconds 5
}

# ---------------------------------------------------------------------------
# 2. Dashboard
# ---------------------------------------------------------------------------
Write-Step "Dashboard (port $DashboardPort)"
$portBusy = Get-NetTCPConnection -LocalPort $DashboardPort -State Listen -ErrorAction SilentlyContinue
if ($portBusy) {
    Write-Ok "already listening on :$DashboardPort"
} else {
    $dashLog = Join-Path $RunDir "dashboard.log"
    $dashArgs = @("-m", "streamlit", "run", "trading_bot\streamlit_app.py",
                  "--server.port", "$DashboardPort", "--server.headless", "true")
    $p = Start-Process -FilePath $Python -ArgumentList $dashArgs `
            -RedirectStandardOutput $dashLog -RedirectStandardError "$dashLog.err" `
            -WindowStyle Hidden -PassThru
    $p.Id | Out-File -Encoding ascii (Join-Path $RunDir "dashboard.pid")
    Write-Warn2 "launched (PID $($p.Id)) - waiting for it to bind the port..."
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        if (Get-NetTCPConnection -LocalPort $DashboardPort -State Listen -ErrorAction SilentlyContinue) {
            $ready = $true; break
        }
    }
    if ($ready) { Write-Ok "listening on :$DashboardPort  ->  http://localhost:$DashboardPort" }
    else { Write-Warn2 "not confirmed yet - check $dashLog if it doesn't come up" }
}

# ---------------------------------------------------------------------------
# 3. Headless engine
# ---------------------------------------------------------------------------
Write-Step "Trading engine"
$checkScript = Join-Path $RunDir '_check_heartbeat.py'
@'
from trading_bot.storage import BotStorage
from datetime import datetime, timezone
hb = BotStorage().get_setting('engine_heartbeat', None)
if not hb:
    print('NONE')
else:
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb)).total_seconds()
    print('LIVE' if age < 20 else 'STALE')
'@ | Set-Content -Encoding ascii $checkScript
$hbCheck = & $Python $checkScript
if ($hbCheck.Trim() -eq "LIVE") {
    Write-Ok "already running (heartbeat is current)"
} else {
    $engineLog = Join-Path $RunDir "engine.log"
    $engineArgs = @("-u", "-m", "trading_bot.run_live_auto_bot")
    if ($Strategy -ne "") { $engineArgs += @("--strategy", $Strategy) }
    $p = Start-Process -FilePath $Python -ArgumentList $engineArgs `
            -RedirectStandardOutput $engineLog -RedirectStandardError "$engineLog.err" `
            -WindowStyle Hidden -PassThru
    $p.Id | Out-File -Encoding ascii (Join-Path $RunDir "engine.pid")
    Write-Warn2 "launched (PID $($p.Id)) - waiting for the first heartbeat..."
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 2
        $chk = & $Python $checkScript
        if ($chk.Trim() -eq "LIVE") { $ready = $true; break }
    }
    if ($ready) { Write-Ok "connected to MT5 and running - see $engineLog" }
    else {
        Write-Host "    Engine did not confirm a heartbeat within 40s." -ForegroundColor Red
        Write-Host "    Check $engineLog and $engineLog.err - a common cause is MT5" -ForegroundColor Red
        Write-Host "    still finishing login, or Algo Trading not enabled in the terminal." -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Dashboard: http://localhost:$DashboardPort"
Write-Host "Logs:      $RunDir"
Write-Host "Stop with: ./scripts/stop_bot.ps1"

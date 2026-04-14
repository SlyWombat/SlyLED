<#
.SYNOPSIS
    Install / uninstall / check the SlyLED DevGUI as a Windows scheduled task.

.DESCRIPTION
    Registers tools\devgui\server.py as a scheduled task that starts at user
    logon and restarts on failure.  Runs under the current user account so it
    has access to the project directory and Python environment.

.PARAMETER Action
    install   - Create the scheduled task (starts at logon).
    uninstall - Remove the scheduled task and kill any running instance.
    status    - Show whether the task exists and its current state.
    start     - Manually start the task now.
    stop      - Stop the running task.

.EXAMPLE
    .\service.ps1 install
    .\service.ps1 status
    .\service.ps1 uninstall
#>

param(
    [Parameter(Mandatory=$true, Position=0)]
    [ValidateSet('install','uninstall','status','start','stop')]
    [string]$Action
)

$ErrorActionPreference = 'Continue'

$TaskName   = 'SlyLED DevGUI'
$Port       = 9090
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir '..\..\')).Path
$ServerPy   = Join-Path $ScriptDir 'server.py'

# Find Python
$Python = Get-Command python -ErrorAction SilentlyContinue |
          Select-Object -ExpandProperty Source -First 1
if (-not $Python) {
    $Python = Get-Command python3 -ErrorAction SilentlyContinue |
              Select-Object -ExpandProperty Source -First 1
}
if (-not $Python) {
    Write-Error "Python not found on PATH."
    exit 1
}

function Install-DevGuiTask {
    # Require elevation — schtasks /create needs admin
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "Elevating to Administrator..." -ForegroundColor Cyan
        Start-Process powershell.exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$($MyInvocation.ScriptName)`" install"
        return
    }

    # Remove existing task first (idempotent — ignore if not found)
    schtasks /delete /tn $TaskName /f 2>$null | Out-Null

    # schtasks /tr requires careful quoting for paths with spaces
    $tr = "\`"$Python\`" \`"$ServerPy\`" --port $Port"
    cmd /c "schtasks /create /tn `"$TaskName`" /tr `"$tr`" /sc onlogon /rl highest /f"

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create scheduled task."
        exit 1
    }

    # Add a firewall rule for the port
    netsh advfirewall firewall delete rule name="SlyLED DevGUI TCP $Port" 2>$null | Out-Null
    netsh advfirewall firewall add rule `
        name="SlyLED DevGUI TCP $Port" `
        dir=in action=allow protocol=TCP localport=$Port | Out-Null

    Write-Host "Installed scheduled task '$TaskName' (port $Port, runs at logon)." -ForegroundColor Green
    Write-Host "Run '.\service.ps1 start' to start it now, or it will start on next logon."
}

function Uninstall-DevGuiTask {
    # Kill running python instances for this server
    Get-Process python*, python3* -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'devgui[\\/]server\.py' } |
        Stop-Process -Force -ErrorAction SilentlyContinue

    schtasks /delete /tn $TaskName /f 2>$null | Out-Null

    netsh advfirewall firewall delete rule name="SlyLED DevGUI TCP $Port" 2>$null | Out-Null

    Write-Host "Uninstalled scheduled task '$TaskName' and removed firewall rule." -ForegroundColor Yellow
}

function Get-DevGuiStatus {
    $task = schtasks /query /tn $TaskName /fo LIST 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Task '$TaskName' is NOT installed." -ForegroundColor Yellow
        return
    }

    Write-Host "--- Scheduled Task ---" -ForegroundColor Cyan
    $task | ForEach-Object { Write-Host $_ }

    # Check if the process is actually running
    $running = Get-Process python*, python3* -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'devgui[\\/]server\.py' }

    if ($running) {
        Write-Host "`nProcess is RUNNING (PID: $($running.Id -join ', '))" -ForegroundColor Green
        Write-Host "  http://localhost:$Port"
    } else {
        Write-Host "`nProcess is NOT running." -ForegroundColor Yellow
        Write-Host "  Run '.\service.ps1 start' to start it."
    }
}

function Start-DevGuiTask {
    schtasks /run /tn $TaskName 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Task not installed. Run '.\service.ps1 install' first." -ForegroundColor Red
        exit 1
    }
    Start-Sleep -Seconds 2
    Write-Host "Started '$TaskName' -> http://localhost:$Port" -ForegroundColor Green
}

function Stop-DevGuiTask {
    Get-Process python*, python3* -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'devgui[\\/]server\.py' } |
        Stop-Process -Force -ErrorAction SilentlyContinue

    schtasks /end /tn $TaskName 2>$null | Out-Null

    Write-Host "Stopped '$TaskName'." -ForegroundColor Yellow
}

switch ($Action) {
    'install'   { Install-DevGuiTask }
    'uninstall' { Uninstall-DevGuiTask }
    'status'    { Get-DevGuiStatus }
    'start'     { Start-DevGuiTask }
    'stop'      { Stop-DevGuiTask }
}

# SlyLED Windows Parent — launcher
# Run from repo root or desktop/windows/
param(
    [int]$Port = 5000
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent | Split-Path -Parent

# Locate Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Error "Python not found. Install Python 3.10+ and ensure it is on PATH."
    exit 1
}

# Install/upgrade dependencies quietly if needed
$req = Join-Path $PSScriptRoot "requirements.txt"
& $python.Source -m pip install -q -r $req

# Launch server
$server = Join-Path $root "desktop\shared\parent_server.py"
Write-Host "Starting SlyLED parent on http://localhost:$Port ..."
& $python.Source $server --port $Port

# Test script for Giga LED Project (Rainbow Cycle sketch)
# Compiles main.ino for Arduino Giga R1 WiFi. Optionally uploads if -Upload is passed.
# Requires: arduino-cli, FastLED in ./libraries (script sets ARDUINO_DIRECTORIES_USER to this project)

param(
    [switch]$Upload
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

# Use this project as Arduino user dir so ./libraries (e.g. FastLED) is found
$env:ARDUINO_DIRECTORIES_USER = $ProjectRoot

$FQBN = "arduino:mbed_giga:giga"
$SketchDir = Join-Path $ProjectRoot "main"

Write-Host "Project: $ProjectRoot" -ForegroundColor Cyan
Write-Host "FQBN:    $FQBN" -ForegroundColor Cyan
Write-Host ""

# Compile
Write-Host "Compiling main.ino ..." -ForegroundColor Yellow
$compileResult = arduino-cli compile --fqbn $FQBN $SketchDir 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host $compileResult
    Write-Host "Compile failed." -ForegroundColor Red
    exit 1
}
Write-Host $compileResult
Write-Host "Compile succeeded." -ForegroundColor Green

if (-not $Upload) {
    Write-Host "`nSkipping upload (pass -Upload to compile and upload)." -ForegroundColor Gray
    exit 0
}

# Find port
$ports = arduino-cli board list --format json 2>&1 | ConvertFrom-Json
$gigaPort = ($ports.boards | Where-Object { $_.matching_boards -match "giga" } | Select-Object -First 1).port.address
if (-not $gigaPort) {
    Write-Host "No Giga R1 board found. Connect the board and try again." -ForegroundColor Red
    exit 1
}

Write-Host "`nUploading to $gigaPort ..." -ForegroundColor Yellow
$uploadResult = arduino-cli compile --upload --port $gigaPort --fqbn $FQBN $SketchDir 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host $uploadResult
    Write-Host "Upload failed." -ForegroundColor Red
    exit 1
}
Write-Host $uploadResult
Write-Host "Upload succeeded." -ForegroundColor Green

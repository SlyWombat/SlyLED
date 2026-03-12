# build.ps1 — increment minor version, compile, and upload to Arduino Giga R1
# Usage: .\build.ps1 [-Port COM7] [-NoUpload]
param(
    [string]$Port = "COM7",
    [switch]$NoUpload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Bump minor version ────────────────────────────────────────────────────────
$versionFile = "$PSScriptRoot\main\version.h"
$content = Get-Content $versionFile -Raw

if ($content -match '#define APP_MINOR (\d+)') {
    $oldMinor = [int]$Matches[1]
    $newMinor = $oldMinor + 1
    $content = $content -replace "#define APP_MINOR $oldMinor", "#define APP_MINOR $newMinor"
    Set-Content $versionFile $content -NoNewline
    $major = if ($content -match '#define APP_MAJOR (\d+)') { $Matches[1] } else { "?" }
    Write-Host "Version bumped to $major.$newMinor" -ForegroundColor Cyan
} else {
    Write-Error "Could not parse APP_MINOR from $versionFile"
}

# ── Compile (and upload) ──────────────────────────────────────────────────────
$env:ARDUINO_DIRECTORIES_USER = $PSScriptRoot
$cli = "$env:LOCALAPPDATA\Arduino\arduino-cli.exe"

if ($NoUpload) {
    & $cli compile --fqbn arduino:mbed_giga:giga main
} else {
    & $cli compile --upload --port $Port --fqbn arduino:mbed_giga:giga main
}

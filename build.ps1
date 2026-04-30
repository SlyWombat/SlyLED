# build.ps1 — increment minor version, compile, and upload
# Usage: .\build.ps1 [-Port COM7] [-Board giga|esp32|d1mini|gyro] [-NoUpload]
#
# -Board : "giga"   → arduino:mbed_giga:giga      (Arduino Giga R1 WiFi)
#          "esp32"  → esp32:esp32:esp32             (ESP32 Dev Module)
#          "d1mini" → esp8266:esp8266:d1_mini       (LOLIN D1 Mini / D1 R2)
#          "gyro"   → esp32:esp32:esp32s3           (Waveshare ESP32-S3 LCD 1.28)
#          (omit)   → auto-detect from connected board
# -NoUpload : compile only (no upload, no version bump)
param(
    [string]$Port    = "COM7",
    [string]$Board   = "",
    [switch]$NoUpload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$cli = "$env:LOCALAPPDATA\Arduino\arduino-cli.exe"
$env:ARDUINO_DIRECTORIES_USER = $PSScriptRoot

# ── Resolve target board ───────────────────────────────────────────────────────
if ($Board -eq "") {
    Write-Host "Auto-detecting board..." -ForegroundColor Cyan
    $boardList = & $cli board list 2>&1
    if ($boardList -match "arduino:mbed_giga:giga") {
        $Board = "giga"
        Write-Host "Detected: Arduino Giga R1 WiFi" -ForegroundColor Cyan
    } elseif ($boardList -match "esp32:esp32") {
        $Board = "esp32"
        Write-Host "Detected: ESP32 Dev Module" -ForegroundColor Cyan
    } elseif ($boardList -match "esp8266:esp8266") {
        $Board = "d1mini"
        Write-Host "Detected: LOLIN D1 Mini (ESP8266)" -ForegroundColor Cyan
    } elseif ($boardList -match "esp32:esp32:esp32s3") {
        $Board = "gyro"
        Write-Host "Detected: Waveshare ESP32-S3 LCD 1.28 (gyro board)" -ForegroundColor Cyan
    } else {
        Write-Error "No supported board detected. Use -Board giga, esp32, d1mini, or gyro"
    }
}

$extraFlags = @()
switch ($Board.ToLower()) {
    "giga"   { $fqbn = "arduino:mbed_giga:giga" }
    "esp32"  { $fqbn = "esp32:esp32:esp32" }
    "d1mini" { $fqbn = "esp8266:esp8266:d1_mini" }
    "gyro"   {
        $fqbn = "esp32:esp32:esp32s3"
        # Use compiler.{cpp,c}.extra_flags — `build.extra_flags` overwrites
        # core defines on the ESP32 platform. Pass each --build-property
        # entry as a separate arg pair so PowerShell doesn't collapse them
        # into a single quoted token (arduino-cli rejects that as
        # "unknown flag: --build-property build.extra_flags").
        $extraFlags = @(
            "--build-property", "compiler.cpp.extra_flags=-DGYRO_BOARD",
            "--build-property", "compiler.c.extra_flags=-DGYRO_BOARD"
        )
    }
    default  { Write-Error "Unknown board '$Board'. Use 'giga', 'esp32', 'd1mini', or 'gyro'" }
}

Write-Host "Target: $fqbn" -ForegroundColor Cyan

# ── Bump firmware minor version (upload only) ───────────────────────────────
# NOTE: version.h tracks FIRMWARE version (for ESP32/D1 Mini/Giga child).
# The app/orchestrator version (parent_server.py, installer, Android) is
# managed separately — firmware only changes when firmware code changes.
$versionFile = "$PSScriptRoot\main\version.h"
$content = Get-Content $versionFile -Raw

if (-not $NoUpload) {
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
} else {
    $minor = if ($content -match '#define APP_MINOR (\d+)') { $Matches[1] } else { "?" }
    $major = if ($content -match '#define APP_MAJOR (\d+)') { $Matches[1] } else { "?" }
    Write-Host "Compile check (no version bump) - current: $major.$minor" -ForegroundColor Yellow
}

# ── Compile (and upload) ──────────────────────────────────────────────────────
$buildPath = "$PSScriptRoot\build\$Board"
New-Item -ItemType Directory -Force -Path $buildPath | Out-Null

if ($NoUpload) {
    & $cli compile --fqbn $fqbn --build-path $buildPath @extraFlags main
} else {
    & $cli compile --upload --port $Port --fqbn $fqbn --build-path $buildPath @extraFlags main
}

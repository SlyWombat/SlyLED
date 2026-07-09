# build.ps1 — increment minor version, compile, and upload
# Usage: .\build.ps1 [-Port COM7] [-Board giga|esp32|d1mini|gyro|mmwave] [-NoUpload]
#
# -Board : "giga"   → arduino:mbed_giga:giga        (Arduino Giga R1 WiFi)
#          "esp32"  → esp32:esp32:esp32             (ESP32 Dev Module)
#          "d1mini" → esp8266:esp8266:d1_mini       (LOLIN D1 Mini / D1 R2)
#          "gyro"   → esp32:esp32:esp32s3           (Waveshare ESP32-S3 LCD 1.28)
#          "mmwave" → esp32:esp32:esp32c61          (ESP32-C61 MMwave radar node,
#                     isolated toolchain via arduino-cli-mmwave.yaml, sketch mmwave/)
#          (omit)   → auto-detect from connected board
# -NoUpload : compile only (no upload, no version bump)
#
# Machine-specific constants (arduino-cli path, default COM port) come from
# build.config.json at repo root; fallbacks below match the historical values.
param(
    [string]$Port    = "",
    [string]$Board   = "",
    [switch]$NoUpload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Machine-specific constants (build.config.json, fallbacks baked in) ────────
$buildConfig = $null
$buildConfigPath = Join-Path $PSScriptRoot "build.config.json"
if (Test-Path $buildConfigPath) {
    try { $buildConfig = Get-Content $buildConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Write-Host "WARN: build.config.json unreadable - using built-in defaults" -ForegroundColor Yellow }
}
function Get-BuildConfig([string]$name, [string]$default) {
    $v = $default
    if ($buildConfig -and $buildConfig.PSObject.Properties[$name] -and $buildConfig.$name) { $v = $buildConfig.$name }
    return [Environment]::ExpandEnvironmentVariables($v)
}

$cli = Get-BuildConfig 'arduinoCli' "$env:LOCALAPPDATA\Arduino\arduino-cli.exe"
if ($Port -eq "") { $Port = Get-BuildConfig 'defaultPort' 'COM7' }
$env:ARDUINO_DIRECTORIES_USER = $PSScriptRoot

# ── Resolve target board ───────────────────────────────────────────────────────
if ($Board -eq "") {
    Write-Host "Auto-detecting board..." -ForegroundColor Cyan
    $boardList = & $cli board list 2>&1
    # Most-specific FQBN first: "esp32:esp32" is a substring of the S3/C61
    # FQBNs, so the generic check must come last (#902 — pre-fix an S3 gyro
    # board auto-detected as a plain ESP32).
    if ($boardList -match "arduino:mbed_giga:giga") {
        $Board = "giga"
        Write-Host "Detected: Arduino Giga R1 WiFi" -ForegroundColor Cyan
    } elseif ($boardList -match "esp32:esp32:esp32s3") {
        $Board = "gyro"
        Write-Host "Detected: Waveshare ESP32-S3 LCD 1.28 (gyro board)" -ForegroundColor Cyan
    } elseif ($boardList -match "esp32:esp32:esp32c61") {
        $Board = "mmwave"
        Write-Host "Detected: ESP32-C61 (MMwave radar node)" -ForegroundColor Cyan
    } elseif ($boardList -match "esp32:esp32") {
        $Board = "esp32"
        Write-Host "Detected: ESP32 Dev Module" -ForegroundColor Cyan
    } elseif ($boardList -match "esp8266:esp8266") {
        $Board = "d1mini"
        Write-Host "Detected: LOLIN D1 Mini (ESP8266)" -ForegroundColor Cyan
    } else {
        Write-Error "No supported board detected. Use -Board giga, esp32, d1mini, gyro, or mmwave"
    }
}

$extraFlags  = @()
$configArgs  = @()
$sketchDir   = "main"
$versionFile = "$PSScriptRoot\main\version.h"
$verPrefix   = "APP"
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
    "mmwave" {
        # MMwave radar node (#902): its own sketch dir (mmwave/, not the
        # FastLED-era main/), its own version track (mmwave/version.h,
        # MMW_* macros), and the isolated C61 toolchain — the config file
        # points arduino-cli at .arduino-mmwave/data so the stable boards'
        # core installation never moves.
        $fqbn        = "esp32:esp32:esp32c61:FlashSize=8M,PartitionScheme=default_8MB"
        $configArgs  = @("--config-file", "$PSScriptRoot\arduino-cli-mmwave.yaml")
        $sketchDir   = "mmwave"
        $versionFile = "$PSScriptRoot\mmwave\version.h"
        $verPrefix   = "MMW"
    }
    default  { Write-Error "Unknown board '$Board'. Use 'giga', 'esp32', 'd1mini', 'gyro', or 'mmwave'" }
}

Write-Host "Target: $fqbn" -ForegroundColor Cyan

# ── Bump firmware minor version (upload only) ───────────────────────────────
# NOTE: the version header tracks FIRMWARE version only. main/version.h
# (APP_*) covers the boards built from main/; mmwave/version.h (MMW_*) is
# the radar node's independent track. The app/orchestrator version
# (parent_server.py, installer, Android) is managed separately — firmware
# only changes when firmware code changes.
$content = Get-Content $versionFile -Raw

if (-not $NoUpload) {
    if ($content -match "#define ${verPrefix}_MINOR (\d+)") {
        $oldMinor = [int]$Matches[1]
        $newMinor = $oldMinor + 1
        $content = $content -replace "#define ${verPrefix}_MINOR $oldMinor", "#define ${verPrefix}_MINOR $newMinor"
        Set-Content $versionFile $content -NoNewline
        $major = if ($content -match "#define ${verPrefix}_MAJOR (\d+)") { $Matches[1] } else { "?" }
        Write-Host "Version bumped to $major.$newMinor" -ForegroundColor Cyan
    } else {
        Write-Error "Could not parse ${verPrefix}_MINOR from $versionFile"
    }
} else {
    $minor = if ($content -match "#define ${verPrefix}_MINOR (\d+)") { $Matches[1] } else { "?" }
    $major = if ($content -match "#define ${verPrefix}_MAJOR (\d+)") { $Matches[1] } else { "?" }
    Write-Host "Compile check (no version bump) - current: $major.$minor" -ForegroundColor Yellow
}

# ── Compile (and upload) ──────────────────────────────────────────────────────
$buildPath = "$PSScriptRoot\build\$Board"
New-Item -ItemType Directory -Force -Path $buildPath | Out-Null
$sketchPath = Join-Path $PSScriptRoot $sketchDir

if ($NoUpload) {
    & $cli @configArgs compile --fqbn $fqbn --build-path $buildPath @extraFlags $sketchPath
} else {
    & $cli @configArgs compile --upload --port $Port --fqbn $fqbn --build-path $buildPath @extraFlags $sketchPath
}

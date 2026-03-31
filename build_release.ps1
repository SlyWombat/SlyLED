# build_release.ps1 — Build all platforms, increment patch version, compile, package
#
# Usage: powershell -ExecutionPolicy Bypass -File build_release.ps1
#
# Builds: ESP32 firmware, D1 Mini firmware, Windows exe, Android APK
# Increments APP_PATCH in version.h and syncs to all platform version strings

param(
    [switch]$SkipFirmware,
    [switch]$SkipWindows,
    [switch]$SkipAndroid
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

Write-Host "`n=== SlyLED Release Build ===" -ForegroundColor Cyan

# ── Step 1: Increment patch version ──────────────────────────────────────────
$versionFile = "$root\main\version.h"
$content = Get-Content $versionFile -Raw
if ($content -match '#define APP_MAJOR\s+(\d+)') { $major = $Matches[1] }
if ($content -match '#define APP_MINOR\s+(\d+)') { $minor = $Matches[1] }
if ($content -match '#define APP_PATCH\s+(\d+)') { $patch = [int]$Matches[1] + 1 }
$version = "$major.$minor.$patch"
Write-Host "Version: $version" -ForegroundColor Green

# Write version.h
@"
#pragma once
#define APP_MAJOR $major
#define APP_MINOR $minor
#define APP_PATCH $patch
"@ | Set-Content $versionFile -Encoding UTF8

# Sync to parent_server.py
(Get-Content "$root\desktop\shared\parent_server.py" -Raw) -replace 'VERSION = "[^"]+"', "VERSION = `"$version`"" | Set-Content "$root\desktop\shared\parent_server.py" -Encoding UTF8

# Sync to Android build.gradle.kts
(Get-Content "$root\android\app\build.gradle.kts" -Raw) -replace 'versionName = "[^"]+"', "versionName = `"$version`"" | Set-Content "$root\android\app\build.gradle.kts" -Encoding UTF8

# Sync to firmware registry
(Get-Content "$root\firmware\registry.json" -Raw) -replace '"version": "[^"]+"', "`"version`": `"$version`"" | Set-Content "$root\firmware\registry.json" -Encoding UTF8

Write-Host "All versions synced to $version" -ForegroundColor Green

# ── Step 2: Compile firmware ─────────────────────────────────────────────────
if (-not $SkipFirmware) {
    $cli = "$env:LOCALAPPDATA\Arduino\arduino-cli.exe"
    $env:ARDUINO_DIRECTORIES_USER = $root

    Write-Host "`n--- ESP32 Firmware ---" -ForegroundColor Yellow
    & $cli compile --clean --fqbn esp32:esp32:esp32 "$root\main" --output-dir "$root\firmware\esp32"
    if ($LASTEXITCODE -ne 0) { Write-Host "ESP32 FAILED" -ForegroundColor Red; exit 1 }

    Write-Host "`n--- D1 Mini Firmware ---" -ForegroundColor Yellow
    & $cli compile --clean --fqbn esp8266:esp8266:d1_mini "$root\main" --output-dir "$root\firmware\d1mini"
    if ($LASTEXITCODE -ne 0) { Write-Host "D1 Mini FAILED" -ForegroundColor Red; exit 1 }

    Write-Host "Firmware compiled" -ForegroundColor Green
}

# ── Step 3: Windows Desktop (PyInstaller) ────────────────────────────────────
if (-not $SkipWindows) {
    Write-Host "`n--- Windows Desktop ---" -ForegroundColor Yellow
    Set-Location "$root\desktop\windows"
    python build.py
    if ($LASTEXITCODE -ne 0) { Write-Host "Windows build FAILED" -ForegroundColor Red; exit 1 }
    $exeSize = (Get-Item "$root\desktop\windows\dist\SlyLED.exe").Length
    Write-Host "SlyLED.exe: $([math]::Round($exeSize/1MB, 1)) MB" -ForegroundColor Green

    # Build installer via Inno Setup
    $iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $iscc)) { $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" }
    if (Test-Path $iscc) {
        Write-Host "Building installer..." -ForegroundColor Yellow
        & $iscc "$root\desktop\windows\installer.iss"
        if ($LASTEXITCODE -eq 0) {
            $setupSize = (Get-Item "$root\desktop\windows\dist\SlyLED-Setup.exe").Length
            Write-Host "SlyLED-Setup.exe: $([math]::Round($setupSize/1MB, 1)) MB" -ForegroundColor Green
        } else {
            Write-Host "Installer build FAILED (non-fatal)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Inno Setup not found — skipping installer (exe still available)" -ForegroundColor Yellow
    }
    Set-Location $root
}

# ── Step 4: Android APK ─────────────────────────────────────────────────────
if (-not $SkipAndroid) {
    Write-Host "`n--- Android APK ---" -ForegroundColor Yellow
    $env:JAVA_HOME = 'C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot'
    $env:ANDROID_SDK_ROOT = 'C:\Android\Sdk'
    Set-Location "$root\android"
    .\gradlew.bat assembleRelease --no-daemon
    if ($LASTEXITCODE -ne 0) { Write-Host "Android FAILED" -ForegroundColor Red; exit 1 }
    $apkPath = Get-ChildItem -Path "C:\Android\build\slyled-app" -Recurse -Filter "app-release.apk" | Select-Object -First 1
    if ($apkPath) {
        $apkSize = $apkPath.Length
        Write-Host "APK: $([math]::Round($apkSize/1MB, 1)) MB at $($apkPath.FullName)" -ForegroundColor Green
    }
    Set-Location $root
}

# ── Summary ──────────────────────────────────────────────────────────────────
Write-Host "`n=== Build Complete: v$version ===" -ForegroundColor Cyan
Write-Host "  Firmware:  firmware\esp32\main.ino.bin, firmware\d1mini\main.ino.bin"
Write-Host "  Windows:   desktop\windows\dist\SlyLED.exe"
Write-Host "  Installer: desktop\windows\dist\SlyLED-Setup.exe"
Write-Host "  Android:   C:\Android\build\slyled-app\outputs\apk\release\app-release.apk"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  git add -A && git commit -m 'feat: v$version' && git push origin 3d"
Write-Host "  gh release create v$version --target 3d --title 'v$version'"

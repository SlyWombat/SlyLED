# build_release.ps1 — Build all platforms, increment patch version, compile, package
#
# Usage: powershell -ExecutionPolicy Bypass -File build_release.ps1
#        powershell -ExecutionPolicy Bypass -File build_release.ps1 -SetVersion "8.3.0"
#
# Builds: ESP32 firmware, D1 Mini firmware, Windows exe, Windows installer, Android APK
# Increments APP_PATCH in version.h and syncs to all platform version strings
# Creates a git tag for the release version

param(
    [switch]$SkipFirmware,
    [switch]$SkipWindows,
    [switch]$SkipAndroid,
    [string]$SetVersion = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

Write-Host "`n=== SlyLED Release Build ===" -ForegroundColor Cyan

# ── Step 1: Determine version ───────────────────────────────────────────────
$versionFile = "$root\main\version.h"
$content = Get-Content $versionFile -Raw
if ($content -match '#define APP_MAJOR\s+(\d+)') { $major = [int]$Matches[1] }
if ($content -match '#define APP_MINOR\s+(\d+)') { $minor = [int]$Matches[1] }
if ($content -match '#define APP_PATCH\s+(\d+)') { $patch = [int]$Matches[1] }

if ($SetVersion) {
    # Manual version override (e.g. -SetVersion "9.0.0")
    $parts = $SetVersion.Split(".")
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    $patch = if ($parts.Length -ge 3) { [int]$parts[2] } else { 0 }
} else {
    # Auto-increment patch
    $patch = $patch + 1
}
$version = "$major.$minor.$patch"

# ── Step 1b: Validate no version regression ─────────────────────────────────
# Check latest git tag to ensure we're not going backwards
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if ($gitCmd) {
    $latestTag = & git describe --tags --abbrev=0 2>$null
    if ($latestTag -and $latestTag.StartsWith("v")) {
        $tagVer = $latestTag.TrimStart("v")
        $tagParts = $tagVer.Split(".")
        $curParts = $version.Split(".")
        $tagNum = [int]$tagParts[0] * 10000 + [int]$tagParts[1] * 100 + [int]$tagParts[2]
        $curNum = [int]$curParts[0] * 10000 + [int]$curParts[1] * 100 + [int]$curParts[2]
        if ($curNum -lt $tagNum) {
            Write-Host "ERROR: Version $version is lower than latest tag $latestTag" -ForegroundColor Red
            Write-Host "       Use -SetVersion to set a higher version" -ForegroundColor Yellow
            exit 1
        }
    }
} else {
    Write-Host "  (git not on PATH - skipping tag validation)" -ForegroundColor Yellow
}

Write-Host "Version: $version" -ForegroundColor Green

# ── Step 2: Write version to all platform files ─────────────────────────────
# version.h (firmware)
@"
#pragma once
#define APP_MAJOR $major
#define APP_MINOR $minor
#define APP_PATCH $patch
"@ | Set-Content $versionFile -Encoding UTF8

# parent_server.py (desktop)
(Get-Content "$root\desktop\shared\parent_server.py" -Raw) -replace 'VERSION = "[^"]+"', "VERSION = `"$version`"" | Set-Content "$root\desktop\shared\parent_server.py" -Encoding UTF8

# Android build.gradle.kts
(Get-Content "$root\android\app\build.gradle.kts" -Raw) -replace 'versionName = "[^"]+"', "versionName = `"$version`"" | Set-Content "$root\android\app\build.gradle.kts" -Encoding UTF8

# Firmware registry
(Get-Content "$root\firmware\registry.json" -Raw) -replace '"version": "[^"]+"', "`"version`": `"$version`"" | Set-Content "$root\firmware\registry.json" -Encoding UTF8

Write-Host "All versions synced to $version" -ForegroundColor Green

# ── Step 3: Compile firmware ────────────────────────────────────────────────
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

# ── Step 4: Windows Desktop (PyInstaller) ───────────────────────────────────
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
        Write-Host "Inno Setup not found - skipping installer (exe still available)" -ForegroundColor Yellow
    }
    Set-Location $root
}

# ── Step 5: Android APK ────────────────────────────────────────────────────
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

# ── Step 6: Copy to dist/ ──────────────────────────────────────────────────
Write-Host "`n--- Copying to dist/ ---" -ForegroundColor Yellow
$distDir = "$root\dist"
if (-not (Test-Path $distDir)) { New-Item -ItemType Directory -Path $distDir | Out-Null }

Copy-Item "$root\firmware\esp32\main.ino.merged.bin" "$distDir\esp32-firmware-merged.bin" -Force -ErrorAction SilentlyContinue
Copy-Item "$root\firmware\d1mini\main.ino.bin" "$distDir\d1mini-firmware.bin" -Force -ErrorAction SilentlyContinue
Copy-Item "$root\desktop\windows\dist\SlyLED.exe" "$distDir\SlyLED.exe" -Force -ErrorAction SilentlyContinue
Copy-Item "$root\desktop\windows\dist\SlyLED-Setup.exe" "$distDir\SlyLED-Setup.exe" -Force -ErrorAction SilentlyContinue
$apk = Get-ChildItem -Path "C:\Android\build\slyled-app" -Recurse -Filter "app-release.apk" | Select-Object -First 1
if ($apk) { Copy-Item $apk.FullName "$distDir\SlyLED.apk" -Force }
$dbgApk = Get-ChildItem -Path "C:\Android\build\slyled-app" -Recurse -Filter "app-debug.apk" | Select-Object -First 1
if ($dbgApk) { Copy-Item $dbgApk.FullName "$distDir\SlyLED-debug.apk" -Force }
Write-Host "dist/ updated" -ForegroundColor Green

# ── Step 7: Create git tag ──────────────────────────────────────────────────
if ($gitCmd) {
    Write-Host "`n--- Git tag ---" -ForegroundColor Yellow
    $tagName = "v$version"
    $existingTag = & git tag -l $tagName 2>$null
    if ($existingTag) {
        Write-Host "Tag $tagName already exists - skipping" -ForegroundColor Yellow
    }
    else {
        & git tag -a $tagName -m "Release $tagName"
        Write-Host "Created tag: $tagName" -ForegroundColor Green
    }
} else {
    Write-Host "`n  (git not on PATH - skipping tag creation)" -ForegroundColor Yellow
}

# ── Summary ─────────────────────────────────────────────────────────────────
Write-Host "`n=== Build Complete: v$version ===" -ForegroundColor Cyan
Write-Host "  Firmware:  firmware\esp32\main.ino.bin, firmware\d1mini\main.ino.bin"
Write-Host "  Windows:   desktop\windows\dist\SlyLED.exe"
Write-Host "  Installer: desktop\windows\dist\SlyLED-Setup.exe"
Write-Host "  Android:   dist\SlyLED.apk"
Write-Host "  dist/:     All binaries copied"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  git add -A && git commit -m 'release: v$version' && git push origin main --tags"
Write-Host "  gh release create v$version --target main --title 'v$version'"

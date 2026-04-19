# build_release.ps1 — Build all platforms with independent version tracks
#
# Usage: powershell -ExecutionPolicy Bypass -File build_release.ps1
#        powershell -ExecutionPolicy Bypass -File build_release.ps1 -SkipFirmware -SkipAndroid
#        powershell -ExecutionPolicy Bypass -File build_release.ps1 -SetAppVersion "1.2.0"
#
# Version tracks (all independent):
#   App (desktop + Android):  parent_server.py VERSION → installer.iss, build.gradle.kts
#   ESP32 firmware:           registry.json "child-led-esp32"
#   D1 Mini firmware:         registry.json "child-led-d1mini"
#   Giga DMX bridge:          registry.json "dmx-bridge-esp32"
#   Giga Child:               registry.json "child-led-giga"
#   Giga Parent:              registry.json "parent-giga"
#   Camera (Orange Pi):       registry.json "camera-orangepi" + camera_server.py VERSION

param(
    [switch]$SkipFirmware,
    [switch]$SkipWindows,
    [switch]$SkipAndroid,
    [string]$SetAppVersion = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

Write-Host "`n=== SlyLED Release Build ===" -ForegroundColor Cyan

# ── Helper: increment a "major.minor.patch" version string ─────────────────
function Increment-Patch([string]$ver) {
    $parts = $ver.Split(".")
    $parts[2] = [string]([int]$parts[2] + 1)
    return ($parts -join ".")
}

# ── Helper: read/write registry.json ───────────────────────────────────────
$regPath = "$root\firmware\registry.json"
function Read-Registry { Get-Content $regPath -Raw | ConvertFrom-Json }
function Save-Registry($reg) { $reg | ConvertTo-Json -Depth 5 | Set-Content $regPath -Encoding UTF8 }

function Get-FwVersion([string]$id) {
    $reg = Read-Registry
    $entry = $reg.firmware | Where-Object { $_.id -eq $id }
    if ($entry) { return $entry.version } else { return "0.0.0" }
}

function Set-FwVersion([string]$id, [string]$ver) {
    $reg = Read-Registry
    $entry = $reg.firmware | Where-Object { $_.id -eq $id }
    if ($entry) { $entry.version = $ver }
    Save-Registry $reg
}

# ── Helper: write version.h from a version string ─────────────────────────
function Write-VersionH([string]$ver) {
    $parts = $ver.Split(".")
    @"
#pragma once
#define APP_MAJOR $($parts[0])
#define APP_MINOR $($parts[1])
#define APP_PATCH $($parts[2])
"@ | Set-Content "$root\main\version.h" -Encoding UTF8
}

# ── Step 1: Determine app version ──────────────────────────────────────────
$serverPy = Get-Content "$root\desktop\shared\parent_server.py" -Raw
if ($serverPy -match 'VERSION = "([^"]+)"') { $appVersion = $Matches[1] } else { $appVersion = "1.0.0" }

if ($SetAppVersion) {
    $appVersion = $SetAppVersion
} else {
    $appVersion = Increment-Patch $appVersion
}

# Validate no regression against git tags
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if ($gitCmd) {
    $latestTag = & git describe --tags --abbrev=0 2>$null
    if ($latestTag -and $latestTag.StartsWith("v")) {
        $tagVer = $latestTag.TrimStart("v")
        $tagParts = $tagVer.Split(".")
        $curParts = $appVersion.Split(".")
        $tagNum = [int]$tagParts[0] * 10000 + [int]$tagParts[1] * 100 + [int]$tagParts[2]
        $curNum = [int]$curParts[0] * 10000 + [int]$curParts[1] * 100 + [int]$curParts[2]
        if ($curNum -lt $tagNum) {
            Write-Host "ERROR: App version $appVersion is lower than latest tag $latestTag" -ForegroundColor Red
            Write-Host "       Use -SetAppVersion to set a higher version" -ForegroundColor Yellow
            exit 1
        }
    }
}

Write-Host "App version: $appVersion" -ForegroundColor Green

# ── Step 2: Sync app version to all app platform files ─────────────────────
# parent_server.py
(Get-Content "$root\desktop\shared\parent_server.py" -Raw) -replace 'VERSION = "[^"]+"', "VERSION = `"$appVersion`"" | Set-Content "$root\desktop\shared\parent_server.py" -Encoding UTF8

# Android build.gradle.kts
(Get-Content "$root\android\app\build.gradle.kts" -Raw) -replace 'versionName = "[^"]+"', "versionName = `"$appVersion`"" | Set-Content "$root\android\app\build.gradle.kts" -Encoding UTF8

Write-Host "App versions synced to $appVersion" -ForegroundColor Green

# ── Step 3: Compile firmware (each board increments independently) ─────────
if (-not $SkipFirmware) {
    $cli = "$env:LOCALAPPDATA\Arduino\arduino-cli.exe"
    $env:ARDUINO_DIRECTORIES_USER = $root

    # --- ESP32 ---
    $espVer = Increment-Patch (Get-FwVersion "child-led-esp32")
    Write-VersionH $espVer
    Write-Host "`n--- ESP32 Firmware v$espVer ---" -ForegroundColor Yellow
    & $cli compile --clean --fqbn esp32:esp32:esp32 "$root\main" --output-dir "$root\firmware\esp32"
    if ($LASTEXITCODE -ne 0) { Write-Host "ESP32 FAILED" -ForegroundColor Red; exit 1 }
    Set-FwVersion "child-led-esp32" $espVer

    # --- D1 Mini ---
    $d1Ver = Increment-Patch (Get-FwVersion "child-led-d1mini")
    Write-VersionH $d1Ver
    Write-Host "`n--- D1 Mini Firmware v$d1Ver ---" -ForegroundColor Yellow
    & $cli compile --clean --fqbn esp8266:esp8266:d1_mini "$root\main" --output-dir "$root\firmware\d1mini"
    if ($LASTEXITCODE -ne 0) { Write-Host "D1 Mini FAILED" -ForegroundColor Red; exit 1 }
    Set-FwVersion "child-led-d1mini" $d1Ver

    # Note: Giga boards (child-led-giga, parent-giga, dmx-bridge-esp32) compile
    # separately — increment their registry entry when building those targets.

    Write-Host "`nFirmware compiled: ESP32 v$espVer, D1 Mini v$d1Ver" -ForegroundColor Green
}

# ── Step 4: Windows Desktop (PyInstaller + Inno Setup) ────────────────────
if (-not $SkipWindows) {
    Write-Host "`n--- Windows Desktop (App v$appVersion) ---" -ForegroundColor Yellow
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

# ── Step 5: Android APK ───────────────────────────────────────────────────
if (-not $SkipAndroid) {
    Write-Host "`n--- Android APK (App v$appVersion) ---" -ForegroundColor Yellow
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

# ── Step 6: Copy to dist/ ─────────────────────────────────────────────────
Write-Host "`n--- Copying to dist/ ---" -ForegroundColor Yellow
$distDir = "$root\dist"
if (-not (Test-Path $distDir)) { New-Item -ItemType Directory -Path $distDir | Out-Null }

Copy-Item "$root\firmware\esp32\main.ino.merged.bin" "$distDir\esp32-firmware-merged.bin" -Force -ErrorAction SilentlyContinue
Copy-Item "$root\firmware\d1mini\main.ino.bin" "$distDir\d1mini-firmware.bin" -Force -ErrorAction SilentlyContinue
Copy-Item "$root\desktop\windows\dist\SlyLED.exe" "$distDir\SlyLED.exe" -Force -ErrorAction SilentlyContinue
Copy-Item "$root\desktop\windows\dist\SlyLED-Setup.exe" "$distDir\SlyLED-Setup.exe" -Force -ErrorAction SilentlyContinue
$apk = Get-ChildItem -Path "C:\Android\build\slyled-app" -Recurse -Filter "app-release.apk" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($apk) { Copy-Item $apk.FullName "$distDir\SlyLED.apk" -Force }
$dbgApk = Get-ChildItem -Path "C:\Android\build\slyled-app" -Recurse -Filter "app-debug.apk" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($dbgApk) { Copy-Item $dbgApk.FullName "$distDir\SlyLED-debug.apk" -Force }
Write-Host "dist/ updated" -ForegroundColor Green

# Step 6b: Refresh registry SHA-256 hashes (#568 security review).
# Any binary we just rebuilt needs its `sha256` in registry.json re-pinned
# so downloads can verify integrity. Walks every registry entry that
# declares a releaseAsset and, if the matching file lives in dist/,
# updates sha256 to the fresh hash. Unchanged binaries keep their hash.
Write-Host "`n--- Refreshing registry SHA-256 hashes ---" -ForegroundColor Yellow
$reg = Read-Registry
$anyChanged = $false
foreach ($fw in $reg.firmware) {
    $asset = $fw.releaseAsset
    if (-not $asset) { continue }
    $distPath = Join-Path $distDir $asset
    if (-not (Test-Path $distPath)) { continue }
    $newHash = (Get-FileHash -Algorithm SHA256 -Path $distPath).Hash.ToLower()
    $oldHash = $null
    if ($fw.PSObject.Properties['sha256']) { $oldHash = $fw.sha256 }
    if ($newHash -ne $oldHash) {
        $anyChanged = $true
        if ($fw.PSObject.Properties['sha256']) {
            $fw.sha256 = $newHash
        } else {
            $fw | Add-Member -MemberType NoteProperty -Name sha256 -Value $newHash
        }
        $shortHash = $newHash.Substring(0, 12)
        Write-Host "  $($fw.id): sha256 -> $shortHash..." -ForegroundColor Green
    }
}
if ($anyChanged) {
    Save-Registry $reg
    Write-Host "registry.json SHAs updated - commit with the release" -ForegroundColor Green
} else {
    Write-Host "All SHAs already match dist/ binaries" -ForegroundColor Gray
}

# ── Step 7: Create git tag (app version only) ─────────────────────────────
if ($gitCmd) {
    Write-Host "`n--- Git tag ---" -ForegroundColor Yellow
    $tagName = "v$appVersion"
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

# ── Summary ────────────────────────────────────────────────────────────────
Write-Host "`n=== Build Complete ===" -ForegroundColor Cyan
Write-Host "  App:       v$appVersion (desktop + Android)" -ForegroundColor White
$reg = Read-Registry
foreach ($fw in $reg.firmware) {
    Write-Host "  $($fw.id): v$($fw.version)" -ForegroundColor Gray
}
Write-Host ""
Write-Host "  dist/:     All binaries copied"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  git add -A && git commit -m 'release: v$appVersion' && git push origin main --tags"
Write-Host "  gh release create v$appVersion --target main --title 'v$appVersion'"

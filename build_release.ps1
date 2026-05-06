# build_release.ps1 — Build all platforms with independent version tracks
#
# Usage: powershell -ExecutionPolicy Bypass -File build_release.ps1
#        powershell -ExecutionPolicy Bypass -File build_release.ps1 -SkipFirmware -SkipAndroid
#        powershell -ExecutionPolicy Bypass -File build_release.ps1 -SetAppVersion "1.2.0"
#
# Version tracks (all independent — see #824):
#   Orchestrator (desktop):   parent_server.py VERSION → installer.iss
#   Android APK:              android/app/build.gradle.kts versionName/versionCode
#                             (own track since #824 — was previously synced
#                              to orchestrator, hiding half-shipped releases)
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
    [switch]$ForceFirmware,
    [string]$SetAppVersion = "",
    # #version-clobber-guard: required to step a firmware's major version up.
    # Without this the script refuses any v7→v8 (etc.) bump because that's how
    # the registry got contaminated by the legacy unified-track era. See
    # memory/reference_firmware_field_versions.md for the per-board truth.
    [switch]$AllowMajorBump
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
    # Operator owns firmware versions (see memory/feedback_firmware_version_
    # authority.md). When the script auto-bumps, also update releaseTag so it
    # stays in sync with the new version label.
    if ($entry -and $entry.releaseTag) {
        $tagPrefix = ($entry.releaseTag -replace 'v[0-9]+\.[0-9]+\.[0-9]+$', '')
        if ($tagPrefix) { $entry.releaseTag = "${tagPrefix}v${ver}" }
    }
    Save-Registry $reg
}

# #version-clobber-guard — block accidental v8.x contamination.
# The legacy unified-build era (pre-2026-04-04) parked every firmware on
# v8.x. The actual per-board independent tracks are LED v7.5.x, DMX bridge
# v7.5.x, gyro v1.x, camera v1.x. If Increment-Patch ever proposes a v8.x
# bump, that's the unified-track ghost coming back; refuse unless
# -AllowMajorBump is explicitly passed.
function Assert-NoMajorBumpRegression([string]$id, [string]$current, [string]$proposed) {
    $maj = [int]($proposed.Split('.')[0])
    $curMaj = [int]($current.Split('.')[0])
    if ($maj -ge 8 -and $curMaj -lt 8) {
        Write-Host ("ABORT: would bump " + $id + " from v" + $current + " to v" + $proposed) -ForegroundColor Red
        Write-Host "       v8.x track is permanently retired (see memory/reference_firmware_field_versions.md)" -ForegroundColor Red
        Write-Host "       Pass -AllowMajorBump if you really mean it." -ForegroundColor Red
        throw "v8 bump blocked for $id"
    }
}

# True when the registry entry has been flagged on hold by the operator
# (e.g. parent-giga). On-hold entries are skipped entirely - no compile,
# no version bump, no source-hash update, no release publish.
function Test-FwOnHold([string]$id) {
    $reg = Read-Registry
    $entry = $reg.firmware | Where-Object { $_.id -eq $id }
    return ($entry -and $entry.PSObject.Properties['onHold'] -and $entry.onHold)
}

function Set-FwSourceHash([string]$id, [string]$hash) {
    $reg = Read-Registry
    $entry = $reg.firmware | Where-Object { $_.id -eq $id }
    if (-not $entry) { return }
    if ($entry.PSObject.Properties['sourceHash']) {
        $entry.sourceHash = $hash
    } else {
        $entry | Add-Member -MemberType NoteProperty -Name sourceHash -Value $hash
    }
    Save-Registry $reg
}

function Get-FwSourceHash([string]$id) {
    $reg = Read-Registry
    $entry = $reg.firmware | Where-Object { $_.id -eq $id }
    if ($entry -and $entry.PSObject.Properties['sourceHash']) { return $entry.sourceHash }
    return ""
}

# Hash the firmware source tree the arduino-cli compiler actually consumes.
# Includes main/*.{ino,h,cpp,c,hpp} + libraries/**/*.{h,cpp,c,hpp,ino} +
# arduino_secrets.h is intentionally excluded (gitignored, not part of release).
# version.h is excluded — it's an *output* of the bump, not an input.
function Get-FirmwareSourceHash {
    $files = @()
    $files += Get-ChildItem -Path "$root\main" -Include *.ino,*.h,*.cpp,*.c,*.hpp -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne 'version.h' -and $_.Name -ne 'arduino_secrets.h' }
    if (Test-Path "$root\libraries") {
        $files += Get-ChildItem -Path "$root\libraries" -Include *.ino,*.h,*.cpp,*.c,*.hpp -File -Recurse -ErrorAction SilentlyContinue
    }
    $files = $files | Sort-Object FullName
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $combined = New-Object System.IO.MemoryStream
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($root.Length).TrimStart('\','/').Replace('\','/')
        $relBytes = [System.Text.Encoding]::UTF8.GetBytes($rel + "`n")
        $combined.Write($relBytes, 0, $relBytes.Length)
        $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
        $combined.Write($bytes, 0, $bytes.Length)
        $combined.WriteByte(0)
    }
    $combined.Position = 0
    $hashBytes = $sha.ComputeHash($combined)
    $combined.Dispose()
    $sha.Dispose()
    return ($hashBytes | ForEach-Object { $_.ToString('x2') }) -join ''
}

# ── Helper: hash Android app source for the build gate ───────────────────
# Excludes versionCode / versionName lines from build.gradle.kts so a
# version-bump-only release doesn't kick off a 5-minute Gradle rebuild.
# Excludes build outputs, the gradle cache, and the IDE caches.
function Get-AndroidSourceHash {
    $files = @()
    $files += Get-ChildItem -Path "$root\android\app\src" -Include *.kt,*.java,*.xml -File -Recurse -ErrorAction SilentlyContinue
    $extra = @(
        "$root\android\app\build.gradle.kts",
        "$root\android\app\proguard-rules.pro",
        "$root\android\build.gradle.kts",
        "$root\android\settings.gradle.kts",
        "$root\android\gradle.properties"
    )
    foreach ($e in $extra) {
        if (Test-Path $e) { $files += Get-Item $e }
    }
    $files = $files | Sort-Object FullName
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $combined = New-Object System.IO.MemoryStream
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($root.Length).TrimStart('\','/').Replace('\','/')
        $relBytes = [System.Text.Encoding]::UTF8.GetBytes($rel + "`n")
        $combined.Write($relBytes, 0, $relBytes.Length)
        if ($rel -eq 'android/app/build.gradle.kts') {
            # Strip versionCode / versionName so a version-only bump doesn't
            # invalidate the cache. Match the assignment lines regardless of
            # whitespace.
            $lines = Get-Content $f.FullName |
                     Where-Object { $_ -notmatch '^\s*versionCode\s*=' -and `
                                    $_ -notmatch '^\s*versionName\s*=' }
            $bytes = [System.Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
        } else {
            $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
        }
        $combined.Write($bytes, 0, $bytes.Length)
        $combined.WriteByte(0)
    }
    $combined.Position = 0
    $hashBytes = $sha.ComputeHash($combined)
    $combined.Dispose()
    $sha.Dispose()
    return ($hashBytes | ForEach-Object { $_.ToString('x2') }) -join ''
}

# Cache file lives outside firmware/registry.json (which only tracks board
# firmware) but follows the same gating idea — tracked in git so a fresh
# checkout knows what was last built.
$androidCachePath = "$root\android\.build-cache.json"
function Get-AndroidStoredHash {
    if (-not (Test-Path $androidCachePath)) { return "" }
    try {
        $j = Get-Content $androidCachePath -Raw | ConvertFrom-Json
        return $j.sourceHash
    } catch { return "" }
}
function Set-AndroidStoredHash([string]$hash, [string]$ver) {
    $obj = [pscustomobject]@{ sourceHash = $hash; lastBuiltVersion = $ver; lastBuiltTs = (Get-Date -Format 'o') }
    $obj | ConvertTo-Json | Set-Content $androidCachePath -Encoding UTF8
}

# ── Helper: hash orchestrator (desktop) source for the version-bump gate ─
# Excludes the VERSION line in parent_server.py so a version-only bump
# doesn't invalidate its own gate. Without this gate, the orchestrator
# patch number drifted upward on every build_release.ps1 run regardless
# of whether anything actually changed (logged as "release: vX" git tags
# with empty diffs); see operator complaint 2026-05-05.
function Get-OrchestratorSourceHash {
    $files = @()
    $files += Get-ChildItem -Path "$root\desktop" -Include *.py,*.html,*.js,*.css -File -Recurse -ErrorAction SilentlyContinue
    $extra = @(
        "$root\desktop\windows\installer.iss",
        "$root\desktop\windows\build.py",
        "$root\desktop\windows\run.ps1"
    )
    foreach ($e in $extra) {
        if (Test-Path $e) { $files += Get-Item $e }
    }
    $files = $files | Sort-Object FullName -Unique
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $combined = New-Object System.IO.MemoryStream
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($root.Length).TrimStart('\','/').Replace('\','/')
        $relBytes = [System.Text.Encoding]::UTF8.GetBytes($rel + "`n")
        $combined.Write($relBytes, 0, $relBytes.Length)
        if ($rel -eq 'desktop/shared/parent_server.py') {
            # Strip VERSION = "..." so a version-only bump doesn't
            # invalidate the cache.
            $lines = Get-Content $f.FullName |
                     Where-Object { $_ -notmatch '^\s*VERSION\s*=\s*"' }
            $bytes = [System.Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
        } else {
            $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
        }
        $combined.Write($bytes, 0, $bytes.Length)
        $combined.WriteByte(0)
    }
    $combined.Position = 0
    $hashBytes = $sha.ComputeHash($combined)
    $combined.Dispose()
    $sha.Dispose()
    return ($hashBytes | ForEach-Object { $_.ToString('x2') }) -join ''
}

$orchCachePath = "$root\desktop\.build-cache.json"
function Get-OrchStoredHash {
    if (-not (Test-Path $orchCachePath)) { return "" }
    try {
        $j = Get-Content $orchCachePath -Raw | ConvertFrom-Json
        return $j.sourceHash
    } catch { return "" }
}
function Set-OrchStoredHash([string]$hash, [string]$ver) {
    $obj = [pscustomobject]@{ sourceHash = $hash; lastBuiltVersion = $ver; lastBuiltTs = (Get-Date -Format 'o') }
    $obj | ConvertTo-Json | Set-Content $orchCachePath -Encoding UTF8
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
# Source-hash-gate the orchestrator like Android: only bump the patch
# number when something under desktop/ actually changed. Pre-#824, this
# script bumped every run regardless, leaking empty version tags into
# git. The Android source-hash cache lives next to its source; mirror
# that for desktop/.
$serverPy = Get-Content "$root\desktop\shared\parent_server.py" -Raw
if ($serverPy -match 'VERSION = "([^"]+)"') { $orchCurVer = $Matches[1] } else { $orchCurVer = "1.0.0" }

$orchSrcHash = Get-OrchestratorSourceHash
$orchStored = Get-OrchStoredHash
$orchSourceUnchanged = ($orchStored -eq $orchSrcHash) -and (-not $SetAppVersion)

if ($SetAppVersion) {
    $appVersion = $SetAppVersion
} elseif ($orchSourceUnchanged) {
    $appVersion = $orchCurVer
    Write-Host "Orchestrator source unchanged - keeping v$appVersion" -ForegroundColor Gray
} else {
    $appVersion = Increment-Patch $orchCurVer
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

# ── Step 2: Sync orchestrator version (Android tracks independently) ──────
# `$appVersion` is the orchestrator (desktop) track only — parent_server.py
# + installer.iss + the SPA. Android has its OWN version in
# android/app/build.gradle.kts → versionName, bumped by the source-hash
# gate in Step 5 below. The two tracks deliberately drift: a server-only
# bug fix bumps orchestrator without touching Android, and vice-versa,
# so the operator's mismatch banner (#824) actually surfaces something
# real instead of always reading "matched".
(Get-Content "$root\desktop\shared\parent_server.py" -Raw) -replace 'VERSION = "[^"]+"', "VERSION = `"$appVersion`"" | Set-Content "$root\desktop\shared\parent_server.py" -Encoding UTF8

Write-Host "Orchestrator version: $appVersion (Android tracks independently)" -ForegroundColor Green

# ── Step 3: Compile firmware (per-board, only when source changed) ─────────
# Source-hash gate: each board entry in registry.json carries `sourceHash`
# (sha256 of every firmware-input file). We rebuild + bump only when the
# current hash differs. -SkipFirmware skips the entire step. -ForceFirmware
# rebuilds every board regardless of hash.
if (-not $SkipFirmware) {
    $cli = "$env:LOCALAPPDATA\Arduino\arduino-cli.exe"
    $env:ARDUINO_DIRECTORIES_USER = $root

    $srcHash = Get-FirmwareSourceHash
    Write-Host "`nFirmware source hash: $($srcHash.Substring(0,12))..." -ForegroundColor Gray

    # --- ESP32 ---
    if (Test-FwOnHold "child-led-esp32") {
        Write-Host "ESP32 firmware: onHold flag set - skipping" -ForegroundColor Gray
    } else {
    $espStored = Get-FwSourceHash "child-led-esp32"
    if (-not $ForceFirmware -and $espStored -eq $srcHash) {
        Write-Host "ESP32 firmware: source unchanged - skipping (v$(Get-FwVersion 'child-led-esp32'))" -ForegroundColor Gray
    } else {
        $espCur = Get-FwVersion "child-led-esp32"
        $espVer = Increment-Patch $espCur
        if (-not $AllowMajorBump) { Assert-NoMajorBumpRegression "child-led-esp32" $espCur $espVer }
        Write-VersionH $espVer
        Write-Host "`n--- ESP32 Firmware v$espVer ---" -ForegroundColor Yellow
        & $cli compile --clean --fqbn esp32:esp32:esp32 "$root\main" --output-dir "$root\firmware\esp32"
        if ($LASTEXITCODE -ne 0) { Write-Host "ESP32 FAILED" -ForegroundColor Red; exit 1 }
        Set-FwVersion "child-led-esp32" $espVer
        Set-FwSourceHash "child-led-esp32" $srcHash
    }
    }

    # --- D1 Mini ---
    if (Test-FwOnHold "child-led-d1mini") {
        Write-Host "D1 Mini firmware: onHold flag set - skipping" -ForegroundColor Gray
    } else {
    $d1Stored = Get-FwSourceHash "child-led-d1mini"
    if (-not $ForceFirmware -and $d1Stored -eq $srcHash) {
        Write-Host "D1 Mini firmware: source unchanged - skipping (v$(Get-FwVersion 'child-led-d1mini'))" -ForegroundColor Gray
    } else {
        $d1Cur = Get-FwVersion "child-led-d1mini"
        $d1Ver = Increment-Patch $d1Cur
        if (-not $AllowMajorBump) { Assert-NoMajorBumpRegression "child-led-d1mini" $d1Cur $d1Ver }
        Write-VersionH $d1Ver
        Write-Host "`n--- D1 Mini Firmware v$d1Ver ---" -ForegroundColor Yellow
        & $cli compile --clean --fqbn esp8266:esp8266:d1_mini "$root\main" --output-dir "$root\firmware\d1mini"
        if ($LASTEXITCODE -ne 0) { Write-Host "D1 Mini FAILED" -ForegroundColor Red; exit 1 }
        Set-FwVersion "child-led-d1mini" $d1Ver
        Set-FwSourceHash "child-led-d1mini" $srcHash
    }
    }

    # --- Gyro (ESP32-S3, BOARD_GYRO) ---
    # Same source-hash gate as ESP32/D1 — without this the gyro version drifted
    # silently because build_release.ps1 never tracked it (1.2.0 → 8.5.20
    # hand-bumped on 2026-04-30; see follow-up #769 / #768 context). Now its
    # version moves only when main/ or libraries/ actually change.
    if (Test-FwOnHold "gyro-esp32s3") {
        Write-Host "Gyro firmware: onHold flag set - skipping" -ForegroundColor Gray
    } else {
    $gyroStored = Get-FwSourceHash "gyro-esp32s3"
    if (-not $ForceFirmware -and $gyroStored -eq $srcHash) {
        Write-Host "Gyro firmware: source unchanged - skipping (v$(Get-FwVersion 'gyro-esp32s3'))" -ForegroundColor Gray
    } else {
        $gyroCur = Get-FwVersion "gyro-esp32s3"
        $gyroVer = Increment-Patch $gyroCur
        if (-not $AllowMajorBump) { Assert-NoMajorBumpRegression "gyro-esp32s3" $gyroCur $gyroVer }
        Write-VersionH $gyroVer
        Write-Host "`n--- Gyro Firmware v$gyroVer (BOARD_GYRO) ---" -ForegroundColor Yellow
        # ESP32 Arduino core honours compiler.cpp/c.extra_flags, not
        # build.extra_flags — same pattern build.ps1 uses for the gyro target.
        & $cli compile --clean --fqbn esp32:esp32:esp32s3 "$root\main" `
            --output-dir "$root\firmware\esp32s3" `
            --build-property "compiler.cpp.extra_flags=-DGYRO_BOARD" `
            --build-property "compiler.c.extra_flags=-DGYRO_BOARD"
        if ($LASTEXITCODE -ne 0) { Write-Host "Gyro FAILED" -ForegroundColor Red; exit 1 }
        Set-FwVersion "gyro-esp32s3" $gyroVer
        Set-FwSourceHash "gyro-esp32s3" $srcHash
    }
    }

    # Gyro test diagnostic build (#776) retired 2026-05-05 — superseded by
    # the gyro-esp32s3 telemetry path (CMD_GYRO_BATT, /api/gyro/state).
    # Build step + registry entry deleted; source files in main/TestGyro*
    # remain only because deleting them is a separate cleanup pass.

    # Note: Giga boards (child-led-giga, parent-giga, dmx-bridge-esp32) compile
    # separately — increment their registry entry when building those targets.

    Write-Host "`nFirmware step complete (rebuild only on source change)" -ForegroundColor Green
}

# ── Step 4: Windows Desktop (PyInstaller + Inno Setup) ────────────────────
if (-not $SkipWindows) {
    if ($orchSourceUnchanged -and -not $ForceFirmware) {
        Write-Host "`n--- Windows Desktop ---" -ForegroundColor Yellow
        Write-Host "Windows: orchestrator source unchanged - skipping (v$appVersion, cached SlyLED-Setup.exe in dist/)" -ForegroundColor Gray
    } else {
        Write-Host "`n--- Windows Desktop (App v$appVersion) ---" -ForegroundColor Yellow
        Set-Location "$root\desktop\windows"
        # Master script owns the app version — block build.py's auto-patch-bump
        # so parent_server.py stays at the version we just synced.
        $env:SLYLED_SKIP_VERSION_BUMP = "1"
        python build.py
        Remove-Item Env:SLYLED_SKIP_VERSION_BUMP -ErrorAction SilentlyContinue
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
        # Pin the orchestrator source-hash now that the rebuild succeeded.
        Set-OrchStoredHash $orchSrcHash $appVersion
    }
}

# ── Step 5: Android APK ───────────────────────────────────────────────────
if (-not $SkipAndroid) {
    $env:JAVA_HOME = 'C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot'
    $env:ANDROID_SDK_ROOT = 'C:\Android\Sdk'

    # Read Android's CURRENT versionName as the source of truth — mirrors
    # how firmware boards read from registry.json. Android tracks its own
    # patch level independently from $appVersion (orchestrator).
    $androidGradle = Get-Content "$root\android\app\build.gradle.kts" -Raw
    if ($androidGradle -match 'versionName\s*=\s*"([^"]+)"') {
        $androidCurVer = $Matches[1]
    } else {
        $androidCurVer = "1.0.0"
    }

    $androidSrcHash = Get-AndroidSourceHash
    $androidStored = Get-AndroidStoredHash
    if (-not $ForceFirmware -and $androidStored -eq $androidSrcHash) {
        Write-Host "`n--- Android APK ---" -ForegroundColor Yellow
        Write-Host "Android APK: source unchanged - skipping (v$androidCurVer, cached $((Get-Item $androidCachePath).LastWriteTime))" -ForegroundColor Gray
        $androidVer = $androidCurVer
    } else {
        # Bump the Android patch independently — same Increment-Patch logic
        # firmware boards use. Operator can hand-edit build.gradle.kts to
        # set a specific version (e.g. for a major bump) and this picks it
        # up as the new baseline.
        $androidVer = Increment-Patch $androidCurVer
        Write-Host "`n--- Android APK v$androidVer (was v$androidCurVer) ---" -ForegroundColor Yellow

        # Bump versionCode too so Play Store / sideload upgrade detection
        # works. versionCode lives next to versionName.
        if ($androidGradle -match 'versionCode\s*=\s*(\d+)') {
            $newCode = [int]$Matches[1] + 1
            $androidGradle = $androidGradle -replace 'versionCode\s*=\s*\d+', "versionCode = $newCode"
        }
        $androidGradle = $androidGradle -replace 'versionName = "[^"]+"', "versionName = `"$androidVer`""
        Set-Content "$root\android\app\build.gradle.kts" -Value $androidGradle -Encoding UTF8

        Set-Location "$root\android"
        .\gradlew.bat assembleRelease --no-daemon
        if ($LASTEXITCODE -ne 0) { Write-Host "Android FAILED" -ForegroundColor Red; exit 1 }
        $apkPath = Get-ChildItem -Path "C:\Android\build\slyled-app" -Recurse -Filter "app-release.apk" | Select-Object -First 1
        if ($apkPath) {
            $apkSize = $apkPath.Length
            Write-Host "APK: $([math]::Round($apkSize/1MB, 1)) MB at $($apkPath.FullName)" -ForegroundColor Green
        }
        Set-AndroidStoredHash $androidSrcHash $androidVer
        Set-Location $root
    }
}

# ── Step 6: Copy to dist/ ─────────────────────────────────────────────────
Write-Host "`n--- Copying to dist/ ---" -ForegroundColor Yellow
$distDir = "$root\dist"
if (-not (Test-Path $distDir)) { New-Item -ItemType Directory -Path $distDir | Out-Null }

Copy-Item "$root\firmware\esp32\main.ino.merged.bin" "$distDir\esp32-firmware-merged.bin" -Force -ErrorAction SilentlyContinue
Copy-Item "$root\firmware\d1mini\main.ino.bin" "$distDir\d1mini-firmware.bin" -Force -ErrorAction SilentlyContinue
# Bins that the prior build_release.ps1 forgot — the SHA-refresh step
# below walks dist/ and re-pins registry.json sha256 from whatever's
# there, so missing copies meant the registry stayed pinned to the
# previous release's hashes. Add gyro / gyro-test / dmx-bridge so
# every firmware entry's dist/ artifact matches the just-rebuilt bin.
Copy-Item "$root\firmware\esp32s3\main.ino.merged.bin" "$distDir\esp32s3-gyro-firmware.bin" -Force -ErrorAction SilentlyContinue
Copy-Item "$root\firmware\esp32s3-test\main.ino.merged.bin" "$distDir\esp32s3-gyro-test-firmware.bin" -Force -ErrorAction SilentlyContinue
Copy-Item "$root\firmware\esp32-dmx\main.ino.merged.bin" "$distDir\esp32-dmx-bridge-firmware.bin" -Force -ErrorAction SilentlyContinue
Copy-Item "$root\desktop\windows\dist\SlyLED.exe" "$distDir\SlyLED.exe" -Force -ErrorAction SilentlyContinue
Copy-Item "$root\desktop\windows\dist\SlyLED-Setup.exe" "$distDir\SlyLED-Setup.exe" -Force -ErrorAction SilentlyContinue
# Operator-canonical APK output: `dist/slyled-android.apk` (release-
# signed). The release variant is the operator-facing artifact;
# debug APK is a build-only intermediate and stays out of dist/.
$apk = Get-ChildItem -Path "C:\Android\build\slyled-app" -Recurse -Filter "app-release.apk" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($apk) { Copy-Item $apk.FullName "$distDir\slyled-android.apk" -Force }
Write-Host "dist/ updated" -ForegroundColor Green

# Mirror dist/ to the OneDrive pickup folder so the operator finds the
# finals where they always look for them. The work tree is now
# /mnt/d/SlyLED (D:\SlyLED) per 2026-05-06 directive; OneDrive holds
# only the operator-facing /dist mirror, no source.
$onedriveDist = "D:\OneDrive\My Documents\ElectricRV\Development\Projects\Lighting Arduino\dist"
if (Test-Path (Split-Path $onedriveDist -Parent)) {
    if (-not (Test-Path $onedriveDist)) {
        New-Item -ItemType Directory -Path $onedriveDist -Force | Out-Null
    }
    # Copy every artifact in $distDir to the OneDrive mirror. Force-
    # overwrite so a stale OneDrive copy from before the migration is
    # replaced cleanly.
    Get-ChildItem -Path $distDir -File | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $onedriveDist $_.Name) -Force -ErrorAction SilentlyContinue
    }
    Write-Host "dist/ mirrored to $onedriveDist (operator pickup)" -ForegroundColor Green
} else {
    Write-Host "OneDrive parent path not present; skipping mirror" -ForegroundColor Yellow
}

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

# Step 6c — #832: copy firmware/registry.json to %APPDATA%\SlyLED\firmware\
# so a running orchestrator (frozen exe or python) sees freshly-bumped
# versions without a reinstall. Pre-fix the bundled registry inside the
# PyInstaller exe was the only source — newly-built local firmware was
# invisible to the Firmware tab + OTA. Mirrors the binary-cache layout
# already used by `_FW_CACHE_DIR`, so the override-path read from
# `firmware_manager.load_registry(..., cache_dir=_FW_CACHE_DIR)` finds it.
$appDataFw = Join-Path $env:APPDATA "SlyLED\firmware"
if (-not (Test-Path $appDataFw)) {
    New-Item -ItemType Directory -Path $appDataFw -Force | Out-Null
}
Copy-Item $regPath (Join-Path $appDataFw "registry.json") -Force
Write-Host "registry.json copied to $appDataFw (live-OTA visibility)" -ForegroundColor Green

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
Write-Host "  Orchestrator (desktop): v$appVersion" -ForegroundColor White
if ($androidVer) {
    Write-Host "  Android APK:            v$androidVer" -ForegroundColor White
}
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

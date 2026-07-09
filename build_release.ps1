# build_release.ps1 — Build all platforms with independent version tracks
#
# Usage: powershell -ExecutionPolicy Bypass -File build_release.ps1
#        powershell -ExecutionPolicy Bypass -File build_release.ps1 -SkipFirmware -SkipAndroid
#        powershell -ExecutionPolicy Bypass -File build_release.ps1 -SetAppVersion "1.2.0"
#        powershell -ExecutionPolicy Bypass -File build_release.ps1 -DryRun
#        powershell -ExecutionPolicy Bypass -File build_release.ps1 -CompileOnly -Board mmwave
#
# Version tracks (all independent — see #824):
#   Orchestrator (desktop):   parent_server.py VERSION → installer.iss
#   Android APK:              android/app/build.gradle.kts versionName/versionCode
#                             (own track since #824 — was previously synced
#                              to orchestrator, hiding half-shipped releases)
#   Firmware boards:          firmware/registry.json — one entry per board,
#                             each with its own version + sourceHash gate
#                             (#902: the firmware step is registry-driven;
#                              per-board build metadata lives in the entry:
#                              autoBuild / sketch / hashPaths / versionFile /
#                              arduinoConfigFile / buildFlags)
#   Camera (Linux SBC):       registry.json "camera-node" + camera_server.py VERSION
#
# Machine-specific paths (arduino-cli, JDK, Android SDK, OneDrive mirror, …)
# come from build.config.json at repo root; missing file/keys fall back to
# the historical defaults baked in below.

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
    [switch]$AllowMajorBump,
    # -DryRun: print exactly what would build / bump / copy / tag, touch nothing.
    [switch]$DryRun,
    # -CompileOnly: compile firmware only — no version bumps, no registry
    # writes, no dist copies, no OneDrive mirror, no git tag, no desktop /
    # Android steps. Outputs land in build\compile-only\<id> (gitignored)
    # so the sha256-pinned bins under firmware/ stay untouched. Combine
    # with -Board to compile a single board.
    [switch]$CompileOnly,
    # Registry id (or short alias: esp32 / d1mini / gyro / dmx / mmwave) to
    # restrict the firmware step to one board. Mainly for -CompileOnly.
    [string]$Board = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

Write-Host "`n=== SlyLED Release Build ===" -ForegroundColor Cyan
if ($DryRun)     { Write-Host "DRY RUN - printing the plan, modifying nothing" -ForegroundColor Yellow }
if ($CompileOnly) { Write-Host "COMPILE ONLY - no version bumps, dist copies, tags, or mirroring" -ForegroundColor Yellow }

# ── Machine-specific constants (build.config.json, fallbacks baked in) ──────
$buildConfig = $null
$buildConfigPath = Join-Path $root "build.config.json"
if (Test-Path $buildConfigPath) {
    try { $buildConfig = Get-Content $buildConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Write-Host "WARN: build.config.json unreadable - using built-in defaults" -ForegroundColor Yellow }
}
function Get-BuildConfig([string]$name, [string]$default) {
    $v = $default
    if ($buildConfig -and $buildConfig.PSObject.Properties[$name] -and $buildConfig.$name) { $v = $buildConfig.$name }
    return [Environment]::ExpandEnvironmentVariables($v)
}
$androidBuildDir = Get-BuildConfig 'androidBuildDir' 'C:\Android\build\slyled-app'

# ── Helper: increment a "major.minor.patch" version string ─────────────────
function Increment-Patch([string]$ver) {
    $parts = $ver.Split(".")
    $parts[2] = [string]([int]$parts[2] + 1)
    return ($parts -join ".")
}

# ── Helper: read/write registry.json ───────────────────────────────────────
# Read/write explicitly as UTF-8 *without* BOM. Windows PowerShell 5.1
# defaults to ANSI on read (the historical `â€”` mojibake) and BOM-ful
# UTF-8 on write (which breaks python's json.load). Pin both directions.
$regPath = "$root\firmware\registry.json"
function Read-Registry { Get-Content $regPath -Raw -Encoding UTF8 | ConvertFrom-Json }
function Save-Registry($reg) {
    $json = $reg | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($regPath, $json + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
}

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
    if ($entry -and $entry.PSObject.Properties['releaseTag'] -and $entry.releaseTag) {
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

# Hash the source tree a board's compile actually consumes (#902: per-board —
# each registry entry declares its inputs in `hashPaths`; pre-#902 every ESP
# board shared one main/+libraries hash, so a gyro-only edit patch-bumped
# unrelated boards; that sharing is now explicit per entry).
# arduino_secrets.h is intentionally excluded (gitignored, not part of release).
# version.h is excluded — it's an *output* of the bump, not an input.
# For hashPaths ["main","libraries"] this reproduces the pre-#902 hash
# byte-for-byte, so stored sourceHash values stay valid across the migration.
function Get-SourceHash([string[]]$paths) {
    $files = @()
    foreach ($p in $paths) {
        $full = Join-Path $root $p
        if (Test-Path $full -PathType Container) {
            $files += Get-ChildItem -Path $full -Include *.ino,*.h,*.cpp,*.c,*.hpp -File -Recurse -ErrorAction SilentlyContinue
        } elseif (Test-Path $full) {
            $files += Get-Item $full
        }
    }
    $files = $files |
        Where-Object { $_.Name -ne 'version.h' -and $_.Name -ne 'arduino_secrets.h' } |
        Sort-Object FullName -Unique
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

# ── Helper: bump the MAJOR/MINOR/PATCH defines in a version header ─────────
# Handles both macro prefixes in the tree (#902): main/version.h uses APP_*,
# mmwave/version.h uses MMW_* — each board's registry entry points at its
# own `versionFile`. Rewrites the three defines in place so include guards
# and comments survive.
function Write-VersionFile([string]$path, [string]$ver) {
    $parts = $ver.Split(".")
    $content = Get-Content $path -Raw -Encoding UTF8
    if ($content -notmatch '#define\s+([A-Z0-9_]+?)_MAJOR\b') {
        throw "no *_MAJOR define found in $path"
    }
    $prefix = $Matches[1]
    $content = $content -replace "(#define\s+${prefix}_MAJOR\s+)\d+", "`${1}$($parts[0])"
    $content = $content -replace "(#define\s+${prefix}_MINOR\s+)\d+", "`${1}$($parts[1])"
    $content = $content -replace "(#define\s+${prefix}_PATCH\s+)\d+", "`${1}$($parts[2])"
    Set-Content $path $content -NoNewline -Encoding UTF8
}

# ── Step 1: Determine app version ──────────────────────────────────────────
# Source-hash-gate the orchestrator like Android: only bump the patch
# number when something under desktop/ actually changed. Pre-#824, this
# script bumped every run regardless, leaking empty version tags into
# git. The Android source-hash cache lives next to its source; mirror
# that for desktop/. (-CompileOnly skips the orchestrator entirely.)
$gitCmd = Get-Command git -ErrorAction SilentlyContinue

if (-not $CompileOnly) {
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

    # ── Step 2: Sync orchestrator version (Android tracks independently) ──
    # `$appVersion` is the orchestrator (desktop) track only — parent_server.py
    # + installer.iss + the SPA. Android has its OWN version in
    # android/app/build.gradle.kts → versionName, bumped by the source-hash
    # gate in Step 5 below. The two tracks deliberately drift: a server-only
    # bug fix bumps orchestrator without touching Android, and vice-versa,
    # so the operator's mismatch banner (#824) actually surfaces something
    # real instead of always reading "matched".
    if ($DryRun) {
        Write-Host "DRY RUN: would sync parent_server.py VERSION to $appVersion" -ForegroundColor Gray
    } else {
        (Get-Content "$root\desktop\shared\parent_server.py" -Raw) -replace 'VERSION = "[^"]+"', "VERSION = `"$appVersion`"" | Set-Content "$root\desktop\shared\parent_server.py" -Encoding UTF8
    }

    Write-Host "Orchestrator version: $appVersion (Android tracks independently)" -ForegroundColor Green
}

# ── Step 3: Compile firmware (registry-driven, per-board change gate) ──────
# #902: loop over firmware/registry.json instead of unrolled per-board
# blocks. Each entry with `autoBuild: true` compiles from its `sketch` dir,
# gated by a sha256 of its own `hashPaths` (stored per entry as
# `sourceHash`). `arduinoConfigFile` (mmwave) routes the compile through an
# isolated arduino-cli data dir so the C61 core can't disturb the default
# toolchain. `buildFlags` become compiler.{cpp,c}.extra_flags (the ESP32
# core ignores build.extra_flags). Entries with `autoBuild: false` — Giga
# boards (dfu/manual flash) and camera-node (SSH-deployed Python) — are
# never compiled here: increment their registry entry when building those
# targets by hand, exactly as before.
# -SkipFirmware skips the entire step. -ForceFirmware rebuilds every board
# regardless of hash. -CompileOnly compiles (ignoring the gate) without
# bumping versions or writing the registry.
if (-not $SkipFirmware) {
    $cli = Get-BuildConfig 'arduinoCli' "$env:LOCALAPPDATA\Arduino\arduino-cli.exe"
    $env:ARDUINO_DIRECTORIES_USER = $root

    $boardAliases = @{
        'esp32'  = 'child-led-esp32'
        'd1mini' = 'child-led-d1mini'
        'gyro'   = 'gyro-esp32s3'
        'dmx'    = 'dmx-bridge-esp32'
    }
    $boardFilter = $Board
    if ($boardFilter -and $boardAliases.ContainsKey($boardFilter)) { $boardFilter = $boardAliases[$boardFilter] }

    $regSnapshot = Read-Registry
    $hashCache = @{}
    $compiledBoards = @()
    $matchedFilter = $false

    foreach ($fw in $regSnapshot.firmware) {
        $id = $fw.id
        if ($boardFilter -and $id -ne $boardFilter) { continue }
        $matchedFilter = $true

        $auto = ($fw.PSObject.Properties['autoBuild'] -and $fw.autoBuild)
        if (-not $auto) {
            Write-Host "${id}: not auto-built (flashMethod $($fw.flashMethod)) - bump its registry entry when built by hand" -ForegroundColor Gray
            continue
        }
        if (Test-FwOnHold $id) {
            Write-Host "${id}: onHold flag set - skipping" -ForegroundColor Gray
            continue
        }

        # Per-board change gate: hash exactly the inputs this board's
        # compile consumes. Identical hashPaths share one computation.
        $hashKey = ($fw.hashPaths -join '|')
        if (-not $hashCache.ContainsKey($hashKey)) { $hashCache[$hashKey] = Get-SourceHash $fw.hashPaths }
        $srcHash = $hashCache[$hashKey]
        $stored = Get-FwSourceHash $id
        $curVer = Get-FwVersion $id

        if (-not $CompileOnly -and -not $ForceFirmware -and $stored -eq $srcHash) {
            Write-Host "${id}: source unchanged - skipping (v$curVer)" -ForegroundColor Gray
            continue
        }

        if ($CompileOnly) {
            $newVer = $curVer
        } else {
            $newVer = Increment-Patch $curVer
            if (-not $AllowMajorBump) { Assert-NoMajorBumpRegression $id $curVer $newVer }
        }

        # Output dir + declared artifact come from the entry's `file`
        # (tolerate a leading "firmware/" — paths are relative to firmware/).
        $relFile = ($fw.file -replace '^firmware/', '')
        $outDir = Join-Path "$root\firmware" (Split-Path $relFile -Parent)
        if ($CompileOnly) {
            # A compile check must not clobber the released bins under
            # firmware/ — their sha256 is pinned in the registry. Send
            # outputs to the gitignored build/ scratch area instead.
            $outDir = Join-Path $root "build\compile-only\$id"
        }
        $sketchDir = Join-Path $root $fw.sketch

        if ($DryRun) {
            $cfgNote = ""
            if ($fw.PSObject.Properties['arduinoConfigFile'] -and $fw.arduinoConfigFile) { $cfgNote = ", config $($fw.arduinoConfigFile)" }
            Write-Host "${id}: WOULD build v$curVer -> v$newVer (sketch $($fw.sketch), fqbn $($fw.fqbn)$cfgNote, hash $($srcHash.Substring(0,12))...)" -ForegroundColor Yellow
            continue
        }

        if (-not $CompileOnly) { Write-VersionFile (Join-Path $root $fw.versionFile) $newVer }
        Write-Host "`n--- $($fw.name) [$id] v$newVer ---" -ForegroundColor Yellow

        $cliArgs = @()
        if ($fw.PSObject.Properties['arduinoConfigFile'] -and $fw.arduinoConfigFile) {
            # Isolated toolchain (e.g. the ESP32-C61 core, arduino-cli-mmwave.yaml):
            # its own data/downloads dirs so the default cores never move.
            $cliArgs += @('--config-file', (Join-Path $root $fw.arduinoConfigFile))
        }
        $cliArgs += @('compile', '--clean', '--fqbn', $fw.fqbn, $sketchDir, '--output-dir', $outDir)
        if ($fw.PSObject.Properties['buildFlags'] -and $fw.buildFlags) {
            # ESP32 Arduino core honours compiler.cpp/c.extra_flags, not
            # build.extra_flags — same pattern build.ps1 uses.
            $cliArgs += @('--build-property', "compiler.cpp.extra_flags=$($fw.buildFlags)",
                          '--build-property', "compiler.c.extra_flags=$($fw.buildFlags)")
        }
        & $cli @cliArgs
        if ($LASTEXITCODE -ne 0) { Write-Host "$id FAILED" -ForegroundColor Red; exit 1 }
        $compiledBoards += $id

        # Make sure the registry-declared artifact exists — arduino-cli names
        # outputs <sketch>.ino[.merged].bin; when the entry's `file` uses a
        # different name (mmwave.bin), publish the merged image under it.
        # (Skipped in -CompileOnly: outputs stay in the scratch dir.)
        if (-not $CompileOnly) {
            $declared = Join-Path "$root\firmware" $relFile
            if (-not (Test-Path $declared)) {
                $sketchName = Split-Path $fw.sketch -Leaf
                $merged = Join-Path $outDir "$sketchName.ino.merged.bin"
                $plain  = Join-Path $outDir "$sketchName.ino.bin"
                if (Test-Path $merged) { Copy-Item $merged $declared -Force }
                elseif (Test-Path $plain) { Copy-Item $plain $declared -Force }
            }
        }

        if (-not $CompileOnly) {
            Set-FwVersion $id $newVer
            Set-FwSourceHash $id $srcHash
        }
    }

    if ($boardFilter -and -not $matchedFilter) {
        Write-Host "No registry entry matched -Board '$Board' (use a registry id or alias: esp32/d1mini/gyro/dmx/mmwave)" -ForegroundColor Red
        exit 1
    }

    Write-Host "`nFirmware step complete (rebuild only on source change)" -ForegroundColor Green
}

# -CompileOnly stops here: no desktop/Android builds, no dist copies, no
# SHA re-pinning, no OneDrive mirror, no git tag.
if ($CompileOnly) {
    Write-Host "`n=== Compile-only complete ===" -ForegroundColor Cyan
    if (-not $SkipFirmware) {
        foreach ($b in $compiledBoards) { Write-Host "  compiled: $b" -ForegroundColor White }
    }
    exit 0
}

# ── Step 4: Windows Desktop (PyInstaller + Inno Setup) ────────────────────
if (-not $SkipWindows) {
    if ($orchSourceUnchanged -and -not $ForceFirmware) {
        Write-Host "`n--- Windows Desktop ---" -ForegroundColor Yellow
        Write-Host "Windows: orchestrator source unchanged - skipping (v$appVersion, cached SlyLED-Setup.exe in dist/)" -ForegroundColor Gray
    } elseif ($DryRun) {
        Write-Host "`n--- Windows Desktop ---" -ForegroundColor Yellow
        Write-Host "DRY RUN: would build SlyLED.exe (PyInstaller) + SlyLED-Setup.exe (Inno Setup) at v$appVersion" -ForegroundColor Yellow
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
        $iscc = Join-Path (Get-BuildConfig 'innoSetupDir' "$env:LOCALAPPDATA\Programs\Inno Setup 6") "ISCC.exe"
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
    $env:JAVA_HOME = Get-BuildConfig 'javaHome' 'C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot'
    $env:ANDROID_SDK_ROOT = Get-BuildConfig 'androidSdkRoot' 'C:\Android\Sdk'

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
    } elseif ($DryRun) {
        $androidVer = Increment-Patch $androidCurVer
        Write-Host "`n--- Android APK ---" -ForegroundColor Yellow
        Write-Host "DRY RUN: would bump Android v$androidCurVer -> v$androidVer (+versionCode) and run gradlew assembleRelease" -ForegroundColor Yellow
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
        $apkPath = Get-ChildItem -Path $androidBuildDir -Recurse -Filter "app-release.apk" -ErrorAction SilentlyContinue | Select-Object -First 1
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
if (-not $DryRun -and -not (Test-Path $distDir)) { New-Item -ItemType Directory -Path $distDir | Out-Null }

function Copy-DistItem([string]$src, [string]$dst) {
    if ($DryRun) {
        if (Test-Path $src) { Write-Host "  DRY RUN: would copy $src -> $dst" -ForegroundColor Gray }
        return
    }
    Copy-Item $src $dst -Force -ErrorAction SilentlyContinue
}

Copy-DistItem "$root\firmware\esp32\main.ino.merged.bin" "$distDir\esp32-firmware-merged.bin"
Copy-DistItem "$root\firmware\d1mini\main.ino.bin" "$distDir\d1mini-firmware.bin"
# Bins that the prior build_release.ps1 forgot — the SHA-refresh step
# below walks dist/ and re-pins registry.json sha256 from whatever's
# there, so missing copies meant the registry stayed pinned to the
# previous release's hashes. Add gyro / gyro-test / dmx-bridge so
# every firmware entry's dist/ artifact matches the just-rebuilt bin.
Copy-DistItem "$root\firmware\esp32s3\main.ino.merged.bin" "$distDir\esp32s3-gyro-firmware.bin"
Copy-DistItem "$root\firmware\esp32s3-test\main.ino.merged.bin" "$distDir\esp32s3-gyro-test-firmware.bin"
Copy-DistItem "$root\firmware\esp32-dmx\main.ino.merged.bin" "$distDir\esp32-dmx-bridge-firmware.bin"
# #870 — also publish app-only binaries for OTA. ESP32 OTA appends
# bytes to the inactive OTA app partition (~1.5 MB on 4 MB flash);
# the merged image overflows the partition AND fails magic-byte
# validation. The orchestrator's OTA proxy serves these via the
# registry's `otaAsset` field; pre-#870 it fell back to the merged
# binary and silently broke OTA on every fresh-cache child.
Copy-DistItem "$root\firmware\esp32\main.ino.bin" "$distDir\esp32-firmware-app.bin"
Copy-DistItem "$root\firmware\esp32s3\main.ino.bin" "$distDir\esp32s3-gyro-firmware-app.bin"
Copy-DistItem "$root\firmware\esp32-dmx\main.ino.bin" "$distDir\esp32-dmx-bridge-firmware-app.bin"
Copy-DistItem "$root\desktop\windows\dist\SlyLED.exe" "$distDir\SlyLED.exe"
Copy-DistItem "$root\desktop\windows\dist\SlyLED-Setup.exe" "$distDir\SlyLED-Setup.exe"
# Operator-canonical APK output: `dist/slyled-android.apk` (release-
# signed). The release variant is the operator-facing artifact;
# debug APK is a build-only intermediate and stays out of dist/.
$apk = Get-ChildItem -Path $androidBuildDir -Recurse -Filter "app-release.apk" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($apk) { Copy-DistItem $apk.FullName "$distDir\slyled-android.apk" }
if ($DryRun) {
    Write-Host "DRY RUN: dist/ not modified" -ForegroundColor Yellow
} else {
    Write-Host "dist/ updated" -ForegroundColor Green
}

# Mirror dist/ to the OneDrive pickup folder so the operator finds the
# finals where they always look for them. The work tree is now
# /mnt/d/SlyLED (D:\SlyLED) per 2026-05-06 directive; OneDrive holds
# only the operator-facing /dist mirror, no source.
$onedriveDist = Get-BuildConfig 'onedriveDistDir' 'D:\OneDrive\My Documents\ElectricRV\Development\Projects\Lighting Arduino\dist'
if (Test-Path (Split-Path $onedriveDist -Parent)) {
    if ($DryRun) {
        Write-Host "DRY RUN: would mirror dist/ to $onedriveDist (operator pickup)" -ForegroundColor Yellow
    } else {
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
    }
} else {
    Write-Host "OneDrive parent path not present; skipping mirror" -ForegroundColor Yellow
}

# Step 6b: Refresh registry SHA-256 hashes (#568 security review).
# Any binary we just rebuilt needs its `sha256` in registry.json re-pinned
# so downloads can verify integrity. Walks every registry entry that
# declares a releaseAsset and, if the matching file lives in dist/,
# updates sha256 to the fresh hash. Unchanged binaries keep their hash.
if ($DryRun) {
    Write-Host "`n--- Refreshing registry SHA-256 hashes ---" -ForegroundColor Yellow
    Write-Host "DRY RUN: would re-pin sha256/otaSha256 in registry.json from dist/ binaries" -ForegroundColor Yellow
    Write-Host "DRY RUN: would copy registry.json to $(Join-Path $env:APPDATA 'SlyLED\firmware') (live-OTA visibility)" -ForegroundColor Yellow
} else {
    Write-Host "`n--- Refreshing registry SHA-256 hashes ---" -ForegroundColor Yellow
    $reg = Read-Registry
    $anyChanged = $false
    foreach ($fw in $reg.firmware) {
        $asset = $null
        if ($fw.PSObject.Properties['releaseAsset']) { $asset = $fw.releaseAsset }
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
        # #870 — pin otaSha256 alongside sha256 so the OTA proxy can
        # verify the app-only binary it serves matches the published
        # release. Skip entries without otaAsset (D1 Mini, Giga, parent-
        # giga, dmx-bridge-giga, camera).
        $otaAsset = $null
        if ($fw.PSObject.Properties['otaAsset']) { $otaAsset = $fw.otaAsset }
        if ($otaAsset) {
            $otaPath = Join-Path $distDir $otaAsset
            if (Test-Path $otaPath) {
                $otaHash = (Get-FileHash -Algorithm SHA256 -Path $otaPath).Hash.ToLower()
                $oldOtaHash = $null
                if ($fw.PSObject.Properties['otaSha256']) { $oldOtaHash = $fw.otaSha256 }
                if ($otaHash -ne $oldOtaHash) {
                    $anyChanged = $true
                    if ($fw.PSObject.Properties['otaSha256']) {
                        $fw.otaSha256 = $otaHash
                    } else {
                        $fw | Add-Member -MemberType NoteProperty -Name otaSha256 -Value $otaHash
                    }
                    $shortOta = $otaHash.Substring(0, 12)
                    Write-Host "  $($fw.id): otaSha256 -> $shortOta..." -ForegroundColor Green
                }
            } else {
                Write-Host "  $($fw.id): otaAsset $otaAsset missing in dist/ (#870 - OTA will 502 until rebuilt)" -ForegroundColor Yellow
            }
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
}

# ── Step 7: Create git tag (app version only) ─────────────────────────────
if ($gitCmd) {
    Write-Host "`n--- Git tag ---" -ForegroundColor Yellow
    $tagName = "v$appVersion"
    $existingTag = & git tag -l $tagName 2>$null
    if ($existingTag) {
        Write-Host "Tag $tagName already exists - skipping" -ForegroundColor Yellow
    }
    elseif ($DryRun) {
        Write-Host "DRY RUN: would create annotated tag $tagName" -ForegroundColor Yellow
    }
    else {
        & git tag -a $tagName -m "Release $tagName"
        Write-Host "Created tag: $tagName" -ForegroundColor Green
    }
} else {
    Write-Host "`n  (git not on PATH - skipping tag creation)" -ForegroundColor Yellow
}

# ── Summary ────────────────────────────────────────────────────────────────
if ($DryRun) {
    Write-Host "`n=== Dry run complete - nothing was modified ===" -ForegroundColor Cyan
} else {
    Write-Host "`n=== Build Complete ===" -ForegroundColor Cyan
}
Write-Host "  Orchestrator (desktop): v$appVersion" -ForegroundColor White
if ($androidVer) {
    Write-Host "  Android APK:            v$androidVer" -ForegroundColor White
}
$reg = Read-Registry
foreach ($fw in $reg.firmware) {
    Write-Host "  $($fw.id): v$($fw.version)" -ForegroundColor Gray
}
if (-not $DryRun) {
    Write-Host ""
    Write-Host "  dist/:     All binaries copied"
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  git add -A && git commit -m 'release: v$appVersion' && git push origin main --tags"
    Write-Host "  gh release create v$appVersion --target main --title 'v$appVersion'"
}

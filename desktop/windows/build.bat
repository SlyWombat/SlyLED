@echo off
rem ============================================================
rem  build.bat — Build SlyLED Parent Windows installer
rem  Run from the desktop\windows\ directory (double-click or cmd)
rem ============================================================
setlocal enabledelayedexpansion

set "WINDIR=%~dp0"
set "SHARED=%~dp0..\shared"
set "SPA=%SHARED%\spa"

echo.
echo ===== SlyLED Parent Build =====
echo.

rem ── 1. Verify Python ─────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo        Install Python 3.11+ from https://python.org and add to PATH.
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo Using: %%v

rem ── 2. Create / activate venv ────────────────────────────────────────────────
if not exist "%WINDIR%.venv" (
    echo Creating virtual environment...
    python -m venv "%WINDIR%.venv"
)
call "%WINDIR%.venv\Scripts\activate.bat"

rem ── 3. Install dependencies ───────────────────────────────────────────────────
echo Installing dependencies...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r "%WINDIR%requirements.txt"
python -m pip install --quiet pyinstaller>=6.3

rem ── 4. Clean previous artefacts ──────────────────────────────────────────────
if exist "%WINDIR%dist\SlyLED.exe"  del /f /q "%WINDIR%dist\SlyLED.exe"
if exist "%WINDIR%build\SlyLED"     rmdir /s /q "%WINDIR%build\SlyLED"

rem ── 5. PyInstaller (via build.py to avoid cmd quoting issues with spaces) ───
echo Building executable...
python "%WINDIR%build.py"

if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed. See output above.
    call "%WINDIR%.venv\Scripts\deactivate.bat" 2>nul
    pause & exit /b 1
)

echo.
echo Executable: %WINDIR%dist\SlyLED.exe

rem ── 6. Inno Setup (optional) ─────────────────────────────────────────────────
where iscc >nul 2>&1
if not errorlevel 1 (
    echo Building installer...
    iscc "%WINDIR%installer.iss"
    if errorlevel 1 (
        echo ERROR: Inno Setup failed.
        call "%WINDIR%.venv\Scripts\deactivate.bat" 2>nul
        pause & exit /b 1
    )
    echo Installer: %WINDIR%dist\SlyLED-Parent-Setup.exe
) else (
    echo NOTE: Inno Setup ^(iscc.exe^) not on PATH — skipping installer.
    echo       Install from https://jrsoftware.org/isinfo.php
    echo       Then re-run build.bat or run: iscc installer.iss
)

call "%WINDIR%.venv\Scripts\deactivate.bat" 2>nul
echo.
echo ===== Build complete =====
echo.
pause

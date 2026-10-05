@echo off
REM ---------------------------------------------------------------------------
REM  build-windows.bat — one-shot Windows build for Bunney POS.
REM
REM  Double-click this, or run it from a terminal in the project root:
REM
REM      build-windows.bat
REM
REM  It creates the virtual environment, installs the dependencies, regenerates
REM  the icon, builds dist\windows\BunneyPOS.exe, and — if Inno Setup is
REM  installed — also produces dist\windows\BunneyPOS_v1.0.0_Setup.exe.
REM
REM  Requires Python 3.11+ on PATH as `py` or `python'.
REM
REM  PASSWORD: The installer password is read from the SETUP_PASSWORD
REM  environment variable or a .env file in the project root. It is never
REM  stored in this script or in installer.iss.
REM ---------------------------------------------------------------------------

setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Bunney POS - Windows build
echo ============================================================
echo.

REM --- locate Python ---------------------------------------------------------
set PY=
where py >nul 2>&1 && set PY=py
if "%PY%"=="" (
    where python >nul 2>&1 && set PY=python
)
if "%PY%"=="" (
    echo ERROR: Python was not found on PATH.
    echo        Install Python 3.11 or newer from https://python.org
    echo        and tick "Add python.exe to PATH" during setup.
    exit /b 1
)
echo Using Python: %PY%
%PY% --version
echo.

REM --- resolve the installer password ---------------------------------------
REM  Priority: 1) SETUP_PASSWORD env var  2) .env file in project root
set SETUP_PASSWORD=
if not "%SETUP_PASSWORD%"=="" goto :password_ok

if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if /i "%%a"=="SETUP_PASSWORD" (
            set SETUP_PASSWORD=%%b
            goto :password_ok
        )
    )
)

echo ERROR: SETUP_PASSWORD is not set.
echo.
echo   Set it with:
echo     set SETUP_PASSWORD=your_password_here
echo.
echo   Or create a .env file in the project root with:
echo     SETUP_PASSWORD=your_password_here
echo.
echo   The password is never stored in installer.iss or this script.
exit /b 1

:password_ok
echo Installer password: resolved from %SETUP_PASSWORD%...
echo.

REM --- virtual environment ---------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo ERROR: could not create the virtual environment.
        exit /b 1
    )
) else (
    echo Virtual environment already exists.
)
set VPY=.venv\Scripts\python.exe

REM --- dependencies ----------------------------------------------------------
echo.
echo Installing dependencies...
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo ERROR: dependency installation failed.
    exit /b 1
)
"%VPY%" -m pip install pyinstaller --quiet
if errorlevel 1 (
    echo ERROR: could not install PyInstaller.
    exit /b 1
)
echo Dependencies ready.

REM --- icon ------------------------------------------------------------------
echo.
echo Generating the Windows icon from logo.svg...
"%VPY%" tools\make_icons.py
if errorlevel 1 (
    echo WARNING: icon generation failed; the build will use the bundled SVG.
)

REM --- build the exe ---------------------------------------------------------
echo.
echo Building BunneyPOS.exe (this takes a few minutes)...
"%VPY%" -m PyInstaller --clean --noconfirm --distpath "dist\windows" windows_build.spec
if errorlevel 1 (
    echo ERROR: PyInstaller failed. See the output above.
    exit /b 1
)

if not exist "dist\windows\BunneyPOS.exe" (
    echo ERROR: dist\windows\BunneyPOS.exe was not produced.
    exit /b 1
)
echo.
echo Built dist\windows\BunneyPOS.exe

REM --- optional: build the installer ----------------------------------------
echo.
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if "%ISCC%"=="" if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe

if "%ISCC%"=="" (
    echo Inno Setup 6 was not found, so the installer was skipped.
    echo Install it from https://jrsoftware.org/isdl.php and re-run this script
    echo to also produce dist\windows\BunneyPOS_v1.0.0_Setup.exe
) else (
    echo Compiling the installer with Inno Setup...
    "%ISCC%" /DSetupPassword="%SETUP_PASSWORD%" installer.iss
    if errorlevel 1 (
        echo WARNING: Inno Setup reported an error; see the output above.
    ) else (
        echo Built dist\windows\BunneyPOS_v1.0.0_Setup.exe
    )
)

echo.
echo ============================================================
echo  Done.
echo ============================================================
echo.
echo  Portable:  dist\windows\BunneyPOS.exe
echo  Installer: dist\windows\BunneyPOS_v1.0.0_Setup.exe
echo.
echo  The installer is password-protected. The password is read from
echo  the SETUP_PASSWORD environment variable or .env file at build time.
echo  It is never stored in installer.iss or this script.
echo.
echo  Data is written next to the exe, or to %%LOCALAPPDATA%%\bunney-pos
echo  when the exe sits somewhere read-only like Program Files.
echo.
endlocal

@echo off
REM ---------------------------------------------------------------------------
REM  build-windows.bat — one-shot Windows build for Bunney POS.
REM
REM  Double-click this, or run it from a terminal in the project root:
REM
REM      build-windows.bat
REM
REM  It creates the virtual environment, installs the dependencies, regenerates
REM  the icon, builds dist\BunneyPOS.exe, and — if Inno Setup is installed —
REM  also produces dist\BunneyPOS_v1.0.0_Setup.exe.
REM
REM  Requires Python 3.11+ on PATH as `py` or `python`.
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
"%VPY%" -m PyInstaller --clean --noconfirm windows_build.spec
if errorlevel 1 (
    echo ERROR: PyInstaller failed. See the output above.
    exit /b 1
)

if not exist "dist\BunneyPOS.exe" (
    echo ERROR: dist\BunneyPOS.exe was not produced.
    exit /b 1
)
echo.
echo Built dist\BunneyPOS.exe

REM --- optional: build the installer ----------------------------------------
echo.
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if "%ISCC%"=="" if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe

if "%ISCC%"=="" (
    echo Inno Setup 6 was not found, so the installer was skipped.
    echo Install it from https://jrsoftware.org/isdl.php and re-run this script
    echo to also produce dist\BunneyPOS_v1.0.0_Setup.exe
) else (
    echo Compiling the installer with Inno Setup...
    "%ISCC%" installer.iss
    if errorlevel 1 (
        echo WARNING: Inno Setup reported an error; see the output above.
    ) else (
        echo Built dist\BunneyPOS_v1.0.0_Setup.exe
    )
)

echo.
echo ============================================================
echo  Done.
echo ============================================================
echo.
echo  Portable:  dist\BunneyPOS.exe            (copy to any PC and run)
echo  Installer: dist\BunneyPOS_v1.0.0_Setup.exe
echo.
echo  Data is written next to the exe, or to %%LOCALAPPDATA%%\bunney-pos
echo  when the exe sits somewhere read-only like Program Files.
echo.
endlocal

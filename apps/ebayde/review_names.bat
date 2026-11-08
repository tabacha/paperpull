@echo off
cd /d "%~dp0"
rem  review_names.bat          -> your account (config.json)
rem  review_names.bat spouse   -> config.spouse.json
if "%~1"=="" (set "CFG=") else (set "CFG=--config config.%~1.json")
echo ============================================================
echo  eBay.de Receipts - review names
echo ============================================================
if not "%~1"=="" echo Account: %~1
echo.
echo Goes through the receipts whose names the app was unsure of, one at a
echo time. Type a better name, press Enter to keep the one it has, or type
echo q to stop. Each name you type renames its PDF, and the spreadsheets
echo follow.
echo.
rem This folder's own Python when setup.bat made one. Otherwise, in a
rem checkout, the one setup-all.bat made for every app in gui\.venv, once
rem it holds what an app needs. A folder the PaperPull app made has no
rem setup.bat, and runs on the Python the installer put in your user
rem folder. Each step ends outside any parentheses, so the exit code is the
rem app's own.
set "PY=.venv\Scripts\python.exe"
if exist "%PY%" goto run
if exist setup.bat goto shared
set "PY=%LOCALAPPDATA%\PaperPull\python\python.exe"
if exist "%PY%" goto run
echo PaperPull was not found where its installer puts it. If you unzipped it
echo somewhere else, run this in that folder instead.
echo   paperpull.bat ebayde review-names
echo.
pause
exit /b 1

:shared
set "PY=..\..\gui\.venv\Scripts\python.exe"
if not exist "%PY%" goto notsetup
"%PY%" -c "import importlib.util as u, sys; sys.exit(not all(map(u.find_spec, ['paperpull_core', 'playwright', 'pypdf'])))" >nul 2>&1
if not errorlevel 1 goto run
:notsetup
if exist "..\..\setup-all.bat" (
  echo This app is not set up yet. Run setup-all.bat in the PaperPull folder first.
) else (
  echo This app is not set up yet - run setup.bat first.
)
echo.
pause
exit /b 1

:run
"%PY%" ebayde_receipts.py --review-names %CFG%
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%

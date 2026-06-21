@echo off
chcp 65001 >nul
rem Speculum - ONE-CLICK real data: backend + frontend + browser.
rem First time only: setup.bat (deps) -> setup-keys.bat (keys) -> load-real-data.bat (data)
cd /d "%~dp0"

if not exist "server\.env" goto noenv
if not exist "server\speculum_real.db" goto nodata

echo [run-real] backend window (uvicorn :8000) ...
start "speculum-server-real" cmd /k "cd /d server& set SPECULUM_DATABASE_URL=sqlite:///./speculum_real.db& uvicorn app.main:app --port 8000"

echo [run-real] frontend window (next dev :3000) ...
start "speculum-client" cmd /k "cd /d client& pnpm dev"

echo [run-real] waiting for servers to start ...
timeout /t 7 >nul
echo [run-real] opening browser localhost:3000 ...
start "" "http://localhost:3000"

echo.
echo [run-real] opened. http://localhost:3000  (as_of 2024-06-28). close the 2 windows to stop.
pause
goto end

:noenv
echo [run-real] server\.env not found - run setup-keys.bat first (creates keys from keys.txt).
pause
goto end

:nodata
echo [run-real] server\speculum_real.db not found - run load-real-data.bat first.
pause
:end

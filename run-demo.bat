@echo off
rem Speculum dev - one-click: backend + frontend in separate windows
rem run setup.bat ONCE first.
cd /d "%~dp0"
echo [demo] launching backend window (uvicorn :8000) ...
start "speculum-server" cmd /k run-server.bat
echo [demo] launching frontend window (next dev :3000) ...
start "speculum-client" cmd /k run-client.bat
echo [demo] waiting for servers to start ...
timeout /t 7 >nul
echo [demo] opening browser localhost:3000 ...
start "" "http://localhost:3000"
echo.
echo [demo] two windows opened (server + client).
echo [demo] browser: http://localhost:3000  (as_of 2024-06-28)
pause

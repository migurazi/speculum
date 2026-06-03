@echo off
rem Speculum dev - frontend (Next.js) on port 3000
cd /d "%~dp0client"
echo [client] http://localhost:3000  (as_of 2024-06-28)
echo [client] backend must be running on :8000
pnpm dev
echo.
echo [client] next dev stopped.
pause

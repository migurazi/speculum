@echo off
rem Speculum dev - first-time setup (run once)
echo [setup] installing server deps: pip install -e .
cd /d "%~dp0server"
pip install -e .
if errorlevel 1 goto err
echo.
echo [setup] installing client deps: pnpm install
cd /d "%~dp0client"
pnpm install
if errorlevel 1 goto err
echo.
echo [setup] DONE. Next: setup-keys.bat -> load-real-data.bat -> run-real.bat
pause
goto end
:err
echo.
echo [setup] FAILED - see errors above.
pause
:end

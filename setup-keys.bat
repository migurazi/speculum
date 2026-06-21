@echo off
rem UTF-8 codepage so Korean output from python scripts displays correctly
chcp 65001 >nul
rem Speculum - API key setup: keys.txt (repo root) -> server\.env
rem Run once before loading real data (DART/ECOS/KOSIS/FSC). KRX prices need no key.
cd /d "%~dp0server"
echo [setup-keys] keys.txt -^> server\.env ...
python -m scripts.setup_env_from_keys
if errorlevel 1 goto err
echo.
echo [setup-keys] DONE. Next: load-real-data.bat
pause
goto end
:err
echo.
echo [setup-keys] FAILED - check keys.txt format/existence (see errors above).
pause
:end

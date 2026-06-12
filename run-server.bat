@echo off
rem Speculum dev - backend (FastAPI/uvicorn) on port 8000
cd /d "%~dp0server"
if exist "speculum_dev.db" goto runserver
echo [seed] creating speculum_dev.db ...
python -m scripts.seed_demo
:runserver
set "SPECULUM_DATABASE_URL=sqlite:///./speculum_dev.db"
echo [server] http://localhost:8000
echo [server] try http://localhost:8000/api/stocks/005930?as_of=2024-06-28
uvicorn app.main:app --port 8000
echo.
echo [server] uvicorn stopped (check errors above).
pause

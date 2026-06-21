@echo off
rem UTF-8 codepage so Korean output from python scripts displays correctly
chcp 65001 >nul
rem Speculum - load REAL market data -> speculum_real.db (instead of demo seed)
rem Run setup-keys.bat first to create server\.env.
rem   KRX prices/names = no key (pykrx/FDR) / DART financials = DART_API_KEY
rem After: run-real.bat (backend + frontend)
setlocal
cd /d "%~dp0server"

if not exist ".env" goto noenv

set "SPECULUM_DATABASE_URL=sqlite:///./speculum_real.db"

rem ===== targets (edit if needed) =====
rem default = major KOSPI tickers. as_of = a trading day with real data (2024-06-28).
set "CODES=005930 000660 035420 005380 051910 035720 005490 105560 055550 035250"
set "ASOF=2024-06-28"
set "FY=2024"
set "FQ=1"

if exist "speculum_real.db" goto haveschema
echo [load] creating speculum_real.db schema (alembic upgrade head) ...
alembic upgrade head
if errorlevel 1 goto err
:haveschema

echo.
echo [load] (1/4) KRX daily price/market-cap (no key; price kept even if market-cap down) ...
python -m batch.scheduler --job krx --market KOSPI --codes %CODES% --observed-date %ASOF%

echo.
echo [load] (2/4) price history backfill 1y (chart time series; pykrx, no key) ...
python -m scripts.backfill_prices %CODES% --as-of %ASOF% --days 365

echo.
echo [load] (3/4) stock master (real names/market; FDR, no key) - avoids detail 404 ...
python -m scripts.ingest_stocks_master %CODES%

echo.
echo [load] (4/4) DART financials FY%FY% Q%FQ% (needs DART_API_KEY; some tickers may skip) ...
python -m batch.scheduler --job dart --codes %CODES% --fiscal-year %FY% --fiscal-quarter %FQ%

echo.
echo [load] DONE. real data loaded (as_of=%ASOF%).
echo [load] Next: run-real.bat (backend + frontend)
echo.
echo [load] note - some metrics may be N/A due to env/data limits:
echo [load]   * market-cap/PER/PBR : KRX market-cap endpoint not reachable here (needs KRX_ID/PW). price is fine.
echo [load]   * ROE/EPS (TTM)      : needs 4 quarters of financials - load more quarters to compute.
echo [load]   * dividend yield     : dividends not loaded - add via (crno bootstrap takes a while):
echo [load]       python -m batch.scheduler --job dividend --codes %CODES% --observed-date %ASOF%
echo [load]   * chart/total-return : OK (price history backfilled).
pause
goto end

:noenv
echo [load] server\.env not found - run setup-keys.bat first (creates keys from keys.txt).
pause
goto end

:err
echo.
echo [load] FAILED - check errors above.
pause

:end
endlocal

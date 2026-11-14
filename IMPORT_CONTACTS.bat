@echo off
REM Drag CSV / XLSX / JSON / Parquet files onto this file to add them to the lead database.
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag your contact files onto this .bat file, or put them in LEAD_INDEX\uploads and run:
  echo    python lead_index.py ingest --file "LEAD_INDEX\uploads\yourfile.csv"
  pause
  exit /b 1
)
set ARGS=
:loop
if "%~1"=="" goto run
set ARGS=%ARGS% --file "%~1"
shift
goto loop
:run
echo Importing into leads.duckdb ...
python -u lead_index.py ingest %ARGS%
if errorlevel 1 py -u lead_index.py ingest %ARGS%
echo.
pause

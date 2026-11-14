@echo off
cd /d "%~dp0"
echo Linking Lead Studio to LEAD_INDEX\leads.parquet (62.7M contacts)...
echo.
python -u lead_index.py link
if errorlevel 1 py -u lead_index.py link
echo.
echo Done. Restart Lead Studio and open the Contacts page.
pause

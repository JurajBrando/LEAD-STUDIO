@echo off
REM Builds LeadStudio.exe in this folder (one time, about 1-2 minutes). Needs Python and internet for PyInstaller.
REM The exe is only the starter and window: the tools themselves still run with the Python installed on this PC.
cd /d "%~dp0"
python -m pip install --quiet --upgrade pyinstaller || goto :err
python -m PyInstaller --onefile --console --name LeadStudio --distpath . --workpath _build --specpath _build lead_studio.py || goto :err
echo.
echo DONE. Double-click LeadStudio.exe in this folder. You can make a desktop shortcut to it.
pause
exit /b 0
:err
echo.
echo The build failed. You can still use START_LEAD_STUDIO.bat - it does the same thing.
pause

@echo off
REM Starts Lead Studio in its own window (no browser tabs, no address bar).
REM Closing that window stops Lead Studio. Running tools keep going.
title Lead Studio
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install it from https://www.python.org/downloads/ then run this again.
  pause
  exit /b 1
)
python lead_studio.py

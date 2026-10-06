@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -AutoSetup
if errorlevel 1 (
  echo.
  echo Startup failed. See the message above, or open README.md.
  pause
)

@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0skillos-one-click-launcher\scripts\Restore-SkillOSDemoState.ps1"
echo.
echo Press any key to close this restore window. SkillOS keeps running in the background.
pause >nul

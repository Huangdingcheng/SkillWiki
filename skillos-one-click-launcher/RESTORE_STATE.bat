@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Restore-SkillOSDemoState.ps1"
echo.
echo Press any key to close this restore window. SkillOS keeps running in the background.
pause >nul

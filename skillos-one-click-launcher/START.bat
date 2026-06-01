@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\Start-SkillOSDemo.ps1"
if errorlevel 1 (
  echo.
  echo SkillOS demo failed to start. Check the error above, then press any key to close this window.
  pause >nul
)
endlocal

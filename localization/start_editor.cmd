@echo off
setlocal
cd /d "%~dp0.."

where git >nul 2>&1
if errorlevel 1 (
  echo Git is required to run the localization editor.
  echo Install Git for Windows, then double-click this file again.
  pause
  exit /b 1
)

where py >nul 2>&1
if not errorlevel 1 (
  py -3 -B "localization\tools\editor_server.py"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Python 3.10 or newer is required to run the editor.
    echo Install Python for Windows, then double-click this file again.
    pause
    exit /b 1
  )
  python -B "localization\tools\editor_server.py"
)

if errorlevel 1 (
  echo The editor did not start. Read the error above, then try again.
  pause
  exit /b 1
)
endlocal

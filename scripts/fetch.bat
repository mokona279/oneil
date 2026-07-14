@echo off
REM Auto-date incremental data update (double-clickable). Args pass to fetch.ps1.
REM   fetch.bat                     full universe, to today
REM   fetch.bat -Symbols 005930     single symbol (quick)
REM   fetch.bat -DryRun             plan only
powershell -ExecutionPolicy Bypass -File "%~dp0fetch.ps1" %*
pause

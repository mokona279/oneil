@echo off
REM 매일 워크플로우 실행 래퍼 (더블클릭 가능). 인자는 daily.ps1 로 전달.
REM   예) daily.bat -SkipFetch     daily.bat -Backtest
powershell -ExecutionPolicy Bypass -File "%~dp0daily.ps1" %*
pause

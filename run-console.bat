@echo off
rem То же самое, но с консолью — видно логи и ошибки.
cd /d "%~dp0"
py -3.11 -m mcmonitor
pause

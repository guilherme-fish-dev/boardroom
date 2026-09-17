@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist "boardroom.pid" (
    echo [Boardroom] Nao parece estar em execucao ^(boardroom.pid nao encontrado^).
    exit /b 0
)

set /p PID=<boardroom.pid
echo !PID!| findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo [Boardroom] boardroom.pid invalido, removendo.
    del "boardroom.pid"
    exit /b 0
)

taskkill /PID !PID! /T /F >nul 2>&1

del "boardroom.pid"
echo [Boardroom] Parado ^(PID !PID!^).

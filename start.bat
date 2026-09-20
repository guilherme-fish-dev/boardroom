@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [Boardroom] .venv nao encontrado. Rode primeiro:
    echo   python -m venv .venv
    echo   .venv\Scripts\activate
    echo   pip install -r requirements.txt
    exit /b 1
)

if exist "boardroom.pid" (
    set /p OLDPID=<boardroom.pid
    tasklist /FI "PID eq !OLDPID!" 2>NUL | find /I "!OLDPID!" >NUL
    if not errorlevel 1 (
        echo [Boardroom] Ja esta em execucao ^(PID !OLDPID!^). Rode stop.bat primeiro se quiser reiniciar.
        exit /b 1
    ) else (
        del "boardroom.pid"
    )
)

powershell -NoProfile -Command ^
    "$p = Start-Process -FilePath '.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000' -WindowStyle Hidden -RedirectStandardOutput 'boardroom.log' -RedirectStandardError 'boardroom.err.log' -PassThru; Set-Content -Path 'boardroom.pid' -Value $p.Id -NoNewline"

if not exist "boardroom.pid" (
    echo [Boardroom] Falha ao iniciar o servidor. Veja boardroom.err.log.
    exit /b 1
)

set /p PID=<boardroom.pid
if "!PID!"=="" (
    echo [Boardroom] Falha ao iniciar o servidor ^(pid file vazio^). Veja boardroom.err.log.
    del "boardroom.pid"
    exit /b 1
)
echo ======================================================================
echo  [Boardroom] Servidor iniciado com sucesso ^(PID !PID!^)
echo ======================================================================
echo.
echo  Acesso neste computador:
echo    http://localhost:8000
echo.
echo  Acesso de outros aparelhos na mesma rede WiFi / local:
.venv\Scripts\python.exe -c "import socket; ips = socket.gethostbyname_ex(socket.gethostname())[2]; print('\n'.join(f'   http://{ip}:8000' for ip in ips if not ip.startswith(('127.', '169.254.'))))"
echo.
echo  Logs: boardroom.log / boardroom.err.log
echo ======================================================================
echo.

timeout /t 2 /nobreak >nul
start "" http://localhost:8000

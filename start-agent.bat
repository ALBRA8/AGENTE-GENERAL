@echo off
title AGENTE-GENERAL
cd /d "%~dp0\dashboard"

echo ============================================================
echo        AGENTE-GENERAL — Iniciando...
echo ============================================================

where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js no instalado. Descarga: https://nodejs.org/
    pause
    exit /b 1
)

if not exist "node_modules" (
    echo Instalando dependencias...
    call npm install --no-audit --no-fund
)

if not exist ".env.local" (
    echo Creando .env.local...
    for /f "delims=" %%s in ('node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"') do set SECRET=%%s
    (
        echo INTERNAL_API_SECRET=%SECRET%
        echo WHATSAPP_DAEMON_URL=http://localhost:3002
        echo MAX_MEDIA_BYTES=8388608
        echo MAX_CONCURRENT_MEDIA=2
        echo MAX_MEDIA_QUEUE=20
        echo MAX_RECONNECT_ATTEMPTS=10
        echo NVIDIA_API_KEY=pon-aqui-tu-nvidia-api-key
        echo GROQ_API_KEY=pon-aqui-tu-groq-api-key
    ) > .env.local
)

taskkill /f /im node.exe >nul 2>nul
timeout /t 2 /nobreak >nul

start /b node src\server\whatsapp-daemon.js
timeout /t 3 /nobreak >nul
start /b node_modules\.bin\next dev -p 3000

set /a COUNT=0
:WAIT
timeout /t 1 /nobreak >nul
set /a COUNT+=1
netstat -an | findstr ":3000" >nul 2>nul && goto READY
if %COUNT% lss 20 goto WAIT

:READY
start http://localhost:3000

echo.
echo ============================================================
echo   AGENTE-GENERAL corriendo
echo ============================================================
echo   Dashboard: http://localhost:3000
echo   Daemon:    http://localhost:3002/status
echo.
echo   Para conectar WhatsApp:
echo   1. Pestana WhatsApp → Conectar por QR
echo   2. Escanea el QR desde tu celular
echo.
echo   Cierra esta ventana para detener todo
echo ============================================================
echo.

:LOOP
timeout /t 9999 /nobreak >nul
goto LOOP

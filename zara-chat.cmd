@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_and_run.ps1" -Cli %*
set "ZARA_RC=%ERRORLEVEL%"
rem Con doble clic la ventana se cierra al terminar: si algo fallo, se espera a una tecla
rem para que se pueda leer el error (sin Python, dependencias que no instalan, etc.).
if not "%ZARA_RC%"=="0" (
    echo.
    echo [!] ZARA termino con errores, codigo %ZARA_RC%. Revise los mensajes anteriores.
    pause
)
exit /b %ZARA_RC%

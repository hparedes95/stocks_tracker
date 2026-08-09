@echo off
REM Doble clic para abrir el dashboard.
REM
REM Existe porque PowerShell bloquea por defecto la ejecucion de scripts .ps1
REM descargados, y el mensaje de error que da no dice como arreglarlo. El
REM -ExecutionPolicy Bypass de aqui abajo solo afecta a esta ejecucion: no
REM cambia la configuracion de tu equipo.

cd /d "%~dp0..\.."

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo No hay entorno todavia. Preparandolo, tarda un par de minutos...
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\windows\stocks.ps1" setup
    if errorlevel 1 goto :error

    echo.
    echo Generando datos de prueba...
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\windows\stocks.ps1" demo
)

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\windows\stocks.ps1" run
goto :eof

:error
echo.
echo Algo ha fallado en la instalacion. Lee el mensaje de arriba.
pause

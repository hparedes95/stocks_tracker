@echo off
title Instalar Stocks Tracker
setlocal

REM Este es el unico fichero que hay que descargar. Se baja el instalador de
REM GitHub y lo ejecuta.
REM
REM Es un .bat y no un .ps1 porque Windows bloquea por defecto los .ps1
REM descargados de internet, y el error que da no explica como arreglarlo. El
REM -ExecutionPolicy Bypass de abajo afecta solo a esta ejecucion: no cambia la
REM configuracion de tu equipo.

set "REPO=hparedes95/stocks_tracker"
set "BRANCH=claude/stock-market-monitoring-dashboard-7yf0nb"
set "INSTALLER=%TEMP%\stockstracker_install.ps1"

echo.
echo   Stocks Tracker
echo   ==============
echo.
echo   Se instalara en tu carpeta de usuario. No hace falta ser administrador.
echo.

REM Si el .bat esta junto al resto del repositorio (descarga completa), se usa
REM el instalador local en lugar de volver a bajarlo.
if exist "%~dp0install.ps1" (
    echo   Usando el instalador local.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Branch "%BRANCH%"
    goto :done
)

echo   Descargando el instalador...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "try { Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/%REPO%/%BRANCH%/installer/install.ps1' -OutFile '%INSTALLER%' }" ^
  "catch { Write-Host ''; Write-Host ('  No se ha podido descargar: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }"

if errorlevel 1 goto :error
if not exist "%INSTALLER%" goto :error

powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER%" -Branch "%BRANCH%"
del "%INSTALLER%" >nul 2>&1

:done
endlocal
exit /b 0

:error
echo.
echo   No se ha podido descargar el instalador.
echo   Comprueba tu conexion a internet y vuelve a intentarlo.
echo.
pause
endlocal
exit /b 1

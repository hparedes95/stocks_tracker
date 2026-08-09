@echo off
REM Doble clic para abrir el dashboard.
REM
REM Antes de arrancar, se pone al dia si hace falta. En un ordenador personal la
REM tarea nocturna se pierde cada vez que el equipo esta apagado, y sin esto el
REM dashboard acabaria mostrando la semana pasada como si fuera hoy.
REM
REM Existe como .bat porque PowerShell bloquea por defecto los .ps1 descargados
REM y el mensaje de error no dice como arreglarlo. El -ExecutionPolicy Bypass
REM afecta solo a esta ejecucion: no cambia la configuracion del equipo.

title Stocks Tracker
cd /d "%~dp0..\.."

set PS=powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\windows\stocks.ps1"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo No hay entorno todavia. Preparandolo, tarda un par de minutos...
    echo.
    %PS% setup
    if errorlevel 1 goto :error
)

echo.
echo Comprobando si hay datos nuevos del mercado...
echo.
%PS% update

%PS% run
goto :eof

:error
echo.
echo Algo ha fallado. Lee el mensaje de arriba.
pause

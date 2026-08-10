@echo off
REM Doble clic para descargar el universo completo y dejarlo todo calculado.
REM
REM Es lo que hay que ejecutar UNA VEZ despues de instalar, si quieres el
REM ranking sobre las ~600 empresas y no solo los indices de la portada.
REM
REM Tarda entre 20 y 45 minutos segun tu conexion y lo que Yahoo tarde en
REM responder. Se puede dejar corriendo y hacer otra cosa; el ordenador no se
REM puede apagar mientras tanto.
REM
REM Existe como .bat porque PowerShell bloquea por defecto los .ps1 descargados
REM y el mensaje de error no dice como arreglarlo. El -ExecutionPolicy Bypass
REM afecta solo a esta ejecucion: no cambia la configuracion del equipo.

title Stocks Tracker - universo completo
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
echo   Descarga del universo completo
echo   ==============================
echo.
echo   Son cuatro pasos y entre 20 y 45 minutos en total.
echo   Puedes minimizar esta ventana, pero no la cierres.
echo.

%PS% universo
if errorlevel 1 goto :error

echo.
echo   Listo. Ya puedes abrir el dashboard.
echo.
pause
goto :eof

:error
echo.
echo   Algo ha fallado. Lee el mensaje de arriba.
echo   Se puede volver a ejecutar: lo ya descargado no se repite.
echo.
pause

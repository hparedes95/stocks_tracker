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
setlocal

REM Buscar la instalacion. La primera version daba por hecho que este fichero
REM estaba DENTRO de la carpeta del programa, y quien se lo descargaba suelto
REM desde GitHub lo ejecutaba desde Descargas: ahi no hay ningun stocks.ps1 y
REM el error no decia por que.
set "APP=%~dp0..\.."
if exist "%APP%\scripts\windows\stocks.ps1" goto :found

set "APP=%LOCALAPPDATA%\StocksTracker"
if exist "%APP%\scripts\windows\stocks.ps1" goto :found

echo.
echo   No encuentro Stocks Tracker instalado.
echo.
echo   Se ha buscado en:
echo     %~dp0..\..
echo     %LOCALAPPDATA%\StocksTracker
echo.
echo   Instala primero el programa con "Instalar Stocks Tracker.bat".
echo.
pause
exit /b 1

:found
cd /d "%APP%"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File "%APP%\scripts\windows\stocks.ps1""

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
echo   Carpeta: %APP%
echo   Son cuatro pasos y entre 20 y 45 minutos en total.
echo   Puedes minimizar esta ventana, pero no la cierres.
echo.

%PS% universo
if errorlevel 1 goto :error

echo.
echo   Listo. Ya puedes abrir el dashboard.
echo.
pause
exit /b 0

:error
echo.
echo   Algo ha fallado. Lee el mensaje de arriba.
echo   Se puede volver a ejecutar: lo ya descargado no se repite.
echo.
pause
exit /b 1

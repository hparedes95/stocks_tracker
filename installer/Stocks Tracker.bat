@echo off
REM ===========================================================================
REM  Stocks Tracker - el UNICO fichero que necesitas.
REM ===========================================================================
REM
REM  Doble clic. Hace lo que haga falta segun el estado en que este:
REM
REM    - No instalado      -> lo instala (Python, entorno, dependencias)
REM    - Sin datos reales  -> descarga el universo completo
REM    - Datos viejos      -> los pone al dia
REM    - Todo listo        -> abre el dashboard
REM
REM  Es el UNICO fichero del proyecto que se maneja a mano. Hubo tres
REM  (instalar, ver y descargar) y obligaban a saber cual tocaba y en que
REM  orden, o sea a llevar la cuenta del estado interno del programa. Se han
REM  borrado del repositorio.
REM
REM  NUNCA genera datos inventados. Este dashboard se usa para decidir
REM  inversiones reales y un numero falso con aspecto de real es peor que no
REM  tener numero.
REM
REM  Es un .bat y no un .ps1 porque Windows bloquea por defecto los .ps1
REM  descargados de internet. El -ExecutionPolicy Bypass afecta solo a esta
REM  ejecucion: no cambia la configuracion de tu equipo.
REM ===========================================================================

@title Stocks Tracker
setlocal

set "REPO=hparedes95/stocks_tracker"
set "BRANCH=claude/stock-market-monitoring-dashboard-7yf0nb"
set "APP=%LOCALAPPDATA%\StocksTracker"
set "INSTALLER=%TEMP%\stockstracker_install.ps1"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass"

echo.
echo   ==========================================
echo    Stocks Tracker
echo   ==========================================
echo.

REM --- 1. Instalar si hace falta -------------------------------------------
if exist "%APP%\.venv\Scripts\python.exe" goto :instalado

echo   Primera vez. Voy a instalarlo todo.
echo   Entre 25 y 50 minutos: entorno, dependencias y descarga de mercado.
echo   Puedes minimizar la ventana. No la cierres.
echo.

REM Si este fichero esta junto al repositorio, se usa el instalador local.
if exist "%~dp0install.ps1" (
    %PS% -File "%~dp0install.ps1" -Branch "%BRANCH%" -UniversoCompleto
    if errorlevel 1 goto :error
    goto :abrir
)

echo   Descargando el instalador...
%PS% -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "try { Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/%REPO%/%BRANCH%/installer/install.ps1' -OutFile '%INSTALLER%' }" ^
  "catch { Write-Host ('  No se ha podido descargar: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }"
if errorlevel 1 goto :sinred
if not exist "%INSTALLER%" goto :sinred

%PS% -File "%INSTALLER%" -Branch "%BRANCH%" -UniversoCompleto
if errorlevel 1 goto :error
del "%INSTALLER%" >nul 2>&1
goto :abrir

REM --- 2. Ya instalado: primero el CODIGO, luego los datos ------------------
:instalado
cd /d "%APP%"

REM La primera version de este fichero actualizaba los datos y nunca el
REM programa: las correcciones no llegaban por muchas veces que se abriera, y
REM el usuario veia el mismo fallo despues de "reinstalar". Se compara la
REM version instalada con la publicada y solo se reinstala si difieren, para no
REM rehacer el entorno de Python en cada arranque.
set "LOCAL_SHA="
if exist "%APP%\.version" set /p LOCAL_SHA=<"%APP%\.version"

for /f "usebackq delims=" %%S in (`%PS% -Command ^
  "try { (Invoke-RestMethod -UseBasicParsing -TimeoutSec 15 -Uri 'https://api.github.com/repos/%REPO%/commits/%BRANCH%').sha } catch { '' }"`) do set "REMOTE_SHA=%%S"

if not defined REMOTE_SHA goto :sinversion
if "%LOCAL_SHA%"=="%REMOTE_SHA%" goto :aldia

echo   Hay una version nueva del programa. Actualizando...
echo.
%PS% -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "try { Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/%REPO%/%BRANCH%/installer/install.ps1' -OutFile '%INSTALLER%' }" ^
  "catch { exit 1 }"
if errorlevel 1 goto :sinversion
%PS% -File "%INSTALLER%" -Branch "%BRANCH%"
if errorlevel 1 goto :error
del "%INSTALLER%" >nul 2>&1
cd /d "%APP%"
goto :datos

:sinversion
echo   No se ha podido comprobar si hay version nueva. Se sigue con la actual.
echo.
goto :datos

:aldia
echo   El programa esta al dia.
echo.

:datos

REM Hay ranking? Si no, falta el universo completo: es lo que da el ranking,
REM las senales y los candidatos. Sin el, el dashboard solo muestra indices.
%PS% -File "%APP%\scripts\windows\stocks.ps1" tiene-universo >nul 2>&1
if errorlevel 1 (
    echo   Falta el universo completo. Lo descargo ahora.
    echo   Entre 20 y 45 minutos. Puedes minimizar la ventana.
    echo.
    %PS% -File "%APP%\scripts\windows\stocks.ps1" universo
    if errorlevel 1 goto :error
) else (
    echo   Comprobando si hay datos nuevos del mercado...
    echo.
    %PS% -File "%APP%\scripts\windows\stocks.ps1" update
)

REM --- 3. Abrir -------------------------------------------------------------
:abrir
echo.
echo   Abriendo el dashboard. Cierra esta ventana para pararlo.
echo.
cd /d "%APP%"
%PS% -File "%APP%\scripts\windows\stocks.ps1" run
goto :fin

:sinred
echo.
echo   No se ha podido descargar el instalador.
echo   Comprueba tu conexion a internet y vuelve a intentarlo.
echo.
pause
exit /b 1

:error
echo.
echo   Algo ha fallado. Lee el mensaje de arriba.
echo   Se puede volver a ejecutar: lo ya descargado no se repite.
echo.
pause
exit /b 1

:fin
endlocal
exit /b 0

<#
.SYNOPSIS
    Equivalente al Makefile para Windows.

.DESCRIPTION
    El proyecto usa `make`, que Windows no trae. Este script hace lo mismo.

    Uso:  .\scripts\windows\stocks.ps1 <tarea>

    Tareas:
      setup     Crea el entorno e instala las dependencias
      demo      Genera datos sinteticos (sin internet) y los calcula
      real      Cambia los datos de prueba por precios reales (rapido)
      update    Actualiza solo si los datos estan viejos
      autostart Programa la actualizacion diaria automatica
      ingest    Descarga el universo completo de Yahoo Finance
      universo  TODO de una vez: descarga, calcula, puntua y valida
      compute   Recalcula indicadores, factores, senales y scores
      presets   Puntua el universo con los cinco estilos de inversion
      validate  Valida las senales contra su historico
      alerts    Evalua las reglas de aviso
      watch     Vigila el mercado en vivo
      watchtest Simula un desplome del 8% para probar los avisos
      run       Arranca el dashboard y abre el navegador
      daily     Ciclo completo: ingesta + calculo + alertas
      test      Ejecuta los tests
      lint      Comprueba el estilo del codigo
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'demo', 'ingest', 'universo', 'compute', 'presets', 'validate',
                 'alerts', 'watch', 'watchtest', 'run', 'daily', 'test',
                 'real', 'update', 'autostart', 'autostart-off',
                 'lint', 'help')]
    [string]$Task = 'help'
)

$ErrorActionPreference = 'Stop'
# En PowerShell 7.4+ un comando nativo que sale con codigo != 0 lanza
# excepcion. Este script lee $LASTEXITCODE a proposito (--check-stale sale
# con 1 cuando hacen falta datos), asi que se desactiva ese comportamiento.
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

# Rango de Python soportado, el mismo que declara pyproject.toml.
# Son dos ficheros que no se pueden validar entre si: si cambia alli,
# hay que cambiarlo aqui.
$script:MinPy = 11
$script:MaxPy = 14

# La raiz del proyecto son dos niveles por encima de este script, de modo que
# funcione desde cualquier carpeta.
#
# $PSScriptRoot no siempre esta relleno: sale vacio si el contenido del script
# se pega en la consola o se invoca de formas que no son `-File`. Cuando eso
# pasaba, `Join-Path` fallaba con "no se puede enlazar el argumento con el
# parametro 'Path' porque es una cadena vacia", que no dice nada util. Por eso
# hay tres candidatos y una comprobacion final.
$script:Candidates = @()
if ($PSScriptRoot) { $script:Candidates += (Join-Path $PSScriptRoot '..\..') }
if ($MyInvocation.MyCommand.Path) {
    $script:Candidates += (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\..')
}
$script:Candidates += $PWD.Path
$script:Candidates += (Join-Path $env:LOCALAPPDATA 'StocksTracker')

$Root = $null
foreach ($candidate in $script:Candidates) {
    if (-not $candidate) { continue }
    # pyproject.toml es la marca de que esto es de verdad la raiz y no una
    # carpeta cualquiera dos niveles por encima de donde se lanzo el script.
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        $Root = (Resolve-Path $candidate).Path
        break
    }
}

if (-not $Root) {
    Write-Host ""
    Write-Host "  No encuentro la carpeta de Stocks Tracker." -ForegroundColor Red
    Write-Host "  Se ha buscado en:" -ForegroundColor DarkGray
    foreach ($candidate in $script:Candidates) {
        if ($candidate) { Write-Host "    $candidate" -ForegroundColor DarkGray }
    }
    Write-Host ""
    Write-Host "  Ejecutalo desde la carpeta del programa, o instalalo con" -ForegroundColor Yellow
    Write-Host "  'Instalar Stocks Tracker.bat'." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

Set-Location $Root

# En Windows el ejecutable del entorno vive en Scripts\, no en bin/.
$Py = Join-Path $Root '.venv\Scripts\python.exe'


function Write-Step($text) {
    Write-Host ""
    Write-Host "==> $text" -ForegroundColor Cyan
}

function Assert-Venv {
    if (-not (Test-Path $Py)) {
        Write-Host "No hay entorno todavia. Ejecuta primero:" -ForegroundColor Yellow
        Write-Host "    .\scripts\windows\stocks.ps1 setup" -ForegroundColor Yellow
        exit 1
    }
}

function Test-PythonExe($exe, $prefix = @()) {
    if (-not $exe) { return $null }
    try {
        $version = (& $exe @prefix '--version' 2>&1 | Out-String).Trim()
    } catch { return $null }
    if ($version -notmatch 'Python\s+3\.(\d+)') { return $null }
    $minor = [int]$Matches[1]
    if ($minor -lt $script:MinPy -or $minor -gt $script:MaxPy) { return $null }
    return @{ Exe = $exe; Prefix = $prefix; Version = $version }
}

function Find-Python {
    <#
        Mirar solo el PATH no basta: el instalador de Python no lo modifica
        salvo que marques la casilla, asi que lo normal es tenerlo instalado y
        que `python` no exista en la consola. Se busca tambien en el registro y
        en las carpetas habituales.
    #>
    $pyCmd = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($pyCmd) {
        foreach ($v in @('-3.13', '-3.12', '-3.14', '-3.11', '-3')) {
            $found = Test-PythonExe $pyCmd.Source @($v)
            if ($found) { return $found }
        }
    }

    foreach ($candidate in @('python', 'python3')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $found = Test-PythonExe $cmd.Source
        if ($found) { return $found }
    }

    foreach ($hive in @('HKLM:\SOFTWARE\Python\PythonCore',
                        'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore',
                        'HKCU:\SOFTWARE\Python\PythonCore')) {
        if (-not (Test-Path $hive)) { continue }
        foreach ($key in Get-ChildItem $hive -ErrorAction SilentlyContinue) {
            $installPath = (Get-ItemProperty (Join-Path $key.PSPath 'InstallPath') `
                            -ErrorAction SilentlyContinue).'(default)'
            if ($installPath) {
                $found = Test-PythonExe (Join-Path $installPath 'python.exe')
                if ($found) { return $found }
            }
        }
    }

    foreach ($pattern in @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "$env:ProgramFiles\Python3*\python.exe",
        "${env:ProgramFiles(x86)}\Python3*\python.exe",
        "C:\Python3*\python.exe"
    )) {
        foreach ($exe in (Get-ChildItem $pattern -ErrorAction SilentlyContinue |
                          Sort-Object FullName -Descending)) {
            $found = Test-PythonExe $exe.FullName
            if ($found) { return $found }
        }
    }
    return $null
}


switch ($Task) {

    'setup' {
        Write-Step "Buscando Python compatible (3.11 a 3.14)"
        $python = Find-Python
        if (-not $python) {
            Write-Host "No se ha encontrado un Python compatible (3.11 a 3.14)." -ForegroundColor Red
            Write-Host ""
            Write-Host "Instalalo con:" -ForegroundColor Yellow
            Write-Host "    winget install Python.Python.3.12"
            Write-Host ""
            Write-Host "Cierra y vuelve a abrir PowerShell despues de instalarlo."
            exit 1
        }
        Write-Host "  $($python.Exe)"

        Write-Step "Creando el entorno en .venv"
        & $python.Exe @($python.Prefix) -m venv .venv

        Write-Step "Instalando dependencias (tarda un par de minutos)"
        & $Py -m pip install --upgrade pip --quiet
        & $Py -m pip install -e ".[data,dev]"

        Write-Step "Creando el almacen de datos"
        & $Py -m stocks_tracker.core.db --migrate

        Write-Host ""
        Write-Host "Listo. Ahora, para verlo funcionando sin esperar descargas:" -ForegroundColor Green
        Write-Host "    .\scripts\windows\stocks.ps1 demo"
        Write-Host "    .\scripts\windows\stocks.ps1 run"
    }

    'demo' {
        Assert-Venv
        Write-Step "Generando datos sinteticos (no sale a internet)"
        & $Py -m stocks_tracker.ingest.run_ingest --what all --provider synthetic
        Write-Step "Calculando indicadores, factores y senales"
        & $Py -m stocks_tracker.compute.run_compute
        Write-Step "Puntuando con los cinco estilos de inversion"
        & $Py -m stocks_tracker.compute.run_compute --only scores --all-presets
        Write-Step "Validando las senales contra su historico"
        & $Py -m stocks_tracker.backtest.run_backtest --tag-signals
        Write-Host ""
        Write-Host "Datos de prueba listos. Son INVENTADOS: sirven para ver la" -ForegroundColor Yellow
        Write-Host "aplicacion, no para decidir nada." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Arrancalo con:  .\scripts\windows\stocks.ps1 run" -ForegroundColor Green
    }

    'real' {
        # De datos de prueba a precios reales, por el camino mas corto.
        # Primero los indices (15 valores, un minuto) para que la portada
        # cuadre ya; el universo completo despues, que es lo que tarda.
        Assert-Venv
        Write-Step "Borrando los datos de prueba"
        & $Py -m stocks_tracker.ingest.run_ingest --drop-synthetic --what prices `
            --universes INDICES,MACRO --years 3
        Write-Step "Calculando con los precios reales"
        & $Py -m stocks_tracker.compute.run_compute
        Write-Host ""
        Write-Host "Ya puedes abrir el dashboard: los indices son reales." -ForegroundColor Green
        Write-Host "Para el universo completo (varios minutos):" -ForegroundColor Yellow
        Write-Host "    .\scripts\windows\stocks.ps1 ingest"
    }

    'update' {
        # Se pone al dia solo si hace falta. Lo llama el lanzador en cada
        # arranque, asi que tiene que ser instantaneo cuando no hay nada nuevo.
        Assert-Venv
        & $Py -m stocks_tracker.ingest.run_ingest --check-stale
        if ($LASTEXITCODE -eq 0) { return }

        Write-Step "Actualizando datos del mercado"
        # --drop-synthetic es obligatorio aqui: el simulador genera series
        # hasta hoy, asi que sin borrarlas la descarga incremental las ve
        # al dia y no trae nada. Es inocuo si no hay datos de prueba.
        & $Py -m stocks_tracker.ingest.run_ingest --drop-synthetic --what all
        if ($LASTEXITCODE -eq 75) {
            Write-Host "Ya hay otra actualizacion en marcha; se abre con lo que hay." -ForegroundColor Yellow
            return
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "La descarga ha fallado. Se conservan los datos anteriores." -ForegroundColor Yellow
            return
        }
        & $Py -m stocks_tracker.compute.run_compute
        & $Py -m stocks_tracker.compute.run_compute --only scores --all-presets
        & $Py -m stocks_tracker.alerts.run_alerts
    }

    'autostart' {
        # Registra la actualizacion nocturna en el Programador de tareas.
        # StartWhenAvailable es lo que hace que sirva en un ordenador personal:
        # si a las 23:15 estaba apagado, la tarea corre al encenderlo en lugar
        # de perderse hasta el dia siguiente.
        $taskScript = Join-Path $Root 'scripts\windows\stocks.ps1'
        $daily = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$taskScript`" daily" `
            -WorkingDirectory $Root
        $trigger = New-ScheduledTaskTrigger -Daily -At '23:15'
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
            -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Hours 2)

        Register-ScheduledTask -TaskName 'Stocks Tracker - actualizacion diaria' `
            -Action $daily -Trigger $trigger -Settings $settings `
            -Description 'Descarga precios, recalcula y evalua las alertas.' `
            -Force | Out-Null

        Write-Host ""
        Write-Host "Actualizacion automatica activada." -ForegroundColor Green
        Write-Host "  Cada dia a las 23:15, con el mercado americano ya cerrado."
        Write-Host "  Si el ordenador esta apagado, se ejecuta al encenderlo."
        Write-Host ""
        Write-Host "Para desactivarla:  .\scripts\windows\stocks.ps1 autostart-off"
    }

    'autostart-off' {
        Unregister-ScheduledTask -TaskName 'Stocks Tracker - actualizacion diaria' `
            -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "Actualizacion automatica desactivada." -ForegroundColor Yellow
    }

    'ingest' {
        Assert-Venv
        Write-Step "Descargando datos reales (la primera vez tarda varios minutos)"
        & $Py -m stocks_tracker.ingest.run_ingest --what all
    }

    'universo' {
        # Los cuatro pasos que hacen falta para pasar de "solo indices" a tener
        # el universo entero listo, en el orden correcto y sin que haya que
        # acordarse de ninguno.
        #
        # Aqui SI se para al primer fallo, al reves que en 'daily': calcular
        # sobre una descarga incompleta produce un ranking que parece correcto
        # y no lo es, y eso es peor que no tener ranking.
        Assert-Venv
        $steps = @(
            @{ Name = 'Descargando el universo completo (10-25 min)'
               Args = @('-m', 'stocks_tracker.ingest.run_ingest', '--what', 'all') },
            @{ Name = 'Calculando indicadores, factores y senales (3-8 min)'
               Args = @('-m', 'stocks_tracker.compute.run_compute') },
            @{ Name = 'Puntuando con los estilos de inversion (2-5 min)'
               Args = @('-m', 'stocks_tracker.compute.run_compute', '--only', 'scores',
                        '--all-presets') },
            @{ Name = 'Validando las senales contra su historico (2-5 min)'
               Args = @('-m', 'stocks_tracker.backtest.run_backtest', '--tag-signals') }
        )
        $n = 0
        foreach ($step in $steps) {
            $n++
            Write-Step "[$n/$($steps.Count)] $($step.Name)"
            & $Py @($step.Args)
            # 75 = habia otro proceso descargando y esta ejecucion no hizo
            # nada. No es un fallo, pero tampoco se puede seguir: los pasos
            # siguientes calcularian sobre una descarga que no ocurrio.
            # 76 = hay indicadores pero ningun instrumento que puntuar. La
            # causa casi siempre es una ingesta de universo incompleta, y
            # seguir dejaria el dashboard sin ranking sin decir por que.
            if ($LASTEXITCODE -eq 76) {
                Write-Host ""
                Write-Host "  El calculo no ha encontrado nada que puntuar." -ForegroundColor Red
                Write-Host "  La descarga del universo se ha quedado a medias." -ForegroundColor Yellow
                Write-Host "  Vuelve a ejecutar esto cuando tengas conexion estable." -ForegroundColor Yellow
                Write-Host ""
                exit 1
            }
            if ($LASTEXITCODE -eq 75) {
                Write-Host ""
                Write-Host "  Ya hay otra descarga en marcha." -ForegroundColor Yellow
                Write-Host "  Espera a que termine (mira si hay otra ventana abierta" -ForegroundColor Yellow
                Write-Host "  o la tarea programada de las 23:15) y vuelve a intentarlo." -ForegroundColor Yellow
                Write-Host ""
                exit 75
            }
            if ($LASTEXITCODE -ne 0) {
                Write-Host ""
                Write-Host "  Ha fallado: $($step.Name)" -ForegroundColor Red
                Write-Host "  No se continua. Los pasos siguientes darian un" -ForegroundColor Yellow
                Write-Host "  ranking calculado sobre datos incompletos." -ForegroundColor Yellow
                exit 1
            }
        }
        Write-Host ""
        Write-Host "Universo completo listo." -ForegroundColor Green
        Write-Host "Ya puedes abrir el dashboard: el ranking cubre todo el universo."
    }

    'compute' {
        Assert-Venv
        & $Py -m stocks_tracker.compute.run_compute
    }

    'presets' {
        Assert-Venv
        & $Py -m stocks_tracker.compute.run_compute --only scores --all-presets
    }

    'validate' {
        Assert-Venv
        & $Py -m stocks_tracker.backtest.run_backtest --tag-signals
    }

    'alerts' {
        Assert-Venv
        & $Py -m stocks_tracker.alerts.run_alerts
    }

    'watch' {
        Assert-Venv
        Write-Step "Vigilando el mercado. Ctrl+C para parar."
        & $Py -m stocks_tracker.watch.run_watch
    }

    'watchtest' {
        Assert-Venv
        & $Py -m stocks_tracker.watch.run_watch --test-crash 8
    }

    'daily' {
        Assert-Venv
        # El equivalente de scripts/daily_update.sh, que es bash y aqui no vale.
        # Un paso que falla NO detiene los siguientes: es preferible tener el
        # dashboard con datos de ayer que dejarlo a medias sin alertas.
        foreach ($step in @(
            @{ Name = 'Ingesta'; Args = @('-m', 'stocks_tracker.ingest.run_ingest', '--what', 'all') },
            @{ Name = 'Calculo'; Args = @('-m', 'stocks_tracker.compute.run_compute') },
            @{ Name = 'Alertas'; Args = @('-m', 'stocks_tracker.alerts.run_alerts') }
        )) {
            Write-Step $step.Name
            try { & $Py @($step.Args) }
            catch { Write-Host "  $($step.Name) ha fallado, se continua." -ForegroundColor Yellow }
        }
    }

    'test' {
        Assert-Venv
        & $Py -m pytest -q
    }

    'lint' {
        Assert-Venv
        & $Py -m ruff check src tests
    }

    'run' {
        Assert-Venv
        Write-Step "Arrancando el dashboard en http://127.0.0.1:8501"
        Write-Host "Ctrl+C en esta ventana para pararlo."
        Write-Host ""
        # Se abre el navegador con unos segundos de margen para que Streamlit
        # haya levantado; si se abre antes, sale un error de conexion y parece
        # que la aplicacion no funciona.
        Start-Job -ScriptBlock {
            Start-Sleep -Seconds 5
            Start-Process 'http://127.0.0.1:8501'
        } | Out-Null

        # 127.0.0.1 de forma deliberada: Streamlit no tiene autenticacion y
        # exponerlo en la red dejaria tu cartera abierta a cualquiera.
        & $Py -m streamlit run src/stocks_tracker/app/main.py `
            --server.address 127.0.0.1 --server.port 8501 --server.headless true
    }

    default {
        Get-Help $PSCommandPath -Detailed
    }
}

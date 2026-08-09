<#
.SYNOPSIS
    Equivalente al Makefile para Windows.

.DESCRIPTION
    El proyecto usa `make`, que Windows no trae. Este script hace lo mismo.

    Uso:  .\scripts\windows\stocks.ps1 <tarea>

    Tareas:
      setup     Crea el entorno e instala las dependencias
      demo      Genera datos sinteticos (sin internet) y los calcula
      ingest    Descarga datos reales de Yahoo Finance
      compute   Recalcula indicadores, factores, senales y scores
      presets   Puntua el universo con los cinco estilos de inversion
      validate  Valida las senales contra su historico
      alerts    Evalua las reglas de aviso
      watch     Vigila el mercado en vivo
      watchtest Simula un desplome del 8% para probar los avisos
      run       Arranca el dashboard y abre el navegador
      daily     Ciclo completo: ingesta + calculo + alertas
      test      Ejecuta los tests
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'demo', 'ingest', 'compute', 'presets', 'validate',
                 'alerts', 'watch', 'watchtest', 'run', 'daily', 'test', 'help')]
    [string]$Task = 'help'
)

$ErrorActionPreference = 'Stop'

# La raiz del proyecto son dos niveles por encima de este script, de modo que
# funcione desde cualquier carpeta.
$Root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
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

function Find-Python {
    # `python` en Windows puede ser el alias de la Microsoft Store, que no
    # instala nada y solo abre la tienda. `py -3` es mas fiable cuando existe.
    foreach ($candidate in @('py', 'python', 'python3')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $args = if ($candidate -eq 'py') { @('-3', '--version') } else { @('--version') }
            $version = & $cmd.Source @args 2>&1
            if ($version -match 'Python 3\.(\d+)') {
                $minor = [int]$Matches[1]
                if ($minor -ge 11 -and $minor -le 13) {
                    return @{ Exe = $cmd.Source; Prefix = $(if ($candidate -eq 'py') { @('-3') } else { @() }) }
                }
            }
        } catch { continue }
    }
    return $null
}


switch ($Task) {

    'setup' {
        Write-Step "Buscando Python 3.11-3.13"
        $python = Find-Python
        if (-not $python) {
            Write-Host "No se ha encontrado Python 3.11, 3.12 o 3.13." -ForegroundColor Red
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

    'ingest' {
        Assert-Venv
        Write-Step "Descargando datos reales (la primera vez tarda varios minutos)"
        & $Py -m stocks_tracker.ingest.run_ingest --what all
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

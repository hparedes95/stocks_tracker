<#
.SYNOPSIS
    Stocks Tracker: analisis, escenarios y asesor de acciones.
.DESCRIPTION
    run: abre el programa. update: actualiza datos al abrir.
    universo: descarga, calcula y valida el universo completo.
    consejo -Caja 1500: calcula propuestas sin ejecutar operaciones.
    auditar: contrasta precios. calibrar: estudia la evidencia historica.
    autostart-off: retira las tareas antiguas. No se crean tareas programadas.
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'demo', 'ingest', 'universo', 'puerta', 'claves', 'mercados',
                 'polymarket', 'calibracion', 'cripto', 'pendientes', 'ciclo',
                 'tiene-universo',
                 'compute', 'presets', 'validate',
                 'validate-freeze', 'validate-confirm',
                 'auditar', 'oro', 'huella', 'lista-universo',
                 'consejo', 'calibrar', 'porque',
                 'alerts', 'watch', 'watchtest', 'run', 'daily', 'test',
                 'real', 'update', 'autostart', 'autostart-off',
                 'backup-check', 'report', 'lint', 'help')]
    [string]$Task = 'help',

    # Efectivo disponible para invertir. El programa NO puede saberlo: no
    # habla con tu banco ni con tu broker, y el extracto solo trae
    # posiciones. Con cero, las compras salen vetadas por falta de tamano,
    # que es lo correcto: recomendar comprar con dinero que no existe es
    # peor que no recomendar nada.
    [double]$Caja = 0,

    # El valor sobre el que preguntar en la tarea 'porque'.
    [string]$Valor = ''
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
    Write-Host "  'Stocks Tracker.bat'." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

Set-Location $Root

# Antes de descargar o abrir, detener las tareas antiguas de esta aplicacion.
if ($Task -in @('run', 'update', 'universo', 'daily')) {
    & (Join-Path $Root 'scripts\windows\remove-legacy-tasks.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

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
        & $Py -m pip install --require-hashes -r requirements-dev.lock
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Py -m pip install -e . --no-deps

        Write-Step "Creando el almacen de datos"
        & $Py -m stocks_tracker.core.db --migrate

        Write-Host ""
        Write-Host "Listo. Ahora, para verlo funcionando sin esperar descargas:" -ForegroundColor Green
        Write-Host "    .\scripts\windows\stocks.ps1 demo"
        Write-Host "    .\scripts\windows\stocks.ps1 run"
        # Barrera contra subir credenciales. El repositorio es publico y la
        # parte del bot maneja una clave privada de wallet: una clave de API se
        # revoca, una privada no. Activarla a mano es no activarla.
        git config core.hooksPath scripts/git-hooks 2>$null
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
        #
        # SON DOS PREGUNTAS, NO UNA, Y CONFUNDIRLAS DEJO EL DASHBOARD PARADO.
        #
        # Antes esto era: preguntar "hace falta descargar?" y, si la respuesta
        # era no, IRSE. Sin calcular.
        #
        # Las dos cosas se separan solas cada dos por tres: la descarga de la
        # noche trae los precios, el calculo revienta o lo para la puerta de
        # calidad, y desde ese momento el programa no vuelve a calcular por su
        # cuenta nunca. La descarga sigue siendo reciente, asi que este bloque
        # se seguia yendo por la puerta de atras en cada arranque. Los precios
        # entrando cada noche y la portada ensenando el martes indefinidamente.
        #
        # Ahora se pregunta por separado y se calcula si hay precios sin
        # calcular, se haya descargado hoy algo o no.
        Assert-Venv

        & $Py -m stocks_tracker.ingest.run_ingest --check-stale
        $descargar = ($LASTEXITCODE -ne 0)

        if ($descargar) {
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
                # NO se sale: puede haber precios de una descarga anterior sin
                # calcular, y eso hay que arreglarlo aunque hoy falle la red.
            }
        }

        & $Py -m stocks_tracker.compute.run_compute --check-stale
        if ($LASTEXITCODE -eq 0) { return }

        Write-Step "Calculando indicadores, ranking y senales"
        & $Py -m stocks_tracker.compute.run_compute
        if ($LASTEXITCODE -eq 77) {
            # La puerta de calidad se ha negado a calcular. Es el sistema
            # funcionando, pero si no se dice aqui el usuario ve el dashboard
            # con datos viejos y ningun motivo: exactamente lo que pasaba.
            Write-Host ""
            Write-Host "  El calculo NO se ha ejecutado: los datos tienen problemas graves." -ForegroundColor Yellow
            Write-Host "  Mira 'Estado de los datos' en el dashboard para ver cuales." -ForegroundColor Yellow
            Write-Host ""
            return
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "El calculo ha fallado. Se abre con los datos anteriores." -ForegroundColor Yellow
            return
        }
        & $Py -m stocks_tracker.compute.run_compute --only scores --all-presets
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Py -m stocks_tracker.compute.run_advice
    }

    'autostart' {
        Write-Host "Funcion retirada. Usa el asesor y el analisis desde el dashboard."
        exit 2
    }

    'autostart-off' {
        & (Join-Path $PSScriptRoot 'remove-legacy-tasks.ps1')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    'ingest' {
        Assert-Venv
        Write-Step "Descargando datos reales (la primera vez tarda varios minutos)"
        & $Py -m stocks_tracker.ingest.run_ingest --what all
    }

    'backup-check' {
        Assert-Venv
        & $Py -m stocks_tracker.core.db --verify-backups
    }

    'report' {
        Assert-Venv
        & $Py -m stocks_tracker.core.daily_report
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
            # --drop-synthetic aqui es obligatorio. Sin el, los datos de
            # prueba de una instalacion antigua sobreviven a todas las
            # descargas posteriores: la ingesta es incremental, ve las series
            # inventadas al dia y no trae nada para esos tickers. El aviso rojo
            # se quedaba encendido para siempre sin que nada lo explicase.
            @{ Name = 'Descargando el universo completo (10-25 min)'
               Args = @('-m', 'stocks_tracker.ingest.run_ingest',
                        '--drop-synthetic', '--what', 'all') },
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

    'claves' {
        # Que credenciales hay, cuales faltan y como conseguirlas. Nunca
        # imprime un valor: es para mirar en pantalla sin miedo.
        Assert-Venv
        & $Py -m stocks_tracker.core.secrets
    }

    'mercados' {
        Write-Host "Funcion retirada. Usa el asesor y el analisis desde el dashboard."
        exit 2
    }

    'polymarket' {
        Write-Host "Funcion retirada. Usa el asesor y el analisis desde el dashboard."
        exit 2
    }

    'cripto' {
        Write-Host "Funcion retirada. Usa el asesor y el analisis desde el dashboard."
        exit 2
    }

    'ciclo' {
        Write-Host "Funcion retirada. Usa el asesor y el analisis desde el dashboard."
        exit 2
    }

    'pendientes' {
        Write-Host "Funcion retirada. Usa el asesor y el analisis desde el dashboard."
        exit 2
    }

    'calibracion' {
        Write-Host "Funcion retirada. Usa el asesor y el analisis desde el dashboard."
        exit 2
    }

    'tiene-universo' {
        # Lo usa "Stocks Tracker.bat" para saber que hacer sin preguntar nada.
        # El criterio es el ranking y no el numero de precios: se puede tener
        # medio millon de filas de los indices y seguir sin un solo candidato
        # que mirar, que es lo que le importa a quien abre el programa.
        Assert-Venv
        $n = & $Py -c "from stocks_tracker.core.db import query; import sys; sys.stdout.write(str(int(query('SELECT COUNT(*) AS n FROM factor_scores')['n'][0])))" 2>$null
        if ($LASTEXITCODE -ne 0) { exit 1 }
        if ([int]$n -ge 200) { exit 0 }
        exit 1
    }

    'puerta' {
        Write-Host "Funcion retirada. Usa el asesor y el analisis desde el dashboard."
        exit 2
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

    # Los tres pasos van en este orden y no se pueden saltar. El tramo
    # posterior a backtest.confirmation_from NO se toca en el descubrimiento:
    # es lo unico que distingue una senal de una casualidad bien contada.
    'validate-freeze' {
        Assert-Venv
        & $Py -m stocks_tracker.backtest.run_backtest --congelar
    }

    'validate-confirm' {
        Assert-Venv
        Write-Step "Gasta el tramo reservado. Solo se puede una vez por senal."
        & $Py -m stocks_tracker.backtest.run_backtest --fase confirmacion --tag-signals
    }

    'reconciliar' {
        Write-Host "Funcion retirada. Usa el asesor y el analisis desde el dashboard."
        exit 2
    }

    'oro' {
        Assert-Venv
        Write-Step "Recalculando la referencia de regresion financiera"
        Write-Host "  Imprime lo que cambia ANTES de escribirlo. Revisalo."
        & $Py scripts/regenerar_oro.py
    }

    'consejo' {
        Assert-Venv
        Write-Step "Calculando que haria hoy"
        if ($Caja -le 0) {
            Write-Host "  Sin efectivo declarado: las compras saldran vetadas."
            Write-Host "  Usa -Caja 1500 para decir cuanto tienes disponible."
        }
        & $Py -m stocks_tracker.compute.run_advice --caja $Caja
    }

    'porque' {
        Assert-Venv
        if (-not $Valor) {
            Write-Host "  Falta el valor. Ejemplo: stocks.ps1 porque -Valor MSFT"
            exit 1
        }
        & $Py -m stocks_tracker.compute.run_advice --por-que $Valor
    }

    'calibrar' {
        Assert-Venv
        Write-Step "Midiendo el liston de compra contra el pasado"
        Write-Host "  Necesita el ranking historico: stocks.ps1 compute con --history."
        & $Py -m stocks_tracker.compute.run_advice --calibrar
    }

    'huella' {
        Assert-Venv
        # Existe porque el sintoma "en mi otro ordenador salen otras
        # oportunidades" no se diagnostica mirando las dos pantallas: las
        # dos ensenan una lista igual de convincente. Comparar dos huellas
        # de ocho caracteres lo resuelve en un segundo.
        & $Py -m stocks_tracker.compute.run_compute --universo
    }

    'lista-universo' {
        Assert-Venv
        # El paso siguiente a la huella. Aquella dice SI dos ordenadores
        # puntuaron lo mismo; esta dice EN QUE se diferencian, que es lo unico
        # que se puede arreglar: "b175e2e9 contra c3fea71c" no se lee.
        $destino = if ($Valor) { $Valor } else { 'universo.txt' }
        & $Py -m stocks_tracker.compute.run_compute --universo-lista $destino
    }

    'auditar' {
        Assert-Venv
        Write-Step "Cruzando precios con un segundo proveedor"
        Write-Host "  Cartera, valores con senal y una muestra rotatoria."
        Write-Host "  No se audita el universo entero: los limites gratuitos no dan."
        & $Py -m stocks_tracker.ingest.run_audit
        if ($LASTEXITCODE -eq 78) {
            Write-Host ""
            Write-Host "  Ninguna fuente ha podido contrastar nada." -ForegroundColor Yellow
            Write-Host "  Los precios siguen siendo los de siempre; lo que falta"
            Write-Host "  es la confirmacion de un segundo proveedor."
        }
    }

    'alerts' {
        Write-Host "Funcion retirada. Usa el asesor y el analisis desde el dashboard."
        exit 2
    }

    'watch' {
        Write-Host "Funcion retirada. Usa el asesor y el analisis desde el dashboard."
        exit 2
    }

    'watchtest' {
        Write-Host "Funcion retirada. Usa el asesor y el analisis desde el dashboard."
        exit 2
    }

    'daily' {
        Assert-Venv
        # Actualizacion manual. Ninguna tarea se registra ni ejecuta ordenes.
        foreach ($step in @(
            @('-m', 'stocks_tracker.ingest.run_ingest', '--what', 'all'),
            @('-m', 'stocks_tracker.compute.run_compute'),
            @('-m', 'stocks_tracker.compute.run_advice'),
            @('-m', 'stocks_tracker.core.daily_report')
        )) {
            & $Py @step
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
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

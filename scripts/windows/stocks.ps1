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
      puerta    Valida la estrategia de cripto contra el historico
      claves    Muestra que credenciales faltan y como conseguirlas
      mercados  Estado de Kraken y Polymarket: que falta para operar
      polymarket Comprueba la lectura de Polymarket (no necesita wallet)
      calibracion Se equivoca Polymarket lo bastante como para apostar?
      cripto    Historico de cripto (Yahoo) y comparacion con Kraken
      pendientes Ordenes que el freno de mano dejo esperando tu visto bueno
      ciclo     Un ciclo del bot de cripto (lo llama el programador)
      tiene-universo  Codigo 0 si el universo completo esta descargado
      compute   Recalcula indicadores, factores, senales y scores
      presets   Puntua el universo con los cinco estilos de inversion
      validate  Valida las senales contra su historico (descubrimiento)
      validate-freeze   Congela lo que llego a estable
      validate-confirm  Lo comprueba en el tramo reservado
      reconciliar Contrasta tu cartera con la del broker
      oro       Reescribe la referencia de regresion financiera
      auditar   Cruza precios con un segundo proveedor y da su veredicto
      consejo   Calcula que hacer hoy con tu cartera y con el mercado, y lo
                deja escrito. Acepta -Caja para decir cuanto efectivo tienes
      calibrar  Mide si el liston de compra ha batido al indice en el pasado.
                Solo vale para la mitad del ranking que sale de precios
      huella    Dice contra que universo se calculo el ranking. Compara la
                huella entre dos ordenadores: si difiere, es normal que las
                oportunidades no coincidan
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
    [ValidateSet('setup', 'demo', 'ingest', 'universo', 'puerta', 'claves', 'mercados',
                 'polymarket', 'calibracion', 'cripto', 'pendientes', 'ciclo',
                 'tiene-universo',
                 'compute', 'presets', 'validate',
                 'validate-freeze', 'validate-confirm',
                 'auditar', 'oro', 'huella', 'consejo', 'calibrar',
                 'alerts', 'watch', 'watchtest', 'run', 'daily', 'test',
                 'real', 'update', 'autostart', 'autostart-off',
                 'lint', 'help')]
    [string]$Task = 'help',

    # Efectivo disponible para invertir. El programa NO puede saberlo: no
    # habla con tu banco ni con tu broker, y el extracto solo trae
    # posiciones. Con cero, las compras salen vetadas por falta de tamano,
    # que es lo correcto: recomendar comprar con dinero que no existe es
    # peor que no recomendar nada.
    [double]$Caja = 0
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
            # Cripto tambien: es incremental y tarda segundos cuando no hay nada
            # nuevo. Sin esto, el universo de acciones se mantendria al dia y el
            # de cripto se quedaria congelado en la fecha de la instalacion, con
            # el bot decidiendo sobre precios viejos.
            & $Py -m stocks_tracker.ingest.ingest_crypto
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

        # --- El bot de cripto -------------------------------------------------
        # Cada seis horas y no una vez al dia: cripto no cierra, y un stop que
        # solo se mira a las 23:15 no es un stop, es una consulta. Seis horas
        # tampoco es proteccion de verdad  - eso serian minutos -  pero es lo que
        # da un ordenador personal sin tenerlo encendido a proposito.
        #
        # No a la hora en punto: si coincidiera con la actualizacion de datos,
        # el bot leeria el almacen mientras se escribe y DuckDB solo admite un
        # escritor. El desfase de veinte minutos evita el choque.
        $botScript = Join-Path $Root 'scripts\windows\stocks.ps1'
        $bot = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$botScript`" ciclo" `
            -WorkingDirectory $Root
        $botTrigger = New-ScheduledTaskTrigger -Once -At '00:20' `
            -RepetitionInterval (New-TimeSpan -Hours 6)
        $botSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
            -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

        Register-ScheduledTask -TaskName 'Stocks Tracker - ciclo del bot' `
            -Action $bot -Trigger $botTrigger -Settings $botSettings `
            -Description 'Ciclo del bot de cripto: propone, aplica el riesgo y ejecuta.' `
            -Force | Out-Null

        Write-Host ""
        Write-Host "Automatizacion activada." -ForegroundColor Green
        Write-Host "  Datos:  cada dia a las 23:15, con el mercado americano cerrado."
        Write-Host "  Bot:    cada 6 horas, empezando a las 00:20."
        Write-Host "  Si el ordenador esta apagado, se ejecutan al encenderlo."
        Write-Host ""
        Write-Host "  OJO: con el ordenador apagado el bot NO opera." -ForegroundColor Yellow
        Write-Host "  Un stop no se dispara y una senal no se ejecuta. En cripto," -ForegroundColor Yellow
        Write-Host "  que no cierra, un fin de semana son 48 h sin vigilancia." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Para desactivarla:  .\scripts\windows\stocks.ps1 autostart-off"
    }

    'autostart-off' {
        # Las DOS tareas. Quitar solo la de datos dejaria el bot operando
        # despues de que el usuario creyera haberlo desactivado, que es la
        # peor forma posible de que un boton de apagado no apague.
        foreach ($nombre in @('Stocks Tracker - actualizacion diaria',
                              'Stocks Tracker - ciclo del bot')) {
            Unregister-ScheduledTask -TaskName $nombre `
                -Confirm:$false -ErrorAction SilentlyContinue
        }
        Write-Host "Automatizacion desactivada: ni datos ni bot." -ForegroundColor Yellow
        Write-Host "El bot no volvera a operar hasta que la vuelvas a activar."
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
               Args = @('-m', 'stocks_tracker.backtest.run_backtest', '--tag-signals') },
            # El historico de cripto va aqui y no en un comando aparte: si
            # hubiera que acordarse de ejecutarlo, el bot de cripto se quedaria
            # sin datos y sin decir por que. Sale de Yahoo porque Kraken solo
            # da dos anos.
            @{ Name = 'Descargando el historico de cripto (2-4 min)'
               Args = @('-m', 'stocks_tracker.ingest.ingest_crypto') },
            @{ Name = 'Calculando indicadores de cripto (1-2 min)'
               Args = @('-m', 'stocks_tracker.compute.run_compute', '--only', 'indicators') }
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
        # Que falta en cada mercado para poder operar, en una frase por venue.
        Assert-Venv
        & $Py -m stocks_tracker.trading.venues
    }

    'polymarket' {
        # Comprueba que la API publica de Polymarket devuelve lo que el codigo
        # espera. No necesita wallet ni clave: solo lee.
        #
        # Existe porque el lector se escribio sin poder llamar a Polymarket
        #  - la red del entorno de desarrollo lo bloquea - , asi que la primera
        # vez que se ejecuta de verdad es aqui. Mejor que lo diga esto, con
        # nombre y apellidos del campo que no cuadra, a que reviente luego.
        Assert-Venv
        & $Py -m stocks_tracker.trading.brokers.polymarket_public
    }

    'cripto' {
        # Historico de los pares del mandato desde Kraken. El endpoint es
        # publico: no hacen falta claves ni cuenta.
        #
        # Kraken entrega 720 velas diarias como mucho, unos dos anos, y no hay
        # forma de pedir mas atras. El comando lo dice al terminar, porque dos
        # anos de cripto caben dentro de una sola subida y un backtest ahi
        # puede salir estupendo sin que la estrategia valga nada.
        Assert-Venv
        # Incremental, y al terminar compara con Kraken. El historico sale de
        # Yahoo porque Kraken solo da dos anos; se opera en Kraken. Las dos
        # series NO se empalman: el salto en la fecha de union lo leeria el
        # momentum como senal. La comparacion dice cuanto se separan.
        #
        # Para rehacer los ocho anos enteros:
        #     .venv\Scripts\python.exe -m stocks_tracker.ingest.ingest_crypto --full
        & $Py -m stocks_tracker.ingest.ingest_crypto --comparar
    }

    'ciclo' {
        # Un ciclo del bot de cripto: propone, aplica el riesgo y ejecuta lo
        # que no cruce un freno. Lo llama el Programador de tareas cada seis
        # horas; a mano sirve para ver que haria ahora mismo.
        #
        # El modo sale del mandato (`mode:` en trading.yaml) y NO se fija
        # aqui: escribirlo en el script significaria que cambiarlo en el
        # mandato no hace nada, y el bot programado seguiria simulando con
        # aspecto de estar operando.
        #
        # Mientras la puerta de cripto no este superada,
        # `venues.require_tradeable` lo para con una frase que dice que falta.
        # Eso es lo correcto: sin validar no se opera.
        Assert-Venv
        & $Py -m stocks_tracker.trading.run_bot --venue kraken
        & $Py -m stocks_tracker.trading.confirm
    }

    'pendientes' {
        # Lo que el freno de mano dejo esperando. En 'guarded' el bot opera
        # solo salvo lo que cruce un freno  - importe anormal, primera orden con
        # dinero real, abrir estando en perdidas -  y eso espera aqui.
        #
        # Aprobar NO se salta el riesgo: la orden ya paso por el mandato. Lo
        # que estaba en pausa era el ultimo paso, y el precio se comprueba de
        # nuevo antes de enviarla.
        Assert-Venv
        & $Py -m stocks_tracker.trading.confirm
    }

    'calibracion' {
        # El examen de Polymarket, y su logica va al reves que la de acciones.
        # Alli se mide si una estrategia gano dinero. Aqui el precio ES la
        # probabilidad, asi que un mercado que acierta es un mercado en el que
        # NO se debe operar: se gana 0,70 el 30 % de las veces y se pierde
        # 0,30 el 70 %, que es cero antes de la horquilla.
        #
        # Aprobar significa haber encontrado una desviacion repetida y mayor
        # que los costes. Suspender es la respuesta normal.
        #
        # No necesita wallet ni clave: solo lee mercados ya resueltos.
        Assert-Venv
        & $Py -m stocks_tracker.trading.polymarket_calibration
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
        # Puerta 1: decide si la estrategia del bot puede pasar a operar en
        # papel. Son tres pasos porque el backtest necesita indicadores y
        # ranking de todo el historico, no solo de las ultimas 400 sesiones.
        #
        # No se ejecuta sola nunca: validar una estrategia es una decision, no
        # una tarea de mantenimiento.
        Assert-Venv
        Write-Step "[1/3] Indicadores sobre todo el historico (5-15 min)"
        & $Py -m stocks_tracker.compute.run_compute --only indicators --full-history
        if ($LASTEXITCODE -ne 0) { exit 1 }

        Write-Step "[2/3] Ranking historico del perfil del bot (3-10 min)"
        & $Py -m stocks_tracker.compute.run_compute --history 10
        if ($LASTEXITCODE -ne 0) { exit 1 }

        Write-Step "[3/3] Backtest con costes y umbrales (10-20 min con robustez)"
        & $Py -m stocks_tracker.trading.run_bot --venue kraken --gate --robustez
        $veredicto = $LASTEXITCODE

        Write-Host ""
        if ($veredicto -eq 0) {
            Write-Host "La estrategia queda certificada para operar EN PAPEL." -ForegroundColor Green
            Write-Host "No es una prediccion de rentabilidad. Lee el informe entero."
        } else {
            Write-Host "La estrategia NO queda certificada. No se opera con ella." -ForegroundColor Yellow
            Write-Host "Es el sistema funcionando: se ajusta o se descarta."
        }
        exit $veredicto
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
        Assert-Venv
        Write-Step "Contrastando tu cartera con la del broker"
        & $Py -m stocks_tracker.trading.reconcile_cli
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
            @{ Name = 'Ingesta de cripto'; Args = @('-m', 'stocks_tracker.ingest.ingest_crypto') },
            @{ Name = 'Calculo'; Args = @('-m', 'stocks_tracker.compute.run_compute') },
            @{ Name = 'Consejos'; Args = @('-m', 'stocks_tracker.compute.run_advice') },
            @{ Name = 'Alertas'; Args = @('-m', 'stocks_tracker.alerts.run_alerts') }
        )) {
            Write-Step $step.Name
            try { & $Py @($step.Args) }
            catch { Write-Host "  $($step.Name) ha fallado, se continua." -ForegroundColor Yellow }
        }

        # La validacion de la estrategia se ejecuta SOLA, una vez por semana.
        # No es mantenimiento: es el examen que decide si el bot puede operar.
        # Se automatiza porque el resultado tiene que existir aunque nadie
        # abra una consola nunca, y se guarda para leerlo en el dashboard.
        #
        # Los domingos, y ademas la PRIMERA vez que hay datos suficientes.
        #
        # Semanal y no diario porque el backtest recorre diez anos: una sesion
        # mas mueve el Sharpe en el tercer decimal, asi que a diario serian
        # siete informes casi identicos. Y porque repetir un examen cada dia
        # hasta que salga bien es hacer trampa por cadencia, aunque no sea la
        # intencion.
        #
        # Pero hacer esperar al domingo el PRIMER veredicto no protege de nada:
        # solo retrasa la unica informacion que todavia no existe.
        $sinInforme = $false
        try {
            $n = & $Py -c "from stocks_tracker.core.db import query; import sys; sys.stdout.write(str(int(query('SELECT COUNT(*) AS n FROM gate_reports')['n'][0])))" 2>$null
            $sinInforme = ([int]$n -eq 0)
        } catch { $sinInforme = $false }

        if ((Get-Date).DayOfWeek -eq 'Sunday' -or $sinInforme) {
            & $Py -m stocks_tracker.compute.run_compute --only indicators --full-history
            if ($LASTEXITCODE -eq 0) { & $Py -m stocks_tracker.compute.run_compute --history 10 }
            # El calculo del historico de arriba NO es del bot: alimenta el
            # ranking y las senales que se leen en el dashboard, y por eso se
            # queda aunque el bot de acciones se haya retirado.
            #
            # La puerta de CRIPTO y el estudio de Polymarket son los dos
            # examenes que deciden si cada mercado puede operar, y su resultado
            # tiene que existir aunque nadie abra una consola nunca. Se leen en
            # el dashboard.
            #
            # No detienen nada si fallan: un suspenso es un resultado, y que
            # Polymarket no responda un domingo no puede dejar sin ejecutar lo
            # demas.
            Write-Step 'Validando la estrategia de cripto'
            try { & $Py -m stocks_tracker.trading.run_bot --venue kraken --gate --robustez }
            catch { Write-Host "  La puerta de cripto ha fallado, se continua." -ForegroundColor Yellow }

            Write-Step 'Midiendo la calibracion de Polymarket'
            try { & $Py -m stocks_tracker.trading.polymarket_calibration }
            catch { Write-Host "  El estudio de Polymarket ha fallado, se continua." -ForegroundColor Yellow }
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

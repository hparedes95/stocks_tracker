<#
.SYNOPSIS
    Instalador de Stocks Tracker para Windows.

.DESCRIPTION
    Lo llama 'Stocks Tracker.bat', que es el unico fichero que el usuario
    maneja. Descarga el proyecto, prepara el entorno de Python, trae
    precios reales y deja el acceso directo en el Escritorio.

    No hace falta tener git ni saber usar la consola: se descarga el ZIP
    del repositorio.

    Se instala en la carpeta del usuario (%LOCALAPPDATA%), no en
    Archivos de programa, para no necesitar permisos de administrador.
#>

param(
    [string]$Branch = 'claude/stock-market-monitoring-dashboard-7yf0nb',
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'StocksTracker'),
    # NO se generan datos de prueba salvo que se pidan expresamente. Existieron
    # para poder ver la aplicacion sin esperar la descarga, y costaron dos
    # perdidas de confianza: precios inventados con el mismo aspecto que los
    # reales, y luego restos que sobrevivian a las descargas posteriores y
    # mantenian el aviso rojo encendido. Un dashboard para decidir inversiones
    # no puede arrancar con numeros falsos.
    [switch]$ConDatosDePrueba,
    [switch]$UniversoCompleto
)

$ErrorActionPreference = 'Stop'
# En PowerShell 7.4+ un comando nativo con codigo != 0 lanza excepcion, y
# aqui se lee $LASTEXITCODE a proposito.
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

# Rango de Python soportado, el mismo que declara pyproject.toml.
# Son dos ficheros que no se pueden validar entre si: si cambia alli,
# hay que cambiarlo aqui.
$script:MinPy = 11
$script:MaxPy = 14
$Repo = 'hparedes95/stocks_tracker'

function Write-Step($n, $total, $text) {
    Write-Host ""
    Write-Host "[$n/$total] $text" -ForegroundColor Cyan
}

function Fail($message, $hint) {
    Write-Host ""
    Write-Host "  $message" -ForegroundColor Red
    if ($hint) { Write-Host "  $hint" -ForegroundColor Yellow }
    Write-Host ""
    Read-Host "Pulsa Intro para cerrar"
    exit 1
}

Write-Host ""
Write-Host "  Stocks Tracker" -ForegroundColor Green
Write-Host "  Instalacion en $InstallDir"
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Python
# ---------------------------------------------------------------------------
Write-Step 1 8 "Comprobando Python"

function Test-PythonExe($exe, $prefix = @()) {
    <#
        Comprueba que un ejecutable es un Python compatible de verdad.

        Hace falta porque en Windows `python` suele ser el alias de la
        Microsoft Store: un fichero de cero bytes que no interpreta nada, solo
        abre la tienda.
    #>
    if (-not $exe) { return $null }
    try {
        $raw = & $exe @prefix '--version' 2>&1
        $version = ($raw | Out-String).Trim()
    } catch { return $null }

    # Sin punto final obligatorio: hay instalaciones que dicen "Python 3.12"
    # a secas, y exigir el parche las descartaba sin motivo.
    if ($version -notmatch 'Python\s+3\.(\d+)') { return $null }
    $minor = [int]$Matches[1]
    if ($minor -lt $script:MinPy -or $minor -gt $script:MaxPy) { return $null }
    return @{ Exe = $exe; Prefix = $prefix; Version = $version }
}

function Find-Python {
    <#
        Busca en cuatro sitios, por orden de fiabilidad. Mirar solo el PATH no
        basta: el instalador de Python no lo modifica salvo que marques la
        casilla, y winget tampoco, asi que lo normal es tenerlo instalado y
        que `python` no exista en la consola.
    #>
    $checked = @()

    # 1. El lanzador `py`, que sabe donde estan todas las versiones instaladas.
    $pyCmd = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($pyCmd) {
        foreach ($v in @('-3.13', '-3.12', '-3.14', '-3.11', '-3')) {
            $found = Test-PythonExe $pyCmd.Source @($v)
            if ($found) { return $found }
        }
        # `py -0p` lista version y ruta de cada instalacion.
        try {
            foreach ($line in (& $pyCmd.Source '-0p' 2>&1)) {
                if ("$line" -match '([A-Za-z]:\\[^\s].*python\.exe)') {
                    $checked += $Matches[1]
                    $found = Test-PythonExe $Matches[1]
                    if ($found) { return $found }
                }
            }
        } catch { }
    }

    # 2. Lo que haya en el PATH.
    foreach ($candidate in @('python', 'python3')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        # No se descarta WindowsApps: si Python viene de la Microsoft
        # Store, ese alias SI es un interprete. El stub de cuando no esta
        # instalado no imprime version, asi que Test-PythonExe lo filtra solo.
        $checked += $cmd.Source
        $found = Test-PythonExe $cmd.Source
        if ($found) { return $found }
    }

    # 3. El registro, que es donde queda constancia de la instalacion.
    foreach ($hive in @('HKLM:\SOFTWARE\Python\PythonCore',
                        'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore',
                        'HKCU:\SOFTWARE\Python\PythonCore')) {
        if (-not (Test-Path $hive)) { continue }
        foreach ($key in Get-ChildItem $hive -ErrorAction SilentlyContinue) {
            $installPath = (Get-ItemProperty (Join-Path $key.PSPath 'InstallPath') `
                            -ErrorAction SilentlyContinue).'(default)'
            if (-not $installPath) { continue }
            $exe = Join-Path $installPath 'python.exe'
            if (Test-Path $exe) {
                $checked += $exe
                $found = Test-PythonExe $exe
                if ($found) { return $found }
            }
        }
    }

    # 4. Las carpetas donde se instala habitualmente.
    $patterns = @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "$env:ProgramFiles\Python3*\python.exe",
        "${env:ProgramFiles(x86)}\Python3*\python.exe",
        "C:\Python3*\python.exe"
    )
    foreach ($pattern in $patterns) {
        foreach ($exe in (Get-ChildItem $pattern -ErrorAction SilentlyContinue |
                          Sort-Object FullName -Descending)) {
            $checked += $exe.FullName
            $found = Test-PythonExe $exe.FullName
            if ($found) { return $found }
        }
    }

    $script:PythonChecked = $checked
    return $null
}

$python = Find-Python
if ($python) {
    Write-Host "  Encontrado: $($python.Version)"
    Write-Host "  $($python.Exe)" -ForegroundColor DarkGray
} else {
    Write-Host "  No hay una version compatible (hace falta de la 3.11 a la 3.14)."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "No se ha podido instalar Python automaticamente." `
             "Descargalo de https://www.python.org/downloads/ y vuelve a ejecutar esto."
    }

    Write-Host "  Instalando Python 3.12..." -ForegroundColor Yellow
    # `--scope user` evita el aviso de administrador; si winget dice que ya
    # estaba instalado, la busqueda posterior lo encontrara igualmente porque
    # ahora se mira tambien el disco y el registro, no solo el PATH.
    try {
        winget install --id Python.Python.3.12 --source winget --scope user `
            --accept-package-agreements --accept-source-agreements --silent
    } catch {
        # "Ya esta instalado" es un fallo de winget pero un exito para
        # nosotros: la busqueda de despues lo encontrara en el disco.
        Write-Host "  winget: $($_.Exception.Message)" -ForegroundColor DarkGray
    }

    # winget no refresca el PATH de la sesion en curso.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
    $python = Find-Python

    if (-not $python) {
        Write-Host ""
        Write-Host "  Se ha buscado en:" -ForegroundColor DarkGray
        if ($script:PythonChecked) {
            foreach ($path in ($script:PythonChecked | Select-Object -Unique)) {
                Write-Host "    $path" -ForegroundColor DarkGray
            }
        } else {
            Write-Host "    (ninguna ruta candidata)" -ForegroundColor DarkGray
        }
        Fail "Python esta instalado pero no se encuentra el ejecutable." `
             ("Prueba a cerrar esta ventana y ejecutar el instalador otra vez. " +
              "Si sigue fallando, ejecuta 'py -0p' en PowerShell y pasame la salida.")
    }
    Write-Host "  Instalado: $($python.Version)"
    Write-Host "  $($python.Exe)" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 2. Descarga
# ---------------------------------------------------------------------------
Write-Step 2 8 "Descargando el proyecto desde GitHub"

$temp = Join-Path $env:TEMP ("stockstracker_" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $temp -Force | Out-Null
$zip = Join-Path $temp 'source.zip'
$url = "https://github.com/$Repo/archive/refs/heads/$Branch.zip"

try {
    # Las descargas de PowerShell son mucho mas rapidas sin la barra de
    # progreso: la actualiza en cada bloque y eso domina el tiempo total.
    $previous = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    $ProgressPreference = $previous
} catch {
    Fail "No se ha podido descargar el proyecto: $($_.Exception.Message)" `
         "Comprueba tu conexion. URL: $url"
}
Write-Host "  Descargado ($([math]::Round((Get-Item $zip).Length / 1MB, 1)) MB)"

Write-Step 3 8 "Instalando en $InstallDir"

# Copia de seguridad en un sitio ESTABLE y con nombre, no en una carpeta
# temporal con nombre aleatorio. Si el proceso se corta entre el borrado y la
# restauracion  - y el borrado es lo mas destructivo que hace este script -  los
# datos del usuario tienen que poder recuperarse sin adivinar en que carpeta de
# %TEMP% quedaron.
$backup = Join-Path $env:LOCALAPPDATA 'StocksTracker.backup'

if (Test-Path $InstallDir) {
    if (Test-Path $backup) { Remove-Item $backup -Recurse -Force }
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    foreach ($item in @('data', '.env', 'config')) {
        $source = Join-Path $InstallDir $item
        if (Test-Path $source) {
            Copy-Item $source -Destination $backup -Recurse -Force
            Write-Host "  Conservando $item"
            # Reinstalar es tambien la forma de actualizar el programa. Si ya
            # habia almacen, sus precios se conservan y NO hay que volver a
            # generar datos de prueba encima.
            if ($item -eq 'data') { $script:PreservedData = $true }
        }
    }

    # Salir de la carpeta ANTES de borrarla. Windows no deja borrar el
    # directorio de trabajo de un proceso: el borrado se lleva por delante todo
    # el contenido y falla al final, con $ErrorActionPreference='Stop' abortando
    # el script justo despues de haber vaciado la instalacion. El lanzador se
    # ejecuta con esta carpeta como directorio actual, asi que pasaba siempre.
    Set-Location $env:LOCALAPPDATA
    Remove-Item $InstallDir -Recurse -Force
} elseif (Test-Path $backup) {
    # Hay copia pero no instalacion: una actualizacion anterior se corto a
    # medias. Se recupera en lugar de empezar de cero y perder el universo.
    Write-Host "  Recuperando datos de una actualizacion interrumpida" -ForegroundColor Yellow
    $script:PreservedData = $true
}

Expand-Archive -Path $zip -DestinationPath $temp -Force
$extracted = Get-ChildItem $temp -Directory |
    Where-Object { $_.Name -like 'stocks_tracker-*' } |
    Select-Object -First 1
if (-not $extracted) { Fail "El ZIP descargado no tiene el formato esperado." $null }

Move-Item $extracted.FullName $InstallDir

# Marca de version instalada. La usa "Stocks Tracker.bat" para saber si el
# codigo de la carpeta esta al dia sin tener que reinstalar en cada arranque.
# Sin esto, el lanzador actualizaba los datos pero nunca el programa, y las
# correcciones no llegaban nunca por muchas veces que se abriera.
try {
    $head = Invoke-RestMethod -UseBasicParsing -TimeoutSec 20 `
        -Uri "https://api.github.com/repos/$Repo/commits/$Branch"
    Set-Content -Path (Join-Path $InstallDir '.version') -Value $head.sha -Encoding ASCII
} catch {
    Write-Host "  (no se ha podido anotar la version instalada)" -ForegroundColor DarkGray
}

if (Test-Path $backup) {
    # El almacen y las claves son del usuario: vuelven a su sitio tal cual.
    foreach ($item in @('data', '.env')) {
        $source = Join-Path $backup $item
        if (Test-Path $source) {
            Copy-Item $source -Destination $InstallDir -Recurse -Force
        }
    }

    # La configuracion NO. Sus ficheros vienen con el programa, y devolver los
    # antiguos encima significa que un cambio de configuracion no llega nunca:
    # se actualizaria el codigo y se quedaria la configuracion de la primera
    # instalacion. Es lo que dejaria una instalacion ya existente sin el bloque
    # `venues`, con el bot diciendo que Kraken no esta configurado y un
    # fichero delante que dice que si.
    #
    # No se borra: si alguna vez se toco a mano, queda al lado para comparar.
    $configVieja = Join-Path $backup 'config'
    if (Test-Path $configVieja) {
        $aparte = Join-Path $InstallDir 'config.anterior'
        if (Test-Path $aparte) { Remove-Item $aparte -Recurse -Force }
        Copy-Item $configVieja -Destination $aparte -Recurse -Force
        Write-Host "  Configuracion actualizada (la anterior, en config.anterior)"
    }

    # Solo se borra la copia cuando los datos ya estan de vuelta en su sitio.
    Remove-Item $backup -Recurse -Force
}

# Fichero de claves. Se crea vacio a partir del ejemplo para que poner una
# credencial sea abrir un fichero que ya existe y escribir detras del igual.
# Sin esto hay que saber que hay que copiar `.env.example` a `.env`, que es
# justo el tipo de paso previo que convierte "pon tus claves" en una consulta.
$envFile = Join-Path $InstallDir '.env'
$envEjemplo = Join-Path $InstallDir '.env.example'
if ((Test-Path $envEjemplo) -and -not (Test-Path $envFile)) {
    Copy-Item $envEjemplo -Destination $envFile -Force
    Write-Host "  Creado .env para tus claves (vacio; el programa funciona sin el)"
}
Write-Host "  Copiado"

# ---------------------------------------------------------------------------
# 4. Entorno
# ---------------------------------------------------------------------------
Write-Step 4 8 "Preparando el entorno (esto tarda un par de minutos)"

Set-Location $InstallDir
& $python.Exe @($python.Prefix) -m venv .venv
$Py = Join-Path $InstallDir '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { Fail "No se ha podido crear el entorno de Python." $null }

& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install -e ".[data,dev]" --quiet
if ($LASTEXITCODE -ne 0) { Fail "Ha fallado la instalacion de dependencias." $null }

& $Py -m stocks_tracker.core.db --migrate
Write-Host "  Entorno listo"

# ---------------------------------------------------------------------------
# 5. Datos de prueba
# ---------------------------------------------------------------------------
if ($script:PreservedData) {
    Write-Step 5 8 "Datos ya existentes: se conservan"
    Write-Host "  Se conserva el almacen de la instalacion anterior."
} elseif ($ConDatosDePrueba) {
    Write-Step 5 8 "Generando datos de prueba (los has pedido con -ConDatosDePrueba)"
    Write-Host "  Son INVENTADOS. El dashboard lo avisara en rojo mientras esten."
    & $Py -m stocks_tracker.ingest.run_ingest --what all --provider synthetic
    & $Py -m stocks_tracker.compute.run_compute
    & $Py -m stocks_tracker.compute.run_compute --only scores --all-presets
} else {
    Write-Step 5 8 "Sin datos de prueba"
    Write-Host "  Se instala vacio y se descargan precios REALES en el paso 7."
    Write-Host "  Nada de lo que veas en el dashboard sera inventado."
}

# ---------------------------------------------------------------------------
# 6. Acceso directo
# ---------------------------------------------------------------------------
Write-Step 6 8 "Creando el acceso directo"

# El acceso directo apunta al MISMO fichero que se descarga de GitHub, no a un
# lanzador aparte que haya que mantener en paralelo. Un solo fichero para
# instalar, actualizar, descargar datos y abrir: cualquier otro reparto obliga
# al usuario a saber cual toca, que es pedirle que lleve la cuenta del estado
# interno del programa.
$launcher = Join-Path $InstallDir 'Stocks Tracker.bat'
$origen = Join-Path $InstallDir 'installer\Stocks Tracker.bat'
if (Test-Path $origen) {
    Copy-Item $origen $launcher -Force
} else {
    Fail "El paquete descargado no trae 'Stocks Tracker.bat'." $null
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Stocks Tracker.lnk')
)
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $InstallDir
$shortcut.Description = 'Dashboard de mercado y deteccion de oportunidades'
$shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,167"
$shortcut.Save()
Write-Host "  Acceso directo creado en el Escritorio"

# ---------------------------------------------------------------------------
# 7. Precios reales
# ---------------------------------------------------------------------------
Write-Step 7 8 "Descargando precios reales de los indices"
Write-Host "  Sustituyen a los datos de prueba. Un minuto."
# Se comprueba $LASTEXITCODE y no try/catch: un comando nativo que sale con
# codigo distinto de cero NO lanza excepcion en Windows PowerShell, asi que el
# catch nunca se ejecutaba y se anunciaba exito aunque la descarga fallase.
& $Py -m stocks_tracker.ingest.run_ingest --drop-synthetic --what prices `
    --universes INDICES,MACRO --years 3
$DescargaOk = ($LASTEXITCODE -eq 0)
if ($DescargaOk) {
    & $Py -m stocks_tracker.compute.run_compute
}
# 77 = los datos tienen problemas graves y el calculo se ha negado a correr.
# Se distingue del fallo de descarga porque lo que hay que hacer es otra cosa:
# aqui la descarga fue bien y lo que falta es arreglar los datos.
$CalculoRechazado = ($LASTEXITCODE -eq 77)

if ($UniversoCompleto -and $LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "  Descargando el universo completo. Entre 20 y 45 minutos." -ForegroundColor Cyan
    Write-Host "  Puedes minimizar la ventana; no la cierres."
    & powershell -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $InstallDir 'scripts\windows\stocks.ps1') universo
}
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Listo: la portada ya muestra el mercado de verdad." -ForegroundColor Green
    $script:Resultado = 'Ok'
} elseif ($CalculoRechazado) {
    # Antes esto se anunciaba como exito: run_compute salia con codigo 0 aunque
    # la puerta de calidad lo parase, asi que el instalador decia "la portada ya
    # muestra el mercado de verdad" sin haberse calculado nada.
    Write-Host ""
    Write-Host "  Los precios se han descargado, pero NO se ha calculado nada." -ForegroundColor Yellow
    Write-Host "  Los datos tienen algun problema grave; el detalle esta arriba."
    Write-Host "  La portada seguira mostrando lo que hubiera antes."
    Write-Host ""
    Write-Host "  Para reintentar solo el calculo:" -ForegroundColor Cyan
    Write-Host "      .\scripts\windows\stocks.ps1 compute"
    $script:Resultado = 'SinCalcular'
} else {
    Write-Host "  No se ha podido descargar ahora." -ForegroundColor Yellow
    Write-Host "  El programa se abrira igualmente y lo reintentara al arrancar."
    $script:Resultado = 'SinDescargar'
}

# ---------------------------------------------------------------------------
# 8. Actualizacion automatica
# ---------------------------------------------------------------------------
Write-Step 8 8 "Programando las tareas automaticas"
# Se llama a `stocks.ps1 autostart` en vez de registrar las tareas aqui.
# Antes este bloque duplicaba la logica de programacion, y el resultado fue
# exactamente lo que pasa siempre con una copia: al anadir el ciclo del bot en
# `autostart`, el instalador siguio programando solo la actualizacion de datos.
# Quien instalaba desde cero se quedaba sin bot y sin ninguna senal de que
# faltaba algo.
try {
    & powershell -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $InstallDir 'scripts\windows\stocks.ps1') autostart
    if ($LASTEXITCODE -ne 0) { throw "codigo $LASTEXITCODE" }
} catch {
    Write-Host "  No se ha podido programar: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  Activalas luego con: .\scripts\windows\stocks.ps1 autostart"
}

Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  ================================================" -ForegroundColor Green
Write-Host "   Instalado." -ForegroundColor Green
Write-Host "  ================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Abrelo con el icono 'Stocks Tracker' del Escritorio."
Write-Host ""
# TRES estados y no un si/no. Con un booleano, "se descargo pero no se calculo"
# caia en el mismo saco que "no se pudo descargar", y el resumen final decia
# "no se han podido descargar precios reales... son DATOS DE PRUEBA" justo
# despues de que el paso 7 hubiera dicho lo contrario en pantalla. De los dos
# mensajes contradictorios, el que se queda en la memoria es el ultimo, y era el
# falso.
switch ($script:Resultado) {
    'Ok' {
        Write-Host "  Los indices muestran ya el mercado real."
    }
    'SinCalcular' {
        Write-Host "  Los precios reales SI se han descargado." -ForegroundColor Yellow
        Write-Host "  Lo que falta es el calculo: se nego a correr porque encontro" -ForegroundColor Yellow
        Write-Host "  algun problema en los datos (el detalle esta mas arriba)."
        Write-Host "  Hasta que se calcule, la portada ensena lo que hubiera antes."
        Write-Host ""
        Write-Host "  Reintenta solo el calculo con:" -ForegroundColor Cyan
        Write-Host "      .\scripts\windows\stocks.ps1 compute"
    }
    default {
        Write-Host "  ATENCION: no se han podido descargar precios reales." -ForegroundColor Yellow
        Write-Host "  Lo que veas de momento son DATOS DE PRUEBA, inventados." -ForegroundColor Yellow
        Write-Host "  El programa lo reintentara al abrirlo. El dashboard avisa en rojo"
        Write-Host "  mientras haya datos inventados."
    }
}
Write-Host ""
Write-Host "  Se actualiza solo: cada noche a las 23:15, y tambien al abrirlo si"
Write-Host "  los datos se han quedado viejos. No tienes que ejecutar nada."
Write-Host ""
Write-Host "  Para el ranking sobre las 600 empresas del universo completo"
Write-Host "  (varios minutos, una sola vez):"
Write-Host "      .\scripts\windows\stocks.ps1 ingest" -ForegroundColor Cyan
Write-Host ""

Write-Host "  A partir de ahora: doble clic en 'Stocks Tracker' del Escritorio."
Write-Host "  Ese mismo icono actualiza, descarga y abre. No hay nada mas."
Write-Host ""

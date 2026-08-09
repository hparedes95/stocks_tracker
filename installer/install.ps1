<#
.SYNOPSIS
    Instalador de Stocks Tracker para Windows.

.DESCRIPTION
    Descarga el proyecto, prepara el entorno de Python, genera datos de
    prueba y deja un acceso directo en el Escritorio.

    No hace falta tener git ni saber usar la consola: se descarga el ZIP
    del repositorio.

    Se instala en la carpeta del usuario (%LOCALAPPDATA%), no en
    Archivos de programa, para no necesitar permisos de administrador.
#>

param(
    [string]$Branch = 'claude/stock-market-monitoring-dashboard-7yf0nb',
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'StocksTracker'),
    [switch]$SkipDemo
)

$ErrorActionPreference = 'Stop'
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
Write-Step 1 6 "Comprobando Python"

function Test-PythonExe($exe, $prefix = @()) {
    <#
        Comprueba que un ejecutable es un Python 3.11-3.13 de verdad.

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
    if ($minor -lt 11 -or $minor -gt 13) { return $null }
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
        foreach ($v in @('-3.12', '-3.11', '-3.13', '-3')) {
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
    Write-Host "  No hay una version compatible (hace falta 3.11, 3.12 o 3.13)."
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
Write-Step 2 6 "Descargando el proyecto desde GitHub"

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

Write-Step 3 6 "Instalando en $InstallDir"

if (Test-Path $InstallDir) {
    # Se conservan los datos y la configuracion: reinstalar no puede borrarte
    # la cartera ni las claves del .env.
    $keep = Join-Path $temp 'keep'
    New-Item -ItemType Directory -Path $keep -Force | Out-Null
    foreach ($item in @('data', '.env', 'config')) {
        $source = Join-Path $InstallDir $item
        if (Test-Path $source) {
            Copy-Item $source -Destination $keep -Recurse -Force
            Write-Host "  Conservando $item"
        }
    }
    Remove-Item $InstallDir -Recurse -Force
}

Expand-Archive -Path $zip -DestinationPath $temp -Force
$extracted = Get-ChildItem $temp -Directory |
    Where-Object { $_.Name -like 'stocks_tracker-*' } |
    Select-Object -First 1
if (-not $extracted) { Fail "El ZIP descargado no tiene el formato esperado." $null }

Move-Item $extracted.FullName $InstallDir

$keep = Join-Path $temp 'keep'
if (Test-Path $keep) {
    Get-ChildItem $keep -Force | ForEach-Object {
        Copy-Item $_.FullName -Destination $InstallDir -Recurse -Force
    }
}
Write-Host "  Copiado"

# ---------------------------------------------------------------------------
# 4. Entorno
# ---------------------------------------------------------------------------
Write-Step 4 6 "Preparando el entorno (esto tarda un par de minutos)"

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
if (-not $SkipDemo) {
    Write-Step 5 6 "Generando datos de prueba"
    Write-Host "  Son inventados: sirven para ver la aplicacion funcionando"
    Write-Host "  sin esperar los diez minutos de la primera descarga real."
    & $Py -m stocks_tracker.ingest.run_ingest --what all --provider synthetic
    & $Py -m stocks_tracker.compute.run_compute
    & $Py -m stocks_tracker.compute.run_compute --only scores --all-presets
    & $Py -m stocks_tracker.backtest.run_backtest --tag-signals
} else {
    Write-Step 5 6 "Datos de prueba omitidos"
}

# ---------------------------------------------------------------------------
# 6. Acceso directo
# ---------------------------------------------------------------------------
Write-Step 6 6 "Creando el acceso directo"

$launcher = Join-Path $InstallDir 'Stocks Tracker.bat'
@"
@echo off
title Stocks Tracker
cd /d "%~dp0"
echo Arrancando el dashboard...
echo.
echo Se abrira solo en el navegador. Cierra esta ventana para pararlo.
echo.
start "" /b powershell -NoProfile -Command "Start-Sleep 6; Start-Process 'http://127.0.0.1:8501'"
".venv\Scripts\python.exe" -m streamlit run src/stocks_tracker/app/main.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
pause
"@ | Set-Content -Path $launcher -Encoding ASCII

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

Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  ================================================" -ForegroundColor Green
Write-Host "   Instalado." -ForegroundColor Green
Write-Host "  ================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Abrelo con el icono 'Stocks Tracker' del Escritorio."
Write-Host ""
Write-Host "  Ahora mismo lleva datos INVENTADOS. Para usar datos reales,"
Write-Host "  abre PowerShell en $InstallDir y ejecuta:"
Write-Host "      .\scripts\windows\stocks.ps1 ingest" -ForegroundColor Cyan
Write-Host "      .\scripts\windows\stocks.ps1 compute" -ForegroundColor Cyan
Write-Host "  La primera descarga tarda varios minutos."
Write-Host ""

$answer = Read-Host "  Abrir el dashboard ahora? (S/n)"
if ($answer -eq '' -or $answer -match '^[sSyY]') {
    Start-Process $launcher
}

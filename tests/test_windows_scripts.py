"""Tests de los scripts de Windows.

No se pueden ejecutar desde aqui (no hay PowerShell), asi que se comprueba lo
que si es verificable de forma estatica. Existen porque un fallo en estos
ficheros no lo detecta ningun otro test y se descubre cuando alguien intenta
instalar el programa y no puede.
"""

from __future__ import annotations

import re
import tomllib

import pytest

from stocks_tracker.core.config import project_root

PS_SCRIPTS = ["installer/install.ps1", "scripts/windows/stocks.ps1"]
BAT_SCRIPTS = ["installer/Instalar Stocks Tracker.bat",
               "scripts/windows/Ver dashboard.bat"]


def read(path: str) -> bytes:
    return (project_root() / path).read_bytes()


def text(path: str) -> str:
    return read(path).decode("utf-8")


# ---------------------------------------------------------------------------
# Rango de versiones de Python
# ---------------------------------------------------------------------------
def supported_range() -> tuple[int, int]:
    """Versiones menores soportadas segun pyproject.toml."""
    data = tomllib.loads((project_root() / "pyproject.toml").read_text("utf-8"))
    spec = data["project"]["requires-python"]
    low = int(re.search(r">=3\.(\d+)", spec).group(1))
    # `<3.15` significa que la ultima soportada es la 3.14.
    high = int(re.search(r"<3\.(\d+)", spec).group(1)) - 1
    return low, high


@pytest.mark.parametrize("script", PS_SCRIPTS)
def test_version_range_matches_pyproject(script):
    """El fallo real que motivo este test: pyproject permitia hasta 3.13 y el
    usuario tenia 3.14, asi que el instalador rechazaba su Python y luego pip
    rechazaba el paquete. Dos ficheros que no se validan entre si."""
    low, high = supported_range()
    content = text(script)

    declared_min = int(re.search(r'\$script:MinPy\s*=\s*(\d+)', content).group(1))
    declared_max = int(re.search(r'\$script:MaxPy\s*=\s*(\d+)', content).group(1))

    assert declared_min == low, f"{script}: MinPy={declared_min}, pyproject={low}"
    assert declared_max == high, f"{script}: MaxPy={declared_max}, pyproject={high}"


@pytest.mark.parametrize("script", PS_SCRIPTS)
def test_version_variables_are_declared_before_use(script):
    """En PowerShell una variable sin definir es $null, y `$minor -gt $null` es
    cierto para cualquier version: el script rechazaria TODOS los Python sin
    dar ningun error."""
    content = text(script)
    declaration = content.find('$script:MinPy =')
    usage = content.find('-lt $script:MinPy')

    assert declaration != -1, f"{script}: MinPy no se declara"
    assert usage != -1, f"{script}: MinPy no se usa"
    assert declaration < usage, f"{script}: MinPy se usa antes de declararse"


@pytest.mark.parametrize("script", PS_SCRIPTS)
def test_probes_every_supported_version_with_the_py_launcher(script):
    low, high = supported_range()
    content = text(script)
    probes = re.search(r"foreach \(\$v in @\(([^)]+)\)\)", content).group(1)
    for minor in range(low, high + 1):
        assert f"'-3.{minor}'" in probes, (
            f"{script}: no se prueba `py -3.{minor}`, que si esta soportada"
        )


# ---------------------------------------------------------------------------
# Finales de linea
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("script", BAT_SCRIPTS + PS_SCRIPTS)
def test_windows_scripts_use_crlf(script):
    """raw.githubusercontent.com sirve el blob tal cual, sin convertir finales
    de linea. Un .bat con saltos de Unix puede cortar la ejecucion en `goto`
    sin dar un error que lo explique."""
    raw = read(script)
    assert b"\r\n" in raw, f"{script} no tiene CRLF"
    assert raw.replace(b"\r\n", b"").count(b"\n") == 0, (
        f"{script} mezcla CRLF con LF sueltos"
    )
    # Normalizar dos veces produce \r\r\n, que rompe la comparacion de
    # cadenas al editar el fichero y ya provoco que un bloque entero no se
    # insertara.
    assert b"\r\r" not in raw, f"{script} tiene retornos de carro duplicados"


@pytest.mark.parametrize("script", BAT_SCRIPTS)
def test_bat_files_are_pure_ascii(script):
    """cmd.exe usa la pagina de codigos OEM: un acento en UTF-8 sale ilegible
    o rompe la linea."""
    raw = read(script)
    non_ascii = [b for b in raw if b > 127]
    assert not non_ascii, f"{script} tiene {len(non_ascii)} bytes no ASCII"


# ---------------------------------------------------------------------------
# Coherencia con el resto del proyecto
# ---------------------------------------------------------------------------
def test_gitattributes_keeps_windows_scripts_unnormalised():
    """Sin esto, git guarda los .bat con LF y la descarga directa los sirve
    rotos. Y el orden importa: gana la ULTIMA regla que coincide."""
    lines = [
        line.strip()
        for line in (project_root() / ".gitattributes").read_text("utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "* text=auto" in lines
    for pattern in ("*.bat -text", "*.ps1 -text"):
        assert pattern in lines, f"falta la regla `{pattern}`"
        assert lines.index(pattern) > lines.index("* text=auto"), (
            f"`{pattern}` va antes de `* text=auto` y queda anulada"
        )


def test_powershell_tasks_cover_the_makefile_targets():
    """El script de Windows es el sustituto del Makefile: si se anade un
    objetivo alli y no aqui, el usuario de Windows se queda sin el."""
    makefile = (project_root() / "Makefile").read_text("utf-8")
    targets = set(re.findall(r"^([a-z][a-z-]*):", makefile, re.MULTILINE))
    targets -= {"help", "setup-min", "fmt", "clean", "migrate", "ingest-demo",
                "compute-presets", "alerts-dry", "watch-test", "watch-status",
                "repair"}

    tasks = text("scripts/windows/stocks.ps1")
    equivalents = {"ingest-demo": "demo", "compute-presets": "presets",
                   "watch-test": "watchtest"}
    for target in sorted(targets):
        task = equivalents.get(target, target)
        assert f"'{task}'" in tasks, (
            f"`make {target}` no tiene equivalente en stocks.ps1"
        )


@pytest.mark.parametrize("script", PS_SCRIPTS + BAT_SCRIPTS)
def test_scripts_reference_the_right_repository(script):
    content = text(script)
    if "hparedes95" in content:
        assert "hparedes95/stocks_tracker" in content


def test_installer_never_writes_outside_the_user_folder():
    """Se instala en %LOCALAPPDATA% justamente para no pedir administrador."""
    content = text("installer/install.ps1")
    assert "LOCALAPPDATA" in content
    assert "ProgramFiles' 'StocksTracker" not in content


# ---------------------------------------------------------------------------
# El aviso de datos de prueba
# ---------------------------------------------------------------------------
def test_synthetic_warning_is_rendered_on_every_page():
    """Motivado por un fallo de confianza real: el dashboard mostraba el S&P a
    8.489 con datos del simulador mientras el mercado habia cerrado a 7.757, y
    nada en la pantalla decia que fueran inventados.

    El aviso va en main.py, fuera de la navegacion, para que salga en las nueve
    paginas sin que cada una tenga que acordarse.
    """
    main = (project_root() / "src/stocks_tracker/app/main.py").read_text("utf-8")
    assert "render_data_origin_banner()" in main, (
        "El aviso de datos sinteticos no se pinta en main.py"
    )
    assert main.index("render_data_origin_banner()") < main.index("navigation.run()"), (
        "El aviso debe pintarse ANTES del contenido de la pagina"
    )


def test_synthetic_warning_says_the_data_is_not_real():
    """El texto tiene que ser inequivoco: 'modo demo' no le dice a nadie que
    los precios no coinciden con el mercado."""
    common = (project_root() / "src/stocks_tracker/app/components/common.py").read_text("utf-8")
    banner = common[common.index("def render_data_origin_banner"):]
    banner = banner[:banner.index("def render_pending_alerts_badge")]

    for word in ("INVENTADOS", "no sirven para decidir", "ingest"):
        assert word in banner, f"El aviso no menciona '{word}'"
    assert "st.error" in banner, "El aviso debe usar st.error, no un simple caption"


# ---------------------------------------------------------------------------
# Automatizacion
# ---------------------------------------------------------------------------
def test_installer_steps_are_numbered_consistently():
    """Un contador desincronizado ("[7/6]") hace dudar de todo lo demas."""
    src = text("installer/install.ps1")
    steps = re.findall(r"Write-Step (\d+) (\d+) ", src)
    assert steps, "El instalador no numera sus pasos"

    numbers = [int(a) for a, _ in steps]
    totals = {int(b) for _, b in steps}

    assert len(totals) == 1, f"El total cambia entre pasos: {totals}"
    assert max(numbers) == totals.pop(), "El ultimo paso no coincide con el total"
    # Un mismo numero puede repetirse (dos ramas de un if), pero nunca bajar.
    assert numbers == sorted(numbers), f"Los pasos van desordenados: {numbers}"


def test_daily_update_is_scheduled_by_the_installer():
    """Lo pedido es que funcione sin tocar nada: si el instalador no programa
    la tarea, el usuario tiene que acordarse de ejecutarla."""
    src = text("installer/install.ps1")
    assert "Register-ScheduledTask" in src
    assert "StartWhenAvailable" in src, (
        "Sin StartWhenAvailable la tarea se pierde cada noche que el equipo "
        "este apagado, que en un ordenador personal son casi todas"
    )


def test_launcher_updates_before_opening():
    """La red de seguridad de la tarea nocturna: al abrir el programa se pone
    al dia si hace falta."""
    content = text("scripts/windows/Ver dashboard.bat")
    assert "update" in content, "el lanzador no actualiza antes de arrancar"
    assert content.index("update") < content.index("run"), (
        "el lanzador arranca el dashboard antes de actualizar"
    )


def test_installer_generated_launcher_also_updates():
    """El acceso directo del Escritorio es el camino que el instalador dice que
    uses, y es OTRO fichero: se genera dentro de install.ps1. Tenia el mismo
    hueco y el test anterior no lo miraba porque recorria una tupla de un solo
    elemento.
    """
    src = text("installer/install.ps1")
    start = src.index("$launcher = Join-Path")
    launcher = src[start:src.index('| Set-Content -Path $launcher', start)]

    assert "stocks.ps1" in launcher and "update" in launcher, (
        "El lanzador que genera el instalador no llama a `update`"
    )
    assert launcher.index("update") < launcher.index("streamlit run"), (
        "El lanzador arranca Streamlit antes de actualizar"
    )


def test_update_clears_synthetic_data_before_downloading():
    """Sin --drop-synthetic la actualizacion no sirve para nada: el simulador
    genera series hasta hoy, la descarga incremental las ve al dia y no trae
    nada, asi que la bandera de "datos de prueba" no se limpia nunca y cada
    arranque repite una ingesta completa inutil.
    """
    src = text("scripts/windows/stocks.ps1")
    block = src[src.index("'update' {"):src.index("'autostart' {")]
    # Sin comentarios: el que explica por que hace falta la bandera menciona su
    # nombre, y buscarla en crudo daba un falso positivo.
    code = "\n".join(line.split("#", 1)[0] for line in block.splitlines())
    assert "--drop-synthetic" in code, (
        "`update` descarga sin borrar antes los datos de prueba"
    )


@pytest.mark.parametrize("script", PS_SCRIPTS)
def test_no_escaped_backtick_where_a_continuation_was_meant(script):
    """`` es un backtick literal, no una continuacion de linea. Partia el
    comando en dos y la tarea `real` fallaba entera."""
    for number, line in enumerate(text(script).splitlines(), start=1):
        assert not line.rstrip().endswith("``"), (
            f"{script}:{number} acaba en doble backtick: "
            "eso es un backtick escapado, no una continuacion"
        )


@pytest.mark.parametrize("script", PS_SCRIPTS)
def test_native_exit_codes_do_not_throw(script):
    """Estos scripts leen $LASTEXITCODE, pero PowerShell 7.4+ lanza excepcion
    ante un codigo distinto de cero si no se desactiva."""
    assert "PSNativeCommandUseErrorActionPreference" in text(script), (
        f"{script} lee codigos de salida sin desactivar el lanzamiento de "
        "excepciones de PowerShell 7.4+"
    )


def test_staleness_check_never_opens_the_store_for_writing():
    """`needs_update()` corre justo antes de arrancar el dashboard. Si abriera
    el almacen en lectura-escritura, DuckDB rechazaria la conexion cuando el
    dashboard ya lo tiene abierto, y el lanzador leeria ese fallo como "hacen
    falta datos" lanzando una descarga completa cada vez.
    """
    src = (project_root() / "src/stocks_tracker/ingest/run_ingest.py").read_text("utf-8")
    body = src[src.index("def needs_update"):src.index("def drop_synthetic")]

    # Solo el codigo: el comentario que explica por que NO se llama a migrate()
    # menciona la palabra, y buscarla en crudo daba un falso positivo.
    code = "\n".join(
        line.split("#", 1)[0] for line in body.splitlines()
    )

    assert "migrate()" not in code, "needs_update() abre el almacen para escribir"
    assert "read_only=True" in code


def test_every_declared_task_exists_in_the_switch():
    """El fallo que motivo este test: `update` y `autostart` estaban en la
    lista de tareas validas pero no en el switch, asi que ejecutarlas caia en
    el `default` y no hacia absolutamente nada. Sin error, sin mensaje.
    """
    src = text("scripts/windows/stocks.ps1")
    declared = re.search(r"\[ValidateSet\((.*?)\)\]", src, re.S).group(1)
    tasks = set(re.findall(r"'([a-z-]+)'", declared)) - {"help"}

    implemented = set(re.findall(r"^    '([a-z-]+)' \{", src, re.MULTILINE))

    missing = tasks - implemented
    assert not missing, f"Tareas declaradas sin implementar: {sorted(missing)}"


def test_update_task_is_a_noop_when_data_is_fresh():
    """Se ejecuta en cada arranque: si no fuera instantaneo con los datos al
    dia, abrir el programa costaria minutos."""
    src = text("scripts/windows/stocks.ps1")
    block = src[src.index("'update' {"):]
    block = block[:block.index("'autostart' {")]
    assert "--check-stale" in block
    assert "if ($LASTEXITCODE -eq 0) { return }" in block


# ---------------------------------------------------------------------------
# Precios en vivo frente a precios de cierre
# ---------------------------------------------------------------------------
def test_live_prices_are_labelled_as_not_feeding_the_analysis():
    """Dos filas de numeros para los mismos indices, con valores distintos, es
    una invitacion a confundirse. La pestana en vivo tiene que decir de donde
    sale y que NO es lo que usa el ranking.
    """
    page = (project_root() / "src/stocks_tracker/app/pages/1_que_se_mueve_hoy.py"
            ).read_text("utf-8")

    assert "market_overview" in page, "no hay panel de precios en vivo"
    assert "No alimentan el" in page, (
        "la pestana en vivo no aclara que no alimenta el analisis"
    )


def test_the_live_caption_renders_before_the_iframe():
    """El widget es un iframe: si la red lo bloquea deja un hueco mudo, y es la
    primera pestana que se ve al abrir el programa. El aviso, que si se pinta
    siempre, tiene que ir antes.
    """
    page = (project_root() / "src/stocks_tracker/app/pages/1_que_se_mueve_hoy.py"
            ).read_text("utf-8")
    block = page[page.index("with live_tab:"):page.index("with close_tab:")]

    assert block.index("st.caption") < block.index("market_overview"), (
        "el aviso se pinta despues del iframe: si el iframe falla, no se ve nada"
    )


def test_closing_prices_say_which_day_they_are_from():
    page = (project_root() / "src/stocks_tracker/app/pages/1_que_se_mueve_hoy.py"
            ).read_text("utf-8")
    block = page[page.index("with close_tab:"):]
    assert "Cierre del" in block, "las tarjetas de cierre no dicen de que dia son"

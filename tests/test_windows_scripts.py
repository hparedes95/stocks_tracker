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
    assert raw.replace(b"\r\n", b"") .count(b"\n") == 0, (
        f"{script} mezcla CRLF con LF sueltos"
    )


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

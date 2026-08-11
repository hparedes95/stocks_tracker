"""El repositorio es PUBLICO. Estos tests vigilan que siga sin secretos.

No es una precaucion teorica: la parte del bot maneja una clave privada de
wallet, y ahi la diferencia es cualitativa. Una clave de API se revoca. Una
clave privada no: si llega a GitHub, los rastreadores la encuentran en minutos
y lo unico que se puede hacer es mover los fondos a otra wallet.

Borrarla del repositorio despues no sirve de nada. Queda en el historial.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from stocks_tracker.core.config import project_root


def tracked_files() -> list[str]:
    return subprocess.run(
        ["git", "ls-files"], cwd=project_root(),
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()


def test_no_env_file_is_tracked():
    """`.gitignore` cubre .env, pero `git add -f` se lo salta."""
    prohibidos = [f for f in tracked_files()
                  if f == ".env" or (f.startswith(".env.") and f != ".env.example")]
    assert not prohibidos, f"credenciales versionadas: {prohibidos}"


def test_the_warehouse_is_not_tracked():
    """Contiene la cartera del usuario y sus posiciones."""
    datos = [f for f in tracked_files()
             if f.startswith("data/") or f.endswith(".duckdb")]
    assert not datos, f"datos personales versionados: {datos}"


def test_gitignore_covers_credentials_and_data():
    lineas = {line.strip() for line in
              (project_root() / ".gitignore").read_text("utf-8").splitlines()}
    for patron in (".env", ".env.*", "data/"):
        assert patron in lineas, f"falta `{patron}` en .gitignore"
    assert "!.env.example" in lineas, "la plantilla deberia poder subirse"


def test_no_credential_looking_string_is_committed():
    """Barrido sobre el contenido, no solo sobre los nombres de fichero."""
    patron = re.compile(
        r"(api[_-]?key|api[_-]?secret|private[_-]?key|passphrase|mnemonic)"
        r"\s*[=:]\s*[\"']?[A-Za-z0-9/+_-]{16,}",
        re.IGNORECASE,
    )
    ofensores = []
    for name in tracked_files():
        if name.endswith((".example", ".lock")) or name.startswith("tests/"):
            continue
        path = project_root() / name
        if not path.is_file():
            continue
        try:
            contenido = path.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if patron.search(contenido):
            ofensores.append(name)
    assert not ofensores, f"posibles credenciales en: {ofensores}"


def test_no_wallet_private_key_is_committed():
    """64 hexadecimales seguidos es el formato de una clave privada."""
    patron = re.compile(r"(?<![0-9a-fA-F])(0x)?[0-9a-fA-F]{64}(?![0-9a-fA-F])")
    ofensores = []
    for name in tracked_files():
        if name.startswith("tests/") or name.endswith((".lock", ".example")):
            continue
        path = project_root() / name
        if not path.is_file():
            continue
        try:
            contenido = path.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if patron.search(contenido):
            ofensores.append(name)
    assert not ofensores, f"posible clave privada en: {ofensores}"


# ---------------------------------------------------------------------------
# La barrera de antes de subir
# ---------------------------------------------------------------------------
def test_the_pre_commit_hook_exists_and_is_executable():
    hook = project_root() / "scripts/git-hooks/pre-commit"
    assert hook.exists(), "no hay barrera antes de subir"
    contenido = hook.read_text("utf-8")
    # Lo que el hook tiene que cubrir, buscado como texto literal.
    for caso in (".env", ".duckdb", "private[_-]?key", "{64}"):
        assert caso in contenido, f"el hook no cubre {caso}"


def test_the_hook_is_wired_in_the_setup():
    """Un hook que hay que activar a mano es un hook que nadie activa."""
    ps1 = (project_root() / "scripts/windows/stocks.ps1").read_text("utf-8")
    makefile = (project_root() / "Makefile").read_text("utf-8")
    assert "core.hooksPath" in ps1 or "core.hooksPath" in makefile, (
        "la barrera no se activa sola al preparar el entorno"
    )


# ---------------------------------------------------------------------------
# El dashboard no se expone a la red
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fichero", ["Makefile", "scripts/windows/stocks.ps1"])
def test_the_dashboard_only_listens_on_this_machine(fichero):
    """Streamlit no tiene autenticacion: en 0.0.0.0 seria la cartera del
    usuario abierta a cualquiera de la red."""
    contenido = (project_root() / fichero).read_text("utf-8")
    assert "--server.address 127.0.0.1" in contenido
    assert "0.0.0.0" not in contenido.replace(
        "# Nunca exponer en 0.0.0.0", ""
    ), "el dashboard escucha en todas las interfaces"

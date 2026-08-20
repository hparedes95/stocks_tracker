"""Que el CI siga comprobando lo que dice comprobar.

Paso 30 del plan. Un paso borrado del workflow no rompe nada: los tests siguen
existiendo, el CI sigue saliendo verde, y simplemente deja de mirarse una
familia entera. Es la forma mas silenciosa que hay de perder una proteccion.

Se comprueba tambien que los ficheros que nombra existan de verdad. Un `pytest`
sobre una ruta que ya no esta no falla: no recoge nada y devuelve cero, asi que
el paso sale en verde para siempre sin ejecutar ni un test.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
import yaml

from stocks_tracker.core.config import project_root

# Familias que NO pueden desaparecer del CI. Es una lista explicita y no
# deducida: si se dedujera del propio fichero, borrar un paso borraria tambien
# la comprobacion de que ese paso existe, que es justo lo que hay que impedir.
PASOS_OBLIGATORIOS = {
    "Lint",
    "Test",
    "Regresion financiera",
    "Calidad de datos y corrupcion",
    "Proveedores y consenso",
    "Fuga temporal y supervivencia",
    "Secretos",
}


@pytest.fixture(scope="module")
def workflow() -> dict:
    ruta = project_root() / ".github/workflows/ci.yml"
    assert ruta.exists(), "no hay workflow de CI"
    return yaml.safe_load(ruta.read_text("utf-8"))


def pasos(workflow: dict) -> list[dict]:
    return [p for p in workflow["jobs"]["test"]["steps"] if "run" in p]


def test_estan_todas_las_familias(workflow):
    nombres = {p.get("name") for p in pasos(workflow)}

    faltan = PASOS_OBLIGATORIOS - nombres
    assert not faltan, (
        f"el CI ha dejado de comprobar: {sorted(faltan)}. Borrar un paso no "
        "rompe nada y no se nota: los tests siguen ahi y el CI sigue verde."
    )


def test_todos_los_pasos_se_ejecutan_aunque_uno_falle(workflow):
    """Con el corte al primer fallo, arreglar el estilo y descubrir entonces
    que ademas habia una regresion financiera son dos viajes donde deberia
    haber uno."""
    for paso in pasos(workflow):
        if paso.get("name") == "Install":
            continue
        assert paso.get("if") == "always()", (
            f"el paso '{paso.get('name')}' se salta cuando otro falla"
        )


def test_los_ficheros_que_nombra_el_ci_existen(workflow):
    """Un `pytest` sobre una ruta que ya no esta no falla: no recoge nada y
    devuelve cero. El paso sale en verde para siempre sin ejecutar un test."""
    raiz = project_root()
    faltan = []
    for paso in pasos(workflow):
        for palabra in shlex.split(paso["run"].replace("\n", " ")):
            if palabra.startswith("tests/") and not (raiz / palabra).exists():
                faltan.append(f"{paso.get('name')}: {palabra}")

    assert not faltan, (
        "el CI apunta a ficheros que no existen y esos pasos pasan sin "
        "ejecutar nada:\n  " + "\n  ".join(faltan)
    )


def test_el_paso_general_ejecuta_la_suite_entera(workflow):
    """Las familias destacadas son un subconjunto: existen para que se vean con
    nombre propio, no para sustituir a la suite."""
    general = [p for p in pasos(workflow) if p.get("name") == "Test"]

    assert general, "no queda ningun paso que ejecute todos los tests"
    assert "pytest -q" in general[0]["run"]
    assert "tests/" not in general[0]["run"], (
        "el paso general se ha quedado limitado a unos ficheros concretos"
    )


def test_el_ci_no_ignora_fallos_con_continue_on_error(workflow):
    """`continue-on-error` deja el check en verde con el paso rojo dentro. Es
    `if: always()` con otro nombre y consecuencias opuestas."""
    for paso in pasos(workflow):
        assert not paso.get("continue-on-error"), (
            f"'{paso.get('name')}' puede fallar sin que el CI se entere"
        )


def test_el_lint_cubre_todo_el_repositorio(workflow):
    lint = [p for p in pasos(workflow) if p.get("name") == "Lint"][0]

    assert lint["run"].strip() == "ruff check .", (
        "el lint se ha limitado a una carpeta"
    )


def test_el_workflow_no_pide_permisos_de_escritura(workflow):
    """El repositorio es publico. Un workflow con permiso de escritura y un
    paso que ejecuta codigo de un PR ajeno es una via de entrada."""
    permisos = workflow.get("permissions", {})

    assert permisos.get("contents") == "read"
    assert set(permisos) <= {"contents"}, (
        f"el CI pide permisos de mas: {sorted(set(permisos) - {'contents'})}"
    )


def test_hay_un_test_por_cada_modulo_de_verificacion():
    """Guardarrail sobre el proyecto entero, no sobre el CI.

    Los modulos que existen PARA comprobar cosas son los que mas silenciosamente
    pueden quedarse sin cobertura: si uno deja de funcionar, no rompe ninguna
    pantalla y solo deja de detectar.
    """
    raiz = project_root()
    verificadores = {
        "quality", "consensus", "consistency", "corporate", "integrity",
        "lineage", "membership", "quarantine", "reconcile", "golden",
        "multiple_testing", "experiments",
    }
    contenido = "\n".join(
        p.read_text("utf-8") for p in (raiz / "tests").glob("test_*.py")
    )

    sin_cubrir = [m for m in sorted(verificadores) if f"import {m}" not in contenido
                  and f"{m}." not in contenido]
    assert not sin_cubrir, f"modulos de verificacion sin ningun test: {sin_cubrir}"


def test_el_workflow_es_yaml_valido():
    """Un YAML mal formado deja a GitHub sin ejecutar NADA, y en la pagina del
    repositorio eso no se ve como un fallo: se ve como que no hay checks."""
    ruta = Path(project_root() / ".github/workflows/ci.yml")

    datos = yaml.safe_load(ruta.read_text("utf-8"))

    assert isinstance(datos, dict)
    assert datos["jobs"]["test"]["steps"]

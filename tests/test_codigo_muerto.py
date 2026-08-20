"""Codigo de verificacion que no ejecuta nadie.

EL PATRON CONTRA EL QUE VA ESTE FICHERO

`data_quality` estuvo dos meses siendo una tabla vacia. `corporate_actions`,
otros dos. Las dos existian, las dos estaban en el esquema, las dos tenian su
modulo escrito, y ninguna de las dos se llenaba nunca porque nadie llamaba a lo
que las llenaba. Ningun test fallaba: los modulos funcionaban perfectamente en
sus propias pruebas.

Y es un fallo con una forma muy concreta: el codigo de VERIFICACION es el que
mas silenciosamente puede quedarse desconectado. Si una pantalla deja de
llamarse, se ve un hueco. Si una comprobacion deja de llamarse, no se ve nada
—porque lo que hace una comprobacion cuando todo va bien es exactamente nada—.
La diferencia entre "no encuentra problemas" y "no se ejecuta" no se nota
mirando.

QUE COMPRUEBA, EXACTAMENTE

Que ninguna funcion publica de los modulos de verificacion este SIN REFERENCIAR
en todo `src/` y `scripts/`. Los tests no cuentan: una funcion cuyo unico
llamante es su propio test esta demostrando que funciona algo que no se ejecuta.

Se mira el arbol sintactico y no el texto. Con una busqueda de texto, un nombre
mencionado en un `__all__`, en un comentario o dentro de una cadena contaria
como uso, y eso es justo lo que hace un `__all__`: dar apariencia de uso a lo
que no lo tiene. Al escribir esta comprobacion con texto plano encontraba dos
huerfanas; con el arbol, cuatro.

QUE HACER CUANDO FALLA

Tres salidas, y las tres son buenas:

1. Conectarla donde hacia falta. Suele ser esto: la funcion existe porque
   alguien vio una comprobacion que faltaba, y se quedo a medias.
2. Hacerla privada con `_`. Si es un detalle interno, no tiene por que ser API.
3. Borrarla. Una comprobacion que nadie ejecuta no protege de nada, y ademas
   estorba: al leerla parece que esa parte esta cubierta.

Lo que NO vale es anadirla a una lista de excepciones. Este test existe porque
el proyecto ya se creyo cubierto por codigo que no corria.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from stocks_tracker.core.config import project_root

# Los modulos que existen PARA comprobar cosas. Es la misma lista que vigila
# `test_ci.test_hay_un_test_por_cada_modulo_de_verificacion`, que pregunta lo
# complementario: aquel mira si tienen tests, este si tienen llamantes. Un
# modulo puede tener las dos cosas mal a la vez y ninguna de las dos preguntas
# por separado lo detecta.
VERIFICADORES = (
    "quality", "consensus", "consistency", "corporate", "integrity", "lineage",
    "membership", "quarantine", "reconcile", "golden", "multiple_testing",
    "experiments", "audit",
)


def _fichero(nombre: str) -> pathlib.Path:
    encontrados = sorted((project_root() / "src").rglob(f"{nombre}.py"))
    assert len(encontrados) == 1, (
        f"se esperaba un solo {nombre}.py y hay {len(encontrados)}: {encontrados}"
    )
    return encontrados[0]


def _fuentes() -> list[pathlib.Path]:
    """Todo el codigo que se ejecuta de verdad. Los tests NO estan aqui."""
    raiz = project_root()
    return [p for p in sorted((raiz / "src").rglob("*.py"))
            if "__pycache__" not in p.parts] + \
           [p for p in sorted((raiz / "scripts").rglob("*.py"))
            if "__pycache__" not in p.parts]


def _nombres_usados() -> set[str]:
    """Todo identificador que aparece USADO en el codigo de produccion.

    Del arbol sintactico, no del texto: asi un nombre dentro de un `__all__`, de
    un comentario o de un docstring no cuenta como llamada. Es la diferencia
    entre "alguien lo escribe" y "alguien lo ejecuta".
    """
    usados: set[str] = set()
    for ruta in _fuentes():
        for nodo in ast.walk(ast.parse(ruta.read_text("utf-8"))):
            if isinstance(nodo, ast.Name):
                usados.add(nodo.id)
            elif isinstance(nodo, ast.Attribute):
                usados.add(nodo.attr)
            elif isinstance(nodo, (ast.Import, ast.ImportFrom)):
                usados.update(alias.name for alias in nodo.names)
    return usados


def _publicas(ruta: pathlib.Path) -> list[str]:
    """Funciones publicas del nivel superior del modulo."""
    return [n.name for n in ast.parse(ruta.read_text("utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not n.name.startswith("_")]


@pytest.mark.parametrize("modulo", VERIFICADORES)
def test_ninguna_comprobacion_se_queda_sin_llamante(modulo):
    usados = _nombres_usados()
    huerfanas = [f for f in _publicas(_fichero(modulo)) if f not in usados]

    assert not huerfanas, (
        f"en `{modulo}` hay comprobaciones que no ejecuta nadie fuera de sus "
        f"propios tests: {huerfanas}. Una comprobacion desconectada no protege "
        "de nada y ademas estorba, porque al leerla parece que esa parte esta "
        "cubierta. Conectala, hazla privada con `_`, o borrala."
    )


def test_la_lista_de_verificadores_apunta_a_ficheros_que_existen():
    """Si un modulo se renombrara y esta lista no, el test de arriba pasaria a
    no vigilar nada sin decirlo."""
    for modulo in VERIFICADORES:
        assert _fichero(modulo).exists()


def test_el_detector_veria_una_funcion_muerta():
    """Contraprueba. Un detector que no detecta nada pasa igual de verde que
    uno que funciona, y los dos se leen igual desde fuera.

    Se le da un modulo de mentira con una funcion que no llama nadie y se
    comprueba que la senala. Sin esto, `_nombres_usados()` podria estar
    devolviendo el universo entero de identificadores del proyecto —cosa que
    casi hace— y el test de arriba no fallaria jamas.
    """
    usados = _nombres_usados()

    assert "funcion_que_no_existe_en_ninguna_parte" not in usados
    # Y el contraste: algo que si se usa de verdad.
    assert "revisar" in usados, (
        "`integrity.revisar` se llama desde el dashboard; si no aparece, el "
        "detector no esta leyendo el codigo que cree que lee"
    )

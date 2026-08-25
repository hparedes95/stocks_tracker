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
    # `advice_store` entra aqui despues de que su `guardar()` se quedara sin
    # llamante durante un commit entero. La pagina calculaba las
    # recomendaciones y las pintaba, pero nadie las escribia: el marcador de
    # aciertos habria seguido vacio para siempre y la seccion entera habria
    # sido un horoscopo sin que nada fallara.
    "advice_store",
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


def _usados_de(modulo: str) -> set[str]:
    """Nombres usados COMO SUYOS: `modulo.func` o `from ... import func`.

    POR QUE NO BASTA CON BUSCAR EL NOMBRE SUELTO

    La primera version de este detector juntaba todos los identificadores del
    codigo en un solo conjunto. Con eso, `guardar` contaba como usado si
    CUALQUIER modulo tenia un `guardar` vivo, y hay cuatro que lo tienen.

    Paso de verdad: `advice_store.guardar` se quedo sin llamante durante un
    commit entero y este fichero no dijo nada, porque `quality.guardar` si
    tenia. La pagina calculaba las recomendaciones y las pintaba, pero nadie
    las escribia: el marcador de aciertos se habria quedado vacio para siempre
    y la seccion entera habria sido un horoscopo, sin que fallara nada.

    La alternativa que descarte fue prohibir los nombres compartidos. Habria
    obligado a renombrar ocho modulos que funcionan, y el problema no era el
    nombre: era que el detector no miraba de quien.
    """
    usados: set[str] = set()
    for ruta in _fuentes():
        arbol = ast.parse(ruta.read_text("utf-8"))
        # Con que alias se ha importado aqui: `from ..core import quality as q`
        # tiene que contar igual que `quality`.
        alias_del_modulo = {modulo}
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.ImportFrom):
                for a in nodo.names:
                    if a.name == modulo and a.asname:
                        alias_del_modulo.add(a.asname)
                    elif a.name != modulo and nodo.module \
                            and nodo.module.endswith(modulo):
                        # `from ..core.quality import evaluar`: la funcion se
                        # usa suelta, pero se sabe de quien es.
                        usados.add(a.asname or a.name)
            elif isinstance(nodo, ast.Import):
                for a in nodo.names:
                    if a.name.endswith(f".{modulo}") and a.asname:
                        alias_del_modulo.add(a.asname)

        propio = ruta.stem == modulo
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Attribute) and isinstance(nodo.value, ast.Name):
                if nodo.value.id in alias_del_modulo:
                    usados.add(nodo.attr)
            elif propio and isinstance(nodo, ast.Name):
                # Dentro de su propio fichero, la llamada es un nombre suelto.
                # Una funcion que solo usa `evaluar` por dentro NO esta muerta:
                # esta alcanzada, que es lo que este detector persigue. Sin esta
                # rama saltaban diez falsos positivos en `quality` y el detector
                # se habria acabado desactivando por pesado, que es como se
                # pierde una comprobacion util.
                usados.add(nodo.id)
    return usados


@pytest.mark.parametrize("modulo", VERIFICADORES)
def test_ninguna_comprobacion_se_queda_sin_llamante(modulo):
    fichero = _fichero(modulo)
    usados = _usados_de(modulo)
    # Lo que el propio modulo usa por dentro no cuenta como llamante: una
    # funcion publica que solo se llama a si misma sigue estando muerta.
    huerfanas = [f for f in _publicas(fichero) if f not in usados]

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


def test_el_detector_no_se_deja_enganar_por_un_nombre_de_otro_modulo(tmp_path,
                                                                     monkeypatch):
    """LA CONTRAPRUEBA DEL ARREGLO, y el agujero que costo un commit entero.

    La primera version juntaba todos los identificadores del codigo en un solo
    conjunto. Con eso, `guardar` contaba como usado si CUALQUIER modulo tenia un
    `guardar` vivo, y hay cuatro que lo tienen.

    Paso de verdad: `advice_store.guardar` se quedo sin llamante y este fichero
    no dijo nada, porque `quality.guardar` si tenia. La pagina calculaba
    recomendaciones y las pintaba, nadie las escribia, y el marcador de aciertos
    se habria quedado vacio para siempre sin que fallara ni un test.

    Aqui se monta ese caso exacto: un modulo cuya funcion no llama nadie, y otro
    con una funcion del MISMO nombre que si se usa.
    """
    import ast as _ast

    muerto = tmp_path / "src" / "muerto.py"
    muerto.parent.mkdir(parents=True)
    muerto.write_text("def guardar(x):\n    return x\n", "utf-8")
    (tmp_path / "src" / "vivo.py").write_text(
        "def guardar(x):\n    return x\n", "utf-8")
    (tmp_path / "src" / "usa.py").write_text(
        "from . import vivo\n\n\ndef f(x):\n    return vivo.guardar(x)\n", "utf-8")

    fuentes = sorted((tmp_path / "src").rglob("*.py"))
    monkeypatch.setattr("tests.test_codigo_muerto._fuentes", lambda: fuentes)

    publicas = [n.name for n in _ast.parse(muerto.read_text("utf-8")).body
                if isinstance(n, _ast.FunctionDef)]

    assert "guardar" not in _usados_de("muerto"), (
        "el detector cuenta como vivo un `guardar` que pertenece a otro modulo"
    )
    assert "guardar" in _usados_de("vivo"), (
        "el detector no reconoce una llamada legitima; seria inservible"
    )
    assert publicas == ["guardar"]

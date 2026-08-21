"""Un DataFrame dentro de `df.attrs` tumba la descarga entera.

LA AVERIA, TAL Y COMO SALIO EN LA MAQUINA DEL USUARIO

    File "...providers\\chain.py", line 94, in fetch_ohlcv
        result = result.drop(columns=["source"]).merge(
    ...
    File "...pandas\\core\\generic.py", line 6170, in __finalize__
        have_same_attrs = all(obj.attrs == attrs for obj in objs[1:])
    ValueError: The truth value of a DataFrame is ambiguous.

La ingesta de precios reventaba entera. Y el sintoma que veia el usuario no era
"ha fallado la descarga": era que el dashboard llevaba dias sin avanzar y la
pantalla decia CERO descargas fallidas, porque el proceso moria antes de llegar
a escribir la fila de fallo.

EL MECANISMO, QUE ES LO QUE HAY QUE RECORDAR

`df.attrs` es un diccionario que pandas ARRASTRA de una operacion a otra. Para
decidir si puede arrastrarlo, compara los `attrs` de los dos operandos con `==`.

Comparar dos DataFrames con `==` no devuelve True ni False: devuelve otro
DataFrame, elemento a elemento. Y `all(...)` intenta evaluarlo como condicion,
que es justo lo que pandas prohibe.

Asi que meter un DataFrame en `attrs` NO falla al meterlo. Falla mucho despues,
en el primer `merge` o `concat` que toque esa tabla, con un error que no
menciona `attrs` por ningun sitio y que apunta a una linea que no tiene la
culpa. Por eso hacia falta un test y no un comentario.

LA REGLA: en `attrs` solo entran cosas que se comparan con `==` sin drama.
Numeros, cadenas, listas y diccionarios de esos. Nunca un DataFrame ni un array
de numpy.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core import corporate, db
from stocks_tracker.providers import chain as chain_mod
from stocks_tracker.providers.base import normalize_ohlcv
from stocks_tracker.providers.yfinance_provider import _extraer_acciones

HOY = date(2026, 8, 20)


def _ohlcv(ticker: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "ticker": ticker, "date": HOY, "open": 100.0, "high": 101.0,
        "low": 99.0, "close": 100.0, "adj_close": 100.0, "volume": 1_000,
    }])


class _Proveedor:
    """Un proveedor de mentira que devuelve precios y eventos."""

    def __init__(self, name: str, tickers: list[str], eventos: list | None = None):
        self.name = name
        self._tickers = tickers
        self._eventos = eventos if eventos is not None else []

    def supports(self, ticker: str) -> bool:  # noqa: ARG002
        return True

    def fetch_ohlcv(self, tickers, start, end, interval="1d"):  # noqa: ARG002
        mios = [t for t in tickers if t in self._tickers]
        df = normalize_ohlcv(
            pd.concat([_ohlcv(t) for t in mios], ignore_index=True)
            if mios else pd.DataFrame(),
            self.name,
        )
        df.attrs["failed_tickers"] = [t for t in tickers if t not in mios]
        df.attrs["requests_used"] = 1
        df.attrs["corporate_actions"] = self._eventos
        return df


# ---------------------------------------------------------------------------
# La averia
# ---------------------------------------------------------------------------
def test_la_cadena_no_revienta_con_eventos_en_los_attrs():
    """EL CASO EXACTO. Dos proveedores, el segundo releva al primero, y la
    cadena hace el `merge` que destapaba el fallo."""
    eventos = [{"ticker": "AAA", "date": HOY, "action_type": "dividend",
                "value": 0.5}]
    cadena = chain_mod.ChainPriceProvider([
        _Proveedor("uno", ["AAA"], eventos),
        _Proveedor("dos", ["BBB"], []),
    ])

    df = cadena.fetch_ohlcv(["AAA", "BBB"], HOY, HOY)

    assert set(df["ticker"]) == {"AAA", "BBB"}
    assert set(df["source"]) == {"uno", "dos"}


def test_un_dataframe_en_los_attrs_habria_reventado():
    """CONTRAPRUEBA, y es la que da valor al test de arriba.

    Se reproduce a mano lo que hacia el codigo viejo —meter un DataFrame en
    `attrs`— y se comprueba que pandas revienta de verdad. Sin esto, el test
    anterior podria estar pasando porque el `merge` ya no se ejecuta, no porque
    el fallo este arreglado.
    """
    izquierda = pd.DataFrame({"ticker": ["AAA"], "date": [HOY], "x": [1]})
    derecha = pd.DataFrame({"ticker": ["AAA"], "date": [HOY], "source": ["uno"]})
    izquierda.attrs["corporate_actions"] = pd.DataFrame({"a": [1, 2]})
    derecha.attrs["corporate_actions"] = pd.DataFrame({"a": [3, 4]})

    with pytest.raises(ValueError, match="truth value of a DataFrame"):
        izquierda.merge(derecha, on=["ticker", "date"], how="left")


def test_los_eventos_salen_como_lista_y_no_como_dataframe():
    """La regla, comprobada en el sitio donde se genera."""
    frame = pd.DataFrame({
        "ticker": ["AAA", "AAA"], "date": [HOY, HOY],
        "dividends": [0.0, 0.5], "stock_splits": [0.0, 0.0],
    })

    salida = _extraer_acciones([frame])

    assert isinstance(salida, list)
    assert salida and isinstance(salida[0], dict)
    assert salida[0]["action_type"] == "dividend"


def test_ningun_proveedor_mete_algo_no_comparable_en_los_attrs():
    """Guardarrail sobre TODOS los proveedores, presentes y futuros.

    El fallo no estaba en la cadena: estaba en lo que un proveedor le metia. Con
    un test solo sobre la cadena, el proximo proveedor que guarde un DataFrame
    en `attrs` vuelve a tumbar la ingesta y nadie se entera hasta que pasa en
    una maquina de verdad.
    """
    import ast

    from stocks_tracker.core.config import project_root

    prohibidos = []
    for ruta in sorted((project_root() / "src/stocks_tracker/providers").glob("*.py")):
        arbol = ast.parse(ruta.read_text("utf-8"))
        for nodo in ast.walk(arbol):
            # `x.attrs["algo"] = <llamada>` donde la llamada sea a pd.DataFrame
            # o a algo que devuelva uno es lo que hay que mirar; aqui se caza el
            # caso literal, que es el que hubo.
            if not isinstance(nodo, ast.Assign):
                continue
            for destino in nodo.targets:
                if (isinstance(destino, ast.Subscript)
                        and isinstance(destino.value, ast.Attribute)
                        and destino.value.attr == "attrs"):
                    fuente = ast.unparse(nodo.value)
                    if "DataFrame(" in fuente or "pd.concat" in fuente:
                        prohibidos.append(f"{ruta.name}: {ast.unparse(nodo)}")

    assert not prohibidos, (
        "un DataFrame en `attrs` tumba la ingesta en el primer merge, con un "
        f"error que no menciona `attrs`: {prohibidos}"
    )


def test_los_attrs_de_los_proveedores_se_comparan_sin_reventar():
    """La propiedad de verdad, y no la forma de escribirla.

    El test de arriba mira el codigo; este mira el comportamiento. Un valor que
    no se pueda comparar con `==` sin ambiguedad no puede viajar en `attrs`.
    """
    df = _Proveedor("uno", ["AAA"], [{"ticker": "AAA"}]).fetch_ohlcv(
        ["AAA"], HOY, HOY)
    otro = _Proveedor("uno", ["AAA"], [{"ticker": "AAA"}]).fetch_ohlcv(
        ["AAA"], HOY, HOY)

    for clave, valor in df.attrs.items():
        assert not isinstance(valor, (pd.DataFrame, pd.Series, np.ndarray)), (
            f"attrs[{clave!r}] es un {type(valor).__name__}"
        )
    # Y la comparacion que hace pandas por dentro tiene que dar un booleano.
    assert bool(df.attrs == otro.attrs) in (True, False)


# ---------------------------------------------------------------------------
# Y que los eventos lleguen de verdad al almacen
# ---------------------------------------------------------------------------
@pytest.fixture
def almacen(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub


def test_la_cadena_propaga_los_dividendos(almacen):
    """EL SEGUNDO FALLO DEL MISMO SITIO, y explica por que `corporate_actions`
    seguia vacia.

    La cadena construye un DataFrame nuevo con `concat`, y `attrs` no viaja
    solo. Los dividendos que yfinance SI estaba trayendo se tiraban a la basura
    una linea antes de guardarlos: el trabajo de pedirlos se hacia y el
    resultado se perdia.
    """
    eventos = [
        {"ticker": "AAA", "date": HOY, "action_type": "dividend", "value": 0.5},
        {"ticker": "BBB", "date": HOY, "action_type": "split", "value": 2.0},
    ]
    cadena = chain_mod.ChainPriceProvider([
        _Proveedor("uno", ["AAA"], eventos[:1]),
        _Proveedor("dos", ["BBB"], eventos[1:]),
    ])

    df = cadena.fetch_ohlcv(["AAA", "BBB"], HOY, HOY)

    assert df.attrs["corporate_actions"], (
        "la cadena se ha comido los dividendos que traian los proveedores"
    )
    with db.connect() as conn:
        assert corporate.guardar(conn, df.attrs["corporate_actions"]) == 2
        guardados = corporate.leer(conn)
    assert set(guardados["action_type"]) == {"dividend", "split"}


def test_guardar_acepta_lista_y_dataframe(almacen):
    """Las dos formas, porque los proveedores mandan lista y los tests y el
    codigo antiguo mandan DataFrame."""
    filas = [{"ticker": "AAA", "date": HOY, "action_type": "dividend", "value": 1.0}]

    with db.connect() as conn:
        assert corporate.guardar(conn, filas) == 1
        assert corporate.guardar(conn, pd.DataFrame(filas)) == 0   # ya estaba
        assert corporate.guardar(conn, []) == 0
        assert corporate.guardar(conn, None) == 0

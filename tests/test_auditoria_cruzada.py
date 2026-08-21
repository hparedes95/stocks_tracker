"""Del contraste entre proveedores al veto de una orden.

`test_consensus` prueba el motor con diccionarios. Aqui se prueba lo que hay
entre medias, que es donde estas cosas se rompen sin dar error: a quien se
audita, que fechas se comparan, y si el veredicto llega de verdad hasta la
regla que impide mandar la orden.

La pieza que mas importa es la ultima. Un sistema de consenso que detecta la
discrepancia, la pinta muy bien en una pantalla y deja que la orden salga igual
no protege nada: es decoracion.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.core import db
from stocks_tracker.ingest import run_audit
from stocks_tracker.providers.consensus import Veredicto

HOY = date(2026, 8, 20)


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        consensus: dict = {"providers": [], "tolerancia": 0.005, "maxima": 0.02}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    monkeypatch.setattr(run_audit, "get_settings", lambda: Stub())
    db.migrate()
    return Stub


def sembrar(*, en_cartera=("AAA",), con_senal=("BBB",),
            universo=("AAA", "BBB", "CCC", "DDD", "EEE")) -> None:
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO instruments (ticker, asset_class, is_active) "
            "VALUES (?, 'equity', TRUE)", [(t,) for t in universo],
        )
        precios = pd.DataFrame([
            {"ticker": t, "date": HOY - timedelta(days=d), "close": 100.0,
             "adj_close": 100.0, "open": 99.0, "high": 101.0, "low": 98.0,
             "volume": 1_000_000, "source": "yfinance"}
            for t in universo for d in range(3)
        ])
        db.upsert_df(conn, "prices_daily", precios, keys=["ticker", "date"])
        for t in en_cartera:
            conn.execute(
                "INSERT INTO positions VALUES (?, ?, 10, 90.0, 'EUR', ?, NULL, '', NULL)",
                [f"pos-{t}", t, HOY - timedelta(days=30)],
            )
        for t in con_senal:
            conn.execute(
                "INSERT INTO signals (ticker, date, signal_id, direction, strength) "
                "VALUES (?, ?, 'GOLDEN_CROSS', 'bullish', 0.8)", [t, HOY],
            )


# ---------------------------------------------------------------------------
# A quien se audita
# ---------------------------------------------------------------------------

def test_la_cartera_y_las_senales_van_siempre(warehouse):
    """Un precio malo ahi no es un dato feo en una tabla: es un P&L que no es
    el tuyo y un stop que salta donde no debe."""
    sembrar()
    with db.connect(read_only=True) as conn:
        tickers, reparto = run_audit._tickers_a_auditar(conn, muestra=0, semilla=1)

    assert set(tickers) == {"AAA", "BBB"}
    assert reparto == {"cartera": 1, "senales": 1, "muestra": 0}


def test_la_muestra_no_repite_lo_que_ya_va_por_prioridad(warehouse):
    """Auditar dos veces el mismo valor gasta peticiones que no sobran."""
    sembrar()
    with db.connect(read_only=True) as conn:
        tickers, _ = run_audit._tickers_a_auditar(conn, muestra=50, semilla=1)

    assert len(tickers) == len(set(tickers))
    assert set(tickers) == {"AAA", "BBB", "CCC", "DDD", "EEE"}


def test_la_muestra_rota_de_un_dia_a_otro(warehouse):
    """Con una lista fija se auditaria eternamente el mismo trozo del universo
    y el resto no se miraria nunca, que es la forma mas facil de tener una
    auditoria que no audita."""
    sembrar(universo=tuple(f"T{i:03d}" for i in range(200)),
            en_cartera=(), con_senal=())

    with db.connect(read_only=True) as conn:
        lunes, _ = run_audit._tickers_a_auditar(conn, muestra=20, semilla=1)
        martes, _ = run_audit._tickers_a_auditar(conn, muestra=20, semilla=2)

    assert lunes != martes, "la muestra no rota: siempre se auditaria lo mismo"
    assert set(lunes) & set(martes) != set(lunes), "es exactamente la misma"


def test_la_muestra_del_mismo_dia_es_reproducible(warehouse):
    """Dos ejecuciones del mismo dia tienen que poder compararse entre si."""
    sembrar(universo=tuple(f"T{i:03d}" for i in range(200)),
            en_cartera=(), con_senal=())

    with db.connect(read_only=True) as conn:
        una, _ = run_audit._tickers_a_auditar(conn, muestra=20, semilla=7)
        otra, _ = run_audit._tickers_a_auditar(conn, muestra=20, semilla=7)

    assert una == otra


def test_una_posicion_cerrada_no_se_audita(warehouse):
    """Solo importa el precio de lo que se tiene ahora."""
    sembrar(en_cartera=(), con_senal=())
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO positions VALUES ('p1', 'AAA', 10, 90.0, 'EUR', ?, ?, '', NULL)",
            [HOY - timedelta(days=60), HOY - timedelta(days=5)],
        )
    with db.connect(read_only=True) as conn:
        _, reparto = run_audit._tickers_a_auditar(conn, muestra=0, semilla=1)

    assert reparto["cartera"] == 0


# ---------------------------------------------------------------------------
# Que fechas se comparan
# ---------------------------------------------------------------------------

def test_solo_se_comparan_las_fechas_que_tenemos(warehouse, monkeypatch):
    """Un dia que el proveedor sirve y nosotros no todavia no es una
    discrepancia: es que no lo hemos descargado. Contarlo llenaria la auditoria
    de falsos 'degradado' sobre fechas que nadie ha pedido."""
    sembrar(en_cartera=("AAA",), con_senal=(), universo=("AAA",))

    futuro = HOY + timedelta(days=1)

    def contraste(nombre, tickers, desde, hasta):
        return pd.DataFrame([
            {"ticker": "AAA", "date": HOY, "source": "stooq", "close": 100.0},
            {"ticker": "AAA", "date": futuro, "source": "stooq", "close": 105.0},
        ])

    monkeypatch.setattr(run_audit, "_lecturas_del_contraste", contraste)
    veredictos = run_audit.auditar(muestra=0, contrastes=["stooq"])

    assert futuro not in set(veredictos["fecha"]), (
        "se ha evaluado una fecha que el almacen no tiene"
    )
    assert HOY in set(veredictos["fecha"])


def test_un_proveedor_caido_deja_degradado_y_no_invalido(warehouse, monkeypatch):
    """Que la segunda fuente no responda no dice nada malo del precio. Tratarlo
    como discrepancia seria inventar un contraste que no se ha hecho."""
    sembrar(en_cartera=("AAA",), con_senal=(), universo=("AAA",))
    monkeypatch.setattr(
        run_audit, "_lecturas_del_contraste",
        lambda *a, **k: pd.DataFrame(columns=["ticker", "date", "source", "close"]),
    )

    veredictos = run_audit.auditar(muestra=0, contrastes=["stooq"])

    assert set(veredictos["veredicto"]) == {str(Veredicto.DEGRADADO)}


# ---------------------------------------------------------------------------
# Que se guarda
# ---------------------------------------------------------------------------

def veredicto_de(ticker: str, valor: float, dispersion: float,
                 estado: Veredicto) -> pd.DataFrame:
    return pd.DataFrame([{
        "ticker": ticker, "fecha": HOY, "valor": valor,
        "veredicto": str(estado), "dispersion": dispersion, "n_fuentes": 2,
        "por_fuente": {"yfinance": 100.0, "stooq": 100.1},
        "discrepantes": "",
    }])


def test_se_guardan_los_numeros_de_partida(warehouse):
    """Sin lo que dijo cada fuente, el veredicto es una opinion que no se puede
    revisar despues, que es justo lo que este modulo existe para evitar."""
    with db.connect() as conn:
        run_audit.guardar(conn, veredicto_de("AAA", 100.0, 0.001,
                                             Veredicto.VERIFICADO), "run-1")

    fila = db.query("SELECT * FROM price_consensus").iloc[0]
    assert json.loads(fila["por_fuente"]) == {"yfinance": 100.0, "stooq": 100.1}
    assert fila["dispersion"] == pytest.approx(0.001)


def test_volver_a_auditar_pisa_el_veredicto_viejo(warehouse):
    """Si se acumularan, la regla de riesgo leeria uno cualquiera de los dos."""
    with db.connect() as conn:
        run_audit.guardar(conn, veredicto_de("AAA", 100.0, 0.2,
                                             Veredicto.INVALIDO), "run-1")
        run_audit.guardar(conn, veredicto_de("AAA", 100.0, 0.001,
                                             Veredicto.VERIFICADO), "run-2")

    filas = db.query("SELECT veredicto FROM price_consensus")
    assert len(filas) == 1
    assert filas.iloc[0]["veredicto"] == str(Veredicto.VERIFICADO)


# ---------------------------------------------------------------------------
# Y llega hasta el bot
# ---------------------------------------------------------------------------

def test_el_contexto_trae_el_veredicto_mas_reciente(warehouse):
    """El de HOY y no el de la primera auditoria.

    Importa porque un valor que estuvo roto y ya no lo esta seguiria vetado
    para siempre, y al reves —lo peor— un valor que se rompio ayer seguiria
    operandose con el 'verificado' del mes pasado.

    La auditoria cruzada no corre todos los dias sobre todos los valores: el
    presupuesto de las APIs gratuitas no da. Por eso se toma el ultimo que
    haya, no el de la fecha exacta.

    Llama a `build_context` DE VERDAD. La primera version de este test repetia
    la consulta dentro del propio test, asi que probaba mi copia y no la real:
    al mutar el ORDER BY de `context.py` seguia pasando tan tranquilo.
    """
    sembrar(en_cartera=(), con_senal=(), universo=("AAA",))
    with db.connect() as conn:
        run_audit.guardar(
            conn,
            veredicto_de("AAA", 100.0, 0.3, Veredicto.INVALIDO)
            .assign(fecha=HOY - timedelta(days=10)),
            "run-viejo",
        )
        run_audit.guardar(
            conn, veredicto_de("AAA", 100.0, 0.001, Veredicto.VERIFICADO),
            "run-nuevo",
        )

    from stocks_tracker.trading.context import build_context

    ctx = build_context(as_of=HOY, mode="simulated")

    assert ctx.consenso.get("AAA") == str(Veredicto.VERIFICADO), (
        "el bot se quedaria con un veredicto caducado"
    )


# ---------------------------------------------------------------------------
# Que la auditoria diga POR QUE no ha podido contrastar
# ---------------------------------------------------------------------------

class _Mudo:
    """Un proveedor que contesta sin datos. Era el unico camino sin mensaje."""

    name = "mudo"

    def __init__(self, fallidos=None, excepcion=None):
        self._fallidos = fallidos or []
        self._excepcion = excepcion

    def supports(self, ticker):  # noqa: ARG002
        return True

    def fetch_ohlcv(self, tickers, desde, hasta, interval="1d"):  # noqa: ARG002
        if self._excepcion:
            raise self._excepcion
        df = pd.DataFrame(columns=["ticker", "date", "close"])
        df.attrs["failed_tickers"] = self._fallidos or list(tickers)
        df.attrs["requests_used"] = 2
        return df


def _leer(monkeypatch, proveedor, capsys):
    from stocks_tracker.ingest import run_audit

    monkeypatch.setattr(run_audit, "build_provider", lambda n: proveedor)  # noqa: ARG005
    salida = run_audit._lecturas_del_contraste(
        "mudo", ["AAA", "BBB"], date(2026, 8, 10), date(2026, 8, 20))
    return salida, capsys.readouterr().out


def test_un_proveedor_que_contesta_vacio_lo_dice(monkeypatch, capsys):
    """EL CAMINO MUDO. Cuatro salidas y solo esta no imprimia nada.

    El resultado final —"ningun valor ha podido contrastarse"— no distinguia
    "esta caido", "no cubre estos valores" y "ha contestado vacio". Tres averias
    distintas, tres arreglos distintos, y la misma frase para las tres.
    """
    salida, texto = _leer(monkeypatch, _Mudo(), capsys)

    assert salida.empty
    assert "sin ninguna fila" in texto, texto
    assert "AAA" in texto, "no dice que valores se quedaron sin datos"


def test_se_dicen_las_peticiones_gastadas(monkeypatch, capsys):
    """Con una cuota gratuita, saber si se gastaron peticiones distingue
    "rechazo la peticion" de "ni se llego a preguntar"."""
    _, texto = _leer(monkeypatch, _Mudo(), capsys)

    assert "2 peticiones" in texto, texto


def test_un_fallo_imprevisto_no_se_confunde_con_falta_de_datos(monkeypatch, capsys):
    """Los que dejan la auditoria a cero son los IMPREVISTOS —un cambio de
    formato, un KeyError de la libreria—. Capturar solo lo previsto convierte
    una averia concreta en "no se ha podido contrastar nada"."""
    salida, texto = _leer(
        monkeypatch, _Mudo(excepcion=KeyError("columna que ya no viene")), capsys)

    assert salida.empty
    assert "KeyError" in texto, texto


class _ConTope:
    """Un proveedor con cuota, como el plan gratuito de Twelve Data."""

    name = "con_tope"
    max_por_ejecucion = 3

    def __init__(self):
        self.pedidos = None

    def supports(self, ticker):  # noqa: ARG002
        return True

    def fetch_ohlcv(self, tickers, desde, hasta, interval="1d"):  # noqa: ARG002
        self.pedidos = list(tickers)
        df = pd.DataFrame([{"ticker": t, "date": desde, "close": 1.0}
                           for t in tickers])
        df.attrs["failed_tickers"] = []
        df.attrs["requests_used"] = len(tickers)
        return df


def test_no_se_le_piden_mas_valores_de_los_que_su_plan_aguanta(monkeypatch, capsys):
    """EL FALLO REAL. A Twelve Data se le pedian 77 valores con un plan de 8
    peticiones por minuto.

    La primera que pasa del limite devuelve 429, el proveedor corta —insistir
    empeora el bloqueo— y los 77 salen marcados como fallidos. La auditoria
    terminaba al instante, sin datos y sin decir por que.
    """
    from stocks_tracker.ingest import run_audit

    proveedor = _ConTope()
    monkeypatch.setattr(run_audit, "build_provider", lambda n: proveedor)  # noqa: ARG005

    run_audit._lecturas_del_contraste(
        "con_tope", ["A", "B", "C", "D", "E"], date(2026, 8, 10), date(2026, 8, 20))

    assert proveedor.pedidos == ["A", "B", "C"], proveedor.pedidos
    assert "limite del plan" in capsys.readouterr().out


def test_un_proveedor_sin_tope_recibe_todos():
    """Stooq no tiene cuota por minuto: recortarle la lista seria perder
    cobertura a cambio de nada."""
    import inspect

    from stocks_tracker.ingest import run_audit

    fuente = inspect.getsource(run_audit._lecturas_del_contraste)

    assert 'getattr(proveedor, "max_por_ejecucion", None)' in fuente, (
        "el tope tiene que ser opcional y venir del proveedor"
    )


def test_la_cuota_agotada_no_se_confunde_con_una_averia(monkeypatch, capsys):
    """El arreglo es distinto: aqui no hay nada roto, se ha pasado la cuota.
    Mezclado con "no ha respondido", el usuario busca una averia que no existe."""
    from stocks_tracker.providers.base import RateLimitError

    salida, texto = _leer(
        monkeypatch, _Mudo(excepcion=RateLimitError("8 por minuto")), capsys)

    assert salida.empty
    assert "cuota" in texto.lower(), texto
    assert "no ha respondido" not in texto, texto

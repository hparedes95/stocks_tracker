"""Guardar y releer el diario.

La garantia que se prueba aqui es la que hace que el diario sirva de algo: lo
que escribiste NO se toca nunca. Si la revision pudiera reescribir la tesis, se
reescribiria —la tentacion de "aclarar lo que queria decir" es exactamente el
sesgo que esto viene a frenar— y el diario acabaria siendo una cronica de
aciertos, que es en lo que se convierte la memoria si se la deja sola.
"""

from __future__ import annotations

import pandas as pd
import pytest

from stocks_tracker.app import journal_store as store
from stocks_tracker.core import db
from stocks_tracker.core.journal import Accion, Veredicto


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def leer():
    return store.leer.__wrapped__()


def anotar(**kwargs) -> str:
    base = {"ticker": "AAA", "accion": Accion.COMPRAR, "tesis": "porque si"}
    return store.anotar(**{**base, **kwargs})


# ---------------------------------------------------------------------------
# Lo escrito no se toca
# ---------------------------------------------------------------------------
def test_reviewing_does_not_touch_what_you_wrote(warehouse):
    """La garantia central del diario."""
    ident = anotar(tesis="crece un 20 % y cotiza a PER 12",
                   que_me_haria_salir="si el crecimiento baja del 10 %")
    store.revisar(ident, Veredicto.ERROR, "no mire la deuda")

    fila = leer().iloc[0]
    assert fila["tesis"] == "crece un 20 % y cotiza a PER 12"
    assert fila["que_me_haria_salir"] == "si el crecimiento baja del 10 %"
    assert fila["veredicto"] == "error"
    assert fila["nota_revision"] == "no mire la deuda"


def test_reviewing_twice_keeps_the_original_thesis(warehouse):
    """Cambiar de opinion sobre el veredicto es legitimo; reescribir el motivo
    de entonces, no."""
    ident = anotar(tesis="la tesis original")
    store.revisar(ident, Veredicto.ACIERTO, "primera")
    store.revisar(ident, Veredicto.SUERTE, "pensandolo mejor, fue suerte")

    fila = leer().iloc[0]
    assert fila["tesis"] == "la tesis original"
    assert fila["veredicto"] == "suerte"


def test_the_snapshot_of_the_day_is_stored_with_the_decision(warehouse):
    """Lo que de verdad se sabia ese dia. Sin la foto, al revisar se compara
    contra lo que uno recuerda haber sabido, que es otra cosa."""
    anotar(foto={"precio": 100.0, "precio_mercado": 400.0, "rsi14": 62.0,
                 "composite_pctile": 0.88, "drawdown": -0.05,
                 "above_sma200": True})
    fila = leer().iloc[0]
    assert fila["precio"] == pytest.approx(100.0)
    assert fila["rsi14"] == pytest.approx(62.0)
    assert fila["composite_pctile"] == pytest.approx(0.88)
    assert bool(fila["above_sma200"]) is True


# ---------------------------------------------------------------------------
# La ida y vuelta al nucleo
# ---------------------------------------------------------------------------
def test_a_decision_survives_the_round_trip(warehouse):
    anotar(ticker="BBB", accion=Accion.NO_COMPRAR, tesis="muy caro",
           horizonte_dias=180, conviccion=5, foto={"precio": 50.0})
    e = store.a_entradas(leer())[0]
    assert e.ticker == "BBB"
    assert e.accion is Accion.NO_COMPRAR
    assert e.horizonte_dias == 180
    assert e.conviccion == 5
    assert e.precio == pytest.approx(50.0)


def test_the_inverted_sign_survives_the_round_trip(warehouse):
    """Es lo que se pierde si la accion se guardara como texto suelto y se
    releyera mal: no comprar algo que se hundio saldria como fracaso."""
    anotar(accion=Accion.NO_COMPRAR, foto={"precio": 100.0})
    assert store.a_entradas(leer())[0].resultado(60.0) == pytest.approx(0.40)


def test_an_unknown_action_is_skipped_instead_of_crashing(warehouse):
    """Una edicion a mano o una version futura no pueden tumbar la pagina."""
    anotar()
    with db.connect() as conn:
        conn.execute("UPDATE decision_journal SET accion = 'teletransportar'")
    assert store.a_entradas(leer()) == []


def test_an_unknown_verdict_reads_as_not_reviewed(warehouse):
    """Y no como un veredicto inventado: "no se entiende" es mas honesto que
    elegir uno al azar."""
    ident = anotar()
    store.revisar(ident, Veredicto.ACIERTO)
    with db.connect() as conn:
        conn.execute("UPDATE decision_journal SET veredicto = 'regular'")
    assert store.a_entradas(leer())[0].veredicto is None


def test_a_decision_with_no_snapshot_still_reads(warehouse):
    """Se anota aunque no haya datos del valor: una decision de no comprar algo
    que no seguimos es igual de valiosa."""
    anotar(foto={})
    e = store.a_entradas(leer())[0]
    assert e.precio is None
    assert e.resultado(150.0) is None


# ---------------------------------------------------------------------------
# Orden e identidad
# ---------------------------------------------------------------------------
def test_the_newest_decision_comes_first(warehouse):
    anotar(ticker="VIEJA")
    anotar(ticker="NUEVA")
    assert leer().iloc[0]["ticker"] == "NUEVA"


def test_two_decisions_do_not_share_an_id(warehouse):
    """Con el id repetido, revisar una revisaria las dos."""
    assert anotar() != anotar()


def test_reviewing_one_does_not_review_the_others(warehouse):
    primera, segunda = anotar(ticker="AAA"), anotar(ticker="BBB")
    store.revisar(primera, Veredicto.ACIERTO)
    por_id = {f["id"]: f for _, f in leer().iterrows()}
    assert por_id[primera]["veredicto"] == "acierto"
    # `not nan` es FALSO: un nulo de pandas es verdadero. Sin `pd.isna`, este
    # test pasaria aunque la revision hubiera pisado las dos filas.
    assert pd.isna(por_id[segunda]["veredicto"])


def test_an_empty_journal_reads_as_empty_and_not_as_an_error(warehouse):
    assert leer().empty
    assert store.a_entradas(leer()) == []


# ---------------------------------------------------------------------------
# Almacenes anteriores a esta version
# ---------------------------------------------------------------------------
def test_a_warehouse_without_the_table_reads_as_empty(warehouse):
    """Quien actualice el programa sin volver a descargar datos abriria el
    diario y veria un error de tabla inexistente, que no explica nada."""
    with db.connect() as conn:
        conn.execute("DROP TABLE decision_journal")
    assert leer().empty


def test_writing_creates_the_table_if_it_is_missing(warehouse):
    with db.connect() as conn:
        conn.execute("DROP TABLE decision_journal")
    anotar(tesis="la primera de todas")
    assert leer().iloc[0]["tesis"] == "la primera de todas"


# ---------------------------------------------------------------------------
# Los precios para calcular el resultado
# ---------------------------------------------------------------------------
def test_the_price_used_is_the_most_recent_one(warehouse):
    """Con un precio viejo, el resultado saldria de una fecha cualquiera."""
    from datetime import date, timedelta

    hoy = date.today()
    with db.connect() as conn:
        for cuando, precio in ((hoy - timedelta(days=10), 90.0), (hoy, 130.0)):
            conn.execute(
                "INSERT INTO prices_daily (ticker, date, close, adj_close) "
                "VALUES ('AAA', ?, ?, ?)", [cuando, precio, precio])
    assert store.precios_hoy.__wrapped__(("AAA",))["AAA"] == pytest.approx(130.0)


def test_asking_for_no_tickers_does_not_query_anything(warehouse):
    assert store.precios_hoy.__wrapped__(()) == {}


def test_the_market_price_is_always_fetched_even_if_nobody_journals_it(warehouse,
                                                                      monkeypatch):
    """Nadie anota una decision sobre SPY, asi que pidiendo solo los tickers
    del diario el proxy de mercado se queda sin precio y la comparacion contra
    el mercado no aparece NUNCA en pantalla.

    No daba error: la metrica simplemente faltaba, que es la forma de fallar
    mas dificil de notar.
    """
    from datetime import date

    monkeypatch.setattr(store, "precios_hoy", store.precios_hoy.__wrapped__)
    anotar(ticker="AAA")
    with db.connect() as conn:
        for t in ("AAA", store.MERCADO):
            conn.execute(
                "INSERT INTO prices_daily (ticker, date, close, adj_close) "
                "VALUES (?, ?, 100.0, 100.0)", [t, date.today()])

    precios = store.precios_para(store.a_entradas(leer()))
    assert store.MERCADO in precios, (
        "sin el precio del mercado, 'Descontando el mercado' no se ensena jamas"
    )
    assert "AAA" in precios


def test_the_snapshot_takes_the_percentile_of_the_active_style(warehouse):
    """`factor_scores` guarda un score por ESTILO. Sin filtrar por el hash del
    estilo activo, `LIMIT 1` se quedaba con uno cualquiera: la decision quedaba
    anotada con un percentil que no era el que se estaba mirando en pantalla.
    """
    from datetime import date

    from stocks_tracker.core.scoring import preset_hash

    hoy = date.today()
    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, asset_class) "
                     "VALUES ('AAA', 'equity')")
        conn.execute(
            "INSERT INTO indicators_daily (ticker, date, close, rsi14) "
            "VALUES ('AAA', ?, 100.0, 55.0)", [hoy])
        for estilo, pctile in (("balanced", 0.90), ("momentum", 0.10)):
            conn.execute(
                "INSERT INTO factor_scores (ticker, date, weights_hash, "
                "composite, composite_pctile, coverage) "
                "VALUES ('AAA', ?, ?, 1.0, ?, 0.9)",
                [hoy, preset_hash(estilo), pctile])

    assert store.foto_de("AAA")["composite_pctile"] == pytest.approx(0.90)

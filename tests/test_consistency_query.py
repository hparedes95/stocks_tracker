"""Del almacen al contraste de fundamentales.

`core.consistency` esta probado con diccionarios. Aqui se prueba de donde salen
esos diccionarios: cual es la foto "de ahora", cual la "anterior", y que la
beta se calcule contra el mercado y no contra el propio valor —eso daria 1,00
siempre y el contraste no detectaria nunca nada—.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from stocks_tracker.core import db

HOY = date.today()


@pytest.fixture(autouse=True)
def sin_cache(monkeypatch):
    from stocks_tracker.app import data_access as da

    for nombre in ("get_fundamentals_pair", "get_beta_from_prices",
                   "review_fundamentals", "review_all_fundamentals"):
        fn = getattr(da, nombre)
        monkeypatch.setattr(da, nombre, getattr(fn, "__wrapped__", fn))


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def foto(as_of: date, **campos) -> None:
    columnas = ["ticker", "as_of", *campos]
    huecos = ", ".join("?" for _ in columnas)
    with db.connect() as conn:
        conn.execute(
            f"INSERT INTO fundamentals_snapshot ({', '.join(columnas)}) "
            f"VALUES ({huecos})",
            ["AAA", as_of, *campos.values()],
        )


def revisar(ticker="AAA"):
    from stocks_tracker.app import data_access as da

    return da.review_fundamentals(ticker)


# ---------------------------------------------------------------------------
# Cual es "ahora" y cual "antes"
# ---------------------------------------------------------------------------
def test_the_pair_is_the_two_most_recent_snapshots(warehouse):
    from stocks_tracker.app import data_access as da

    foto(HOY - timedelta(days=30), trailing_pe=10.0)
    foto(HOY - timedelta(days=7), trailing_pe=20.0)
    foto(HOY, trailing_pe=30.0)
    ultima, anterior = da.get_fundamentals_pair("AAA")
    assert ultima["trailing_pe"] == pytest.approx(30.0)
    assert anterior["trailing_pe"] == pytest.approx(20.0)


def test_with_a_single_snapshot_there_is_no_previous_one(warehouse):
    from stocks_tracker.app import data_access as da

    foto(HOY, trailing_pe=20.0)
    ultima, anterior = da.get_fundamentals_pair("AAA")
    assert ultima is not None and anterior is None


def test_a_ticker_with_no_snapshots_gives_nothing(warehouse):
    from stocks_tracker.app import data_access as da

    assert da.get_fundamentals_pair("AAA") == (None, None)


def test_the_jump_between_the_two_snapshots_reaches_the_review(warehouse):
    """La comprobacion de punta a punta del contraste temporal: si la consulta
    trajera dos veces la misma foto, la comparacion daria cero siempre y no
    detectaria ningun salto."""
    foto(HOY - timedelta(days=7), trailing_pe=18.0)
    foto(HOY, trailing_pe=180.0)
    assert "trailing_pe" in revisar().campos_sospechosos


# ---------------------------------------------------------------------------
# La beta se calcula contra el mercado
# ---------------------------------------------------------------------------
def sembrar_retornos(ticker: str, retornos, sesiones=None) -> None:
    sesiones = sesiones or [HOY - timedelta(days=i)
                            for i in range(len(retornos) - 1, -1, -1)]
    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, asset_class) "
                     "VALUES (?, 'equity')", [ticker])
        for cuando, r in zip(sesiones, retornos, strict=False):
            conn.execute(
                "INSERT INTO indicators_daily (ticker, date, close, ret_1d) "
                "VALUES (?, ?, 100.0, ?)", [ticker, cuando, float(r)])


def test_the_beta_is_computed_against_the_market(warehouse):
    """Contra el propio valor daria 1,00 siempre y el contraste no detectaria
    nunca nada: el aviso desapareceria sin dar ningun error."""
    from stocks_tracker.app import data_access as da

    mercado = np.random.default_rng(7).normal(0, 0.01, 200)
    sesiones = [HOY - timedelta(days=i) for i in range(199, -1, -1)]
    sembrar_retornos(da.MERCADO_TICKER, mercado, sesiones)
    sembrar_retornos("AAA", mercado * 2.0, sesiones)

    assert da.get_beta_from_prices("AAA") == pytest.approx(2.0, rel=0.05)


def test_without_market_history_there_is_no_beta(warehouse):
    from stocks_tracker.app import data_access as da

    sembrar_retornos("AAA", np.random.default_rng(8).normal(0, 0.01, 200))
    assert da.get_beta_from_prices("AAA") is None


def test_a_declared_beta_far_from_ours_reaches_the_review(warehouse):
    from stocks_tracker.app import data_access as da

    mercado = np.random.default_rng(9).normal(0, 0.01, 200)
    sesiones = [HOY - timedelta(days=i) for i in range(199, -1, -1)]
    sembrar_retornos(da.MERCADO_TICKER, mercado, sesiones)
    sembrar_retornos("AAA", mercado * 2.0, sesiones)
    foto(HOY, beta=0.2)

    assert "beta" in revisar().campos_sospechosos


# ---------------------------------------------------------------------------
# El precio para contrastar la capitalizacion
# ---------------------------------------------------------------------------
def test_the_market_cap_is_checked_against_our_latest_price(warehouse):
    with db.connect() as conn:
        for cuando, precio in ((HOY - timedelta(days=10), 5.0), (HOY, 100.0)):
            conn.execute(
                "INSERT INTO prices_daily (ticker, date, close, adj_close) "
                "VALUES ('AAA', ?, ?, ?)", [cuando, precio, precio])
    # 100 x 1.000 millones = 100.000 millones; declaran un billon.
    foto(HOY, market_cap=1e12, shares_outstanding=1e9)
    assert "market_cap" in revisar().campos_sospechosos


def test_a_matching_market_cap_passes(warehouse):
    """Con un precio VIEJO muy distinto ademas del de hoy: si la consulta
    cogiera el primero de la serie en vez del ultimo, esto saltaria. Con un
    solo precio en el almacen los dos criterios dan lo mismo y el test pasaria
    igual —que es lo que hacia—.
    """
    with db.connect() as conn:
        for cuando, precio in ((HOY - timedelta(days=200), 5.0), (HOY, 100.0)):
            conn.execute(
                "INSERT INTO prices_daily (ticker, date, close, adj_close) "
                "VALUES ('AAA', ?, ?, ?)", [cuando, precio, precio])
    foto(HOY, market_cap=1e11, shares_outstanding=1e9)
    assert revisar().fiable


def test_the_unadjusted_close_is_what_matches_a_market_cap(warehouse, monkeypatch):
    """`adj_close` corrige splits y dividendos, asi que NO es el precio al que
    cotiza hoy: multiplicado por las acciones no da la capitalizacion.

    Con el ajustado, cualquier valor con historia de dividendos salia marcado
    —el 100 % del universo—, que es la forma de fallar mas inutil que hay: un
    aviso que sale siempre no distingue nada. En el resto de tests los dos
    precios coinciden y por eso no lo detectaban.
    """
    from stocks_tracker.app import data_access as da

    monkeypatch.setattr(da, "data_origin", lambda: {"synthetic": False})
    with db.connect() as conn:
        conn.execute("INSERT INTO prices_daily (ticker, date, close, adj_close) "
                     "VALUES ('AAA', ?, 100.0, 70.0)", [HOY])
    foto(HOY, market_cap=1e11, shares_outstanding=1e9)

    assert revisar().fiable, "ha contrastado con el precio ajustado"
    assert da.review_all_fundamentals().empty


def test_without_prices_the_market_cap_is_not_checked(warehouse):
    foto(HOY, market_cap=1e12, shares_outstanding=1e9)
    assert "market_cap" not in revisar().campos_sospechosos


# ---------------------------------------------------------------------------
# El barrido del universo
# ---------------------------------------------------------------------------
def test_the_sweep_lists_only_the_ones_with_something_wrong(warehouse):
    from stocks_tracker.app import data_access as da

    foto(HOY, profit_margin=9.0)                 # AAA: imposible
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO fundamentals_snapshot (ticker, as_of, profit_margin) "
            "VALUES ('BBB', ?, 0.22)", [HOY])    # BBB: normal

    tabla = da.review_all_fundamentals()
    assert list(tabla["ticker"]) == ["AAA"]
    assert int(tabla.iloc[0]["rotos"]) == 1


def test_the_worst_come_first(warehouse):
    """Un dato imposible pesa mas que una contradiccion: ordenar al reves
    dejaria lo urgente en mitad de la lista."""
    from stocks_tracker.app import data_access as da

    foto(HOY, profit_margin=9.0)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO fundamentals_snapshot "
            "(ticker, as_of, trailing_pe, earnings_yield) "
            "VALUES ('BBB', ?, 20.0, 0.25)", [HOY])

    assert list(da.review_all_fundamentals()["ticker"]) == ["AAA", "BBB"]


def test_a_warehouse_with_no_fundamentals_gives_an_empty_table(warehouse):
    from stocks_tracker.app import data_access as da

    tabla = da.review_all_fundamentals()
    assert tabla.empty
    assert list(tabla.columns) == ["ticker", "rotos", "avisos", "campos",
                                   "detalle"]


def test_a_ticker_with_no_fundamentals_is_not_listed_as_broken(warehouse):
    """"Sin fundamentales" produce un aviso sin campo, y ese no es un dato
    contradictorio: es un dato que falta. Colarlo en la tabla llenaria la
    pantalla de valores que no tienen nada malo."""
    from stocks_tracker.app import data_access as da

    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, asset_class) "
                     "VALUES ('AAA', 'equity')")
    assert da.review_all_fundamentals().empty


# ---------------------------------------------------------------------------
# Datos de prueba
# ---------------------------------------------------------------------------
def test_synthetic_prices_are_not_used_to_contradict_anything(warehouse,
                                                              monkeypatch):
    """El simulador inventa los precios y los fundamentales por separado, asi
    que no cuadran nunca. Contrastarlos marcaba el 95 % del universo y lo unico
    que ensenaba es a ignorar los avisos, antes incluso de tener datos reales.
    """
    from stocks_tracker.app import data_access as da

    monkeypatch.setattr(da, "data_origin", lambda: {"synthetic": True})
    with db.connect() as conn:
        conn.execute("INSERT INTO prices_daily (ticker, date, close, adj_close) "
                     "VALUES ('AAA', ?, 100.0, 100.0)", [HOY])
    foto(HOY, market_cap=1e12, shares_outstanding=1e9)     # no cuadra de sobra

    assert revisar().fiable
    assert da.review_all_fundamentals().empty


def test_with_real_prices_the_same_case_is_flagged(warehouse, monkeypatch):
    """El contrario, para que el test de arriba no pase por el motivo
    equivocado."""
    from stocks_tracker.app import data_access as da

    monkeypatch.setattr(da, "data_origin", lambda: {"synthetic": False})
    with db.connect() as conn:
        conn.execute("INSERT INTO prices_daily (ticker, date, close, adj_close) "
                     "VALUES ('AAA', ?, 100.0, 100.0)", [HOY])
    foto(HOY, market_cap=1e12, shares_outstanding=1e9)

    assert "market_cap" in revisar().campos_sospechosos
    assert not da.review_all_fundamentals().empty


def test_the_sweep_does_not_query_once_per_ticker(warehouse, monkeypatch):
    """Con una consulta por valor esto tardaba minuto y medio con 600
    instrumentos, y una pagina en blanco durante minuto y medio es
    indistinguible de una pagina rota."""
    from stocks_tracker.app import data_access as da

    monkeypatch.setattr(da, "data_origin", lambda: {"synthetic": False})
    with db.connect() as conn:
        for i in range(60):
            conn.execute(
                "INSERT INTO fundamentals_snapshot (ticker, as_of, profit_margin) "
                "VALUES (?, ?, 9.0)", [f"T{i:03d}", HOY])

    consultas = 0
    original = da._fetch

    def contando(sql, params=None):
        nonlocal consultas
        consultas += 1
        return original(sql, params)

    monkeypatch.setattr(da, "_fetch", contando)
    tabla = da.review_all_fundamentals()

    assert len(tabla) == 60
    assert consultas <= 4, f"{consultas} consultas para 60 valores"

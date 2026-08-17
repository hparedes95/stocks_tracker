"""Fundamentales punto-en-el-tiempo: puntuar el pasado sin mirar el futuro.

Es la diferencia entre un backtest honesto y uno que sale bien por
construccion. Si al puntuar 2019 se usan los balances de 2026, la estrategia
"sabe" que empresas iban a publicar buenos numeros siete anos despues, y
cualquier ranking de calidad o valor parece clarividente.

El fallo no daria error ni saldria en pantalla: solo un backtest
sospechosamente bueno, que es el resultado mas facil de creerse.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stocks_tracker.compute.run_compute import (
    MIN_PIT_COVERAGE,
    PRICE_ONLY_FACTORS,
    compute_score_history,
    fundamentals_as_of,
    pit_coverage,
)
from stocks_tracker.core import db


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def sembrar(fotos: list[dict], tickers=("AAA", "BBB"),
            con_indicadores: bool = False) -> None:
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame(
            [{"ticker": t, "asset_class": "equity"} for t in tickers]),
            keys=["ticker"])
        if fotos:
            db.upsert_df(conn, "fundamentals_snapshot", pd.DataFrame(fotos),
                         keys=["ticker", "as_of"])
        if con_indicadores:
            # `compute_score_history` sale antes si no hay indicadores, asi que
            # sin esto los tests del rechazo pasarian por el motivo equivocado.
            filas = [
                {"ticker": t, "date": date(2026, 5, 4) + pd.Timedelta(days=7 * i),
                 "close": 100.0 + i, "rsi14": 50.0, "atr14": 1.0,
                 "realized_vol_60": 0.2, "roc_3m": 0.1, "roc_6m": 0.1,
                 "mom_12_1": 0.1, "above_sma200": True}
                for t in tickers for i in range(4)
            ]
            db.upsert_df(conn, "indicators_daily", pd.DataFrame(filas),
                         keys=["ticker", "date"])


def por_fecha(df) -> dict:
    """Indexa por fecha normalizando el tipo.

    DuckDB devuelve las fechas como `datetime64` al pasar por `fetchdf`, no
    como `date` de Python. Compararlas con `date(...)` no falla: simplemente no
    encuentra nada, que es peor.
    """
    return {pd.Timestamp(f).date(): v
            for f, v in zip(df["date"], df["roe"], strict=False)}


# ---------------------------------------------------------------------------
# La union
# ---------------------------------------------------------------------------
def test_each_date_gets_the_snapshot_that_was_current_then(warehouse):
    """Lo que separa un backtest honesto de uno que se sabe el futuro."""
    sembrar([
        {"ticker": "AAA", "as_of": date(2026, 1, 1), "roe": 0.10},
        {"ticker": "AAA", "as_of": date(2026, 6, 1), "roe": 0.99},
    ])
    with db.connect(read_only=True) as conn:
        df = fundamentals_as_of(conn, [date(2026, 3, 1)])
    assert df["roe"].tolist() == [0.10], "ha usado una foto posterior a la fecha"


def test_a_later_date_gets_the_newer_snapshot(warehouse):
    """El contrario: si siempre devolviera la mas antigua, el test de arriba
    pasaria sin comprobar nada."""
    sembrar([
        {"ticker": "AAA", "as_of": date(2026, 1, 1), "roe": 0.10},
        {"ticker": "AAA", "as_of": date(2026, 6, 1), "roe": 0.99},
    ])
    with db.connect(read_only=True) as conn:
        df = fundamentals_as_of(conn, [date(2026, 7, 1)])
    assert df["roe"].tolist() == [0.99]


def test_the_snapshot_of_the_same_day_counts(warehouse):
    """`as_of <= fecha` y no `<`: la foto se descarga por la noche con datos
    que ya eran publicos ese dia, asi que usarla no es mirar el futuro."""
    sembrar([{"ticker": "AAA", "as_of": date(2026, 6, 1), "roe": 0.42}])
    with db.connect(read_only=True) as conn:
        df = fundamentals_as_of(conn, [date(2026, 6, 1)])
    assert df["roe"].tolist() == [0.42]


def test_a_ticker_without_a_prior_snapshot_is_absent(warehouse):
    """Y no aparece con los ratios de su primera foto futura, que es el fallo
    que se esta evitando."""
    sembrar([{"ticker": "BBB", "as_of": date(2026, 6, 1), "roe": 0.30}])
    with db.connect(read_only=True) as conn:
        df = fundamentals_as_of(conn, [date(2026, 3, 1)])
    assert df.empty


def test_several_dates_in_one_query_do_not_mix(warehouse):
    """Se leen todas las fechas de una vez por velocidad. Si la particion de la
    ventana estuviera mal, una fecha se llevaria la foto de otra."""
    sembrar([
        {"ticker": "AAA", "as_of": date(2026, 1, 1), "roe": 0.10},
        {"ticker": "AAA", "as_of": date(2026, 6, 1), "roe": 0.99},
    ])
    with db.connect(read_only=True) as conn:
        df = fundamentals_as_of(conn, [date(2026, 3, 1), date(2026, 7, 1)])
    valores = por_fecha(df)
    assert valores[date(2026, 3, 1)] == 0.10
    assert valores[date(2026, 7, 1)] == 0.99


# ---------------------------------------------------------------------------
# Cobertura: cuando NO se puede
# ---------------------------------------------------------------------------
def test_coverage_counts_only_snapshots_that_already_existed(warehouse):
    sembrar([{"ticker": "AAA", "as_of": date(2026, 6, 1), "roe": 0.2}])
    with db.connect(read_only=True) as conn:
        cob = pit_coverage(conn, [date(2026, 3, 1), date(2026, 7, 1)])
    assert cob[date(2026, 3, 1)] == 0.0
    assert cob[date(2026, 7, 1)] == 0.5      # AAA de dos instrumentos


def test_scoring_the_past_with_fundamentals_is_refused_without_history(warehouse):
    """El caso de hoy: el historico se empezo a acumular hace unos dias, asi
    que ninguna fecha pasada tiene foto anterior. Puntuar igualmente daria un
    ranking de calidad construido con los balances de manana."""
    sembrar([{"ticker": "AAA", "as_of": date(2026, 6, 1), "roe": 0.2}],
            con_indicadores=True)
    with pytest.raises(ValueError, match="fundamentales"):
        compute_score_history(preset="balanced", years=1)


def test_the_refusal_says_that_time_is_the_only_fix(warehouse):
    """No hay forma de recuperar fotos del pasado: nadie las guardo. Un mensaje
    que no lo diga manda a buscar una opcion que no existe."""
    sembrar([{"ticker": "AAA", "as_of": date(2026, 6, 1), "roe": 0.2}],
            con_indicadores=True)
    with pytest.raises(ValueError, match="se arregla solo con el tiempo"):
        compute_score_history(preset="balanced", years=1)


def test_a_price_only_preset_never_needs_any_of_this(warehouse):
    """`bot_core` no usa fundamentales, asi que no puede quedarse bloqueado por
    falta de historico de fundamentales.

    Con indicadores sembrados a proposito: sin ellos la funcion sale antes de
    llegar a la comprobacion y el test pasaria sin comprobar nada —de hecho lo
    hacia, y la comprobacion de mutaciones lo destapo—.
    """
    sembrar([], con_indicadores=True)
    compute_score_history(preset="bot_core", years=1)   # no debe lanzar


def test_the_price_only_set_is_still_the_safe_fallback():
    """El mensaje de rechazo remite a estos factores: si la lista cambiara sin
    querer, mandaria a usar algo que tampoco funciona."""
    assert PRICE_ONLY_FACTORS == {"momentum", "lowvol", "technical"}
    assert 0.5 < MIN_PIT_COVERAGE <= 1.0


def test_with_enough_history_the_fundamentals_actually_reach_the_scoring(warehouse):
    """El rechazo funcionando no basta: hay que comprobar que cuando SI hay
    historico, los ratios llegan de verdad al ranking.

    Es donde se esconderia un desajuste de tipos: las fechas vienen de DuckDB
    como `datetime64` por los dos lados, y si uno de los dos fuera `date` el
    merge no casaria ninguna fila. No daria error: simplemente puntuaria sin
    fundamentales, en silencio, despues de haber pasado la comprobacion de
    cobertura.
    """
    fechas = [date(2026, 5, 4) + pd.Timedelta(days=7 * i) for i in range(4)]
    fotos = [{"ticker": t, "as_of": date(2026, 1, 1), "roe": 0.15,
              "trailing_pe": 12.0, "profit_margin": 0.2, "revenue_growth_yoy": 0.1,
              "dividend_yield": 0.03, "completeness": 1.0}
             for t in ("AAA", "BBB")]
    sembrar(fotos, con_indicadores=True)

    with db.connect(read_only=True) as conn:
        cob = pit_coverage(conn, [pd.Timestamp(f).date() for f in fechas])
    assert min(cob.values()) >= MIN_PIT_COVERAGE, (
        "el escenario no tiene la cobertura que se quiere probar"
    )

    with db.connect(read_only=True) as conn:
        traidos = fundamentals_as_of(conn, [pd.Timestamp(f).date() for f in fechas])
    assert not traidos.empty
    assert set(traidos["ticker"]) == {"AAA", "BBB"}


def test_coverage_counts_the_same_population_on_both_sides(warehouse):
    """El denominador incluia los ETF, que no publican fundamentales y por
    tanto NUNCA tienen foto: la cobertura salia baja de forma sistematica y
    podia bloquear el ranking sin motivo. Y el numerador contaba cualquier
    ticker con foto, estuviera o no en el universo, con lo que podia pasar de
    1,0 y dejar pasar justo lo que la puerta existe para frenar.
    """
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAA", "asset_class": "equity"},
            {"ticker": "SPY", "asset_class": "etf"},        # nunca tendra foto
        ]), keys=["ticker"])
        db.upsert_df(conn, "fundamentals_snapshot", pd.DataFrame([
            {"ticker": "AAA", "as_of": date(2026, 1, 1), "roe": 0.2},
            # Foto de un ticker que no esta en el universo: no puede contar.
            {"ticker": "FUERA", "as_of": date(2026, 1, 1), "roe": 0.3},
        ]), keys=["ticker", "as_of"])

    with db.connect(read_only=True) as conn:
        cob = pit_coverage(conn, [date(2026, 6, 1)])
    assert cob[date(2026, 6, 1)] == pytest.approx(1.0)

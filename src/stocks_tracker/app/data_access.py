"""Unica capa de lectura de la base de datos desde la interfaz.

Ninguna pagina escribe SQL suelto: todo pasa por aqui, cacheado. Asi la UI
responde en milisegundos y se puede cambiar el esquema en un solo sitio.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from ..core.config import (
    get_active_universes,
    get_breadth_scope,
    get_settings,
    get_universes,
)
from ..core.db import connect
from ..core.timeutils import hours_since

TTL = 900  # 15 minutos: los datos se actualizan una vez al dia


def _fetch(sql: str, params: list | None = None) -> pd.DataFrame:
    with connect(read_only=True) as conn:
        return conn.execute(sql, params or []).fetchdf()


@st.cache_data(ttl=TTL, show_spinner=False)
def last_price_date() -> date | None:
    df = _fetch("SELECT MAX(date) AS d FROM prices_daily")
    if df.empty or pd.isna(df.iloc[0]["d"]):
        return None
    return pd.Timestamp(df.iloc[0]["d"]).date()


@st.cache_data(ttl=TTL, show_spinner=False)
def data_freshness() -> dict:
    """Estado de frescura de los datos, para el aviso de la cabecera."""
    df = _fetch(
        """
        SELECT MAX(finished_at) AS last_run,
               SUM(CASE WHEN status IN ('FAILED','RATE_LIMITED') THEN 1 ELSE 0 END) AS failures
        FROM ingest_log
        """
    )
    last_date = last_price_date()
    last_run = None if df.empty or pd.isna(df.iloc[0]["last_run"]) else df.iloc[0]["last_run"]
    warn_hours = float(get_settings().ui.get("data_freshness_warn_hours", 30))
    hours = hours_since(last_run)
    return {
        "last_price_date": last_date,
        "last_run": last_run,
        "hours_since_run": hours,
        "is_stale": hours is not None and hours > warn_hours,
        "failures": int(df.iloc[0]["failures"] or 0) if not df.empty else 0,
    }


@st.cache_data(ttl=TTL, show_spinner=False)
def instruments() -> pd.DataFrame:
    return _fetch(
        """
        SELECT ticker, name, asset_class, exchange, currency, country,
               gics_sector, investment_type, market_cap, tv_symbol
        FROM instruments WHERE is_active
        """
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_tv_symbol(ticker: str) -> str | None:
    """Simbolo de TradingView, o None.

    Devolver None NO es un fallo: la interfaz dibuja entonces nuestro propio
    grafico. Nunca se renderiza un widget con simbolo invalido, porque mostraria
    "Invalid symbol" y pareceria que la aplicacion esta rota.
    """
    df = _fetch("SELECT tv_symbol FROM instruments WHERE ticker = ?", [ticker])
    if df.empty or pd.isna(df.iloc[0]["tv_symbol"]):
        return None
    return str(df.iloc[0]["tv_symbol"])


@st.cache_data(ttl=TTL, show_spinner=False)
def universe_tickers(universe: str) -> list[str]:
    df = _fetch(
        "SELECT ticker FROM universe_membership WHERE universe = ? AND valid_to IS NULL",
        [universe],
    )
    return df["ticker"].tolist()


def universe_options() -> dict[str, str]:
    """Universos disponibles con nombre legible."""
    specs = get_universes()
    out = {"TODOS": "Todos los mercados"}
    for key in get_active_universes():
        spec = specs.get(key)
        if spec and spec.asset_class in ("equity", "etf"):
            out[key] = spec.name
    return out


def _universe_filter(universe: str) -> tuple[str, list]:
    if universe in (None, "", "TODOS"):
        return "", []
    return (
        " AND i.ticker IN (SELECT ticker FROM universe_membership "
        "WHERE universe = ? AND valid_to IS NULL)",
        [universe],
    )


# --------------------------------------------------------------------------
# Pagina 1: que se mueve hoy
# --------------------------------------------------------------------------
@st.cache_data(ttl=TTL, show_spinner=False)
def get_movers(universe: str = "TODOS", n: int = 10, ascending: bool = False,
               min_dollar_volume: float = 1_000_000) -> pd.DataFrame:
    """Mayores subidas o bajadas del dia.

    El filtro de volumen en euros evita que la lista se llene de valores
    ilíquidos que se mueven un 15% con cuatro operaciones.
    """
    where, params = _universe_filter(universe)
    order = "ASC" if ascending else "DESC"
    return _fetch(
        f"""
        SELECT i.ticker, inst.name, inst.gics_sector, i.close, i.ret_1d,
               i.rel_volume_20, i.rsi14, f.composite_pctile
        FROM indicators_daily i
        JOIN instruments inst ON inst.ticker = i.ticker
        LEFT JOIN factor_scores f ON f.ticker = i.ticker AND f.date = i.date
        WHERE i.date = (SELECT MAX(date) FROM indicators_daily)
          AND inst.asset_class IN ('equity', 'etf')
          AND i.ret_1d IS NOT NULL
          AND i.close * (SELECT volume FROM prices_daily p
                         WHERE p.ticker = i.ticker AND p.date = i.date) > ?
          {where}
        ORDER BY i.ret_1d {order}
        LIMIT ?
        """,
        [min_dollar_volume, *params, n],
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_breakouts_52w(universe: str = "TODOS", high: bool = True) -> pd.DataFrame:
    """Valores que ROMPEN hoy su maximo (o minimo) anual.

    La condicion sobre la sesion anterior es lo que distingue una ruptura de un
    valor que lleva quince dias pegado a maximos: sin ella, la lista repetiria
    los mismos nombres cada dia y dejaria de ser informativa.
    """
    where, params = _universe_filter(universe)
    col = "dist_52w_high" if high else "dist_52w_low"
    cond = f"i.{col} >= -0.002 AND prev.{col} < -0.002" if high else \
           f"i.{col} <= 0.002 AND prev.{col} > 0.002"
    return _fetch(
        f"""
        WITH ranked AS (
            SELECT ticker, date,
                   LAG(date) OVER (PARTITION BY ticker ORDER BY date) AS prev_date
            FROM indicators_daily
        )
        SELECT i.ticker, inst.name, inst.gics_sector, i.close, i.ret_1d,
               i.rel_volume_20, i.{col} AS distancia
        FROM indicators_daily i
        JOIN ranked r ON r.ticker = i.ticker AND r.date = i.date
        JOIN indicators_daily prev ON prev.ticker = i.ticker AND prev.date = r.prev_date
        JOIN instruments inst ON inst.ticker = i.ticker
        WHERE i.date = (SELECT MAX(date) FROM indicators_daily)
          AND inst.asset_class IN ('equity', 'etf')
          AND {cond}
          {where}
        ORDER BY i.ret_1d DESC
        """,
        params,
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_volume_spikes(universe: str = "TODOS", threshold: float = 2.0,
                      n: int = 15) -> pd.DataFrame:
    where, params = _universe_filter(universe)
    return _fetch(
        f"""
        SELECT i.ticker, inst.name, inst.gics_sector, i.close, i.ret_1d,
               i.rel_volume_20, i.rsi14
        FROM indicators_daily i
        JOIN instruments inst ON inst.ticker = i.ticker
        WHERE i.date = (SELECT MAX(date) FROM indicators_daily)
          AND i.rel_volume_20 > ?
          AND inst.asset_class IN ('equity', 'etf')
          {where}
        ORDER BY i.rel_volume_20 DESC
        LIMIT ?
        """,
        [threshold, *params, n],
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_trend_changes(universe: str = "TODOS") -> pd.DataFrame:
    where, params = _universe_filter(universe)
    where = where.replace("i.ticker", "s.ticker")
    return _fetch(
        f"""
        SELECT s.ticker, inst.name, inst.gics_sector, s.signal_id,
               s.direction, s.strength, ind.close, ind.ret_1d
        FROM signals s
        JOIN instruments inst ON inst.ticker = s.ticker
        LEFT JOIN indicators_daily ind
               ON ind.ticker = s.ticker AND ind.date = s.date
        WHERE s.date = (SELECT MAX(date) FROM signals)
          AND inst.asset_class IN ('equity', 'etf')
          AND s.signal_id IN ('GOLDEN_CROSS','DEATH_CROSS','MACD_BULL_CROSS',
                              'MACD_BEAR_CROSS','RSI_OVERSOLD_REVERSAL',
                              'HIGH_52W_BREAKOUT','LOW_52W_BREAKDOWN',
                              'PULLBACK_IN_UPTREND','NEW_DOWNTREND')
          {where}
        ORDER BY s.direction, s.strength DESC
        """,
        params,
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_sector_performance() -> pd.DataFrame:
    """Rendimiento por sector. Mediana, no media: robusta a un valor disparado."""
    return _fetch(
        """
        SELECT inst.gics_sector AS sector,
               COUNT(*) AS n_valores,
               MEDIAN(i.ret_1d) AS ret_1d,
               MEDIAN(i.ret_5d) AS ret_5d,
               MEDIAN(i.roc_1m) AS ret_1m,
               MEDIAN(i.roc_3m) AS ret_3m,
               MEDIAN(i.roc_12m) AS ret_12m,
               AVG(CASE WHEN i.above_sma200 THEN 100.0 ELSE 0.0 END) AS pct_sobre_mm200
        FROM indicators_daily i
        JOIN instruments inst ON inst.ticker = i.ticker
        WHERE i.date = (SELECT MAX(date) FROM indicators_daily)
          AND inst.gics_sector IS NOT NULL AND inst.asset_class = 'equity'
        GROUP BY 1
        HAVING COUNT(*) >= 3
        ORDER BY ret_1d DESC
        """
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_breadth(scope: str | None = None, days: int = 400) -> pd.DataFrame:
    """Serie de amplitud. Sin ambito, el configurado en `universe.yaml`."""
    return _fetch(
        """
        SELECT * FROM breadth_daily
        WHERE scope = ? ORDER BY date DESC LIMIT ?
        """,
        [scope or get_breadth_scope(), days],
    ).sort_values("date")


@st.cache_data(ttl=TTL, show_spinner=False)
def get_rotation() -> pd.DataFrame:
    """Posicion de cada sector en el grafico de rotacion."""
    df = _fetch(
        """
        SELECT * FROM sector_rotation
        WHERE date = (SELECT MAX(date) FROM sector_rotation)
        ORDER BY ratio DESC
        """
    )
    if df.empty:
        return df
    # Las estelas se guardan como JSON para no crear una tabla por punto.
    for col in ("estela_ratio", "estela_momentum"):
        if col in df.columns:
            df[col] = df[col].map(lambda s: json.loads(s) if isinstance(s, str) else [])
    return df


@st.cache_data(ttl=TTL, show_spinner=False)
def get_treemap_data(universe: str = "TODOS", group_col: str = "gics_sector") -> pd.DataFrame:
    """Datos para el mapa de superficie: capitalizacion y variacion del dia."""
    where, params = _universe_filter(universe)
    column = "gics_sector" if group_col == "gics_sector" else "investment_type"
    return _fetch(
        f"""
        SELECT i.ticker, inst.{column} AS gics_sector, inst.market_cap, i.ret_1d
        FROM indicators_daily i
        JOIN instruments inst ON inst.ticker = i.ticker
        WHERE i.date = (SELECT MAX(date) FROM indicators_daily)
          AND inst.asset_class = 'equity'
          AND inst.market_cap IS NOT NULL AND inst.market_cap > 0
          AND inst.{column} IS NOT NULL
          {where}
        """,
        params,
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_macro_series(series_ids: tuple[str, ...] = ()) -> pd.DataFrame:
    """Series macro de FRED. Vacio si no se han descargado."""
    if series_ids:
        placeholders = ", ".join("?" for _ in series_ids)
        return _fetch(
            f"""
            SELECT series_id, date, value FROM macro_series
            WHERE series_id IN ({placeholders}) ORDER BY series_id, date
            """,
            list(series_ids),
        )
    return _fetch("SELECT series_id, date, value FROM macro_series ORDER BY series_id, date")


@st.cache_data(ttl=TTL, show_spinner=False)
def macro_available() -> bool:
    df = _fetch("SELECT COUNT(*) AS n FROM macro_series")
    return bool(not df.empty and int(df.iloc[0]["n"]) > 0)


@st.cache_data(ttl=TTL, show_spinner=False)
def get_macro_prices(tickers: tuple[str, ...]) -> pd.DataFrame:
    """Series de precios usadas como termometro macro (oro, cobre, dolar...)."""
    if not tickers:
        return pd.DataFrame()
    placeholders = ", ".join("?" for _ in tickers)
    return _fetch(
        f"""
        SELECT p.ticker, p.date, p.adj_close, inst.name
        FROM prices_daily p
        JOIN instruments inst ON inst.ticker = p.ticker
        WHERE p.ticker IN ({placeholders})
          AND p.date >= (SELECT MAX(date) FROM prices_daily) - INTERVAL 1500 DAY
        ORDER BY p.ticker, p.date
        """,
        list(tickers),
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_signal_evidence(scope: str | None = None) -> pd.DataFrame:
    """Etiquetas de evidencia historica por senal, ambito y horizonte."""
    if scope:
        return _fetch(
            """
            SELECT * FROM signal_evidence WHERE scope = ?
            ORDER BY signal_id, horizon_days
            """,
            [scope],
        )
    return _fetch("SELECT * FROM signal_evidence ORDER BY signal_id, horizon_days")


@st.cache_data(ttl=TTL, show_spinner=False)
def evidence_by_signal(scope: str = "equity_us", horizon: int = 21) -> dict[str, str]:
    """Mapa senal -> etiqueta, para marcar en gris las que no estan validadas."""
    df = _fetch(
        """
        SELECT signal_id, evidence FROM signal_evidence
        WHERE scope = ? AND horizon_days = ?
        """,
        [scope, horizon],
    )
    if df.empty:
        return {}
    return dict(zip(df["signal_id"], df["evidence"], strict=False))


@st.cache_data(ttl=TTL, show_spinner=False)
def validation_available() -> bool:
    df = _fetch("SELECT COUNT(*) AS n FROM signal_evidence")
    return bool(not df.empty and int(df.iloc[0]["n"]) > 0)


@st.cache_data(ttl=TTL, show_spinner=False)
def get_regime(days: int = 400) -> pd.DataFrame:
    return _fetch(
        "SELECT * FROM regime_daily ORDER BY date DESC LIMIT ?", [days]
    ).sort_values("date")


@st.cache_data(ttl=TTL, show_spinner=False)
def get_market_kpis() -> pd.DataFrame:
    """Indices y activos macro para la fila de indicadores de cabecera."""
    return _fetch(
        """
        SELECT i.ticker, inst.name, i.close, i.ret_1d, i.ret_5d, i.roc_1m,
               i.dist_52w_high, inst.asset_class
        FROM indicators_daily i
        JOIN instruments inst ON inst.ticker = i.ticker
        WHERE i.date = (SELECT MAX(date) FROM indicators_daily)
          AND inst.asset_class IN ('index', 'crypto', 'commodity', 'fx')
        ORDER BY inst.asset_class, i.ticker
        """
    )


# --------------------------------------------------------------------------
# Pagina 3: oportunidades
# --------------------------------------------------------------------------
@st.cache_data(ttl=TTL, show_spinner=False)
def get_candidates(universe: str = "TODOS", sectors: tuple[str, ...] = (),
                   limit: int = 200) -> pd.DataFrame:
    """Ranking con todo lo necesario para explicar cada candidato."""
    where, params = _universe_filter(universe)
    sector_clause = ""
    if sectors:
        placeholders = ", ".join("?" for _ in sectors)
        sector_clause = f" AND inst.gics_sector IN ({placeholders})"
        params = [*params, *sectors]

    return _fetch(
        f"""
        SELECT f.ticker, inst.name, inst.gics_sector, inst.investment_type,
               inst.market_cap, inst.currency, inst.tv_symbol,
               f.composite, f.composite_pctile, f.composite_rank_sector, f.coverage,
               f.value_z, f.growth_z, f.quality_z, f.momentum_z, f.lowvol_z,
               f.dividend_z, f.technical_z,
               i.close, i.ret_1d, i.rsi14, i.above_sma200, i.above_sma50,
               i.days_above_sma200, i.rel_volume_20, i.dist_52w_high,
               i.drawdown, i.realized_vol_252, i.atr_pct, i.mom_12_1,
               i.roc_6m, i.rs_vs_bench_3m, i.macd_hist, i.adx14,
               fu.trailing_pe, fu.price_to_book, fu.ev_to_ebitda, fu.fcf_yield,
               fu.roe, fu.profit_margin, fu.operating_margin,
               fu.revenue_growth_yoy, fu.earnings_growth_yoy,
               fu.net_debt_to_ebitda, fu.dividend_yield, fu.payout_ratio,
               fu.price_to_sales, fu.completeness
        FROM factor_scores f
        JOIN instruments inst ON inst.ticker = f.ticker
        JOIN indicators_daily i ON i.ticker = f.ticker AND i.date = f.date
        LEFT JOIN fundamentals_snapshot fu ON fu.ticker = f.ticker
             AND fu.as_of = (SELECT MAX(as_of) FROM fundamentals_snapshot
                             WHERE ticker = f.ticker)
        WHERE f.date = (SELECT MAX(date) FROM factor_scores)
          {where} {sector_clause}
        ORDER BY f.composite DESC
        LIMIT ?
        """,
        [*params, limit],
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_contributions(ticker: str) -> pd.DataFrame:
    return _fetch(
        """
        SELECT factor, zscore, weight, contribution
        FROM factor_contributions
        WHERE ticker = ? AND date = (SELECT MAX(date) FROM factor_contributions)
        ORDER BY ABS(contribution) DESC
        """,
        [ticker],
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_active_signals(ticker: str) -> list[str]:
    df = _fetch(
        """
        SELECT signal_id FROM signals
        WHERE ticker = ? AND date = (SELECT MAX(date) FROM signals)
        """,
        [ticker],
    )
    return df["signal_id"].tolist()


@st.cache_data(ttl=TTL, show_spinner=False)
def get_sector_medians(sector: str) -> pd.Series:
    """Medianas del sector, para poder decir 'PER 11 frente a 14,8 del sector'."""
    df = _fetch(
        """
        SELECT MEDIAN(fu.trailing_pe) AS trailing_pe,
               MEDIAN(fu.price_to_book) AS price_to_book,
               MEDIAN(fu.ev_to_ebitda) AS ev_to_ebitda,
               MEDIAN(fu.price_to_sales) AS price_to_sales,
               MEDIAN(fu.roe) AS roe,
               MEDIAN(fu.profit_margin) AS profit_margin,
               MEDIAN(fu.dividend_yield) AS dividend_yield,
               MEDIAN(fu.revenue_growth_yoy) AS revenue_growth_yoy,
               MEDIAN(fu.net_debt_to_ebitda) AS net_debt_to_ebitda
        FROM fundamentals_snapshot fu
        JOIN instruments inst ON inst.ticker = fu.ticker
        WHERE inst.gics_sector = ?
          AND fu.as_of = (SELECT MAX(as_of) FROM fundamentals_snapshot)
        """,
        [sector],
    )
    return df.iloc[0] if not df.empty else pd.Series(dtype=float)


@st.cache_data(ttl=TTL, show_spinner=False)
def get_sectors() -> list[str]:
    df = _fetch(
        """
        SELECT DISTINCT gics_sector FROM instruments
        WHERE gics_sector IS NOT NULL AND gics_sector <> '' ORDER BY 1
        """
    )
    return df["gics_sector"].tolist()


# --------------------------------------------------------------------------
# Pagina 4: ficha de valor
# --------------------------------------------------------------------------
@st.cache_data(ttl=TTL, show_spinner=False)
def get_price_history(ticker: str, days: int = 500) -> pd.DataFrame:
    return _fetch(
        """
        SELECT date, open, high, low, close, adj_close, volume
        FROM prices_daily WHERE ticker = ?
        ORDER BY date DESC LIMIT ?
        """,
        [ticker, days],
    ).sort_values("date")


@st.cache_data(ttl=TTL, show_spinner=False)
def get_indicator_history(ticker: str, days: int = 500) -> pd.DataFrame:
    return _fetch(
        """
        SELECT * FROM indicators_daily WHERE ticker = ?
        ORDER BY date DESC LIMIT ?
        """,
        [ticker, days],
    ).sort_values("date")


@st.cache_data(ttl=TTL, show_spinner=False)
def get_signal_history(ticker: str, days: int = 500) -> pd.DataFrame:
    return _fetch(
        """
        SELECT date, signal_id, direction, strength FROM signals
        WHERE ticker = ? ORDER BY date DESC LIMIT ?
        """,
        [ticker, days * 3],
    ).sort_values("date")


@st.cache_data(ttl=TTL, show_spinner=False)
def get_instrument(ticker: str) -> pd.Series | None:
    df = _fetch("SELECT * FROM instruments WHERE ticker = ?", [ticker])
    return None if df.empty else df.iloc[0]


@st.cache_data(ttl=TTL, show_spinner=False)
def get_fundamentals(ticker: str) -> pd.Series | None:
    df = _fetch(
        """
        SELECT * FROM fundamentals_snapshot WHERE ticker = ?
        ORDER BY as_of DESC LIMIT 1
        """,
        [ticker],
    )
    return None if df.empty else df.iloc[0]


@st.cache_data(ttl=TTL, show_spinner=False)
def all_tickers() -> list[str]:
    df = _fetch("SELECT ticker FROM instruments WHERE is_active ORDER BY ticker")
    return df["ticker"].tolist()


# --------------------------------------------------------------------------
# Watchlist (escritura: la unica excepcion al solo-lectura)
# --------------------------------------------------------------------------
def add_to_watchlist(ticker: str, price: float | None = None, note: str = "") -> None:
    from ..core.timeutils import utcnow

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO watchlist (ticker, list_name, added_at, added_price, note)
            VALUES (?, 'default', ?, ?, ?)
            ON CONFLICT (ticker, list_name) DO UPDATE SET note = excluded.note
            """,
            [ticker, utcnow(), price, note],
        )
    get_watchlist.clear()


def remove_from_watchlist(ticker: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM watchlist WHERE ticker = ? AND list_name = 'default'", [ticker]
        )
    get_watchlist.clear()


@st.cache_data(ttl=60, show_spinner=False)
def get_watchlist() -> pd.DataFrame:
    return _fetch(
        """
        SELECT w.ticker, inst.name, inst.gics_sector, w.added_at, w.added_price,
               w.note, i.close, i.ret_1d, f.composite_pctile
        FROM watchlist w
        LEFT JOIN instruments inst ON inst.ticker = w.ticker
        LEFT JOIN indicators_daily i ON i.ticker = w.ticker
             AND i.date = (SELECT MAX(date) FROM indicators_daily)
        LEFT JOIN factor_scores f ON f.ticker = w.ticker
             AND f.date = (SELECT MAX(date) FROM factor_scores)
        WHERE w.list_name = 'default'
        ORDER BY w.added_at DESC
        """
    )

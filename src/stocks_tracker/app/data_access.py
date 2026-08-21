"""Unica capa de lectura de la base de datos desde la interfaz.

Ninguna página escribe SQL suelto: todo pasa por aqui, cacheado. Asi la UI
responde en milisegundos y se puede cambiar el esquema en un solo sitio.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from ..compute.run_compute import sesiones_sin_calcular
from ..core import sesiones
from ..core.config import (
    get_active_universes,
    get_breadth_scope,
    get_settings,
    get_universes,
)
from ..core.db import connect
from ..core.scoring import preset_hash, preset_names
from ..core.textutils import as_text
from ..core.timeutils import hours_since
from ..ingest import run_ingest

TTL = 900  # 15 minutos: los datos se actualizan una vez al dia


def _fetch(sql: str, params: list | None = None) -> pd.DataFrame:
    with connect(read_only=True) as conn:
        return conn.execute(sql, params or []).fetchdf()


def default_preset() -> str:
    return str(get_settings().compute.get("weights_preset", "balanced"))


@st.cache_data(ttl=TTL, show_spinner=False)
def available_presets() -> list[str]:
    """Perfiles con scores ya calculados en el almacen.

    Ofrecer en el selector un perfil que nadie ha calculado dejaria la página
    vacia sin explicar por que.
    """
    stored = set(_fetch("SELECT DISTINCT weights_hash FROM factor_scores")["weights_hash"])
    return [name for name in preset_names() if preset_hash(name) in stored]


def _preset_hash(preset: str | None) -> str:
    """Hash del perfil pedido, con caida al que si este calculado.

    Toda consulta a `factor_scores` DEBE filtrar por este hash: los scores de
    todos los perfiles conviven en la misma tabla y, sin el filtro, cada valor
    aparece una vez por perfil.
    """
    if preset:
        return preset_hash(preset)
    available = available_presets()
    fallback = default_preset()
    if available and fallback not in available:
        fallback = available[0]
    return preset_hash(fallback)


@st.cache_data(ttl=TTL, show_spinner=False)
def last_price_date() -> date | None:
    """Fecha de la sesion que muestra el dashboard.

    No es el ultimo dia con precios, sino la sesion vigente (ver la vista
    `current_session`). La distincion importa: si la etiqueta dijera "cierre
    del 11" mientras las tablas muestran el 10, el usuario tendria otra vez
    numeros que no cuadran sin explicacion.
    """
    df = _fetch("SELECT date AS d FROM current_session")
    if df.empty or pd.isna(df.iloc[0]["d"]):
        # Sin indicadores todavia (instalacion recien hecha), el ultimo precio
        # es lo unico que hay.
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
    sin_calcular, por_que = sesiones_sin_calcular()
    sin_descargar = sesiones_sin_descargar()
    medias = sesiones_a_medias()
    return {
        "last_price_date": last_date,
        "last_run": last_run,
        "hours_since_run": hours,
        # TRES NUMEROS Y NO UNO. "El dashboard no avanza" son tres averias
        # distintas con tres arreglos distintos, y durante dos dias se
        # ensenaron como una sola:
        #
        #   - faltan precios           -> descargar
        #   - hay precios sin calcular -> calcular
        #   - hay sesiones A MEDIAS    -> volver a descargar ESAS sesiones
        #
        # La tercera no la reportaba nadie y era justo la que habia. Una
        # descarga que revienta a mitad deja la sesion con veinte valores de
        # seiscientos: existe, se calcula, y el dashboard no la ensena porque no
        # llega al 60 % de cobertura. Desde fuera se ve igual que las otras dos.
        "sesiones_sin_descargar": sin_descargar,
        "sesiones_sin_calcular": sin_calcular,
        "sesiones_a_medias": medias,
        "por_que_sin_calcular": por_que,
        # `df.empty` NO basta, y es la trampa que tumbaba la pagina de estado en
        # una instalacion recien hecha. Un `SELECT SUM(...)` sin `GROUP BY`
        # SIEMPRE devuelve una fila: sobre una tabla vacia devuelve una fila con
        # NULL, asi que `df.empty` es falso. Y `NaN or 0` no salva nada, porque
        # NaN es verdadero: se colaba entero hasta `int(NaN)`, que revienta.
        "failures": _entero(df.iloc[0]["failures"]) if not df.empty else 0,
        "warn_hours": warn_hours,
        "is_stale": (hours is not None and hours > warn_hours)
                    or sin_descargar > 0 or sin_calcular > 0 or bool(medias),
    }


def sesiones_sin_descargar() -> int:
    """Sesiones de mercado ya cerradas que no estan COMPLETAS en el almacen.

    Completas y no "presentes": con 623 valores, `MAX(date)` se satisface con
    UNO. Cuando la descarga reventaba a mitad y entraban tres indices, el
    maximo decia "ayer" y todo el que preguntaba recibia un si con 620 valores
    sin bajar.

    Los festivos inflan la cuenta —no se conocen aqui— asi que es un TECHO. Para
    decidir si avisar vale: cero significa "no falta nada" con certeza.
    """
    with connect(read_only=True) as conn:
        completa = sesiones.ultima_completa(conn, "prices_daily")
        # EL MISMO ORACULO QUE USA LA INGESTA. Sin esto, en un festivo la
        # pantalla contaba sesiones que no existen y daba la murga para
        # reiniciar, mientras el lanzador decia "al dia" con razon. Dos partes
        # del programa contestando cosas distintas a la misma pregunta es lo que
        # hace que el usuario deje de creerse las dos.
        llego = sesiones.ultima_de_los_indices(conn)
    tope = run_ingest.ultima_sesion_cerrada()
    return sesiones.sesiones_de_mercado(completa, min(llego, tope) if llego else tope)


def sesiones_a_medias() -> list[tuple]:
    """Sesiones que estan en el almacen y el dashboard NO ensena.

    Existen, se han calculado, y no llegan al 60 % de cobertura que exige la
    sesion vigente. Es lo que deja una descarga que revienta a mitad, y el unico
    de los tres problemas de frescura que no se veia por ningun sitio: ni sale
    en descargas fallidas, ni en sesiones sin calcular, ni en sesiones sin
    descargar. Simplemente el dashboard no avanzaba.
    """
    with connect(read_only=True) as conn:
        return sesiones.incompletas(conn, "indicators_daily")


def _entero(valor) -> int:
    """Un agregado de SQL convertido a entero, contando el vacio como cero."""
    return 0 if valor is None or pd.isna(valor) else int(valor)


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
    "Invalid symbol" y pareceria que la aplicacion está rota.
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
             AND f.weights_hash = ?
        WHERE i.date = (SELECT date FROM current_session)
          AND inst.asset_class IN ('equity', 'etf')
          AND i.ret_1d IS NOT NULL
          AND i.close * (SELECT volume FROM prices_daily p
                         WHERE p.ticker = i.ticker AND p.date = i.date) > ?
          {where}
        ORDER BY i.ret_1d {order}
        LIMIT ?
        """,
        [_preset_hash(None), min_dollar_volume, *params, n],
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
               i.rel_volume_20, f.composite_pctile, i.{col} AS distancia
        FROM indicators_daily i
        JOIN ranked r ON r.ticker = i.ticker AND r.date = i.date
        JOIN indicators_daily prev ON prev.ticker = i.ticker AND prev.date = r.prev_date
        JOIN instruments inst ON inst.ticker = i.ticker
        LEFT JOIN factor_scores f ON f.ticker = i.ticker AND f.date = i.date
             AND f.weights_hash = ?
        WHERE i.date = (SELECT date FROM current_session)
          AND inst.asset_class IN ('equity', 'etf')
          AND {cond}
          {where}
        ORDER BY i.ret_1d DESC
        """,
        [_preset_hash(None), *params],
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_volume_spikes(universe: str = "TODOS", threshold: float = 2.0,
                      n: int = 15) -> pd.DataFrame:
    where, params = _universe_filter(universe)
    return _fetch(
        f"""
        SELECT i.ticker, inst.name, inst.gics_sector, i.close, i.ret_1d,
               i.rel_volume_20, i.rsi14, f.composite_pctile
        FROM indicators_daily i
        JOIN instruments inst ON inst.ticker = i.ticker
        LEFT JOIN factor_scores f ON f.ticker = i.ticker AND f.date = i.date
             AND f.weights_hash = ?
        WHERE i.date = (SELECT date FROM current_session)
          AND i.rel_volume_20 > ?
          AND inst.asset_class IN ('equity', 'etf')
          {where}
        ORDER BY i.rel_volume_20 DESC
        LIMIT ?
        """,
        [_preset_hash(None), threshold, *params, n],
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
        WHERE i.date = (SELECT date FROM current_session)
          AND inst.gics_sector IS NOT NULL AND inst.asset_class = 'equity'
        GROUP BY 1
        HAVING COUNT(*) >= 3
        ORDER BY ret_1d DESC
        """
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_breadth(scope: str | None = None, days: int = 400) -> pd.DataFrame:
    """Serie de amplitud. Sin ámbito, el configurado en `universe.yaml`."""
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
        WHERE i.date = (SELECT date FROM current_session)
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
    """Etiquetas de evidencia historica por señal, ámbito y horizonte."""
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
    """Mapa señal -> etiqueta, para marcar en gris las que no estan validadas."""
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
def coverage_by_universe() -> pd.DataFrame:
    """Cuanta informacion fundamental hay, por universo.

    Es el diagnóstico que mas falta hacia para Europa: Yahoo deja muchos
    campos vacios fuera de Estados Unidos, y un valor con la mitad de los
    datos no compite en igualdad con uno que los tiene todos. El score ya
    penaliza por cobertura, pero sin verlo aqui no hay forma de saber si un
    universo entero esta jugando en desventaja.
    """
    return _fetch(
        """
        SELECT m.universe,
               COUNT(DISTINCT m.ticker) AS instrumentos,
               COUNT(DISTINCT fu.ticker) AS con_fundamentales,
               AVG(fu.completeness) AS cobertura_media,
               COUNT(DISTINCT CASE WHEN inst.gics_sector IS NULL THEN m.ticker END)
                   AS sin_sector,
               COUNT(DISTINCT CASE WHEN i.ticker IS NULL THEN m.ticker END)
                   AS sin_precio,
               -- Solo las acciones y los ETF entran en el ranking. Un universo
               -- de indices sin fundamentales no esta en desventaja: es que no
               -- compite.
               COUNT(DISTINCT CASE WHEN inst.asset_class IN ('equity', 'etf')
                                   THEN m.ticker END) AS puntuables
        FROM universe_membership m
        JOIN instruments inst ON inst.ticker = m.ticker
        LEFT JOIN (
            SELECT f.ticker, f.completeness FROM fundamentals_snapshot f
            JOIN (SELECT ticker, MAX(as_of) AS as_of
                  FROM fundamentals_snapshot GROUP BY ticker) l
              USING (ticker, as_of)
        ) fu ON fu.ticker = m.ticker
        LEFT JOIN indicators_daily i ON i.ticker = m.ticker
             AND i.date = (SELECT date FROM current_session)
        WHERE m.valid_to IS NULL
        GROUP BY m.universe
        ORDER BY cobertura_media
        """
    )


@st.cache_data(ttl=60, show_spinner=False)
def data_origin() -> dict:
    """De donde salen los precios que se estan mostrando.

    Existe por un fallo real de confianza: alguien instalo el programa, vio el
    S&P 500 a 8.489 cuando el mercado habia cerrado a 7.757, y penso —con toda
    la razón— que los calculos estaban mal. No lo estaban: eran los datos de
    prueba que genera el instalador. Un dashboard financiero que no distingue a
    simple vista un precio real de uno inventado no vale para nada, por bien
    que calcule.
    """
    df = _fetch(
        "SELECT source, COUNT(*) AS filas FROM prices_daily GROUP BY source"
    )
    if df.empty:
        return {"empty": True, "synthetic": False, "synthetic_share": 0.0,
                "sources": []}

    total = float(df["filas"].sum())
    synthetic = float(
        df[df["source"] == "synthetic"]["filas"].sum()
    ) if "synthetic" in set(df["source"]) else 0.0

    return {
        "empty": False,
        "synthetic": synthetic > 0,
        "synthetic_share": synthetic / total if total else 0.0,
        "sources": sorted(df["source"].tolist()),
    }


@st.cache_data(ttl=TTL, show_spinner=False)
def price_sources() -> pd.DataFrame:
    """De donde viene cada serie de precios, y cuales mezclan fuentes.

    Una serie con dos fuentes tiene un salto artificial el dia del relevo:
    Yahoo ajusta el cierre por dividendos y Stooq no.
    """
    return _fetch(
        """
        SELECT source AS fuente,
               COUNT(DISTINCT ticker) AS instrumentos,
               COUNT(*) AS filas,
               MAX(date) AS hasta
        FROM prices_daily
        GROUP BY source
        ORDER BY filas DESC
        """
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def mixed_source_series() -> pd.DataFrame:
    return _fetch(
        """
        SELECT ticker,
               string_agg(DISTINCT source, ', ') AS fuentes,
               COUNT(DISTINCT source) AS n_fuentes
        FROM prices_daily
        GROUP BY ticker
        HAVING COUNT(DISTINCT source) > 1
        ORDER BY ticker
        """
    )


def regime_components(row) -> dict[str, float]:
    """Desglose del semaforo del dia, ordenado por magnitud.

    Se guarda como texto de un diccionario de Python, no como JSON, asi que
    `json.loads` no vale. `ast.literal_eval` lo lee sin ejecutar nada.
    """
    import ast

    raw = row.get("components") if hasattr(row, "get") else None
    if not raw:
        return {}
    try:
        parsed = ast.literal_eval(str(raw))
    except (ValueError, SyntaxError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    clean = {
        str(k): float(v)
        for k, v in parsed.items()
        if isinstance(v, (int, float)) and float(v) == float(v)
    }
    return dict(sorted(clean.items(), key=lambda kv: abs(kv[1]), reverse=True))


@st.cache_data(ttl=TTL, show_spinner=False)
def get_market_kpis() -> pd.DataFrame:
    """Indices y activos macro para la fila de indicadores de cabecera."""
    return _fetch(
        """
        SELECT i.ticker, inst.name, i.close, i.ret_1d, i.ret_5d, i.roc_1m,
               i.dist_52w_high, inst.asset_class
        FROM indicators_daily i
        JOIN instruments inst ON inst.ticker = i.ticker
        WHERE i.date = (SELECT date FROM current_session)
          AND inst.asset_class IN ('index', 'crypto', 'commodity', 'fx')
        ORDER BY inst.asset_class, i.ticker
        """
    )


# --------------------------------------------------------------------------
# Pagina 3: oportunidades
# --------------------------------------------------------------------------
@st.cache_data(ttl=TTL, show_spinner=False)
def get_candidates(universe: str = "TODOS", sectors: tuple[str, ...] = (),
                   limit: int = 200, preset: str | None = None) -> pd.DataFrame:
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
        WHERE f.weights_hash = ?
          AND f.date = (SELECT date FROM current_session)
          {where} {sector_clause}
        ORDER BY f.composite DESC
        LIMIT ?
        """,
        [_preset_hash(preset), *params, limit],
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def get_contributions(ticker: str, preset: str | None = None) -> pd.DataFrame:
    return _fetch(
        """
        SELECT factor, zscore, weight, contribution
        FROM factor_contributions
        WHERE ticker = ? AND weights_hash = ?
          AND date = (SELECT MAX(date) FROM factor_contributions)
        ORDER BY ABS(contribution) DESC
        """,
        [ticker, _preset_hash(preset)],
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
    """Medianas del sector, para poder decir 'PER 11 frente a 14,8 del sector'.

    El parametro se normaliza aqui ademas de en quien llama: es la ultima
    frontera antes de la base de datos, y un hueco de pandas colandose como
    parametro tumba la página con un error sobre conversiones a DOUBLE que no
    menciona ni el sector ni el ticker.
    """
    sector = as_text(sector)
    if not sector:
        return pd.Series(dtype=float)
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


# --------------------------------------------------------------------------
# Alertas
# --------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def get_alerts(days: int = 30, only_pending: bool = False) -> pd.DataFrame:
    clause = " AND NOT acknowledged" if only_pending else ""
    return _fetch(
        f"""
        SELECT a.*, inst.name, inst.gics_sector
        FROM alerts a
        LEFT JOIN instruments inst ON inst.ticker = a.ticker
        WHERE a.triggered_at >= (CURRENT_TIMESTAMP - INTERVAL (?) DAY) {clause}
        ORDER BY a.triggered_at DESC
        """,
        [days],
    )


@st.cache_data(ttl=60, show_spinner=False)
def count_pending_alerts() -> int:
    df = _fetch("SELECT COUNT(*) AS n FROM alerts WHERE NOT acknowledged")
    return int(df.iloc[0]["n"]) if not df.empty else 0


# --------------------------------------------------------------------------
# Cartera
# --------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def get_positions() -> pd.DataFrame:
    """Posiciones abiertas con su valoracion actual."""
    return _fetch(
        """
        SELECT p.id, p.ticker, p.qty, p.avg_cost, p.currency, p.opened_at, p.note,
               inst.name, inst.gics_sector, inst.investment_type,
               i.close, i.ret_1d, i.above_sma200, i.drawdown, i.realized_vol_252,
               f.composite_pctile, f.value_z, f.growth_z, f.quality_z,
               f.momentum_z, f.lowvol_z, f.dividend_z, f.technical_z
        FROM positions p
        LEFT JOIN instruments inst ON inst.ticker = p.ticker
        LEFT JOIN indicators_daily i ON i.ticker = p.ticker
             AND i.date = (SELECT date FROM current_session)
        LEFT JOIN factor_scores f ON f.ticker = p.ticker
             AND f.date = (SELECT date FROM current_session)
             AND f.weights_hash = ?
        WHERE p.closed_at IS NULL AND p.qty > 0
        ORDER BY p.ticker
        """,
        [_preset_hash(None)],
    )


def add_position(ticker: str, qty: float, avg_cost: float,
                 currency: str = "USD", note: str = "") -> None:
    import uuid

    from ..core.timeutils import utcnow

    with connect() as conn:
        conn.execute(
            "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            [str(uuid.uuid4()), ticker, float(qty), float(avg_cost), currency,
             date.today(), note, utcnow()],
        )
    get_positions.clear()


def replace_positions(frame: pd.DataFrame, note: str = "") -> int:
    """Sustituye la cartera entera por la importada.

    Se reemplaza en vez de anadir porque un extracto es una FOTO completa de lo
    que tienes. Anadiendo, reimportar el mismo fichero duplicaria cada
    posicion; y una posicion que vendiste seguiria contando para siempre.

    Se hace en una transaccion: si algo falla a medias, es preferible quedarse
    con la cartera anterior que con media importada.
    """
    import uuid

    from ..core.timeutils import utcnow

    if frame is None or frame.empty:
        return 0

    now = utcnow()
    today = date.today()
    rows = [
        (
            str(uuid.uuid4()), str(r["ticker"]), float(r["qty"]), float(r["avg_cost"]),
            as_text(r.get("currency")) or "EUR", today, None, note, now,
        )
        for _, r in frame.iterrows()
    ]

    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM positions WHERE closed_at IS NULL")
            conn.executemany(
                "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    get_positions.clear()
    return len(rows)


def close_position(position_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE positions SET closed_at = ? WHERE id = ?", [date.today(), position_id]
        )
    get_positions.clear()
    get_closed_sales.clear()


# Cuantos dias de margen se admiten entre la fecha de venta y la ultima sesion
# con precio. Un fin de semana o un festivo largo caben; si hay que retroceder
# mas, el precio ya no representa el dia de la venta y es mejor no estimar.
_MAX_DIAS_SIN_PRECIO = 7


@st.cache_data(ttl=60, show_spinner=False)
def get_closed_sales(ticker: str, days: int = 400) -> pd.DataFrame:
    """Ventas cerradas de un valor con el resultado ESTIMADO de cada una.

    `positions` guarda a que precio se compro pero no a que precio se vendio,
    asi que el resultado no se puede saber: se estima con el cierre del dia de
    la venta. Es una aproximacion —el precio de ejecucion no es el de cierre y
    `avg_cost` no lleva comisiones— y por eso se devuelve el porcentaje y quien
    lo use decide con margen.

    Sin precio para ese dia, `resultado_pct` sale nulo: significa "no se sabe",
    que no es lo mismo que "fue ganancia".
    """
    # El limite de antiguedad va FUERA del ASOF y no dentro: DuckDB solo admite
    # una desigualdad en la condicion del ASOF, y con dos falla al enlazar la
    # consulta. Se une por la sesion anterior mas cercana y se descarta despues
    # si quedo demasiado lejos.
    return _fetch(
        f"""
        WITH cerradas AS (
            SELECT p.closed_at, p.qty, p.avg_cost, p.currency,
                   pr.close AS cierre, pr.date AS fecha_cierre
            FROM positions p
            ASOF LEFT JOIN prices_daily pr
                 ON pr.ticker = p.ticker AND pr.date <= p.closed_at
            WHERE p.ticker = ?
              AND p.closed_at IS NOT NULL
              AND p.closed_at >= CURRENT_DATE - INTERVAL (?) DAY
        ),
        vigentes AS (
            SELECT *, CASE
                WHEN fecha_cierre IS NOT NULL
                 AND date_diff('day', fecha_cierre, closed_at)
                     <= {_MAX_DIAS_SIN_PRECIO}
                THEN cierre END AS precio_estimado
            FROM cerradas
        )
        SELECT closed_at, qty, avg_cost, currency, precio_estimado,
               CASE WHEN avg_cost > 0 AND precio_estimado IS NOT NULL
                    THEN (precio_estimado / avg_cost - 1) * 100.0
               END AS resultado_pct
        FROM vigentes
        ORDER BY closed_at DESC
        """,
        [ticker, days],
    )


# Los campos que necesita `core.deterioration`. En una lista y no incrustados
# en el SQL para que anadir una comprobacion alli sea una linea aqui.
_CAMPOS_FUND = ("profit_margin", "roe", "revenue_growth_yoy",
                "net_debt_to_ebitda", "payout_ratio")
_CAMPOS_IND = ("above_sma200", "death_cross", "drawdown", "rs_vs_bench_3m",
               "realized_vol_20", "realized_vol_252")


@st.cache_data(ttl=TTL, show_spinner=False)
def get_position_health() -> pd.DataFrame:
    """Cada posicion abierta con sus datos de HOY y los del dia que la compraste.

    Las columnas del pasado llevan el sufijo `_entonces`. Se traen con una
    union ASOF sobre `as_of <= opened_at`, la misma idea punto-en-el-tiempo que
    impide que el ranking historico se sepa el futuro: comparar contra la foto
    de hoy en los dos lados no compararia nada y todo saldria en verde.

    Una posicion comprada antes de que existiera el historico de fundamentales
    no tiene columnas `_entonces`: salen nulas, y el diagnóstico se queda en lo
    que se puede mirar solo con el presente.
    """
    fund = ", ".join(f"f.{c} AS {c}_entonces" for c in _CAMPOS_FUND)
    ind = ", ".join(f"i.{c} AS {c}_entonces" for c in _CAMPOS_IND)
    entonces = _fetch(
        f"""
        WITH abiertas AS (
            SELECT ticker, MIN(opened_at) AS opened_at
            FROM positions WHERE closed_at IS NULL AND qty > 0
            GROUP BY ticker
        ),
        con_fund AS (
            SELECT a.ticker, a.opened_at, {fund}
            FROM abiertas a
            ASOF LEFT JOIN fundamentals_snapshot f
                 ON f.ticker = a.ticker AND f.as_of <= a.opened_at
        )
        SELECT c.*, {ind}
        FROM con_fund c
        ASOF LEFT JOIN indicators_daily i
             ON i.ticker = c.ticker AND i.date <= c.opened_at
        """
    )

    hoy = _fetch(
        f"""
        SELECT a.ticker,
               {", ".join(f"f.{c}" for c in _CAMPOS_FUND)},
               {", ".join(f"i.{c}" for c in _CAMPOS_IND)}
        FROM (SELECT DISTINCT ticker FROM positions
              WHERE closed_at IS NULL AND qty > 0) a
        LEFT JOIN indicators_daily i ON i.ticker = a.ticker
             AND i.date = (SELECT date FROM current_session)
        LEFT JOIN (
            SELECT * FROM fundamentals_snapshot
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY as_of DESC) = 1
        ) f ON f.ticker = a.ticker
        """
    )
    if hoy.empty:
        return hoy
    return hoy.merge(entonces, on="ticker", how="left")


# Proxy del mercado. Es un ETF con precio real en el almacen, no un indice
# teorico: se puede comprar, que es justo la alternativa contra la que tiene
# sentido compararse ("¿habria ganado mas sin hacer nada?").
MERCADO_TICKER = "SPY"


@st.cache_data(ttl=TTL, show_spinner=False)
def get_attribution_inputs() -> pd.DataFrame:
    """Cada posicion con lo que hicieron el mercado y su sector MIENTRAS la tenias.

    Las referencias se miden en la ventana de cada posicion, desde su
    `opened_at` hasta hoy. Compararlas todas contra el mismo periodo —el año
    del indice, pongamos— daria un numero limpio y sin sentido: una compra de
    hace un mes no compite contra doce meses de mercado.

    El sector se aproxima con su ETF (`sector_etfs` en `universe.yaml`). Sin
    ETF o sin sector asignado, `retorno_sector` sale nulo y quien lo use no
    debe separar el efecto sector del de seleccion.
    """
    from ..core.config import get_sector_etfs

    por_sector = {sector: etf for etf, sector in get_sector_etfs().items()}
    if not por_sector:
        return pd.DataFrame()

    filas = ", ".join("(?, ?)" for _ in por_sector)
    params: list = []
    for sector, etf in por_sector.items():
        params.extend([sector, etf])

    return _fetch(
        f"""
        WITH etf_de_sector(sector, etf) AS (VALUES {filas}),
        -- Una fila por COMPRA, no por valor. Dos lotes del mismo valor
        -- comprados con seis meses de diferencia son dos decisiones distintas
        -- con dos ventanas distintas; agruparlos obligaria a inventarse una
        -- fecha de entrada comun y a comparar la segunda compra contra un
        -- mercado que ya habia pasado.
        abiertas AS (
            SELECT p.id, p.ticker, p.opened_at, p.qty, p.qty * p.avg_cost AS coste,
                   inst.gics_sector AS sector
            FROM positions p
            LEFT JOIN instruments inst ON inst.ticker = p.ticker
            WHERE p.closed_at IS NULL AND p.qty > 0 AND p.avg_cost > 0
        ),
        -- Precio de cada referencia el dia de la compra. ASOF por la sesion
        -- anterior mas cercana: una compra en fin de semana o festivo no puede
        -- quedarse sin comparacion.
        con_mercado AS (
            SELECT a.*, m.adj_close AS mercado_entonces
            FROM abiertas a
            ASOF LEFT JOIN (
                SELECT date, close AS adj_close FROM prices_daily WHERE ticker = ?
            ) m ON m.date <= a.opened_at
        ),
        con_etf AS (
            SELECT c.*, e.etf
            FROM con_mercado c LEFT JOIN etf_de_sector e ON e.sector = c.sector
        ),
        con_sector AS (
            SELECT c.*, s.adj_close AS sector_entonces
            FROM con_etf c
            ASOF LEFT JOIN (
                SELECT ticker, date, close AS adj_close FROM prices_daily
            ) s ON s.ticker = c.etf AND s.date <= c.opened_at
        ),
        -- `close` y no `adj_close` en TODAS las patas. Tu retorno se mide
        -- desde `avg_cost`, que es el precio bruto que pagaste; midiendo las
        -- referencias con el ajustado se les regalaban los dividendos
        -- reinvertidos y el mercado salia por delante en la rentabilidad por
        -- dividendo del indice —cerca de dos puntos al ano— sin que nada
        -- fallara. La pantalla ya avisa de que esto ignora dividendos: ahora
        -- los ignora en los dos lados.
        ultimo AS (
            SELECT ticker, LAST(close ORDER BY date) AS cierre
            FROM prices_daily GROUP BY ticker
        )
        SELECT c.id, c.ticker, c.sector, c.etf, c.opened_at, c.qty, c.coste,
               date_diff('day', c.opened_at, CURRENT_DATE) AS dias,
               p.cierre / (c.coste / c.qty) - 1.0 AS retorno,
               m.cierre / NULLIF(c.mercado_entonces, 0) - 1.0 AS retorno_mercado,
               s.cierre / NULLIF(c.sector_entonces, 0) - 1.0 AS retorno_sector
        FROM con_sector c
        LEFT JOIN ultimo p ON p.ticker = c.ticker
        LEFT JOIN ultimo m ON m.ticker = ?
        LEFT JOIN ultimo s ON s.ticker = c.etf
        ORDER BY c.coste DESC
        """,
        [*params, MERCADO_TICKER, MERCADO_TICKER],
    )


# Cuanto puede alejarse el precio disponible de la fecha pedida. Un puente
# largo cabe; mas alla, el precio ya no es el de esa fecha y usarlo convertiria
# el escenario en otro distinto sin decirlo.
_MARGEN_ESCENARIO_DIAS = 10


@st.cache_data(ttl=TTL, show_spinner=False)
def get_window_returns(tickers: tuple[str, ...], desde: date,
                       hasta: date) -> dict:
    """Lo que hizo de verdad cada valor entre dos fechas.

    Solo devuelve los que tienen precio a los DOS lados de la ventana. Un valor
    que empezo a cotizar a mitad del escenario daria un retorno medido desde su
    primer dia, que no es lo que paso: seria una caida recortada justo por
    donde mas cayo.
    """
    if not tickers:
        return {}
    # Las fechas objetivo van como COLUMNA y no como parametro: un ASOF exige
    # comparar dos columnas, y con `pr.date <= ?` DuckDB responde "Missing ASOF
    # JOIN inequality" porque eso es un filtro, no una desigualdad de union.
    df = _fetch(
        f"""
        WITH pedidos(ticker) AS (VALUES {', '.join('(?)' for _ in tickers)}),
        objetivo AS (
            SELECT p.ticker, CAST(? AS DATE) AS ini, CAST(? AS DATE) AS fin
            FROM pedidos p
        ),
        inicio AS (
            SELECT o.ticker, o.ini, o.fin,
                   pr.adj_close AS precio, pr.date AS fecha
            FROM objetivo o
            ASOF LEFT JOIN prices_daily pr
                 ON pr.ticker = o.ticker AND pr.date <= o.ini
        ),
        final AS (
            SELECT o.ticker, pr.adj_close AS precio, pr.date AS fecha
            FROM objetivo o
            ASOF LEFT JOIN prices_daily pr
                 ON pr.ticker = o.ticker AND pr.date <= o.fin
        )
        SELECT i.ticker, f.precio / NULLIF(i.precio, 0) - 1.0 AS retorno
        FROM inicio i JOIN final f USING (ticker)
        WHERE i.precio IS NOT NULL AND f.precio IS NOT NULL
          AND i.fecha >= i.ini - INTERVAL {_MARGEN_ESCENARIO_DIAS} DAY
          AND f.fecha >= i.fin - INTERVAL {_MARGEN_ESCENARIO_DIAS} DAY
          AND i.fecha < f.fecha
        """,
        [*tickers, desde, hasta],
    )
    if df.empty:
        return {}
    return {str(r.ticker): float(r.retorno) for r in df.itertuples()
            if r.retorno is not None and pd.notna(r.retorno)}


@st.cache_data(ttl=TTL, show_spinner=False)
def get_sector_window_returns(desde: date, hasta: date) -> dict:
    """Lo mismo por sector, usando su ETF. Indexado por nombre de sector."""
    from ..core.config import get_sector_etfs

    etfs = get_sector_etfs()
    if not etfs:
        return {}
    por_etf = get_window_returns(tuple(sorted(etfs)), desde, hasta)
    return {sector: por_etf[etf] for etf, sector in etfs.items()
            if etf in por_etf}


@st.cache_data(ttl=TTL, show_spinner=False)
def get_realized_vol(tickers: tuple[str, ...]) -> dict:
    """Volatilidad anual de cada valor, para pesar el riesgo de cada apuesta."""
    if not tickers:
        return {}
    huecos = ", ".join("?" for _ in tickers)
    df = _fetch(
        f"""
        SELECT ticker, realized_vol_252 AS vol FROM indicators_daily
        WHERE ticker IN ({huecos})
          AND date = (SELECT date FROM current_session)
        """,
        list(tickers),
    )
    if df.empty:
        return {}
    return {str(r.ticker): float(r.vol) for r in df.itertuples()
            if r.vol is not None and pd.notna(r.vol) and r.vol > 0}


@st.cache_data(ttl=TTL, show_spinner=False)
def get_fundamentals_pair(ticker: str) -> tuple:
    """La foto de fundamentales mas reciente y la anterior.

    Las dos hacen falta para el contraste temporal: un ratio que se multiplica
    por diez de una descarga a la siguiente casi nunca es la empresa. Existe
    desde que se guarda el historico punto-en-el-tiempo; antes solo habia una
    foto y esta comprobacion no se podia hacer.
    """
    df = _fetch(
        "SELECT * FROM fundamentals_snapshot WHERE ticker = ? "
        "ORDER BY as_of DESC LIMIT 2",
        [ticker],
    )
    if df.empty:
        return None, None
    ultima = df.iloc[0]
    anterior = df.iloc[1] if len(df) > 1 else None
    return ultima, anterior


@st.cache_data(ttl=TTL, show_spinner=False)
def get_beta_from_prices(ticker: str, days: int = 400) -> float | None:
    """Beta calculada con NUESTRAS cotizaciones, no con la que declaran.

    Es el unico contraste de verdad independiente que se puede hacer sin pagar
    una segunda fuente de fundamentales: el proveedor dice un numero y aqui se
    calcula por separado con datos que no vienen de el.
    """
    from ..core.consistency import beta_desde_precios

    if ticker == MERCADO_TICKER:
        return None
    df = _fetch(
        """
        SELECT ticker, date, ret_1d FROM indicators_daily
        WHERE ticker IN (?, ?)
          AND date >= (SELECT date FROM current_session) - INTERVAL (?) DAY
        """,
        [ticker, MERCADO_TICKER, days],
    )
    if df.empty:
        return None
    ancho = df.pivot_table(index="date", columns="ticker", values="ret_1d")
    if ticker not in ancho or MERCADO_TICKER not in ancho:
        return None
    juntos = ancho[[ticker, MERCADO_TICKER]].dropna()
    if juntos.empty:
        return None
    return beta_desde_precios(juntos[ticker].to_numpy(),
                              juntos[MERCADO_TICKER].to_numpy())


@st.cache_data(ttl=TTL, show_spinner=False)
def review_fundamentals(ticker: str):
    """Todo lo que contradice a los fundamentales de un valor."""
    from ..core.consistency import revisar

    ultima, anterior = get_fundamentals_pair(ticker)
    if ultima is None:
        return revisar(ticker, None)

    # `close` y NO `adj_close`: el ajustado corrige splits y dividendos, asi que
    # no es el precio al que cotiza hoy y multiplicado por las acciones NO da la
    # capitalizacion. Con el ajustado, cualquier valor con historia de
    # dividendos salia marcado —el 100 % del universo—, que es la forma de
    # fallar mas inutil: un aviso que sale siempre no distingue nada.
    if data_origin().get("synthetic"):
        cierre = None          # precios inventados: no contrastan nada
    else:
        precio = _fetch(
            "SELECT LAST(close ORDER BY date) AS cierre FROM prices_daily "
            "WHERE ticker = ?", [ticker],
        )
        cierre = (float(precio.iloc[0]["cierre"])
                  if not precio.empty and pd.notna(precio.iloc[0]["cierre"])
                  else None)

    sector = _fetch("SELECT gics_sector FROM instruments WHERE ticker = ?", [ticker])
    return revisar(ticker, ultima, precio=cierre,
                   beta_calculada=get_beta_from_prices(ticker),
                   anterior=anterior,
                   sector=(str(sector.iloc[0]["gics_sector"])
                           if not sector.empty and pd.notna(sector.iloc[0]["gics_sector"])
                           else None))


@st.cache_data(ttl=TTL, show_spinner=False)
def review_all_fundamentals(limit: int = 2000) -> pd.DataFrame:
    """Los valores cuyos fundamentales se contradicen, los peores primero.

    Se recorre el universo entero y no solo la cartera: un dato roto importa
    sobre todo en los valores que TODAVIA no tienes, porque es ahi donde un PER
    inventado de 3 te hace comprar.

    Tres consultas en total y no tres por valor. Con una consulta por ticker
    esto tardaba minuto y medio con 600 instrumentos y la página se quedaba en
    blanco mientras tanto — que es indistinguible de estar rota.

    La beta se deja fuera del barrido a proposito: exige cruzar los retornos de
    cada valor con los del mercado uno a uno, y es lo que hacia lento el
    recorrido. Ese contraste está en la ficha de cada valor, donde se calcula
    solo el que se está mirando.
    """
    from ..core.consistency import revisar

    fotos = _fetch(
        f"""
        SELECT * FROM fundamentals_snapshot
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY as_of DESC) <= 2
        -- Ordenado antes de cortar: sin esto el LIMIT puede quedarse con una
        -- sola de las dos fotos de un valor, y ese valor pierde el contraste
        -- temporal en silencio. Ademas se corta por PAREJAS, no por filas.
        ORDER BY ticker, as_of DESC
        LIMIT {int(limit) * 2}
        """
    )
    if fotos.empty:
        return pd.DataFrame(columns=["ticker", "rotos", "avisos", "campos",
                                     "detalle"])

    # Con precios sinteticos no se contrasta la capitalizacion. El simulador
    # inventa los precios y los fundamentales por separado, asi que no cuadran
    # nunca: el aviso saltaria en el 95 % del universo y lo unico que ensenaria
    # es a ignorar los avisos antes incluso de tener datos reales.
    if data_origin().get("synthetic"):
        por_ticker: dict = {}
    else:
        # `close` y no `adj_close`: el ajustado corrige splits y dividendos y
        # no sirve para contrastar la capitalizacion.
        precios = _fetch(
            "SELECT ticker, LAST(close ORDER BY date) AS cierre "
            "FROM prices_daily GROUP BY ticker"
        )
        por_ticker = {str(r.ticker): float(r.cierre)
                      for r in precios.itertuples()
                      if r.cierre is not None and pd.notna(r.cierre)}

    # El sector hace falta para no aplicar las identidades del margen a bancos
    # y aseguradoras, donde el margen bruto no significa nada. Sin esto la
    # tabla se llena de financieras que no tienen ningun problema, y la pagina
    # entrena a ignorarla. Ver `consistency.sin_margen_bruto`.
    sectores = {
        str(r.ticker): (str(r.gics_sector) if pd.notna(r.gics_sector) else None)
        for r in _fetch("SELECT ticker, gics_sector FROM instruments").itertuples()
    }

    fotos = fotos.sort_values(["ticker", "as_of"], ascending=[True, False])
    filas = []
    for ticker, grupo in fotos.groupby("ticker", sort=False):
        ultima = grupo.iloc[0]
        anterior = grupo.iloc[1] if len(grupo) > 1 else None
        rev = revisar(str(ticker), ultima, precio=por_ticker.get(str(ticker)),
                      anterior=anterior, sector=sectores.get(str(ticker)))
        if rev.avisos:
            filas.append({
                "ticker": str(ticker),
                "rotos": len(rev.rotos),
                "avisos": len(rev.avisos),
                "campos": ", ".join(sorted(rev.campos_sospechosos)),
                "detalle": " · ".join(a.texto for a in rev.avisos),
            })
    if not filas:
        return pd.DataFrame(columns=["ticker", "rotos", "avisos", "campos",
                                     "detalle"])
    return pd.DataFrame(filas).sort_values(["rotos", "avisos"],
                                           ascending=False, ignore_index=True)


@st.cache_data(ttl=TTL, show_spinner=False)
def get_returns_matrix(tickers: tuple[str, ...], days: int = 250) -> pd.DataFrame:
    """Retornos diarios en formato ancho, para calcular correlaciones."""
    if not tickers:
        return pd.DataFrame()
    placeholders = ", ".join("?" for _ in tickers)
    df = _fetch(
        f"""
        SELECT ticker, date, ret_1d FROM indicators_daily
        WHERE ticker IN ({placeholders})
          AND date >= (SELECT date FROM current_session) - INTERVAL (?) DAY
        """,
        [*tickers, days],
    )
    if df.empty:
        return pd.DataFrame()
    return df.pivot_table(index="date", columns="ticker", values="ret_1d")


@st.cache_data(ttl=60, show_spinner=False)
def get_watchlist() -> pd.DataFrame:
    return _fetch(
        """
        SELECT w.ticker, inst.name, inst.gics_sector, w.added_at, w.added_price,
               w.note, i.close, i.ret_1d, f.composite_pctile
        FROM watchlist w
        LEFT JOIN instruments inst ON inst.ticker = w.ticker
        LEFT JOIN indicators_daily i ON i.ticker = w.ticker
             AND i.date = (SELECT date FROM current_session)
        LEFT JOIN factor_scores f ON f.ticker = w.ticker
             AND f.date = (SELECT date FROM current_session)
             AND f.weights_hash = ?
        WHERE w.list_name = 'default'
        ORDER BY w.added_at DESC
        """,
        [_preset_hash(None)],
    )


@st.cache_data(ttl=300, show_spinner=False)
def anos_de_composicion() -> float:
    """Años de composición de universo realmente guardados.

    Es el número que permite decir la verdad sobre el sesgo de supervivencia en
    vez de una promesa: "hay 0,03 años de composición real" se comprueba, "el
    sesgo irá desapareciendo" no.
    """
    from ..core.membership import anos_de_composicion as calcular

    try:
        with connect(read_only=True) as conn:
            return calcular(conn)
    except Exception:  # noqa: BLE001 — almacen viejo o sin la tabla
        return 0.0

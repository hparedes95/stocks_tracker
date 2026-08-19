"""Pipeline de calculo: indicadores -> senales -> factores -> scores -> amplitud.

La UI nunca calcula nada de esto al vuelo: leeria 750 series y tardaria
segundos. Aqui se materializa una vez y el dashboard solo hace SELECT.
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
from rich.console import Console

from ..core import breadth as breadth_mod
from ..core import indicators as ind_mod
from ..core import relative as relative_mod
from ..core import signals as sig_mod
from ..core.config import (
    get_active_universes,
    get_breadth_scope,
    get_factor_config,
    get_sector_etfs,
    get_settings,
    get_universes,
)
from ..core.db import connect, migrate, upsert_df
from ..core.scoring import compute_scores, preset_names, weights_hash

console = Console()

# Hay indicadores pero ningun instrumento que puntuar. Codigo propio para que
# la cadena de la descarga completa pueda pararse en vez de dar por bueno un
# almacen sin ranking.
EXIT_NOTHING_TO_SCORE = 76


def _load_prices(conn, lookback: int | None) -> pd.DataFrame:
    """Precios de todos los tickers. Con `lookback=None`, el historico entero.

    La ventana existe porque recalcular diez anos cada noche es innecesario y
    400 sesiones cubren la MM200 y el momentum 12-1 con margen. Pero para
    validar una estrategia hacen falta los diez anos: sin ellos la puerta 1
    nunca podria comprobar el minimo de cinco, y no por falta de precios sino
    porque los indicadores no llegaban tan atras.
    """
    if lookback is None:
        return conn.execute(
            """
            SELECT ticker, date, open, high, low, close, adj_close, volume
            FROM prices_daily ORDER BY ticker, date
            """
        ).fetchdf()
    return conn.execute(
        """
        SELECT ticker, date, open, high, low, close, adj_close, volume
        FROM prices_daily
        WHERE date >= (SELECT MAX(date) FROM prices_daily) - INTERVAL (?) DAY
        ORDER BY ticker, date
        """,
        [int(lookback * 1.6)],  # margen por fines de semana y festivos
    ).fetchdf()


def compute_indicators(lookback: int | None = None, full: bool = False) -> int:
    settings = get_settings()
    if full:
        lookback = None
        console.print("[cyan]Indicadores sobre el historico completo[/] "
                      "(tarda varios minutos y se hace una vez)")
    else:
        lookback = lookback or int(settings.compute.get("lookback_sessions", 400))

    with connect(read_only=True) as conn:
        prices = _load_prices(conn, lookback)
        bench = conn.execute(
            "SELECT date, adj_close FROM prices_daily WHERE ticker = '^GSPC' ORDER BY date"
        ).fetchdf()

    if prices.empty:
        console.print("[yellow]No hay precios. Ejecuta la ingesta primero.[/]")
        return 0

    prices["date"] = pd.to_datetime(prices["date"])
    bench_series = None
    if not bench.empty:
        bench = bench.copy()
        bench["date"] = pd.to_datetime(bench["date"])
        bench_series = bench.set_index("date")["adj_close"]

    frames: list[pd.DataFrame] = []
    signal_frames: list[pd.DataFrame] = []

    for ticker, group in prices.groupby("ticker", sort=False):
        series = group.set_index("date").sort_index()
        if len(series) < 30:
            continue  # sin historico suficiente ningun indicador es fiable

        ind = ind_mod.compute_all(series, bench_series)
        if ind.empty:
            continue

        detected = sig_mod.detect(ind)
        if not detected.empty:
            detected.insert(0, "ticker", ticker)
            signal_frames.append(detected)

        ind = ind.reset_index().rename(columns={"index": "date"})
        ind.insert(0, "ticker", ticker)
        frames.append(ind)

    if not frames:
        return 0

    all_ind = pd.concat(frames, ignore_index=True)
    all_ind["date"] = pd.to_datetime(all_ind["date"]).dt.date

    # Solo se guarda la ventana pedida: el resto ya estaba calculado.
    cutoff = []
    if lookback is not None and len(all_ind):
        cutoff = sorted(all_ind["date"].unique())[-lookback:]
        all_ind = all_ind[all_ind["date"].isin(set(cutoff))]

    with connect() as conn:
        n = upsert_df(conn, "indicators_daily", all_ind, keys=["ticker", "date"])
        if signal_frames:
            sigs = pd.concat(signal_frames, ignore_index=True)
            sigs["date"] = pd.to_datetime(sigs["date"]).dt.date
            if cutoff:
                sigs = sigs[sigs["date"].isin(set(cutoff))]
            if not sigs.empty:
                upsert_df(conn, "signals", sigs, keys=["ticker", "date", "signal_id"])

    console.print(f"[green]Indicadores: {n} filas[/]")
    return n


def _prune_stale_scores(conn, day, whash: str, scored: list[str]) -> int:
    """Borra scores del mismo dia y perfil para tickers ya no puntuables.

    El upsert actualiza y anade, pero nunca quita. Si un ticker deja de
    cumplir los requisitos —cambia de clase de activo, pierde el sector, se
    queda sin fundamentales— su score del ultimo calculo se queda ahi y sigue
    apareciendo en el ranking de candidatos. Asi es como el indice del dolar
    acabo listado como una accion que comprar.
    """
    if not scored:
        return 0
    placeholders = ",".join(["?"] * len(scored))
    removed = conn.execute(
        f"""
        DELETE FROM factor_scores
        WHERE date = ? AND weights_hash = ? AND ticker NOT IN ({placeholders})
        """,
        [day, whash, *scored],
    ).fetchall()
    conn.execute(
        f"""
        DELETE FROM factor_contributions
        WHERE date = ? AND weights_hash = ? AND ticker NOT IN ({placeholders})
        """,
        [day, whash, *scored],
    )
    return len(removed)


def compute_factor_scores(preset: str | None = None, all_presets: bool = False) -> int:
    """Scores para la ultima fecha disponible.

    Se calcula solo el ultimo dia: el ranking historico solo hace falta para el
    backtest (fase 3), y calcularlo cada noche multiplicaria el tiempo sin que
    nadie lo mire.

    Con `all_presets` se puntua el universo con todos los perfiles de
    `factors.yaml`. La parte cara (leer el almacen, cruzar fundamentales,
    calcular el factor tecnico) se hace UNA vez y solo se repite la
    ponderacion, que es aritmetica sobre un DataFrame ya montado.
    """
    cfg = get_factor_config()
    if all_presets:
        presets = preset_names()
    else:
        presets = [preset or get_settings().compute.get("weights_preset", "balanced")]

    with connect(read_only=True) as conn:
        # La sesion vigente sale de la vista `current_session`, compartida con
        # el dashboard. Tenerla duplicada aqui fue justo el fallo anterior: el
        # calculo puntuaba un dia y las pantallas leian otro.
        session = conn.execute("SELECT date, n FROM current_session").fetchdf()
        if session.empty:
            console.print("[yellow]No hay indicadores. Ejecuta `make compute` tras la ingesta.[/]")
            return 0
        last_date = session["date"].iloc[0]

        newest = conn.execute("SELECT MAX(date) FROM indicators_daily").fetchone()[0]
        if newest is not None and pd.Timestamp(newest) != pd.Timestamp(last_date):
            console.print(
                f"[dim]El almacen llega al {pd.Timestamp(newest):%d/%m/%Y}, pero "
                f"la ultima sesion completa es el {pd.Timestamp(last_date):%d/%m/%Y} "
                f"({int(session['n'].iloc[0])} valores). Se puntua esa.[/]"
            )

        snapshot = conn.execute(
            """
            SELECT i.*, inst.gics_sector, inst.asset_class, inst.market_cap, inst.name
            FROM indicators_daily i
            JOIN instruments inst USING (ticker)
            WHERE i.date = ? AND inst.asset_class IN ('equity', 'etf')
            """,
            [last_date],
        ).fetchdf()

        fundamentals = conn.execute(
            """
            SELECT f.* FROM fundamentals_snapshot f
            JOIN (
                SELECT ticker, MAX(as_of) AS as_of
                FROM fundamentals_snapshot GROUP BY ticker
            ) latest USING (ticker, as_of)
            """
        ).fetchdf()

        active_signals = conn.execute(
            "SELECT ticker, signal_id FROM signals WHERE date = ?", [last_date]
        ).fetchdf()

    if snapshot.empty:
        # No es un aviso menor: sin ranking no hay Oportunidades, ni candidatos
        # para el bot, ni comparacion sectorial. Antes salia con codigo 0 y la
        # cadena del universo seguia hasta anunciar "Universo completo listo"
        # con la tabla de scores vacia.
        console.print("[bold red]Sin instrumentos que puntuar.[/]")
        console.print(
            "[yellow]Hay indicadores pero ningun instrumento con clase "
            "'equity' o 'etf' en la fecha mas reciente. Lo habitual es que la "
            "ingesta del universo fallase o se quedase a medias: vuelve a "
            "ejecutar la descarga completa.[/]"
        )
        raise SystemExit(EXIT_NOTHING_TO_SCORE)

    merged = snapshot.merge(
        fundamentals.drop(columns=["as_of"], errors="ignore"), on="ticker", how="left"
    )

    # Factor tecnico: agregado de estado de tendencia y senales activas del dia.
    signals_by_ticker = (
        active_signals.groupby("ticker")["signal_id"].apply(list).to_dict()
        if not active_signals.empty
        else {}
    )
    merged["technical_raw"] = merged.apply(
        lambda r: sig_mod.technical_score(r, signals_by_ticker.get(r["ticker"], [])), axis=1
    )

    total = 0
    for name in presets:
        weights = cfg.weights(name)
        whash = weights_hash(weights)

        scores, contributions = compute_scores(
            merged, weights, cfg, group_col="gics_sector"
        )
        if scores.empty:
            continue

        scores["date"] = last_date
        scores["weights_hash"] = whash
        contributions["date"] = last_date
        contributions["weights_hash"] = whash

        with connect() as conn:
            n = upsert_df(
                conn, "factor_scores", scores, keys=["ticker", "date", "weights_hash"]
            )
            if not contributions.empty:
                upsert_df(
                    conn, "factor_contributions", contributions,
                    keys=["ticker", "date", "weights_hash", "factor"],
                )
            _prune_stale_scores(conn, last_date, whash, scores["ticker"].tolist())

        total += n
        console.print(f"[green]Scores: {n} valores[/] (perfil '{name}', hash {whash})")

    return total


# Factores que salen solo del precio. Los demas —calidad, valor, crecimiento,
# dividendo— vienen de `fundamentals_snapshot`.
PRICE_ONLY_FACTORS = {"momentum", "lowvol", "technical"}

# Cuantos valores necesitan tener foto ANTERIOR a la fecha para que puntuar esa
# sesion con fundamentales signifique algo. Por debajo, la mitad del universo
# competiria sin ratios contra la otra mitad con ellos, y el ranking mediria
# quien tenia datos, no quien estaba mejor.
MIN_PIT_COVERAGE = 0.80


def fundamentals_as_of(conn, dates: list) -> pd.DataFrame:
    """Los fundamentales VIGENTES en cada fecha, no los de hoy.

    Para cada (fecha, ticker) devuelve la foto con el `as_of` mas reciente que
    NO sea posterior a esa fecha. Es lo que separa un backtest honesto de uno
    que puntua 2019 con los balances de 2026 —y ese sale bien por construccion,
    porque la estrategia "sabe" que empresas iban a publicar buenos numeros—.

    `as_of <= fecha` y no `< fecha`: la foto se descarga por la noche con los
    datos que ya eran publicos ese dia, asi que usarla ese mismo dia no es
    mirar el futuro. Si algun dia la descarga pasa a ser intradia, esto tendria
    que volverse estricto.
    """
    if not dates:
        return pd.DataFrame()
    valores = ", ".join(["(?)"] * len(dates))
    return conn.execute(
        f"""
        SELECT d.fecha AS date, f.*
        FROM (VALUES {valores}) AS d(fecha)
        JOIN fundamentals_snapshot f ON f.as_of <= d.fecha
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY d.fecha, f.ticker ORDER BY f.as_of DESC
        ) = 1
        """,
        list(dates),
    ).fetchdf()


def pit_coverage(conn, dates: list) -> dict:
    """Que fraccion del universo tiene foto anterior a cada fecha.

    Se mide y se informa en vez de suponerlo: el historico de fundamentales
    empezo a acumularse hace poco, asi que hoy casi ninguna fecha pasada lo
    tiene, y eso hay que decirlo con el numero delante.
    """
    if not dates:
        return {}
    marcas = ", ".join(["(?)"] * len(dates))
    filas = conn.execute(
        f"""
        -- Las dos mitades tienen que contar la MISMA poblacion. Antes el
        -- denominador incluia los ETF —que no publican fundamentales y por
        -- tanto nunca tienen foto— y el numerador contaba cualquier ticker con
        -- foto, estuviera o no en `instruments`. La cobertura salia baja de
        -- forma sistematica y podia bloquear el ranking sin motivo, o pasar de
        -- 1,0 y dejar pasar justo lo que la puerta existe para frenar.
        WITH universo AS (
            SELECT ticker FROM instruments WHERE asset_class = 'equity'
        )
        SELECT d.fecha,
               COUNT(DISTINCT f.ticker) AS con_foto,
               (SELECT COUNT(*) FROM universo) AS total
        FROM (VALUES {marcas}) AS d(fecha)
        LEFT JOIN fundamentals_snapshot f
               ON f.as_of <= d.fecha
              AND f.ticker IN (SELECT ticker FROM universo)
        GROUP BY d.fecha ORDER BY d.fecha
        """,
        list(dates),
    ).fetchall()
    return {f[0]: (float(f[1]) / float(f[2]) if f[2] else 0.0) for f in filas}


def compute_score_history(preset: str = "bot_core", years: int = 6,
                          weekday: int = 0) -> int:
    """Ranking historico, para poder validar una estrategia contra el pasado.

    Los presets con fundamentales se admiten SOLO si el almacen tiene foto
    anterior a cada fecha para la mayoria del universo. Sin eso seria puntuar
    2019 con los balances de 2026, y el backtest saldria bien por construccion:
    la estrategia "sabria" que empresas iban a tener buenos numeros siete anos
    despues.

    Hasta hace poco esto se rechazaba siempre, y el comentario decia que
    `fundamentals_snapshot` guardaba solo la foto de hoy. Ya no es cierto: la
    tabla lleva `as_of` en la clave y acumula una serie. Lo que faltaba era
    USARLA con la union correcta, que es `fundamentals_as_of`.

    Se puntua un dia por semana (el de rebalanceo) y no todos: la estrategia
    solo mira el ranking ese dia, y calcular los otros cuatro multiplicaria por
    cinco el tiempo para nada.
    """
    cfg = get_factor_config()
    weights = cfg.weights(preset)
    usados = {f for f, w in weights.items() if w}
    necesita_fundamentales = bool(usados - PRICE_ONLY_FACTORS)
    whash = weights_hash(weights)

    with connect(read_only=True) as conn:
        sessions = conn.execute(
            """
            SELECT DISTINCT i.date AS date
            FROM indicators_daily i JOIN instruments inst USING (ticker)
            WHERE inst.asset_class IN ('equity', 'etf')
              AND i.date >= (SELECT MAX(date) FROM indicators_daily) - INTERVAL (?) DAY
            ORDER BY i.date
            """,
            [int(years * 366)],
        ).fetchdf()

        if sessions.empty:
            console.print("[yellow]Sin historico de indicadores que puntuar.[/]")
            return 0

        days = [d for d in pd.to_datetime(sessions["date"]) if d.weekday() == weekday]
        if not days:
            days = list(pd.to_datetime(sessions["date"]))
        wanted = [d.date() for d in days]

        # Una sola lectura para todas las fechas: 500 consultas sueltas
        # tardarian mas en ir y volver que en calcular.
        placeholders = ",".join(["?"] * len(wanted))
        snapshot = conn.execute(
            f"""
            SELECT i.*, inst.gics_sector, inst.asset_class, inst.market_cap, inst.name
            FROM indicators_daily i
            JOIN instruments inst USING (ticker)
            WHERE i.date IN ({placeholders})
              AND inst.asset_class IN ('equity', 'etf')
            """,
            wanted,
        ).fetchdf()
        signals = conn.execute(
            f"SELECT ticker, date, signal_id FROM signals WHERE date IN ({placeholders})",
            wanted,
        ).fetchdf()

        # Fundamentales VIGENTES en cada fecha. Si el historico no da, se dice
        # con el numero delante en vez de puntuar con huecos: media muestra sin
        # ratios compitiendo contra la otra media mide quien tenia datos.
        fundamentals = pd.DataFrame()
        if necesita_fundamentales:
            cobertura = pit_coverage(conn, wanted)
            floja = {d: c for d, c in cobertura.items() if c < MIN_PIT_COVERAGE}
            if floja:
                peor = min(floja.values())
                raise ValueError(
                    f"El preset '{preset}' usa {sorted(usados - PRICE_ONLY_FACTORS)}, "
                    f"que salen de los fundamentales, y {len(floja)} de "
                    f"{len(wanted)} sesiones no tienen foto anterior para el "
                    f"{MIN_PIT_COVERAGE:.0%} del universo (la peor: "
                    f"{peor:.0%}).\n"
                    "  El historico de fundamentales se acumula desde que se "
                    "instalo el programa, asi que esto se arregla solo con el "
                    "tiempo: no hay forma de recuperar fotos del pasado.\n"
                    f"  Mientras tanto usa un preset de "
                    f"{sorted(PRICE_ONLY_FACTORS)}, como 'bot_core'."
                )
            fundamentals = fundamentals_as_of(conn, wanted)

    if snapshot.empty:
        console.print("[yellow]Sin instrumentos que puntuar en el historico.[/]")
        return 0

    by_date_signals: dict = {}
    if not signals.empty:
        for (day, ticker), group in signals.groupby(["date", "ticker"]):
            by_date_signals.setdefault(day, {})[ticker] = group["signal_id"].tolist()

    all_scores, all_contributions = [], []
    console.print(f"[cyan]Ranking historico:[/] {len(wanted)} sesiones, perfil '{preset}'")

    for day, frame in snapshot.groupby("date"):
        day_signals = by_date_signals.get(day, {})
        frame = frame.copy()
        if not fundamentals.empty:
            del_dia = fundamentals[fundamentals["date"] == day].drop(
                columns=["date", "as_of"], errors="ignore"
            )
            # `how="left"`: un valor sin foto ese dia se queda con los ratios a
            # nulo y el `completeness` del scoring lo penaliza solo. Tirarlo
            # cambiaria el universo segun la fecha y el ranking no seria
            # comparable entre sesiones.
            frame = frame.merge(del_dia, on="ticker", how="left",
                                suffixes=("", "_fund"))
        frame["technical_raw"] = frame.apply(
            lambda r, sigs=day_signals: sig_mod.technical_score(
                r, sigs.get(r["ticker"], [])
            ),
            axis=1,
        )
        scores, contributions = compute_scores(
            frame, weights, cfg, group_col="gics_sector"
        )
        if scores.empty:
            continue
        scores["date"] = day
        scores["weights_hash"] = whash
        all_scores.append(scores)
        if not contributions.empty:
            contributions["date"] = day
            contributions["weights_hash"] = whash
            all_contributions.append(contributions)

    if not all_scores:
        return 0

    scores_df = pd.concat(all_scores, ignore_index=True)
    with connect() as conn:
        n = upsert_df(conn, "factor_scores", scores_df,
                      keys=["ticker", "date", "weights_hash"])
        if all_contributions:
            upsert_df(conn, "factor_contributions",
                      pd.concat(all_contributions, ignore_index=True),
                      keys=["ticker", "date", "weights_hash", "factor"])

    console.print(f"[green]Ranking historico: {n} filas[/] en "
                  f"{scores_df['date'].nunique()} sesiones (hash {whash})")
    return n


def compute_rotation() -> int:
    """Rotacion sectorial: fuerza relativa de cada sector frente al indice.

    Se guarda en `sector_rotation` para que la pagina de sectores no tenga que
    recalcular una matriz de fuerza relativa en cada carga.
    """
    with connect(read_only=True) as conn:
        etf_prices = conn.execute(
            """
            SELECT ticker, date, adj_close FROM prices_daily
            WHERE ticker IN (SELECT ticker FROM instruments WHERE asset_class = 'etf')
            ORDER BY date
            """
        ).fetchdf()
        benchmark = conn.execute(
            "SELECT date, adj_close FROM prices_daily WHERE ticker = 'SPY' ORDER BY date"
        ).fetchdf()

    sector_etfs = get_sector_etfs()
    if etf_prices.empty or benchmark.empty or not sector_etfs:
        console.print("[yellow]Sin ETFs sectoriales para calcular rotacion.[/]")
        return 0

    etf_prices["date"] = pd.to_datetime(etf_prices["date"])
    benchmark["date"] = pd.to_datetime(benchmark["date"])

    wide = etf_prices.pivot_table(index="date", columns="ticker", values="adj_close")
    wide = wide[[c for c in wide.columns if c in sector_etfs]]
    if wide.empty:
        return 0

    bench_series = benchmark.set_index("date")["adj_close"]
    table = relative_mod.rotation_table(wide, bench_series)
    if table.empty:
        return 0

    last_date = pd.to_datetime(etf_prices["date"]).max().date()
    table["date"] = last_date
    table["sector"] = table["nombre"].map(sector_etfs)
    table["etf"] = table["nombre"]
    table["estela_ratio"] = table["estela_ratio"].map(json.dumps)
    table["estela_momentum"] = table["estela_momentum"].map(json.dumps)
    table = table.drop(columns=["nombre"])

    with connect() as conn:
        n = upsert_df(conn, "sector_rotation", table, keys=["date", "etf"])

    leading = table[table["cuadrante"] == relative_mod.LEADING]["sector"].tolist()
    console.print(
        f"[green]Rotacion: {n} sectores[/]"
        + (f" · lideran {', '.join(leading[:3])}" if leading else "")
    )
    return n


def compute_breadth() -> int:
    """Amplitud de mercado por universo y por sector."""
    universes = get_universes()

    with connect(read_only=True) as conn:
        indicators = conn.execute(
            """
            SELECT i.ticker, i.date, i.above_sma50, i.above_sma200, i.ret_1d,
                   i.rsi14, i.dist_52w_high, i.dist_52w_low, i.roc_1m,
                   inst.gics_sector
            FROM indicators_daily i
            JOIN instruments inst USING (ticker)
            WHERE inst.asset_class = 'equity'
            """
        ).fetchdf()
        membership = conn.execute(
            "SELECT universe, ticker FROM universe_membership WHERE valid_to IS NULL"
        ).fetchdf()

    if indicators.empty:
        return 0

    frames: list[pd.DataFrame] = []

    for key in get_active_universes():
        spec = universes.get(key)
        if not spec or spec.asset_class != "equity":
            continue
        tickers = set(membership[membership["universe"] == key]["ticker"])
        subset = indicators[indicators["ticker"].isin(tickers)]
        if not subset.empty:
            frames.append(breadth_mod.compute_breadth(subset, key))

    # Amplitud por sector: donde esta la fortaleza interna del mercado.
    for sector, group in indicators.groupby("gics_sector"):
        if not sector or len(group["ticker"].unique()) < 5:
            continue
        frames.append(breadth_mod.compute_breadth(group, f"GICS:{sector}"))

    if not frames:
        return 0

    all_breadth = pd.concat(frames, ignore_index=True)
    with connect() as conn:
        n = upsert_df(conn, "breadth_daily", all_breadth, keys=["date", "scope"])

    console.print(f"[green]Amplitud: {n} filas[/]")
    return n


def compute_regime() -> int:
    """Semaforo risk-on / risk-off.

    Media de varios componentes normalizados. Se guarda el desglose para que el
    usuario pueda ver QUE esta empujando el semaforo, no solo el numero.
    """
    with connect(read_only=True) as conn:
        vix = conn.execute(
            "SELECT date, adj_close FROM prices_daily WHERE ticker = '^VIX' ORDER BY date"
        ).fetchdf()
        breadth = conn.execute(
            """
            SELECT date, pct_above_sma200, new_highs_52w, new_lows_52w
            FROM breadth_daily
            WHERE scope = ? ORDER BY date
            """,
            [get_breadth_scope()],
        ).fetchdf()
        macro = conn.execute(
            """
            SELECT ticker, date, adj_close FROM prices_daily
            WHERE ticker IN ('GC=F', 'HG=F', 'CL=F', 'DX-Y.NYB', 'SPY', 'IEF', 'XLY', 'XLP')
            ORDER BY date
            """
        ).fetchdf()

    if vix.empty and breadth.empty:
        console.print("[yellow]Sin datos suficientes para el semaforo de riesgo.[/]")
        return 0

    frames: dict[str, pd.Series] = {}
    if not macro.empty:
        macro["date"] = pd.to_datetime(macro["date"])
        wide = macro.pivot_table(index="date", columns="ticker", values="adj_close")
        for col in wide.columns:
            frames[col] = wide[col]

    index = None
    if not vix.empty:
        vix["date"] = pd.to_datetime(vix["date"])
        vix_s = vix.set_index("date")["adj_close"]
        frames["VIX"] = vix_s
        index = vix_s.index
    if not breadth.empty:
        breadth["date"] = pd.to_datetime(breadth["date"])
        breadth = breadth.set_index("date")
        b = breadth["pct_above_sma200"]
        frames["BREADTH"] = b
        # Maximos frente a minimos anuales: mide la fuerza en los extremos, que
        # es informacion distinta de cuantos valores estan sobre su media. En
        # un techo, la amplitud aun aguanta mientras los nuevos maximos ya se
        # secan.
        if "new_highs_52w" in breadth.columns:
            frames["NEW_HIGHS"] = breadth["new_highs_52w"]
            frames["NEW_LOWS"] = breadth["new_lows_52w"]
        index = b.index if index is None else index.union(b.index)

    if index is None or len(index) == 0:
        return 0

    df = pd.DataFrame({k: v.reindex(index).ffill() for k, v in frames.items()})

    components: dict[str, pd.Series] = {}

    if "VIX" in df:
        # Percentil invertido: VIX bajo = apetito por riesgo.
        pct = df["VIX"].rolling(252, min_periods=60).rank(pct=True)
        components["vix"] = (0.5 - pct) * 200
    if "BREADTH" in df:
        components["amplitud"] = (df["BREADTH"] - 50.0) * 2.0
    if "HG=F" in df and "GC=F" in df:
        ratio = df["HG=F"] / df["GC=F"].replace(0, np.nan)
        pct = ratio.rolling(252, min_periods=60).rank(pct=True)
        components["cobre_oro"] = (pct - 0.5) * 200
    if "SPY" in df and "IEF" in df:
        rel = df["SPY"].pct_change(63) - df["IEF"].pct_change(63)
        components["acciones_vs_bonos"] = (rel * 400).clip(-100, 100)
    if "XLY" in df and "XLP" in df:
        rel = df["XLY"].pct_change(63) - df["XLP"].pct_change(63)
        components["ciclico_vs_defensivo"] = (rel * 400).clip(-100, 100)
    if "NEW_HIGHS" in df and "NEW_LOWS" in df:
        highs = df["NEW_HIGHS"].fillna(0)
        lows = df["NEW_LOWS"].fillna(0)
        total = (highs + lows).replace(0, np.nan)
        # Proporcion de maximos sobre el total de extremos, centrada en cero.
        components["maximos_vs_minimos"] = ((highs / total) - 0.5) * 200
    if "SPY" in df:
        # Momentum del mercado: distancia del indice a su media de medio ano.
        sma = df["SPY"].rolling(125, min_periods=60).mean()
        components["momentum_mercado"] = (
            (df["SPY"] / sma - 1.0) * 1000
        ).clip(-100, 100)

    if not components:
        return 0

    comp_df = pd.DataFrame(components)
    risk_score = comp_df.mean(axis=1, skipna=True).clip(-100, 100)

    out = pd.DataFrame(
        {
            "date": [d.date() for d in comp_df.index],
            "vix": df.get("VIX", pd.Series(index=comp_df.index, dtype=float)).to_numpy(),
            "vix_percentile_1y": (
                df["VIX"].rolling(252, min_periods=60).rank(pct=True).to_numpy()
                if "VIX" in df else np.nan
            ),
            "copper_gold_ratio": (
                (df["HG=F"] / df["GC=F"].replace(0, np.nan)).to_numpy()
                if "HG=F" in df and "GC=F" in df else np.nan
            ),
            "pct_above_sma200": df.get("BREADTH", pd.Series(index=comp_df.index, dtype=float)).to_numpy(),
            "risk_score": risk_score.to_numpy(),
        }
    )
    out["regime"] = pd.cut(
        out["risk_score"], bins=[-np.inf, -30, 30, np.inf],
        labels=["risk_off", "neutral", "risk_on"],
    ).astype(str)
    out["components"] = comp_df.round(1).to_dict("records")
    out["components"] = out["components"].astype(str)
    out = out.dropna(subset=["risk_score"])

    if out.empty:
        return 0

    with connect() as conn:
        n = upsert_df(conn, "regime_daily", out, keys=["date"])

    latest = out.iloc[-1]
    console.print(
        f"[green]Semaforo: {latest['regime']}[/] (score {latest['risk_score']:+.0f})"
    )
    return n


def puerta_de_calidad() -> bool:
    """Comprueba los precios antes de calcular sobre ellos. Devuelve si se sigue.

    Solo para lo que invalida el resultado: precios imposibles (un maximo por
    debajo del minimo, un cierre fuera del rango) y reescrituras masivas del
    historico. Los huecos y los tickers desaparecidos ensucian pero no
    invalidan, asi que avisan y dejan pasar.

    La linea esta ahi a proposito. Una puerta que se cierra a menudo se acaba
    abriendo con `--ignorar-calidad` por costumbre, y entonces deja de existir.
    """
    from ..core.ids import ulid
    from ..core.quality import (
        COMPROBACIONES_DEL_ALMACEN,
        bloqueantes,
        evaluar,
        guardar,
        resumen,
    )

    with connect(read_only=True) as conn:
        precios = conn.execute(
            "SELECT ticker, date, open, high, low, close, volume FROM prices_daily"
        ).fetchdf()
    if precios.empty:
        return True

    hallazgos = evaluar(precios)
    run_id = ulid()
    with connect() as conn:
        # Solo las comprobaciones que ESTA funcion puede hacer. Marcar
        # `precios_revisados` como pasado sin poder compararlo con nada
        # tapaba el hallazgo bloqueante de la ingesta, porque la pagina 8
        # ensena el registro mas reciente de cada comprobacion.
        guardar(conn, hallazgos, run_id, list(COMPROBACIONES_DEL_ALMACEN))

    graves = bloqueantes(hallazgos)
    if not graves:
        if hallazgos:
            console.print(f"[dim]Calidad de los datos: {resumen(hallazgos)}.[/]")
        return True

    console.print("[bold red]No se calcula: los datos tienen problemas graves.[/]")
    for h in graves:
        console.print(f"  [red]{h.check}:[/] {h.detail}")
    console.print(
        "[dim]Calcular sobre esto daria numeros con buena pinta y sin sentido. "
        "Vuelve a descargar los precios afectados, o usa --ignorar-calidad si "
        "lo que quieres es diagnosticar.[/]"
    )
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculo de indicadores, factores y scores")
    parser.add_argument("--only", default=None,
                        choices=["indicators", "scores", "breadth", "rotation", "regime"])
    parser.add_argument("--preset", default=None, help="perfil de pesos a usar")
    parser.add_argument(
        "--all-presets", action="store_true",
        help="puntua el universo con todos los perfiles de factors.yaml",
    )
    parser.add_argument("--lookback", type=int, default=None)
    parser.add_argument(
        "--full-history", action="store_true", dest="full_history",
        help="Calcula los indicadores sobre TODO el historico de precios, no "
             "solo las ultimas 400 sesiones. Necesario para validar el bot.",
    )
    parser.add_argument(
        "--history", type=int, default=None, metavar="ANOS",
        help="Calcula el ranking historico del preset del bot, necesario para "
             "validar la estrategia. Solo factores de precio.",
    )
    parser.add_argument(
        "--ignorar-calidad", action="store_true",
        help="Calcula aunque los datos tengan problemas graves. Los resultados "
             "no seran fiables; existe para poder diagnosticar.",
    )
    args = parser.parse_args()

    migrate()

    if not args.ignorar_calidad and not puerta_de_calidad():
        return

    if args.history:
        compute_score_history(preset=args.preset or "bot_core", years=args.history)
        console.print("[bold green]Calculo terminado.[/]")
        return

    steps = {
        "indicators": lambda: compute_indicators(args.lookback,
                                                 full=args.full_history),
        "breadth": compute_breadth,
        "rotation": compute_rotation,
        "regime": compute_regime,
        "scores": lambda: compute_factor_scores(args.preset, args.all_presets),
    }
    order = ["indicators", "breadth", "rotation", "regime", "scores"]

    for name in order:
        if args.only and args.only != name:
            continue
        steps[name]()

    console.print("[bold green]Calculo terminado.[/]")


if __name__ == "__main__":
    main()

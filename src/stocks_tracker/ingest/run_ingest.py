"""Orquestador de la ingesta.

Descarga incremental: se pregunta al almacen por la ultima fecha de cada ticker
y solo se pide lo que falta. El backfill completo se hace una vez.

Degradacion elegante: si un lote falla, se registra en `ingest_log` y el proceso
CONTINUA. Un error de red no debe dejar el almacen a medias sin avisar.
"""

from __future__ import annotations

import argparse
import hashlib
import uuid
from datetime import date, timedelta

import pandas as pd
from rich.console import Console

from ..core import membership, quality
from ..core.config import (
    all_active_tickers,
    get_active_universes,
    get_fred_series,
    get_settings,
    get_universes,
)
from ..core.db import connect, migrate, upsert_df
from ..core.locking import AlreadyRunning, single_writer
from ..core.symbols import resolve_all
from ..core.textutils import is_missing
from ..core.timeutils import utcnow
from ..providers.base import completeness
from ..providers.fred_provider import FredProvider
from ..providers.registry import get_price_provider
from ..providers.universe_provider import es_fiable, resolve_universe

console = Console()

# Codigo de salida cuando otro proceso tenia el bloqueo y no se ha descargado
# nada. Es distinto de 0 (se hizo) y de 1 (fallo la descarga), porque quien
# llama necesita distinguir los tres casos.
EXIT_ALREADY_RUNNING = 75

_FUNDAMENTAL_FIELDS = [
    "trailing_pe", "price_to_book", "price_to_sales", "ev_to_ebitda", "fcf_yield",
    "profit_margin", "operating_margin", "roe", "revenue_growth_yoy",
    "earnings_growth_yoy", "net_debt_to_ebitda", "dividend_yield", "payout_ratio",
]


def _stable_shard(ticker: str, shards: int) -> int:
    """Turno al que pertenece un ticker. Estable entre procesos y reinicios."""
    digest = hashlib.blake2s(ticker.encode(), digest_size=4).digest()
    return int.from_bytes(digest, "big") % shards


def _log(conn, run_id: str, task: str, target: str, status: str,
         rows: int = 0, requests: int = 0, error: str = "") -> None:
    conn.execute(
        "INSERT INTO ingest_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [run_id, utcnow(), utcnow(), task, target, status,
         int(rows), int(requests), error[:500]],
    )


def ingest_universe(provider_name: str | None = None) -> int:
    """Registra instrumentos y pertenencia a universos.

    Aqui se resuelve tambien el simbolo de TradingView: en la ingesta, nunca en
    tiempo de render, para que la UI solo lea una columna ya calculada.
    """
    migrate()
    universes = get_universes()
    provider = get_price_provider(provider_name)
    run_id = str(uuid.uuid4())

    # Los universos con `source: wikipedia` se resuelven contra la lista real de
    # constituyentes; si la descarga falla se usa la lista manual del YAML.
    resolved: dict[str, list[str]] = {}
    origenes: dict[str, str] = {}
    for key in get_active_universes():
        spec = universes.get(key)
        if spec is None:
            continue
        members, origin = resolve_universe(key, spec.tickers, spec.source)
        resolved[key] = members
        # El origen se guarda porque decide si se pueden CERRAR intervalos de
        # composicion. Con una lista de respaldo no se puede: los que faltan no
        # es que hayan salido del indice, es que no se han podido leer.
        origenes[key] = origin
        if spec.source == "wikipedia":
            console.print(f"  {key}: {len(members)} tickers ({origin})")

    tickers = list(dict.fromkeys(t for members in resolved.values() for t in members))
    console.print(f"[cyan]Universo:[/] {len(tickers)} tickers en {len(resolved)} listas")

    # Metadatos ya conocidos. Se leen ANTES de pedir nada por dos motivos
    # distintos, y el segundo es el importante:
    #
    # 1. Velocidad. `fetch_metadata` hace una peticion por ticker: con 617 son
    #    entre cinco y quince minutos, en cada ejecucion, para traer un nombre
    #    y un sector que no cambian de un dia para otro.
    # 2. CORRECCION. El presupuesto de peticiones corta a los 400, y los ~217
    #    restantes se guardaban con la ficha en blanco, PISANDO los metadatos
    #    que ya tenian de la ejecucion anterior. Cada noche se rellenaban unos
    #    y se vaciaban otros, sin avanzar nunca.
    existing: dict[str, dict] = {}
    with connect(read_only=True) as conn:
        rows = conn.execute(
            "SELECT ticker, name, asset_class, exchange, currency, country, "
            "gics_sector, gics_industry, market_cap, updated_at FROM instruments"
        ).fetchdf()
    if not rows.empty:
        existing = {r["ticker"]: dict(r) for _, r in rows.iterrows()}

    ttl_days = int(get_settings().ingest.get("metadata_ttl_days", 30))
    cutoff = utcnow() - timedelta(days=ttl_days)

    def needs_metadata(ticker: str) -> bool:
        row = existing.get(ticker)
        if row is None:
            return True
        # Sin bolsa no hay simbolo de TradingView; sin sector no hay ranking
        # sectorial. Son los dos campos cuya ausencia se nota en pantalla.
        if is_missing(row.get("exchange")) or is_missing(row.get("gics_sector")):
            return True
        updated = row.get("updated_at")
        if updated is None or pd.isna(updated):
            return True
        try:
            return pd.Timestamp(updated) < cutoff
        except (ValueError, TypeError):
            # Una marca de tiempo ilegible tumbaba la ingesta ENTERA con una
            # excepcion sin capturar, y la ingesta corre de madrugada sin nadie
            # delante. Se trata como caducada: volver a consultar el metadato
            # cuesta una peticion y ademas repara la fila.
            return True

    pendientes = [t for t in tickers if needs_metadata(t)]
    if pendientes:
        console.print(
            f"  Metadatos: {len(pendientes)} por consultar de {len(tickers)} "
            f"(el resto se reutiliza)"
        )
        try:
            meta = provider.fetch_metadata(pendientes)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]No se pudieron leer metadatos: {exc}[/]")
            meta = pd.DataFrame(columns=["ticker"])
    else:
        console.print("  Metadatos: al dia, no hace falta consultar ninguno")
        meta = pd.DataFrame(columns=["ticker"])

    fetched = {r["ticker"]: r for r in meta.to_dict("records")} if not meta.empty else {}

    sin_traer = [t for t in pendientes if t not in fetched]
    if sin_traer:
        console.print(
            f"[yellow]  {len(sin_traer)} se quedan sin detalle en esta pasada "
            f"(presupuesto de peticiones agotado). Se recogen en la siguiente.[/]"
        )

    # Clase de activo declarada en la configuracion, por ticker.
    class_by_ticker: dict[str, str] = {}
    for key, members in resolved.items():
        spec = universes.get(key)
        if spec:
            for t in members:
                class_by_ticker.setdefault(t, spec.asset_class)

    # Una fila por ticker del universo: lo recien traido manda, lo ya conocido
    # se conserva, y lo que no hay en ninguno de los dos queda vacio a la
    # espera de otra pasada. Nunca se sustituye un dato por un hueco.
    records = []
    for ticker in tickers:
        row = {"ticker": ticker}
        row.update({k: v for k, v in (existing.get(ticker) or {}).items()
                    if not is_missing(v)})
        row.update({k: v for k, v in (fetched.get(ticker) or {}).items()
                    if not is_missing(v)})
        row["ticker"] = ticker
        row.pop("updated_at", None)
        records.append(row)

    for rec in records:
        declared = class_by_ticker.get(rec["ticker"])
        inferred = rec.get("asset_class")
        # La configuracion gana cuando declara `etf` o `index`: los proveedores
        # clasifican los ETF como acciones con demasiada frecuencia, y un ETF
        # sectorial mal etiquetado deja sin datos toda la rotacion sectorial.
        # Para el resto manda el proveedor, que distingue cripto de materia
        # prima mejor que una etiqueta generica de universo.
        if declared in {"etf", "index"}:
            rec["asset_class"] = declared
        elif is_missing(inferred):
            # `is_missing` y no `not inferred`: un hueco de pandas es NaN, que
            # es VERDADERO, asi que el respaldo no se aplicaba. El instrumento
            # se guardaba sin clase, el ranking filtra por 'equity'/'etf' y el
            # resultado era "Sin instrumentos que puntuar" al final de una
            # ingesta sin ningun error a la vista.
            rec["asset_class"] = declared or "equity"

    enriched = pd.DataFrame(resolve_all(records))
    enriched["is_active"] = True
    enriched["first_seen"] = date.today()
    enriched["last_seen"] = date.today()
    enriched["investment_type"] = enriched["asset_class"]
    enriched["updated_at"] = utcnow()

    with connect() as conn:
        n = upsert_df(conn, "instruments", enriched, keys=["ticker"])

        # La composicion se guarda como INTERVALOS con fecha de entrada y de
        # salida, no como una foto diaria. Antes se insertaba una fila por
        # ticker y por dia con `valid_to` siempre a NULL, de modo que
        # `WHERE valid_to IS NULL` devolvia todos los tickers que habian
        # estado alguna vez: la tabla que existe para evitar el sesgo de
        # supervivencia lo estaba produciendo. Ver core/membership.py.
        colapsadas = membership.compactar(conn)
        if colapsadas:
            console.print(
                f"  [dim]Composicion: {colapsadas} filas duplicadas colapsadas "
                "en intervalos[/]"
            )
        for key, members in resolved.items():
            fiable = es_fiable(origenes.get(key, "manual"))
            cambios = membership.actualizar(conn, key, members, date.today(),
                                            fiable=fiable)
            if cambios["entran"] or cambios["salen"]:
                console.print(
                    f"  [yellow]{key}:[/] entran {len(cambios['entran'])}, "
                    f"salen {len(cambios['salen'])}"
                    + (f" ({', '.join(cambios['salen'][:5])})" if cambios["salen"] else "")
                )
            if cambios["sin_confirmar"]:
                console.print(
                    f"  [yellow]{key}:[/] {len(cambios['sin_confirmar'])} valores "
                    f"no aparecen en la lista de respaldo, pero NO se dan de baja: "
                    f"con '{origenes.get(key)}' no se sabe si salieron del indice "
                    "o solo falto la descarga."
                )
        _log(conn, run_id, "universe", "all", "OK", rows=n)

    unmapped = enriched["tv_symbol"].isna().sum()
    console.print(f"[green]Instrumentos: {n}[/] ({unmapped} sin equivalencia en TradingView)")
    return n


def _tickers_to_download(universes: list[str] | None = None) -> list[str]:
    """Tickers a descargar: los que registro la ingesta de universo.

    Se leen del almacen y no del YAML porque, con `source: wikipedia`, la lista
    real puede tener cientos de valores que el fichero de configuracion no
    enumera. Si el almacen esta vacio se cae a la configuracion.

    Con `universes` se limita a unas listas concretas. Sirve para ver precios
    reales en la pantalla en un minuto en lugar de esperar a que bajen los
    seiscientos valores del universo completo.
    """
    if universes:
        placeholders = ",".join(["?"] * len(universes))
        with connect(read_only=True) as conn:
            df = conn.execute(
                f"""
                SELECT DISTINCT m.ticker FROM universe_membership m
                JOIN instruments i ON i.ticker = m.ticker AND i.is_active
                WHERE m.valid_to IS NULL AND m.universe IN ({placeholders})
                ORDER BY m.ticker
                """,
                universes,
            ).fetchdf()
        if not df.empty:
            return df["ticker"].tolist()

    with connect(read_only=True) as conn:
        df = conn.execute(
            "SELECT ticker FROM instruments WHERE is_active ORDER BY ticker"
        ).fetchdf()
    if df.empty:
        return all_active_tickers()
    return df["ticker"].tolist()


def _last_dates() -> dict[str, date]:
    with connect(read_only=True) as conn:
        df = conn.execute(
            "SELECT ticker, MAX(date) AS last_date FROM prices_daily GROUP BY ticker"
        ).fetchdf()
    if df.empty:
        return {}
    return {r.ticker: pd.Timestamp(r.last_date).date() for r in df.itertuples()}


def ingest_prices(provider_name: str | None = None, full: bool = False,
                  years: int | None = None,
                  universes: list[str] | None = None) -> int:
    """Descarga precios, incremental salvo que se pida backfill completo.

    `years` acorta el historico. El minimo util son 2: los indicadores necesitan
    400 sesiones para la MM200 y el momentum 12-1, y por debajo de eso el
    ranking sale vacio. Diez es lo que hace falta para que el backtest tenga
    algo que medir, pero para ver precios en pantalla sobra con tres.
    """
    migrate()
    settings = get_settings()
    provider = get_price_provider(provider_name)
    run_id = str(uuid.uuid4())

    tickers = _tickers_to_download(universes)
    today = date.today()
    configured = int(settings.ingest.get("backfill_years", 10))
    backfill_start = today - timedelta(days=365 * int(years or configured))
    last = {} if full else _last_dates()

    # Se agrupan por fecha de inicio para poder seguir descargando por lotes.
    by_start: dict[date, list[str]] = {}
    for ticker in tickers:
        start = last.get(ticker)
        start = (start + timedelta(days=1)) if start else backfill_start
        if start > today:
            continue
        by_start.setdefault(start, []).append(ticker)

    if not by_start:
        console.print("[green]Precios ya al dia.[/]")
        return 0

    total = 0
    revisiones_totales = 0
    problemas: list = []
    for start, group in sorted(by_start.items()):
        console.print(f"[cyan]Descargando[/] {len(group)} tickers desde {start}")
        df = provider.fetch_ohlcv(group, start, today + timedelta(days=1))
        failed = df.attrs.get("failed_tickers", [])
        if df.empty:
            with connect() as conn:
                _log(conn, run_id, "prices", str(start), "FAILED", error="sin datos")
            continue
        relayed = df.attrs.get("relayed_tickers", {})
        notes = []
        if failed:
            notes.append(f"{len(failed)} tickers fallidos")
        if relayed:
            # Saber que el respaldo entro en juego es la senal de que la fuente
            # principal se esta rompiendo. Sin registrarlo, el relevo es
            # silencioso y nadie se entera hasta que falla del todo.
            by_provider = ", ".join(
                f"{name}: {sum(1 for v in relayed.values() if v == name)}"
                for name in sorted(set(relayed.values()))
            )
            notes.append(f"relevo -> {by_provider}")

        with connect() as conn:
            # ANTES de escribir: comparar lo que llega con lo que ya hay. Es el
            # unico momento en que se puede. Despues del UPSERT el valor viejo
            # ya no existe en ninguna parte y la reescritura es indetectable
            # para siempre.
            revisadas = _revisiones_del_lote(conn, df)
            n = upsert_df(conn, "prices_daily", df, keys=["ticker", "date"])
            # Se cuentan BARRAS y no filas de (ticker, fecha, campo): con estas
            # ultimas, la consola decia "8 valores reescritos" mientras el
            # hallazgo de al lado decia "3 barras". Dos numeros distintos para
            # lo mismo, en la misma pantalla.
            relevantes = quality.revisiones_relevantes(revisadas)
            barras = (0 if relevantes.empty
                      else len(relevantes[["ticker", "date"]].drop_duplicates()))
            if barras:
                notes.append(f"{barras} barras reescritas por el proveedor")
            status = "PARTIAL" if failed else "OK"
            _log(conn, run_id, "prices", str(start), status, rows=n,
                 requests=df.attrs.get("requests_used", 0),
                 error="; ".join(notes))
            hallazgos = quality.evaluar(df, revisadas, filas_lote=len(df),
                                        instrumentos_ohlc=_con_ohlc(conn))
            quality.guardar(conn, hallazgos, run_id, list(quality.COMPROBACIONES))
        total += n
        revisiones_totales += barras
        problemas.extend(hallazgos)
        console.print(f"  [green]{n} filas[/]")
        if notes:
            console.print(f"  [yellow]{'; '.join(notes)}[/]")

    _avisar_de_la_calidad(problemas, revisiones_totales)
    return total


def _con_ohlc(conn) -> set[str]:
    """Instrumentos de los que se usa el rango del dia y no solo el cierre.

    Acciones y ETF: de ellos salen el ATR, los rangos y los stops. De divisas,
    indices y macro solo se usa el cierre, y ahi un maximo y un minimo que no
    cuadran es una rareza conocida de Yahoo que no invalida ningun calculo.
    """
    return {f[0] for f in conn.execute(
        "SELECT ticker FROM instruments WHERE asset_class IN ('equity', 'etf')"
    ).fetchall()}


def _revisiones_del_lote(conn, df: pd.DataFrame) -> pd.DataFrame:
    """Lo que ya estaba guardado para las mismas (ticker, fecha) del lote.

    Se piden solo esas filas y no la tabla entera: un lote son unos miles de
    filas y `prices_daily` puede tener millones.
    """
    if df.empty:
        return pd.DataFrame()
    try:
        conn.register("_lote", df[["ticker", "date"]])
        existente = conn.execute(
            "SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.volume "
            "FROM prices_daily p JOIN _lote l "
            "ON p.ticker = l.ticker AND p.date = l.date"
        ).fetchdf()
    finally:
        conn.unregister("_lote")
    return quality.revisiones(df, existente)


def _avisar_de_la_calidad(problemas: list, revisiones_totales: int) -> None:
    """Lo dice en pantalla, y con lo que significa.

    Un aviso que solo dice "34 filas revisadas" se ignora. Lo que hay que decir
    es que cualquier resultado calculado antes de esta descarga ya no se puede
    reproducir, porque esa es la consecuencia.
    """
    if revisiones_totales:
        console.print(
            f"[yellow]Aviso:[/] el proveedor ha cambiado {revisiones_totales} "
            "valores que ya estaban guardados. El precio al que cotizo algo un "
            "dia concreto no cambia, asi que o corrigio un error suyo o metio "
            "otro. Los backtests y las validaciones anteriores a esta descarga "
            "ya no se reproducen: conviene volver a ejecutarlos."
        )
    bloqueos = quality.bloqueantes(problemas)
    if bloqueos:
        console.print(f"[red]Calidad de datos: {len(bloqueos)} problemas graves.[/]")
        for h in bloqueos[:5]:
            console.print(f"  [red]{h.check}:[/] {h.detail}")
    elif problemas:
        console.print(f"[dim]Calidad de datos: {quality.resumen(problemas)}.[/]")


def ingest_fundamentals(provider_name: str | None = None, all_tickers: bool = False) -> int:
    """Fundamentales, escalonados por defecto.

    Solo se refresca 1/7 del universo cada noche: los ratios cambian
    trimestralmente y pedirlos a diario es tirar la cuota de peticiones.
    """
    migrate()
    settings = get_settings()
    provider = get_price_provider(provider_name)
    run_id = str(uuid.uuid4())

    tickers = [t for t in _tickers_to_download() if not t.startswith("^")]
    shards = int(settings.ingest.get("fundamentals_shard_count", 7))
    if not all_tickers and shards > 1:
        today_shard = date.today().toordinal() % shards
        # hash() de Python aleatoriza las cadenas en cada proceso: con el, el
        # reparto en turnos cambiaria cada noche y habria tickers que nunca se
        # refrescarian. Hace falta un hash estable.
        tickers = [t for t in tickers if _stable_shard(t, shards) == today_shard]
        console.print(f"[cyan]Fundamentales:[/] turno {today_shard + 1}/{shards} ({len(tickers)} tickers)")
    else:
        console.print(f"[cyan]Fundamentales:[/] universo completo ({len(tickers)} tickers)")

    if not tickers:
        return 0

    try:
        df = provider.fetch_snapshot(tickers)
    except Exception as exc:  # noqa: BLE001
        with connect() as conn:
            _log(conn, run_id, "fundamentals", "shard", "FAILED", error=str(exc))
        console.print(f"[yellow]Fundamentales fallidos: {exc}[/]")
        return 0

    if df.empty:
        return 0

    # `completeness` es lo que despues evita que un valor con la mitad de los
    # campos vacios compita de tu a tu con uno que los tiene todos.
    df["completeness"] = df.apply(lambda r: completeness(r, _FUNDAMENTAL_FIELDS), axis=1)
    df["source"] = getattr(provider, "name", "unknown")

    with connect() as conn:
        n = upsert_df(conn, "fundamentals_snapshot", df, keys=["ticker", "as_of"])
        _log(conn, run_id, "fundamentals", "shard", "OK", rows=n)

    mean_cov = df["completeness"].mean()
    console.print(f"[green]Fundamentales: {n} filas[/] (cobertura media {mean_cov:.0%})")
    return n


def ingest_macro(years: int = 15) -> int:
    """Series macroeconomicas de FRED.

    Es opcional por diseno: sin `FRED_API_KEY` el resto del sistema funciona
    igual y solo la pagina de macro queda incompleta, avisando de ello.
    """
    migrate()
    run_id = str(uuid.uuid4())
    provider = FredProvider()

    if not provider.available:
        console.print(
            "[yellow]Sin FRED_API_KEY:[/] se omiten las series macro. "
            "Consigue una clave gratuita en fred.stlouisfed.org y ponla en .env"
        )
        with connect() as conn:
            _log(conn, run_id, "macro", "fred", "SKIPPED", error="sin clave")
        return 0

    series_ids = list(get_fred_series())
    if not series_ids:
        return 0

    start = date.today() - timedelta(days=365 * years)
    console.print(f"[cyan]Macro:[/] {len(series_ids)} series de FRED desde {start}")

    try:
        df = provider.fetch_series(series_ids, start)
    except Exception as exc:  # noqa: BLE001
        with connect() as conn:
            _log(conn, run_id, "macro", "fred", "FAILED", error=str(exc))
        console.print(f"[yellow]Macro fallido: {exc}[/]")
        return 0

    failed = df.attrs.get("failed_series", [])
    if df.empty:
        return 0

    with connect() as conn:
        n = upsert_df(conn, "macro_series", df, keys=["series_id", "date"])
        _log(conn, run_id, "macro", "fred", "PARTIAL" if failed else "OK", rows=n,
             error=f"{len(failed)} series fallidas" if failed else "")

    console.print(f"[green]Macro: {n} observaciones[/]"
                  + (f" · {len(failed)} series fallidas" if failed else ""))
    return n


def needs_update(max_age_hours: float | None = None) -> tuple[bool, str]:
    """¿Hacen falta datos nuevos? Devuelve (si_hace_falta, motivo).

    Se consulta desde el lanzador para ponerse al dia solo al abrir el
    programa. En un ordenador personal, la tarea programada de la noche se
    pierde cada vez que el equipo esta apagado, y si nadie compensa eso el
    dashboard acaba mostrando la semana pasada como si fuera hoy.

    No depende de Streamlit a proposito: tiene que poder ejecutarse desde un
    .bat antes de que arranque nada.
    """
    settings = get_settings()
    limit = float(
        max_age_hours
        if max_age_hours is not None
        else settings.ui.get("data_freshness_warn_hours", 30)
    )

    # Deliberadamente NO se llama a migrate(): abre el almacen en
    # lectura-escritura y DuckDB rechaza la conexion si el dashboard ya lo
    # tiene abierto para leer. Esta funcion se ejecuta justo antes de arrancar
    # el dashboard, asi que ese choque es el caso normal, no el raro. Si el
    # fichero no existe todavia, la respuesta correcta es "si, hacen falta
    # datos", no un error.
    if not settings.warehouse_path.exists():
        return True, "el almacen todavia no existe"

    try:
        with connect(read_only=True) as conn:
            synthetic = conn.execute(
                "SELECT COUNT(*) FROM prices_daily WHERE source = 'synthetic'"
            ).fetchone()[0]
            last_price = conn.execute("SELECT MAX(date) FROM prices_daily").fetchone()[0]
            last_run = conn.execute(
                "SELECT MAX(finished_at) FROM ingest_log WHERE status IN ('OK','PARTIAL')"
            ).fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        # Si no se puede ni leer, lo prudente es NO lanzar una descarga: puede
        # que otro proceso este escribiendo justo ahora.
        return False, f"no se ha podido consultar el almacen ({type(exc).__name__})"

    if synthetic:
        return True, f"hay {synthetic} precios de prueba"
    if last_price is None:
        return True, "el almacen esta vacio"
    if last_run is None:
        return True, "no consta ninguna descarga"

    hours = (utcnow() - pd.Timestamp(last_run)).total_seconds() / 3600.0
    if hours > limit:
        return True, f"la ultima descarga fue hace {hours:.0f} h"

    return False, f"al dia (ultima descarga hace {hours:.0f} h)"


def drop_synthetic() -> int:
    """Borra los precios inventados del almacen.

    Es obligatorio antes de la primera descarga real, y por una razon que no se
    ve venir: el generador sintetico produce series hasta HOY, asi que la
    ingesta incremental mira la ultima fecha por ticker, la encuentra al dia y
    no descarga nada. El usuario ejecuta la ingesta, no da ningun error, y
    sigue viendo los mismos precios inventados.
    """
    migrate()
    with connect() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM prices_daily WHERE source = 'synthetic'"
        ).fetchone()[0]
        if not before:
            console.print("[green]No hay datos sinteticos que borrar.[/]")
            return 0
        # Todo lo que se calcula a partir de esos precios deja de valer.
        for table in ("prices_daily", "indicators_daily", "factor_scores",
                      "factor_contributions", "signals", "breadth_daily",
                      "regime_daily", "sector_rotation", "signal_evidence"):
            try:
                if table == "prices_daily":
                    conn.execute(
                        "DELETE FROM prices_daily WHERE source = 'synthetic'"
                    )
                else:
                    conn.execute(f"DELETE FROM {table}")
            except Exception as exc:  # noqa: BLE001
                console.print(f"[yellow]{table}: {exc}[/]")

    console.print(
        f"[green]Borrados {before} precios sinteticos[/] y todo lo calculado "
        "a partir de ellos. La cartera y la watchlist se conservan."
    )
    return before


def mixed_source_tickers() -> pd.DataFrame:
    """Series cuyo historico procede de mas de una fuente.

    Importa porque las fuentes no significan lo mismo. Yahoo ajusta el cierre
    por dividendos y Stooq no, asi que una serie servida a medias por cada una
    tiene un salto artificial el dia del relevo. Ese salto no es un movimiento
    del mercado, pero los indicadores no saben distinguirlo: aparece como un
    retorno enorme, contamina la volatilidad y puede disparar una senal.
    """
    with connect(read_only=True) as conn:
        return conn.execute(
            """
            SELECT ticker,
                   COUNT(DISTINCT source) AS n_fuentes,
                   string_agg(DISTINCT source, ', ') AS fuentes,
                   MIN(date) AS desde,
                   MAX(date) AS hasta
            FROM prices_daily
            GROUP BY ticker
            HAVING COUNT(DISTINCT source) > 1
            ORDER BY ticker
            """
        ).fetchdf()


def repair_mixed_sources(provider_name: str | None = None) -> int:
    """Reconstruye desde cero las series con fuentes mezcladas.

    Se borra el historico del ticker y se vuelve a descargar entero, de modo
    que toda la serie tenga la misma convencion de ajuste. Es preferible
    perder cobertura (si la unica fuente disponible tiene menos historico) a
    conservar una serie con un escalon inventado en medio.
    """
    migrate()
    mixed = mixed_source_tickers()
    if mixed.empty:
        console.print("[green]Ninguna serie tiene fuentes mezcladas.[/]")
        return 0

    tickers = mixed["ticker"].tolist()
    console.print(
        f"[yellow]{len(tickers)} series con fuentes mezcladas.[/] "
        "Se reconstruyen enteras desde una sola fuente."
    )

    provider = get_price_provider(provider_name)
    settings = get_settings()
    today = date.today()
    start = today - timedelta(days=365 * int(settings.ingest.get("backfill_years", 10)))
    run_id = str(uuid.uuid4())

    df = provider.fetch_ohlcv(tickers, start, today + timedelta(days=1))
    if df.empty:
        console.print("[red]No se pudo redescargar ninguna serie. No se borra nada.[/]")
        return 0

    # Solo se reemplaza lo que se ha conseguido redescargar. Borrar una serie
    # que luego no se puede reponer seria destruir datos por una mejora.
    recovered = sorted(set(df["ticker"].unique()))
    with connect() as conn:
        conn.execute(
            "DELETE FROM prices_daily WHERE ticker IN "
            f"({','.join(['?'] * len(recovered))})",
            recovered,
        )
        n = upsert_df(conn, "prices_daily", df, keys=["ticker", "date"])
        _log(conn, run_id, "repair", "mixed_sources", "OK", rows=n)

    missing = sorted(set(tickers) - set(recovered))
    console.print(f"[green]{len(recovered)} series reconstruidas ({n} filas).[/]")
    if missing:
        console.print(
            f"[yellow]{len(missing)} sin redescargar, se dejan como estaban:[/] "
            + ", ".join(missing[:10]) + ("..." if len(missing) > 10 else "")
        )
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta de datos de mercado")
    parser.add_argument(
        "--what", default="all",
        choices=["all", "universe", "prices", "fundamentals", "macro"],
        help="que descargar",
    )
    parser.add_argument(
        "--provider", default=None,
        help="fuerza un proveedor (yfinance|stooq|synthetic) en vez de la cadena",
    )
    parser.add_argument("--full", action="store_true", help="backfill completo en vez de incremental")
    parser.add_argument(
        "--years", type=int, default=None,
        help="anos de historico a descargar (por defecto, los de settings.yaml)",
    )
    parser.add_argument(
        "--universes", default=None,
        help="limita a ciertas listas, separadas por comas (p.ej. INDICES,MACRO)",
    )
    parser.add_argument(
        "--check-stale", action="store_true",
        help="solo comprueba: codigo 1 si hacen falta datos nuevos, 0 si no",
    )
    parser.add_argument(
        "--drop-synthetic", action="store_true",
        help="borra los precios inventados antes de descargar los reales",
    )
    parser.add_argument(
        "--repair-mixed", action="store_true",
        help="reconstruye las series cuyo historico mezcla varias fuentes",
    )
    args = parser.parse_args()

    # Solo consulta: no escribe, asi que no toma el bloqueo.
    if args.check_stale:
        stale, reason = needs_update()
        console.print(("[yellow]Hacen falta datos nuevos: " if stale
                       else "[green]Datos ") + reason + "[/]")
        raise SystemExit(1 if stale else 0)

    # Todo lo que escribe va dentro del bloqueo, incluido el borrado de los
    # datos de prueba y la reparacion: son las operaciones mas destructivas y
    # las que peor llevarian un solapamiento.
    try:
        with single_writer("ingesta"):
            if args.repair_mixed:
                repair_mixed_sources(args.provider)
                return
            if args.drop_synthetic:
                drop_synthetic()
            _run(args)
    except AlreadyRunning as exc:
        # Ni exito ni fallo: no se ha descargado nada porque otro proceso tenia
        # el bloqueo. Necesita codigo propio porque los dos llamantes quieren
        # cosas distintas:
        #
        #   - el lanzador del dashboard lo trata como "no pasa nada, sigue":
        #     los datos que hay son los que hay y arrancar es lo importante;
        #   - la descarga del universo tiene que PARAR, porque calcular sobre
        #     una descarga que no ocurrio produce un ranking incompleto que
        #     parece completo.
        #
        # Con codigo 0 los dos veian exito, y la cadena del universo seguia
        # hasta anunciar "Universo completo listo" sin haber bajado un precio.
        console.print(f"[yellow]{exc} Se omite esta ejecucion.[/]")
        raise SystemExit(EXIT_ALREADY_RUNNING) from None


def _run(args) -> None:
    if args.what in ("all", "universe"):
        ingest_universe(args.provider)
    if args.what in ("all", "prices"):
        universes = (
            [u.strip().upper() for u in args.universes.split(",") if u.strip()]
            if args.universes else None
        )
        ingest_prices(args.provider, full=args.full, years=args.years,
                      universes=universes)
    if args.what in ("all", "fundamentals"):
        ingest_fundamentals(args.provider, all_tickers=args.full or args.provider == "synthetic")
    if args.what in ("all", "macro") and args.provider != "synthetic":
        ingest_macro()

    console.print("[bold green]Ingesta terminada.[/]")


if __name__ == "__main__":
    main()

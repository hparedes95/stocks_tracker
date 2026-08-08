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

from ..core.config import all_active_tickers, get_active_universes, get_settings, get_universes
from ..core.db import connect, migrate, upsert_df
from ..core.symbols import resolve_all
from ..core.timeutils import utcnow
from ..providers.base import completeness
from ..providers.registry import get_price_provider

console = Console()

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

    tickers = all_active_tickers()
    console.print(f"[cyan]Universo:[/] {len(tickers)} tickers en {len(get_active_universes())} listas")

    try:
        meta = provider.fetch_metadata(tickers)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]No se pudieron leer metadatos: {exc}[/]")
        meta = pd.DataFrame({"ticker": tickers})

    if meta.empty:
        meta = pd.DataFrame({"ticker": tickers})

    # Clase de activo por universo, como respaldo si el proveedor no la da.
    class_by_ticker: dict[str, str] = {}
    for key in get_active_universes():
        spec = universes.get(key)
        if spec:
            for t in spec.tickers:
                class_by_ticker.setdefault(t, spec.asset_class)

    records = meta.to_dict("records")
    for rec in records:
        rec.setdefault("asset_class", class_by_ticker.get(rec["ticker"], "equity"))
        if not rec.get("asset_class"):
            rec["asset_class"] = class_by_ticker.get(rec["ticker"], "equity")

    enriched = pd.DataFrame(resolve_all(records))
    enriched["is_active"] = True
    enriched["first_seen"] = date.today()
    enriched["last_seen"] = date.today()
    enriched["investment_type"] = enriched["asset_class"]
    enriched["updated_at"] = utcnow()

    membership_rows = []
    for key in get_active_universes():
        spec = universes.get(key)
        if not spec:
            continue
        for t in spec.tickers:
            membership_rows.append(
                {"universe": key, "ticker": t, "valid_from": date.today(), "valid_to": None}
            )

    with connect() as conn:
        n = upsert_df(conn, "instruments", enriched, keys=["ticker"])
        upsert_df(
            conn, "universe_membership", pd.DataFrame(membership_rows),
            keys=["universe", "ticker", "valid_from"],
        )
        _log(conn, run_id, "universe", "all", "OK", rows=n)

    unmapped = enriched["tv_symbol"].isna().sum()
    console.print(f"[green]Instrumentos: {n}[/] ({unmapped} sin equivalencia en TradingView)")
    return n


def _last_dates() -> dict[str, date]:
    with connect(read_only=True) as conn:
        df = conn.execute(
            "SELECT ticker, MAX(date) AS last_date FROM prices_daily GROUP BY ticker"
        ).fetchdf()
    if df.empty:
        return {}
    return {r.ticker: pd.Timestamp(r.last_date).date() for r in df.itertuples()}


def ingest_prices(provider_name: str | None = None, full: bool = False) -> int:
    """Descarga precios, incremental salvo que se pida backfill completo."""
    migrate()
    settings = get_settings()
    provider = get_price_provider(provider_name)
    run_id = str(uuid.uuid4())

    tickers = all_active_tickers()
    today = date.today()
    backfill_start = today - timedelta(days=365 * int(settings.ingest.get("backfill_years", 10)))
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
    for start, group in sorted(by_start.items()):
        console.print(f"[cyan]Descargando[/] {len(group)} tickers desde {start}")
        df = provider.fetch_ohlcv(group, start, today + timedelta(days=1))
        failed = df.attrs.get("failed_tickers", [])
        if df.empty:
            with connect() as conn:
                _log(conn, run_id, "prices", str(start), "FAILED", error="sin datos")
            continue
        with connect() as conn:
            n = upsert_df(conn, "prices_daily", df, keys=["ticker", "date"])
            status = "PARTIAL" if failed else "OK"
            _log(conn, run_id, "prices", str(start), status, rows=n,
                 requests=df.attrs.get("requests_used", 0),
                 error=f"{len(failed)} tickers fallidos" if failed else "")
        total += n
        console.print(f"  [green]{n} filas[/]" + (f" · {len(failed)} fallidos" if failed else ""))

    return total


def ingest_fundamentals(provider_name: str | None = None, all_tickers: bool = False) -> int:
    """Fundamentales, escalonados por defecto.

    Solo se refresca 1/7 del universo cada noche: los ratios cambian
    trimestralmente y pedirlos a diario es tirar la cuota de peticiones.
    """
    migrate()
    settings = get_settings()
    provider = get_price_provider(provider_name)
    run_id = str(uuid.uuid4())

    tickers = [t for t in all_active_tickers() if not t.startswith("^")]
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta de datos de mercado")
    parser.add_argument(
        "--what", default="all",
        choices=["all", "universe", "prices", "fundamentals"],
        help="que descargar",
    )
    parser.add_argument("--provider", default=None, help="fuerza un proveedor (yfinance|synthetic)")
    parser.add_argument("--full", action="store_true", help="backfill completo en vez de incremental")
    args = parser.parse_args()

    if args.what in ("all", "universe"):
        ingest_universe(args.provider)
    if args.what in ("all", "prices"):
        ingest_prices(args.provider, full=args.full)
    if args.what in ("all", "fundamentals"):
        ingest_fundamentals(args.provider, all_tickers=args.full or args.provider == "synthetic")

    console.print("[bold green]Ingesta terminada.[/]")


if __name__ == "__main__":
    main()

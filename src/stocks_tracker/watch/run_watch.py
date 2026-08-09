"""Proceso de vigilancia. Se queda corriendo y avisa si el mercado se desploma.

    python -m stocks_tracker.watch.run_watch          # vigila de verdad
    python -m stocks_tracker.watch.run_watch --once   # una pasada y sale
    python -m stocks_tracker.watch.run_watch --test-crash 8   # simula -8%

Es el unico componente del proyecto pensado para estar siempre encendido, y de
ahi vienen sus dos reglas: no puede caerse por un fallo de red, y no puede
escribir en el almacen (la ingesta nocturna lo toma en exclusiva).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime

import pandas as pd
from rich.console import Console

from ..alerts import notify
from ..core.config import get_settings
from ..core.db import connect
from ..core.timeutils import utcnow
from ..providers.base import ProviderError, empty_quotes
from ..providers.registry import get_price_provider
from . import monitor
from . import state as state_mod
from .config import get_watch_config, is_watch_time, local_timezone

console = Console()

_PORTFOLIO_REFRESH_MINUTES = 60
_stop = False


def _handle_signal(signum, frame) -> None:  # noqa: ARG001
    global _stop
    _stop = True
    console.print("\n[yellow]Parando el vigilante...[/]")


def load_portfolio() -> pd.DataFrame:
    """Posiciones abiertas. Si el almacen esta ocupado, se sigue sin cartera.

    La ingesta nocturna bloquea el fichero mientras escribe. Que el vigilante
    muriera por eso seria absurdo: los indices se siguen vigilando igual.
    """
    try:
        with connect(read_only=True) as conn:
            return conn.execute(
                "SELECT ticker, qty FROM positions WHERE closed_at IS NULL AND qty > 0"
            ).fetchdf()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]No se pudo leer la cartera: {exc}[/]")
        return pd.DataFrame(columns=["ticker", "qty"])


def symbols_to_watch(portfolio: pd.DataFrame) -> list[str]:
    cfg = get_watch_config()
    symbols = list(cfg.all_symbols)
    if cfg.watch_portfolio and not portfolio.empty:
        for ticker in portfolio["ticker"]:
            if ticker not in symbols:
                symbols.append(str(ticker))
    return symbols


def fetch(symbols: list[str], provider_name: str | None) -> pd.DataFrame:
    """Cotizaciones. Un fallo de red devuelve vacio, nunca revienta el bucle."""
    if not symbols:
        return empty_quotes()
    try:
        provider = get_price_provider(provider_name)
    except ProviderError as exc:
        console.print(f"[red]Sin proveedor: {exc}[/]")
        return empty_quotes()

    fetcher = getattr(provider, "fetch_quotes", None)
    if fetcher is None:
        # La cadena puede resolver a un proveedor sin intradia (Stooq no lo
        # tiene). Se intenta con el principal antes de rendirse.
        fetcher = getattr(getattr(provider, "primary", None), "fetch_quotes", None)
    if fetcher is None:
        console.print("[red]El proveedor activo no sirve cotizaciones en vivo.[/]")
        return empty_quotes()

    try:
        return fetcher(symbols)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]{utcnow():%H:%M:%S} fallo al consultar: {exc}[/]")
        return empty_quotes()


def tick(provider_name: str | None, portfolio: pd.DataFrame,
         st: state_mod.WatchState, dry_run: bool = False) -> int:
    """Una pasada completa. Devuelve cuantos avisos se han mandado."""
    cfg = get_watch_config()
    symbols = symbols_to_watch(portfolio)
    quotes = fetch(symbols, provider_name)
    if quotes.empty:
        return 0

    if st.roll_over():
        console.print("[cyan]Sesion nueva: niveles de aviso rearmados.[/]")

    breaches = monitor.evaluate_quotes(quotes, portfolio, cfg)
    alerts = monitor.to_alerts(breaches, st, cfg)
    alerts.extend(monitor.recovery_alerts(quotes, st, cfg))

    worst = _worst_change(quotes)
    console.print(
        f"[grey50]{datetime.now(local_timezone()):%H:%M:%S}[/] "
        f"{len(quotes)} simbolos · peor {worst} · {monitor.summarize(breaches)}"
    )

    if not alerts:
        return 0

    for alert in alerts:
        console.print(f"  [bold]{alert.severity.upper()}[/] {alert.message}")

    if dry_run:
        console.print("[yellow]--dry-run: no se envia ni se guarda nada.[/]")
        return len(alerts)

    results = notify.deliver(alerts)
    for result in results:
        colour = "green" if result.ok else "red"
        console.print(f"  [{colour}]{result.channel}: {result.detail or 'enviado'}[/]")

    state_mod.save(st)
    return len(alerts)


def _worst_change(quotes: pd.DataFrame) -> str:
    valid = quotes[quotes["change_pct"].notna()]
    if valid.empty:
        return "—"
    row = valid.loc[valid["change_pct"].idxmin()]
    return f"{row['ticker']} {float(row['change_pct']) * 100:+.2f}%"


def run(provider_name: str | None = None, once: bool = False,
        dry_run: bool = False, ignore_schedule: bool = False) -> int:
    cfg = get_watch_config()
    if not cfg.enabled and not once:
        console.print("[yellow]El vigilante esta desactivado en config/watch.yaml.[/]")
        return 0

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    st = state_mod.load()
    portfolio = load_portfolio()
    last_portfolio_refresh = time.monotonic()

    console.print(
        f"[bold cyan]Vigilante en marcha[/] · cada {cfg.interval_seconds} s · "
        f"{len(symbols_to_watch(portfolio))} simbolos · "
        f"canales: {', '.join(c['canal'] for c in notify.channel_status() if c['activo']) or 'ninguno'}"
    )
    console.print(
        "[grey50]Los datos de renta variable llegan con unos 15 minutos de "
        "retraso: es lo que sirve Yahoo gratis. La cripto va al momento.[/]"
    )

    total = 0
    while not _stop:
        # Si anades una posicion desde el dashboard, el vigilante debe acabar
        # enterandose sin reiniciarlo.
        if time.monotonic() - last_portfolio_refresh > _PORTFOLIO_REFRESH_MINUTES * 60:
            portfolio = load_portfolio()
            last_portfolio_refresh = time.monotonic()

        watching = ignore_schedule or is_watch_time()
        if watching:
            total += tick(provider_name, portfolio, st, dry_run)
        elif once:
            console.print(
                "[yellow]Fuera del horario de vigilancia. Usa --ignore-schedule "
                "para forzar una pasada.[/]"
            )

        if once:
            break

        # Fuera de horario se comprueba cada pocos minutos por si empieza la
        # sesion, en lugar de sondear el mercado cerrado cada minuto.
        delay = cfg.interval_seconds if watching else 300
        for _ in range(delay):
            if _stop:
                break
            time.sleep(1)

    # En seco NO se guarda: si se guardara, el primer simulacro dejaria los
    # niveles marcados como ya avisados y el segundo no detectaria nada. Es
    # decir, la herramienta de probar el vigilante lo dejaria medio ciego.
    if not dry_run:
        state_mod.save(st)
    console.print(f"[green]Vigilante detenido. {total} avisos enviados.[/]")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Vigilante de mercado en vivo")
    parser.add_argument("--once", action="store_true", help="una sola pasada y salir")
    parser.add_argument("--dry-run", action="store_true",
                        help="detecta y muestra, pero no envia ni guarda")
    parser.add_argument("--provider", default=None,
                        help="fuerza un proveedor (yfinance|synthetic)")
    parser.add_argument("--ignore-schedule", action="store_true",
                        help="vigila aunque el mercado este cerrado")
    parser.add_argument("--test-crash", type=float, default=None, metavar="PCT",
                        help="simula una caida del PCT%% para probar los avisos")
    parser.add_argument("--status", action="store_true",
                        help="muestra la configuracion y sale")
    args = parser.parse_args()

    if args.status:
        _print_status()
        return

    if args.test_crash is not None:
        # El proveedor sintetico lee esta variable y hunde los precios.
        os.environ["STOCKS_TRACKER_FAKE_CRASH"] = str(args.test_crash / 100.0)
        console.print(
            f"[yellow]Simulacro: se fuerza una caida del {args.test_crash:.1f}% "
            "con datos sinteticos.[/]"
        )
        run(provider_name=args.provider or "synthetic", once=True,
            dry_run=args.dry_run, ignore_schedule=True)
        return

    run(args.provider, once=args.once, dry_run=args.dry_run,
        ignore_schedule=args.ignore_schedule)


def _print_status() -> None:
    cfg = get_watch_config()
    portfolio = load_portfolio()
    console.print(f"[bold]Activo:[/] {cfg.enabled}")
    console.print(f"[bold]Intervalo:[/] {cfg.interval_seconds} s")
    console.print(f"[bold]Horario:[/] {get_settings().raw.get('timezone')} · "
                  + ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in cfg.windows))
    console.print(f"[bold]Vigilando ahora:[/] {is_watch_time()}")
    console.print(f"[bold]Simbolos:[/] {', '.join(symbols_to_watch(portfolio))}")
    console.print(f"[bold]Posiciones:[/] {len(portfolio)}")
    for channel in notify.channel_status():
        mark = "si" if channel["listo"] else "no"
        extra = f" (falta {channel['faltan']})" if channel["faltan"] else ""
        console.print(f"  canal {channel['canal']}: listo={mark}{extra}")


if __name__ == "__main__":
    sys.exit(0 if main() is None else 1)

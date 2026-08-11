"""Descarga el historico de cripto de Kraken al mismo almacen que las acciones.

Las velas van a `prices_daily` con `source='kraken'` y los pares se dan de alta
en `instruments` con `asset_class='crypto'`. A partir de ahi, el motor de
indicadores las trata como a cualquier otra serie: RSI, ATR, medias y momentum
salen solos, sin un segundo pipeline que mantener.

El universo NO se descubre: es la lista blanca de `config/trading.yaml`. Con
25 EUR y un minimo de orden de unos 5 EUR no caben mas de cuatro posiciones, y
anadir monedas pequenas solo mete riesgo de liquidez sin diversificar nada.

Sobre la ventana disponible: Kraken entrega como mucho 720 velas diarias, unos
dos anos, y no hay forma de paginar mas atras. Se avisa por pantalla porque un
backtest de dos anos de bitcoin puede salir estupendo por haber caido dentro de
una subida, no por que la estrategia sirva.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from rich.console import Console

from ..core.config import get_trading_config
from ..core.db import connect, migrate, upsert_df
from ..providers.kraken_provider import (
    MAX_CANDLES,
    KrakenPriceProvider,
    earliest_available,
)

console = Console()

# Nombre del universo en `universe_membership`. Sirve para que el resto del
# sistema pueda pedir "los de cripto" sin conocer la lista.
UNIVERSE = "CRYPTO"

_NOMBRES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "ADA": "Cardano",
    "DOT": "Polkadot", "LINK": "Chainlink", "XRP": "XRP", "LTC": "Litecoin",
    "DOGE": "Dogecoin", "AVAX": "Avalanche", "MATIC": "Polygon", "ATOM": "Cosmos",
}


def whitelist() -> list[str]:
    """Los pares del mandato. Si no hay venue cripto configurado, ninguno."""
    try:
        venue = get_trading_config().venue("kraken")
    except Exception:  # noqa: BLE001 — sin configuracion no hay universo
        return []
    permitidos = (venue.universe or {}).get("allowed") or []
    return [str(p) for p in permitidos]


def register_instruments(pairs: list[str]) -> int:
    """Da de alta los pares en `instruments`.

    Sin esto las velas quedarian en `prices_daily` sin ficha, y el ranking las
    ignora: el scoring cruza con `instruments` para saber el tipo de activo. Es
    exactamente el fallo que dejo el ranking vacio con las acciones.
    """
    if not pairs:
        return 0

    import pandas as pd

    hoy = date.today()
    filas = []
    for par in pairs:
        base = par.split("/")[0].upper()
        filas.append({
            "ticker": par,
            "name": _NOMBRES.get(base, base),
            "asset_class": "crypto",
            "exchange": "KRAKEN",
            "currency": par.split("/")[-1].upper(),
            "country": "",
            "gics_sector": "Crypto",
            "gics_industry": "Crypto",
            "investment_type": "crypto",
            "is_active": True,
            "first_seen": hoy,
            "last_seen": hoy,
            "tv_symbol": f"KRAKEN:{base}{par.split('/')[-1].upper()}",
            "tv_exchange": "KRAKEN",
            "tv_verified": False,
            "tv_source": "rule",
            "updated_at": datetime.now(),
        })

    df = pd.DataFrame(filas)
    with connect() as conn:
        n = upsert_df(conn, "instruments", df, keys=["ticker"])
        # Y al universo, para que se puedan pedir por grupo.
        miembros = pd.DataFrame([
            {"universe": UNIVERSE, "ticker": p, "valid_from": hoy, "valid_to": None}
            for p in pairs
        ])
        upsert_df(conn, "universe_membership", miembros,
                  keys=["universe", "ticker", "valid_from"])
    return n


def ingest_crypto_prices(full: bool = False) -> int:
    """Velas diarias de los pares del mandato."""
    migrate()
    pairs = whitelist()
    if not pairs:
        console.print("[yellow]No hay universo cripto en config/trading.yaml.[/]")
        return 0

    register_instruments(pairs)

    provider = KrakenPriceProvider()
    hoy = date.today()
    inicio = earliest_available(hoy)

    if not full:
        # Solo lo que falta. Se retrocede un dia por si la ultima vela estaba
        # a medio cerrar cuando se descargo.
        with connect(read_only=True) as conn:
            fila = conn.execute(
                "SELECT MAX(date) FROM prices_daily WHERE ticker IN "
                f"({', '.join('?' for _ in pairs)})", pairs,
            ).fetchone()
        ultima = fila[0] if fila else None
        if ultima:
            from datetime import timedelta

            inicio = max(inicio, ultima - timedelta(days=1))

    console.print(f"[cyan]Descargando[/] {len(pairs)} pares desde {inicio}")
    df = provider.fetch_ohlcv(pairs, inicio, hoy)

    fallidos = df.attrs.get("failed_tickers", [])
    truncados = df.attrs.get("truncated_tickers", [])

    if df.empty:
        console.print("[red]Kraken no ha devuelto ninguna vela.[/]")
        if fallidos:
            console.print(f"  Fallaron: {', '.join(fallidos)}")
        return 0

    run_id = str(uuid.uuid4())
    with connect() as conn:
        n = upsert_df(conn, "prices_daily", df.assign(
            source="kraken", ingested_at=datetime.now()
        ), keys=["ticker", "date"])
        conn.execute(
            "INSERT INTO ingest_log (run_id, started_at, task, target, status, "
            "rows_written, error) VALUES (?,?,?,?,?,?,?)",
            [run_id, datetime.now(), "crypto_prices", UNIVERSE,
             "PARTIAL" if fallidos else "OK", n,
             f"{len(fallidos)} pares fallidos" if fallidos else ""],
        )

    console.print(f"  [green]{n} filas[/]")
    if fallidos:
        console.print(f"  [yellow]Sin datos: {', '.join(fallidos)}[/]")
    if truncados:
        console.print(
            f"  [yellow]{len(truncados)} pares topan con el limite de "
            f"{MAX_CANDLES} velas de Kraken (~2 anos).[/]"
        )
        console.print(
            "  [yellow]No hay mas historico por esta API. Un backtest de dos "
            "anos de cripto cabe dentro de una sola subida: leelo sabiendo "
            "eso.[/]"
        )
    return n


def coverage() -> dict:
    """Cuantas velas y desde cuando, por par. Lo mira la puerta."""
    pairs = whitelist()
    if not pairs:
        return {}
    with connect(read_only=True) as conn:
        rows = conn.execute(
            "SELECT ticker, COUNT(*) AS n, MIN(date) AS desde, MAX(date) AS hasta "
            f"FROM prices_daily WHERE ticker IN ({', '.join('?' for _ in pairs)}) "
            "GROUP BY ticker ORDER BY ticker", pairs,
        ).fetchall()
    return {r[0]: {"velas": int(r[1]), "desde": r[2], "hasta": r[3]} for r in rows}


def main() -> int:
    """`python -m stocks_tracker.ingest.ingest_crypto`"""
    import argparse

    parser = argparse.ArgumentParser(description="Historico de cripto desde Kraken")
    parser.add_argument("--full", action="store_true",
                        help="Rehace todo el historico disponible")
    args = parser.parse_args()

    ingest_crypto_prices(full=args.full)

    console.print()
    console.print("  [bold]Cobertura[/]")
    for par, datos in coverage().items():
        console.print(f"    {par:<10} {datos['velas']:>4} velas   "
                      f"{datos['desde']} a {datos['hasta']}")
    console.print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

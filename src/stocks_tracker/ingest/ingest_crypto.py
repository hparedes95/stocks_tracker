"""Historico de cripto al mismo almacen que las acciones.

Las velas van a `prices_daily` y los pares se dan de alta en `instruments` con
`asset_class='crypto'`. A partir de ahi el motor de indicadores las trata como
a cualquier otra serie: RSI, ATR, medias y momentum salen solos, sin un
segundo pipeline que mantener.

El universo NO se descubre: es la lista blanca de `config/trading.yaml`. Con
25 EUR y un minimo de orden de unos 5 EUR no caben mas de cuatro posiciones, y
anadir monedas pequenas solo mete riesgo de liquidez sin diversificar nada.

**Por que el historico sale de Yahoo y no de Kraken, si operamos en Kraken.**
Kraken entrega 720 velas diarias como mucho —unos dos anos— y no hay forma de
pedir mas atras. Dos anos de cripto caben dentro de una sola subida: una
estrategia validada ahi puede parecer excelente por haber coincidido con un
mercado alcista. Yahoo da anos de historico de los mismos pares.

**Y por que NO se empalman las dos.** Seria tentador: Yahoo para lo antiguo,
Kraken para lo reciente. Pero son fuentes distintas y no coinciden al centimo,
asi que el punto de union mete un salto artificial en la serie —y un salto es
exactamente lo que una estrategia de momentum lee como senal—. Se inventaria
una operacion que nunca existio, siempre en la misma fecha, y saldria en el
backtest como una ganancia o una perdida que no es de nadie.

Asi que hay una sola serie guardada, de una sola fuente. Kraken se usa para
ejecutar y para el precio del momento de la orden, y `compare_sources()` mide
cuanto se separan las dos para saber que error se esta aceptando, en vez de
suponer que es cero.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

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


class MixedSourceError(RuntimeError):
    """Se iba a empalmar la serie con otra fuente. Ver `_refuse_to_splice`."""


def _refuse_to_splice(source: str, pairs: list[str]) -> None:
    """Impide que la serie de un par acabe con velas de dos fuentes.

    Yahoo y Kraken no coinciden al centimo, asi que el punto de union deja un
    salto artificial —y un salto es exactamente lo que una estrategia de
    momentum lee como senal—. Saldria una operacion que nunca existio, siempre
    en la misma fecha, contada como ganancia o perdida real.

    Falla en vez de avisar porque el resultado de un backtest empalmado tiene
    el mismo aspecto que el de uno bueno.
    """
    with connect(read_only=True) as conn:
        otras = conn.execute(
            "SELECT DISTINCT source FROM prices_daily WHERE source IS NOT NULL "
            f"AND source <> ? AND ticker IN ({', '.join('?' for _ in pairs)})",
            [source, *pairs],
        ).fetchall()
    if otras:
        nombres = ", ".join(sorted(o[0] for o in otras))
        raise MixedSourceError(
            f"Ya hay velas de cripto de '{nombres}' y se iban a anadir de "
            f"'{source}'. Dos fuentes en la misma serie meten un salto en la "
            "fecha de union que el momentum lee como senal.\n"
            "  Borra las anteriores antes de cambiar de fuente:\n"
            f"    DELETE FROM prices_daily WHERE source = '{nombres}' "
            "AND ticker LIKE '%/%'"
        )


def yahoo_symbol(pair: str) -> str:
    """'BTC/EUR' -> 'BTC-EUR', que es como se llama en Yahoo."""
    return pair.replace("/", "-").upper()


def _yahoo_history(pairs: list[str], inicio: date, hoy: date):
    """Historico largo desde Yahoo, devuelto con NUESTROS nombres de par.

    Se renombra de vuelta a 'BTC/EUR' antes de guardar: el ticker del almacen
    tiene que ser el mismo con el que opera el broker, o la estrategia elegiria
    'BTC-EUR' y el bot intentaria comprar algo que Kraken no conoce.
    """
    from ..providers.registry import build_provider

    provider = build_provider("yfinance")
    simbolos = [yahoo_symbol(p) for p in pairs]
    df = provider.fetch_ohlcv(simbolos, inicio, hoy)

    de_vuelta = {yahoo_symbol(p): p for p in pairs}
    if not df.empty:
        df = df.copy()
        df["ticker"] = df["ticker"].map(lambda t: de_vuelta.get(t, t))
    fallidos = [de_vuelta.get(t, t) for t in df.attrs.get("failed_tickers", [])]
    df.attrs["failed_tickers"] = fallidos
    df.attrs["truncated_tickers"] = []
    return df


def ingest_crypto_prices(full: bool = False, source: str = "yfinance",
                         years: int = 8) -> int:
    """Velas diarias de los pares del mandato, de una sola fuente.

    `source` no esta para mezclar: la serie guardada es entera de quien se
    diga. Empalmar Yahoo con Kraken meteria un salto artificial en la fecha de
    union, y un salto es lo que una estrategia de momentum lee como senal.
    """
    migrate()
    pairs = whitelist()
    if not pairs:
        console.print("[yellow]No hay universo cripto en config/trading.yaml.[/]")
        return 0

    register_instruments(pairs)
    # Antes de descargar: si se va a rechazar, no tiene sentido gastar unos
    # minutos de peticiones para acabar tirandolo.
    _refuse_to_splice(source, pairs)

    hoy = date.today()
    if source == "kraken":
        inicio = earliest_available(hoy)
    else:
        inicio = hoy - timedelta(days=365 * years)

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
            inicio = max(inicio, ultima - timedelta(days=1))

    console.print(f"[cyan]Descargando[/] {len(pairs)} pares desde {inicio} ({source})")
    if source == "kraken":
        df = KrakenPriceProvider().fetch_ohlcv(pairs, inicio, hoy)
    else:
        df = _yahoo_history(pairs, inicio, hoy)

    fallidos = df.attrs.get("failed_tickers", [])
    truncados = df.attrs.get("truncated_tickers", [])

    if df.empty:
        console.print(f"[red]{source} no ha devuelto ninguna vela.[/]")
        if fallidos:
            console.print(f"  Fallaron: {', '.join(fallidos)}")
        return 0

    run_id = str(uuid.uuid4())
    with connect() as conn:
        n = upsert_df(conn, "prices_daily", df.assign(
            source=source, ingested_at=datetime.now()
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


def compare_sources(days: int = 180, provider=None) -> dict:
    """Cuanto se separa Kraken de la serie guardada, en el periodo que solapan.

    Se valida contra los precios de Yahoo y se opera contra los de Kraken. Esa
    diferencia no es cero, y suponer que lo es seria el tipo de supuesto que no
    se nota hasta que el bot compra mas caro de lo que el backtest creia.

    Kraken NO se guarda: se pide en memoria y se compara. Escribirlo empalmaria
    las dos series, que es justo lo que `_refuse_to_splice` impide.

    Devuelve, por par, la diferencia relativa media y la peor del periodo.
    """
    pairs = whitelist()
    if not pairs:
        return {}

    hoy = date.today()
    inicio = max(earliest_available(hoy), hoy - timedelta(days=days))
    provider = provider or KrakenPriceProvider()
    kraken = provider.fetch_ohlcv(pairs, inicio, hoy)
    if kraken.empty:
        return {}

    with connect(read_only=True) as conn:
        guardado = conn.execute(
            "SELECT ticker, date, adj_close FROM prices_daily "
            f"WHERE ticker IN ({', '.join('?' for _ in pairs)}) AND date >= ?",
            [*pairs, inicio],
        ).fetchdf()
    if guardado.empty:
        return {}

    import pandas as pd

    izq = kraken[["ticker", "date", "adj_close"]].copy()
    izq["date"] = pd.to_datetime(izq["date"]).dt.date
    guardado["date"] = pd.to_datetime(guardado["date"]).dt.date

    juntos = izq.merge(guardado, on=["ticker", "date"], suffixes=("_kraken", "_guardado"))
    if juntos.empty:
        return {}

    # Relativa y no absoluta: 50 EUR de diferencia es ruido en bitcoin y un
    # disparate en cardano.
    juntos["dif"] = (
        (juntos["adj_close_kraken"] - juntos["adj_close_guardado"]).abs()
        / juntos["adj_close_guardado"].where(juntos["adj_close_guardado"] > 0)
    )

    out = {}
    for ticker, grupo in juntos.groupby("ticker"):
        validas = grupo["dif"].dropna()
        if validas.empty:
            continue
        out[str(ticker)] = {
            "dias": int(len(validas)),
            "media_pct": float(validas.mean() * 100),
            "peor_pct": float(validas.max() * 100),
        }
    return out


# Por encima de esto la serie con la que se valida ya no representa los precios
# a los que se opera, y el backtest deja de decir nada util.
DIVERGENCIA_ACEPTABLE_PCT = 1.0


def render_comparison(informe: dict) -> str:
    lineas = ["", "  Yahoo (con lo que se valida) vs Kraken (donde se opera)",
              "  " + "=" * 62, ""]
    if not informe:
        lineas += ["  Sin datos que comparar todavia.", ""]
        return "\n".join(lineas)

    lineas.append("  Par          dias   media    peor")
    lineas.append("  " + "-" * 40)
    peor_global = 0.0
    for par, d in sorted(informe.items()):
        marca = " *" if d["media_pct"] > DIVERGENCIA_ACEPTABLE_PCT else ""
        peor_global = max(peor_global, d["media_pct"])
        lineas.append(f"  {par:<12} {d['dias']:>4}  {d['media_pct']:>5.2f}%  "
                      f"{d['peor_pct']:>5.2f}%{marca}")
    lineas.append("")
    if peor_global > DIVERGENCIA_ACEPTABLE_PCT:
        lineas += [
            f"  * por encima del {DIVERGENCIA_ACEPTABLE_PCT:.0f}% acordado.",
            "  La serie con la que se valida no representa los precios a los",
            "  que se opera. Conviene decidir antes de validar nada.",
        ]
    else:
        lineas.append(f"  Por debajo del {DIVERGENCIA_ACEPTABLE_PCT:.0f}%: "
                      "se puede validar con Yahoo y operar en Kraken.")
    lineas.append("")
    return "\n".join(lineas)


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

    parser = argparse.ArgumentParser(description="Historico de cripto")
    parser.add_argument("--full", action="store_true",
                        help="Rehace todo el historico disponible")
    parser.add_argument("--source", default="yfinance", choices=("yfinance", "kraken"),
                        help="Fuente de la serie. NO se mezclan.")
    parser.add_argument("--comparar", action="store_true",
                        help="Mide cuanto se separa Kraken de la serie guardada")
    args = parser.parse_args()

    try:
        ingest_crypto_prices(full=args.full, source=args.source)
    except MixedSourceError as exc:
        console.print(f"\n[red]{exc}[/]\n")
        return 1

    console.print()
    console.print("  [bold]Cobertura[/]")
    for par, datos in coverage().items():
        console.print(f"    {par:<10} {datos['velas']:>5} velas   "
                      f"{datos['desde']} a {datos['hasta']}")
    console.print()

    if args.comparar:
        console.print(render_comparison(compare_sources()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

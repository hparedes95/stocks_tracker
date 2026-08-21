"""Auditoria cruzada de precios: pedir el mismo dia a varios proveedores.

POR QUE NO SE AUDITA TODO

Seiscientos valores por tres proveedores todos los dias son 1.800 peticiones
diarias contra APIs gratuitas. Eso no acaba en "datos muy verificados": acaba
en un bloqueo por abuso y en cero datos. El limite no es una molestia que
sortear, es parte del problema a resolver.

Asi que se audita donde el error cuesta dinero, y una muestra del resto:

1. LA CARTERA entera. Un precio equivocado ahi no es un dato feo en una tabla:
   es un P&L que no es el tuyo y un stop que salta donde no debe.
2. LOS VALORES CON SENAL de hoy. Son los que estan a punto de convertirse en
   una orden.
3. UNA MUESTRA ALEATORIA del universo. No para cubrirlo —no se cubre— sino
   para detectar que un proveedor ha empezado a degradarse en general. Un
   muestreo pequeno y constante encuentra eso; auditar siempre los mismos
   veinte valores, no.

La muestra es ALEATORIA de verdad, con semilla del dia. Con una lista fija se
auditaria eternamente el mismo trozo del universo y el resto quedaria sin
mirar para siempre, que es la forma mas facil de tener una auditoria que no
audita.

QUE SE COMPARA

El cierre sin ajustar. El motivo esta en `providers/consensus`: los `adj_close`
de Yahoo y Stooq no son la misma magnitud y compararlos marcaria como
discrepante a todo valor que haya pagado un dividendo.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta

import pandas as pd
from rich.console import Console

from ..core.config import get_settings
from ..core.db import connect, migrate
from ..core.ids import ulid
from ..core.timeutils import utcnow
from ..providers import consensus
from ..providers.base import ProviderError
from ..providers.registry import build_provider

console = Console()

# Cuantos valores del universo se cruzan por ejecucion, ademas de la cartera y
# las senales. Cincuenta con dos proveedores son cien peticiones: cabe de sobra
# en cualquier limite gratuito y en un ano cubre el universo varias veces.
MUESTRA = 50

# Sesiones hacia atras que se piden. Una sola no vale: si el ultimo dia del
# almacen no coincide con el ultimo del proveedor —y no coincide en cuanto hay
# un festivo de por medio— no habria ninguna fecha en comun que comparar.
SESIONES = 5

EXIT_SIN_SEGUNDA_FUENTE = 78


def _tickers_a_auditar(conn, muestra: int, semilla: int) -> tuple[list[str], dict]:
    """Cartera + senales de hoy + muestra aleatoria, sin repetir."""
    cartera = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM positions WHERE closed_at IS NULL"
    ).fetchall()]

    con_senal = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM signals "
        "WHERE date = (SELECT MAX(date) FROM signals)"
    ).fetchall()]

    universo = [r[0] for r in conn.execute(
        "SELECT ticker FROM instruments "
        "WHERE asset_class IN ('equity', 'etf') AND is_active ORDER BY ticker"
    ).fetchall()]

    prioritarios = list(dict.fromkeys(cartera + con_senal))
    resto = [t for t in universo if t not in set(prioritarios)]
    # Semilla del dia: reproducible dentro de la jornada —dos ejecuciones del
    # mismo dia auditan lo mismo y se pueden comparar— y distinta cada dia.
    aleatorios = random.Random(semilla).sample(resto, min(muestra, len(resto)))

    return prioritarios + aleatorios, {
        "cartera": len(cartera),
        "senales": len([t for t in con_senal if t not in set(cartera)]),
        "muestra": len(aleatorios),
    }


def _lecturas_del_almacen(conn, tickers: list[str], desde: date) -> pd.DataFrame:
    """Lo que ya tenemos guardado, con la fuente que lo sirvio."""
    if not tickers:
        return pd.DataFrame(columns=["ticker", "date", "source", "close"])
    marcas = ", ".join("?" for _ in tickers)
    return conn.execute(
        f"""
        SELECT ticker, date, COALESCE(source, 'almacen') AS source, close
        FROM prices_daily
        WHERE ticker IN ({marcas}) AND date >= ? AND close IS NOT NULL
        """,
        [*tickers, desde],
    ).fetchdf()


def _lecturas_del_contraste(nombre: str, tickers: list[str],
                            desde: date, hasta: date) -> pd.DataFrame:
    """Lo que dice un proveedor independiente, pedido en vivo."""
    vacio = pd.DataFrame(columns=["ticker", "date", "source", "close"])
    try:
        proveedor = build_provider(nombre)
    except (ProviderError, ValueError, KeyError) as exc:
        console.print(f"[yellow]No se puede usar {nombre}: {exc}[/]")
        return vacio

    soportados = [t for t in tickers if proveedor.supports(t)]
    if not soportados:
        console.print(f"[dim]{nombre} no cubre ninguno de estos valores.[/]")
        return vacio

    try:
        datos = proveedor.fetch_ohlcv(soportados, desde, hasta)
    except ProviderError as exc:
        # Un proveedor caido no invalida la auditoria: la deja incompleta, y
        # eso se ve en los veredictos DEGRADADO. Inventar un contraste que no
        # se ha podido hacer seria mucho peor que no tenerlo.
        console.print(f"[yellow]{nombre} no ha respondido: {exc}[/]")
        return vacio

    if datos.empty:
        return vacio
    salida = datos[["ticker", "date", "close"]].copy()
    salida["source"] = nombre
    return salida


def _contrastes_disponibles(cfg: dict) -> list[str]:
    """Proveedores de contraste: los del YAML mas los que tengan clave.

    POR QUE NO BASTA CON EL YAML

    Twelve Data solo hace falta cuando hay clave, y la clave vive en el `.env`.
    Pedir ademas una edicion a mano de `settings.yaml` tiene dos problemas y el
    segundo es grave:

    1. Son dos pasos para una sola decision ("quiero una tercera fuente"), y el
       segundo no lo adivina nadie.
    2. El instalador NO conserva `config/` entre actualizaciones —a proposito,
       para que un cambio de configuracion llegue— asi que esa edicion se pierde
       en la siguiente. La tercera fuente se apagaria sola sin que nada avise.

    Con la clave puesta, el proveedor entra. Sin ella, no. Es la misma condicion
    en un solo sitio.

    Y ESTO ES LO QUE CAMBIA DE VERDAD: con DOS fuentes, un desacuerdo deja el
    veredicto en "invalido" y no se sabe cual miente. Con TRES, dos que
    concuerdan hacen mayoria y la discrepante queda nombrada.
    """
    from ..providers import twelve_data_provider as td

    contrastes = list(cfg.get("providers", ["stooq"]))
    if td.api_key() and "twelve_data" not in contrastes:
        contrastes.append("twelve_data")
    return contrastes


def auditar(*, muestra: int = MUESTRA, sesiones: int = SESIONES,
            contrastes: list[str] | None = None) -> pd.DataFrame:
    """Cruza precios y devuelve el veredicto de cada (ticker, fecha)."""
    ajustes = get_settings()
    cfg = getattr(ajustes, "consensus", {}) or {}
    contrastes = contrastes or _contrastes_disponibles(cfg)
    tolerancia = float(cfg.get("tolerancia", consensus.TOLERANCIA_ACUERDO))
    maxima = float(cfg.get("maxima", consensus.MAX_DISCREPANCIA))

    hasta = date.today()
    desde = hasta - timedelta(days=int(sesiones * 7 / 5) + 4)

    with connect(read_only=True) as conn:
        tickers, reparto = _tickers_a_auditar(conn, muestra, hasta.toordinal())
        propias = _lecturas_del_almacen(conn, tickers, desde)

    if not tickers:
        console.print("[yellow]No hay nada que auditar todavia.[/]")
        return pd.DataFrame()

    console.print(
        f"[cyan]Auditando {len(tickers)} valores[/] "
        f"({reparto['cartera']} en cartera, {reparto['senales']} con senal, "
        f"{reparto['muestra']} de muestra) contra {', '.join(contrastes)}"
    )

    lecturas = [propias]
    for nombre in contrastes:
        lecturas.append(_lecturas_del_contraste(nombre, tickers, desde, hasta))

    todas = pd.concat([df for df in lecturas if not df.empty], ignore_index=True)
    if todas.empty:
        console.print("[yellow]Ninguna fuente ha servido datos.[/]")
        return pd.DataFrame()

    # Solo las fechas que TIENE el almacen. Un dia que el proveedor sirve y
    # nosotros no todavia no es una discrepancia: es que no lo hemos
    # descargado, y contarlo llenaria la auditoria de falsos DEGRADADO.
    fechas_nuestras = set(zip(propias["ticker"], pd.to_datetime(propias["date"]).dt.date,
                              strict=True))
    todas["date"] = pd.to_datetime(todas["date"]).dt.date
    todas = todas[[
        (t, d) in fechas_nuestras
        for t, d in zip(todas["ticker"], todas["date"], strict=True)
    ]]

    return consensus.comparar(todas, tolerancia=tolerancia, maxima=maxima)


def guardar(conn, veredictos: pd.DataFrame, run_id: str) -> int:
    """Escribe los veredictos, pisando el de la misma (ticker, fecha)."""
    if veredictos.empty:
        return 0
    filas = veredictos.copy()
    filas = filas.rename(columns={"fecha": "date"})
    filas["por_fuente"] = filas["por_fuente"].map(
        lambda d: json.dumps(d, ensure_ascii=False, sort_keys=True)
    )
    filas["checked_at"] = utcnow()
    filas["run_id"] = run_id

    conn.register("_consenso", filas)
    try:
        conn.execute(
            "DELETE FROM price_consensus WHERE (ticker, date) IN "
            "(SELECT ticker, date FROM _consenso)"
        )
        conn.execute(
            "INSERT INTO price_consensus (ticker, date, valor, veredicto, "
            "dispersion, n_fuentes, por_fuente, discrepantes, checked_at, run_id) "
            "SELECT ticker, date, valor, veredicto, dispersion, n_fuentes, "
            "por_fuente, discrepantes, checked_at, run_id FROM _consenso"
        )
    finally:
        conn.unregister("_consenso")
    return len(filas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cruza precios entre proveedores y guarda el veredicto"
    )
    parser.add_argument("--muestra", type=int, default=MUESTRA)
    parser.add_argument("--sesiones", type=int, default=SESIONES)
    parser.add_argument("--contra", default=None,
                        help="proveedores de contraste, separados por comas")
    args = parser.parse_args(argv)

    migrate()
    contrastes = args.contra.split(",") if args.contra else None
    veredictos = auditar(muestra=args.muestra, sesiones=args.sesiones,
                         contrastes=contrastes)
    if veredictos.empty:
        return 0

    run_id = ulid()
    with connect() as conn:
        guardar(conn, veredictos, run_id)

    conteo = consensus.resumen(veredictos)
    linea = "  ".join(
        f"{consensus.SEMAFORO[consensus.Veredicto(k)]} {k} {v}"
        for k, v in sorted(conteo.items())
    )
    console.print(f"[bold]Veredictos:[/] {linea}")

    rotos = veredictos[veredictos["veredicto"] == str(consensus.Veredicto.INVALIDO)]
    if not rotos.empty:
        console.print(
            f"[bold red]{len(rotos)} precios sin consenso.[/] Las fuentes no "
            "coinciden y no se sabe cual falla, asi que el bot no operara esos "
            "valores:"
        )
        for fila in rotos.head(10).itertuples():
            console.print(f"  [red]{fila.ticker} {fila.fecha}:[/] "
                          f"{fila.por_fuente} (dispersion {fila.dispersion:.2%})")

    solo_una = veredictos[veredictos["n_fuentes"] < 2]
    if len(solo_una) == len(veredictos):
        console.print(
            "[yellow]Ningun valor ha podido contrastarse: solo respondio una "
            "fuente.[/] Los veredictos dicen 'degradado', que es lo que son."
        )
        return EXIT_SIN_SEGUNDA_FUENTE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Calcula las recomendaciones del dia y las DEJA ESCRITAS.

POR QUE ES UN PASO APARTE Y NO ALGO QUE HAGA LA PANTALLA

La primera version de la seccion calculaba los consejos al pintar la pagina y
no los guardaba en ningun sitio. Funcionaba y se veia bien, pero:

1. **El marcador no se habria llenado nunca.** `guardar_recomendaciones` se
   quedo sin llamante durante un commit entero. La pagina ensenaba consejos
   convincentes cada dia y no quedaba constancia de ninguno, asi que dentro de
   seis meses no habria habido nada que puntuar. La seccion entera habria sido
   un horoscopo sin que fallara nada.

2. **Una pagina de Streamlit se re-ejecuta constantemente** —cada clic, cada
   filtro— y abre el almacen en solo lectura. Escribir desde ahi guardaria la
   misma recomendacion diez veces o pelearia con el calculo nocturno.

3. **Lo que se ve y lo que se puntua tienen que ser lo mismo.** Si la pantalla
   recalcula al vuelo y el marcador puntua lo guardado, los dos pueden separarse
   —basta que cambie un precio entre una cosa y otra— y entonces el marcador
   estaria puntuando consejos que nadie vio.

De ahi que esto sea un comando: se ejecuta una vez al dia despues del calculo,
escribe, y la pantalla solo lee.

QUE NO HACE

No ejecuta nada. No manda ordenes, no toca la cartera y no habla con ningun
broker. Escribe una fila por recomendacion accionable y se va.
"""

from __future__ import annotations

import argparse
from datetime import date

import pandas as pd
from rich.console import Console

from ..core import advice, advice_build, fx
from ..core import deterioration as det
from ..core.advice_store import guardar_recomendaciones
from ..core.config import get_settings
from ..core.db import connect, migrate
from ..core.scoring import preset_hash

console = Console()

# Sin recomendaciones que guardar. No es un error —hay dias en que no hay nada
# que hacer, y son la mayoria— pero conviene distinguirlo de "se guardaron 4".
EXIT_SIN_NADA = 0


def _cartera(conn) -> tuple[pd.DataFrame, dict, dict]:
    """Las posiciones abiertas con su peso en euros, y los pesos por sector."""
    posiciones = conn.execute(
        """
        SELECT p.id, p.ticker, p.qty, p.avg_cost, p.currency, p.opened_at,
               inst.gics_sector, i.close
        FROM positions p
        LEFT JOIN instruments inst ON inst.ticker = p.ticker
        LEFT JOIN indicators_daily i ON i.ticker = p.ticker
             AND i.date = (SELECT date FROM current_session)
        WHERE p.closed_at IS NULL AND p.qty > 0
        """
    ).fetchdf()
    if posiciones.empty:
        return posiciones, {}, {}

    tipos = fx.tipos(conn)
    posiciones["valor_eur"] = fx.a_base(
        posiciones["qty"] * posiciones["close"], posiciones["currency"], tipos)
    total = fx.total(posiciones["valor_eur"])
    # `fx.total` se contagia de los NaN a proposito. Si una sola posicion no se
    # puede valorar, los pesos no se pueden repartir y es mejor no dar ninguno
    # que dar unos que suman menos del 100 % sin decirlo.
    reparte = total == total and total > 0
    posiciones["peso_pct"] = (
        posiciones["valor_eur"] / total * 100.0 if reparte else None)

    pesos_sector = (
        posiciones.groupby(posiciones["gics_sector"].fillna(""))["peso_pct"]
        .sum().to_dict() if reparte else {}
    )
    pesos_actuales = (
        dict(zip(posiciones["ticker"], posiciones["peso_pct"], strict=False))
        if reparte else {}
    )
    return posiciones, pesos_sector, pesos_actuales


def _salud(conn) -> pd.DataFrame:
    """Los datos de hoy y los del dia de la compra, para el deterioro.

    Se replica la consulta de `data_access.get_position_health` en vez de
    importarla porque aquella arrastra Streamlit entero —cache incluida— y este
    comando se ejecuta desde una tarea programada sin navegador.

    Los NOMBRES de campo salen de `deterioration` y no de `data_access` por lo
    mismo: importar de alli metia Streamlit por la puerta de atras y llenaba la
    salida del comando con cincuenta y siete lineas de "No runtime found" antes
    de la primera cifra. Y son los campos que el diagnostico sabe mirar, que es
    donde tienen que vivir.
    """
    _CAMPOS_FUND = det.CAMPOS_FUNDAMENTALES
    _CAMPOS_IND = det.CAMPOS_PRECIO

    fund = ", ".join(f"f.{c} AS {c}_entonces" for c in _CAMPOS_FUND)
    ind = ", ".join(f"i.{c} AS {c}_entonces" for c in _CAMPOS_IND)
    hoy_fund = ", ".join(f"fh.{c}" for c in _CAMPOS_FUND)
    hoy_ind = ", ".join(f"ih.{c}" for c in _CAMPOS_IND)
    return conn.execute(
        f"""
        WITH abiertas AS (
            SELECT ticker, MIN(opened_at) AS opened_at
            FROM positions WHERE closed_at IS NULL AND qty > 0
            GROUP BY ticker
        ),
        con_fund AS (
            SELECT a.ticker, a.opened_at, f.as_of AS as_of_entonces, {fund}
            FROM abiertas a
            ASOF LEFT JOIN fundamentals_snapshot f
                 ON f.ticker = a.ticker AND f.as_of <= a.opened_at
        ),
        entonces AS (
            SELECT c.*, i.date AS fecha_entonces, {ind}
            FROM con_fund c
            ASOF LEFT JOIN indicators_daily i
                 ON i.ticker = c.ticker AND i.date <= c.opened_at
        )
        SELECT e.*, fh.as_of AS as_of, ih.date AS fecha, {hoy_fund}, {hoy_ind}
        FROM entonces e
        LEFT JOIN fundamentals_snapshot fh ON fh.ticker = e.ticker
             AND fh.as_of = (SELECT MAX(as_of) FROM fundamentals_snapshot
                             WHERE ticker = e.ticker)
        LEFT JOIN indicators_daily ih ON ih.ticker = e.ticker
             AND ih.date = (SELECT date FROM current_session)
        """
    ).fetchdf()


def calcular_y_guardar(preset: str | None = None, caja: float = 0.0) -> int:
    """Un veredicto por posicion y por candidato, guardando lo accionable.

    `caja` es el efectivo declarado. Por defecto CERO, y con cero las compras
    salen vetadas por falta de tamano: es lo correcto. El programa no habla con
    tu banco y suponer que hay dinero produciria recomendaciones de compra que
    no se pueden ejecutar.
    """
    migrate()
    nombre = preset or get_settings().compute.get("weights_preset", "balanced")
    whash = preset_hash(nombre)

    with connect(read_only=True) as conn:
        sesion = conn.execute("SELECT date FROM current_session").fetchdf()
        if sesion.empty:
            console.print("[yellow]No hay sesion calculada. Ejecuta el "
                          "calculo antes de pedir consejo.[/]")
            return EXIT_SIN_NADA
        dia = pd.Timestamp(sesion["date"].iloc[0]).date()

        posiciones, pesos_sector, pesos_actuales = _cartera(conn)
        salud = _salud(conn) if not posiciones.empty else pd.DataFrame()
        ranking = conn.execute(
            """
            SELECT f.ticker, f.composite_pctile, f.coverage,
                   f.value_z, f.growth_z, f.quality_z, f.momentum_z,
                   f.lowvol_z, f.dividend_z, f.technical_z,
                   inst.gics_sector, i.close, i.atr_pct,
                   fu.payout_ratio, fu.net_debt_to_ebitda, fu.trailing_pe
            FROM factor_scores f
            JOIN instruments inst ON inst.ticker = f.ticker
            JOIN indicators_daily i ON i.ticker = f.ticker AND i.date = f.date
            LEFT JOIN fundamentals_snapshot fu ON fu.ticker = f.ticker
                 AND fu.as_of = (SELECT MAX(as_of) FROM fundamentals_snapshot
                                 WHERE ticker = f.ticker)
            WHERE f.weights_hash = ? AND f.date = ?
            ORDER BY f.composite DESC LIMIT 60
            """,
            [whash, dia],
        ).fetchdf()
        universo = conn.execute(
            "SELECT universe_hash FROM scoring_runs WHERE weights_hash = ? "
            "ORDER BY date DESC LIMIT 1", [whash]).fetchone()

    valor = fx.total(posiciones["valor_eur"]) if not posiciones.empty else 0.0
    equity = (valor if valor == valor else 0.0) + caja

    # La cartera PRIMERO, y no por orden de lectura: una venta libera una de
    # las siete plazas, y sin resolverla antes un buen candidato saldria vetado
    # por una plaza que esta a punto de quedar libre.
    recomendaciones = advice_build.de_la_cartera(
        salud, posiciones, pesos_sector=pesos_sector)
    libera = sum(1 for r in recomendaciones
                 if r.veredicto is advice.Veredicto.VENDER)

    recomendaciones += advice_build.de_los_candidatos(
        ranking, equity=equity, caja=caja,
        n_posiciones=max(len(posiciones) - libera, 0),
        pesos_actuales=pesos_actuales, pesos_sector=pesos_sector,
    )

    precios = {}
    if not posiciones.empty:
        precios.update(dict(zip(posiciones["ticker"], posiciones["close"],
                                strict=False)))
    if not ranking.empty:
        precios.update(dict(zip(ranking["ticker"], ranking["close"],
                                strict=False)))

    with connect() as conn:
        n = guardar_recomendaciones(
            conn, recomendaciones, dia=dia, weights_hash=whash,
            precios=precios,
            universe_hash=(universo[0] if universo else ""))

    _informar(recomendaciones, n, dia, nombre)
    return n


def _informar(recomendaciones: list, guardadas: int, dia: date,
              preset: str) -> None:
    """Lo que se ha decidido, con los silencios incluidos.

    Las SIN_OPINION se cuentan en voz alta. Un resumen que solo dice "4 compras"
    parece mas completo de lo que es y esconde que hay treinta valores sobre los
    que no se ha podido opinar.
    """
    conteo: dict = {}
    for r in recomendaciones:
        conteo[r.veredicto] = conteo.get(r.veredicto, 0) + 1

    console.print(f"[bold]Consejos del {dia:%d/%m/%Y}[/] (perfil '{preset}')")
    for veredicto in advice.Veredicto:
        if conteo.get(veredicto):
            console.print(f"  {advice.ETIQUETA[veredicto]:<22} "
                          f"{conteo[veredicto]}")
    console.print(f"[green]Guardados {guardadas} accionables[/] "
                  "(los 'mantener' no se guardan: nadie actua sobre ellos)")


def por_que(ticker: str) -> int:
    """Por que esta posicion ha recibido su veredicto, con los numeros crudos.

    POR QUE ESTO ES UN COMANDO Y NO UNA CONSULTA A MANO

    Cuando el usuario reporto que MSFT y NVDA le salian como REDUCIR, la unica
    forma de diagnosticarlo era teclear una linea de SQL con comillas anidadas
    dentro de `cmd`. No funciono, y perdimos un intercambio entero en el
    escapado en vez de en el problema.

    Una pantalla que da consejos tiene que poder explicar cualquiera de ellos
    sin que haga falta abrir la base de datos.
    """
    ticker = ticker.strip().upper()
    with connect(read_only=True) as conn:
        fila = conn.execute(
            "SELECT * FROM positions WHERE ticker = ? AND closed_at IS NULL",
            [ticker]).fetchdf()
        if fila.empty:
            console.print(f"[yellow]{ticker} no esta en tu cartera abierta.[/]")
            return 1

        salud = _salud(conn)
        posiciones, pesos_sector, _ = _cartera(conn)

    suya = salud[salud["ticker"] == ticker] if not salud.empty else salud
    if suya.empty:
        console.print(f"[yellow]No hay datos de salud para {ticker}.[/]")
        return 1

    hoy, entonces = det.partir(suya.iloc[0])
    diagnostico = det.diagnosticar(
        ticker, fund_hoy=hoy, fund_entonces=entonces,
        ind_hoy=hoy, ind_entonces=entonces, comparado_con=hoy.get("opened_at"))

    console.print(f"[bold]Por que {ticker} recibe su veredicto[/]")
    console.print()
    # Las FECHAS de las dos fotos, antes que los numeros. Sin ellas, once
    # numeros identicos en las dos columnas se leen como "no ha cambiado nada"
    # cuando lo que dicen es "son la misma fila".
    console.print("[bold]Las dos fotos que se comparan[/]")
    console.print(f"  hoy               precio {_fecha(hoy.get('fecha'))}"
                  f"   fundamentales {_fecha(hoy.get('as_of'))}")
    console.print(f"  cuando compraste  precio {_fecha(entonces.get('fecha'))}"
                  f"   fundamentales {_fecha(entonces.get('as_of'))}")
    console.print(f"  fecha de compra guardada: {_fecha(hoy.get('opened_at'))}")
    if diagnostico.espejo:
        console.print()
        console.print(
            "[bold yellow]Las dos fotos son LA MISMA.[/] La fecha de compra "
            "que tengo es la de hoy, casi seguro la del dia en que importaste "
            "la cartera. Comparar hoy contra hoy no puede encontrar nada, asi "
            "que no se emite diagnostico: se emite SIN OPINION. Corrige la "
            "fecha de compra en 'Cartera y watchlist' y este valor pasa a "
            "juzgarse como los demas.")
    console.print()
    console.print("[bold]Los numeros[/] (hoy / cuando compraste)")
    for campo in det.CAMPOS_FUNDAMENTALES + det.CAMPOS_PRECIO:
        a, b = hoy.get(campo), entonces.get(campo)
        if a is None and b is None:
            continue
        console.print(f"  {campo:<22} {_cifra(a):>12}  /  {_cifra(b):>12}")

    # Un bloque entero de fundamentales vacio no es un detalle: es la mitad del
    # diagnostico —la que dice si el NEGOCIO ha empeorado— sin funcionar. Y en
    # una tabla de "nan" pasa desapercibido.
    if not diagnostico.con_fundamentales:
        console.print()
        console.print(
            f"[bold yellow]No hay fundamentales guardados de {ticker}.[/] "
            "Margen, ROE, crecimiento, deuda y payout estan vacios, asi que la "
            "mitad del diagnostico —la que mira el negocio— no puede correr. "
            "Solo queda el precio. Descarga fundamentales antes de fiarte de "
            "un veredicto sobre este valor.")

    mia = posiciones[posiciones["ticker"] == ticker]
    if not mia.empty and mia["peso_pct"].notna().any():
        peso = float(mia["peso_pct"].iloc[0])
        tope = _limites_del_asesor()["max_position_pct"]
        console.print()
        console.print(f"  peso en tu cartera     {peso:>11.1f} %  "
                      f"(tu tope: {tope:.0f} %)")

    console.print()
    console.print(f"[bold]Diagnostico:[/] {diagnostico.nivel.value} "
                  f"({diagnostico.puntos} puntos)")
    if not diagnostico.comparadas:
        if diagnostico.comparado:
            console.print("  Nada ha empeorado desde que la compraste.")
        else:
            console.print("  Nada COMPARADO: no hay foto del dia de la compra.")
    for s in diagnostico.comparadas:
        marca = "GRAVE" if s.grave else "leve "
        grupo = f" [grupo: {s.grupo}]" if s.grupo else ""
        console.print(f"  [{marca}]{grupo} {s.texto}")

    # Aparte, y con otro encabezado: esto NO ha movido el veredicto y decirlo
    # en la misma lista que lo que si lo mueve seria mezclar dos cosas.
    if diagnostico.observaciones:
        console.print()
        console.print("[bold]Lo que se ve hoy pero no se ha podido comparar[/] "
                      "(no puntua, no cambia el veredicto)")
        for s in diagnostico.observaciones:
            console.print(f"  [dato de hoy] {s.texto}")

    if diagnostico.solo_es_precio and diagnostico.con_fundamentales:
        console.print()
        console.print(
            "[dim]Todo lo encontrado es PRECIO y el negocio no ha empeorado. "
            "Las senales de precio cuentan como UNA —cuando una accion cae, "
            "pierde la MM200 y cruza a la baja por construccion— y no cambian "
            "el veredicto: para el precio esta tu stop.[/]"
        )
    return 0


def _cifra(v) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):.4g}"
    except (TypeError, ValueError):
        return str(v)[:12]


def _fecha(v) -> str:
    """Una fecha legible, o un hueco que se ve.

    Se imprime junto a los numeros para que dos columnas identicas se puedan
    leer como lo que son: la misma fila repetida, no una posicion sin cambios.
    """
    if v is None:
        return "(sin dato)"
    try:
        if bool(pd.isna(v)):
            return "(sin dato)"
    except (TypeError, ValueError):
        pass
    try:
        return f"{pd.Timestamp(v):%d/%m/%Y}"
    except (TypeError, ValueError):
        return str(v)[:10]


def _limites_del_asesor() -> dict:
    from ..core.advice import _limites

    return _limites()


def calibrar(preset: str | None = None, horizonte_meses: int = 6):
    """Mide si el liston de compra ha batido al indice, en lo que se puede.

    Vive con el resto del asesor y no en `backtest/` porque mide UNA regla
    concreta de este modulo —el corte del percentil 90— y no una estrategia
    entera. Lo segundo ya existe y tiene su propia puerta.
    """
    from ..core import advice_calib
    from ..trading import gate

    nombre = preset or "bot_core"
    bloqueos = tuple(gate.find_blockers(preset=nombre))

    with connect(read_only=True) as conn:
        scores = conn.execute(
            "SELECT ticker, date, composite_pctile FROM factor_scores "
            "WHERE weights_hash = ?", [preset_hash(nombre)]).fetchdf()
        precios = conn.execute(
            "SELECT ticker, date, adj_close FROM prices_daily "
            "ORDER BY ticker, date").fetchdf()
        bench = conn.execute(
            "SELECT date, adj_close FROM prices_daily WHERE ticker = '^GSPC' "
            "ORDER BY date").fetchdf()

    serie = (bench.set_index("date")["adj_close"] if not bench.empty
             else pd.Series(dtype=float))
    resultado = advice_calib.calibrar(
        scores, precios, serie, preset=nombre,
        horizonte_meses=horizonte_meses, bloqueos=bloqueos)

    console.print("[bold]Calibracion del liston de compra[/]")
    console.print(advice_calib.veredicto(resultado))
    return resultado


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calcula y guarda las recomendaciones del dia")
    parser.add_argument("--preset", default=None, help="perfil de pesos")
    parser.add_argument(
        "--por-que", default=None, metavar="TICKER", dest="por_que",
        help="Explica por que esa posicion tuya recibe su veredicto, con los "
             "numeros de hoy y los del dia en que la compraste.",
    )
    parser.add_argument(
        "--calibrar", action="store_true",
        help="No calcula consejos: mide si el liston de compra ha batido al "
             "indice historicamente. Solo vale para perfiles de solo precio.",
    )
    parser.add_argument(
        "--caja", type=float, default=0.0,
        help="Efectivo disponible en EUR. El programa no puede saberlo: sin "
             "este dato las compras salen vetadas por falta de tamano, que es "
             "lo correcto.",
    )
    args = parser.parse_args()
    if args.por_que:
        raise SystemExit(por_que(args.por_que))
    if args.calibrar:
        calibrar(args.preset)
        return
    calcular_y_guardar(args.preset, args.caja)


if __name__ == "__main__":
    main()

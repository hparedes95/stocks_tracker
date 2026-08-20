"""Contrastar lo que dice el broker con lo que cree el programa.

Es la unica comprobacion de todo el proyecto que se hace contra la VERDAD. Las
demas contrastan un proveedor con otro, o un dato consigo mismo; aqui hay un
tercero que no se equivoca en lo que importa, porque es quien tiene el dinero.

QUE SE COMPARA

Acciones, coste medio y efectivo. Con eso quedan cubiertos el valor de la
cartera, el P&L y el tamano de la siguiente orden, que es todo lo que decide el
gestor de riesgo.

LAS TRES DIFERENCIAS QUE IMPORTAN, Y NO SON LA MISMA

- CANTIDAD distinta. O hubo una operacion que el programa no registro, o la
  registro dos veces. El tamano de la siguiente orden sale de aqui.
- COSTE MEDIO distinto. La cantidad cuadra pero el P&L no es el tuyo. Suele ser
  una comision no contabilizada, o un dividendo tratado como compra.
- POSICION que existe en un lado y no en el otro. La peor de las tres: una
  posicion que el programa no ve no tiene stop, no entra en el limite de
  exposicion y no sale en ningun aviso. Existe con tu dinero dentro y para el
  programa no existe.

LO QUE NO SE HACE

No se corrige. Podria parecer obvio que el broker tiene razon y que basta con
copiar sus numeros, pero copiarlos BORRA la prueba de que hubo un desajuste, y
con ella la pregunta de por que lo hubo. Un desajuste tiene una causa —una
operacion perdida, una comision mal contada, un split— y esa causa se va a
repetir. Corregir el sintoma en silencio garantiza que se repita.

TAMPOCO SE OCULTA

Si no cuadra, se dice. Es el punto 15 del plan y es la parte facil de escribir y
la dificil de mantener: la tentacion de "redondear" una diferencia de tres euros
es exactamente como empiezan las contabilidades que no cuadran.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Cuanto puede diferir una cantidad sin que cuente. Las acciones se compran
# enteras casi siempre, pero las fracciones existen (planes de reinversion de
# dividendos, brokers que las permiten) y el broker las redondea a menos
# decimales que nosotros.
TOLERANCIA_QTY = 1e-6

# Un centimo. Por debajo es redondeo de la divisa, no un desajuste.
TOLERANCIA_DINERO = 0.01

CUADRA = "cuadra"
DIFIERE = "difiere"


@dataclass(frozen=True)
class Diferencia:
    ticker: str | None
    campo: str
    broker: float | None
    propio: float | None
    detalle: str

    @property
    def importe(self) -> float:
        """Cuanto se separan. Para ordenar por gravedad y no por orden alfabetico."""
        if self.broker is None or self.propio is None:
            return float("inf")
        return abs(self.broker - self.propio)


def _num(valor) -> float | None:
    if valor is None:
        return None
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def comparar(broker: dict, propio: dict, *,
             efectivo_broker: float | None = None,
             efectivo_propio: float | None = None) -> list[Diferencia]:
    """Todas las diferencias entre las dos contabilidades.

    `broker` y `propio` son {ticker: {"qty": x, "avg_cost": y}}.

    Se recorren las dos claves y no solo las del broker: una posicion que el
    PROGRAMA cree tener y el broker no es tan grave como al reves, y mirar solo
    un lado la dejaria invisible.
    """
    fuera: list[Diferencia] = []

    for ticker in sorted(set(broker) | set(propio)):
        suyo = broker.get(ticker)
        nuestro = propio.get(ticker)

        if suyo is not None and nuestro is None:
            cantidad = _num(suyo.get("qty"))
            fuera.append(Diferencia(
                ticker, "posicion_ausente", cantidad, None,
                f"El broker tiene {cantidad:,.6g} de {ticker} y el programa no "
                "sabe que existe. Esa posicion no tiene stop, no cuenta para el "
                "limite de exposicion y no sale en ningun aviso.",
            ))
            continue

        if nuestro is not None and suyo is None:
            cantidad = _num(nuestro.get("qty"))
            fuera.append(Diferencia(
                ticker, "posicion_fantasma", None, cantidad,
                f"El programa cree tener {cantidad:,.6g} de {ticker} y el broker "
                "no. O se vendio y no se registro, o nunca llego a comprarse.",
            ))
            continue

        qty_broker, qty_propio = _num(suyo.get("qty")), _num(nuestro.get("qty"))
        if (qty_broker is not None and qty_propio is not None
                and abs(qty_broker - qty_propio) > TOLERANCIA_QTY):
            fuera.append(Diferencia(
                ticker, "qty", qty_broker, qty_propio,
                f"{ticker}: el broker dice {qty_broker:,.6g} acciones y el "
                f"programa {qty_propio:,.6g}. El tamano de la siguiente orden "
                "sale de este numero.",
            ))

        coste_broker = _num(suyo.get("avg_cost"))
        coste_propio = _num(nuestro.get("avg_cost"))
        if (coste_broker is not None and coste_propio is not None
                and abs(coste_broker - coste_propio) > TOLERANCIA_DINERO):
            fuera.append(Diferencia(
                ticker, "avg_cost", coste_broker, coste_propio,
                f"{ticker}: coste medio {coste_broker:,.4f} en el broker y "
                f"{coste_propio:,.4f} en el programa. La cantidad cuadra, asi "
                "que lo que no es tuyo es el P&L. Suele ser una comision sin "
                "contabilizar o un dividendo tratado como compra.",
            ))

    caja_broker, caja_propio = _num(efectivo_broker), _num(efectivo_propio)
    if (caja_broker is not None and caja_propio is not None
            and abs(caja_broker - caja_propio) > TOLERANCIA_DINERO):
        fuera.append(Diferencia(
            None, "cash", caja_broker, caja_propio,
            f"Efectivo: {caja_broker:,.2f} en el broker y {caja_propio:,.2f} en "
            "el programa. De aqui sale cuanto se puede comprar.",
        ))

    # Lo mas gordo primero. Una lista ordenada por ticker esconde la diferencia
    # de mil euros entre veinte de tres centimos.
    return sorted(fuera, key=lambda d: -d.importe)


def resumen(diferencias: list[Diferencia], n_posiciones: int) -> str:
    if not diferencias:
        return (f"Las {n_posiciones} posiciones cuadran con el broker.")
    graves = [d for d in diferencias
              if d.campo in ("posicion_ausente", "posicion_fantasma")]
    partes = [f"{len(diferencias)} diferencias con el broker"]
    if graves:
        partes.append(f"{len(graves)} de ellas son posiciones que solo existen "
                      "en un lado")
    return ". ".join(partes) + "."


def guardar(conn, diferencias: list[Diferencia], venue: str, run_id: str,
            n_posiciones: int) -> int:
    """Escribe la revision, TAMBIEN cuando todo cuadra.

    Guardar solo los desajustes deja una tabla en la que no se distingue "hoy
    cuadra" de "hace tres meses que nadie lo mira", y esa diferencia es justo la
    que hace falta el dia que algo se descuadra.
    """
    from .timeutils import utcnow

    ahora = utcnow()
    filas = [
        {"checked_at": ahora, "venue": venue, "ticker": d.ticker,
         "campo": d.campo, "broker": d.broker, "propio": d.propio,
         "diferencia": (None if d.broker is None or d.propio is None
                        else d.broker - d.propio),
         "estado": DIFIERE, "detalle": d.detalle, "run_id": run_id}
        for d in diferencias
    ]
    if not filas:
        filas.append({
            "checked_at": ahora, "venue": venue, "ticker": None,
            "campo": "todo", "broker": float(n_posiciones),
            "propio": float(n_posiciones), "diferencia": 0.0,
            "estado": CUADRA,
            "detalle": f"{n_posiciones} posiciones revisadas y todas cuadran.",
            "run_id": run_id,
        })

    frame = pd.DataFrame(filas)
    conn.register("_reconc", frame)
    try:
        conn.execute(
            "INSERT INTO reconciliation (checked_at, venue, ticker, campo, "
            "broker, propio, diferencia, estado, detalle, run_id) "
            "SELECT checked_at, venue, ticker, campo, broker, propio, "
            "diferencia, estado, detalle, run_id FROM _reconc"
        )
    finally:
        conn.unregister("_reconc")
    return len(filas)


def posiciones_del_almacen(conn) -> dict[str, dict]:
    filas = conn.execute(
        "SELECT ticker, qty, avg_cost FROM positions WHERE closed_at IS NULL"
    ).fetchall()
    return {str(t): {"qty": q, "avg_cost": c} for t, q, c in filas}


def ultima_revision(conn) -> pd.DataFrame:
    return conn.execute(
        """
        SELECT * FROM reconciliation
        WHERE checked_at = (SELECT MAX(checked_at) FROM reconciliation)
        ORDER BY estado, ABS(COALESCE(diferencia, 1e18)) DESC
        """
    ).fetchdf()

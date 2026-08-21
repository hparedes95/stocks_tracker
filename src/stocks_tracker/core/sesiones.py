"""Que sesion esta COMPLETA, y por que esa pregunta es la que importa.

EL PROBLEMA QUE RESUELVE

`MAX(date)` miente. Con 623 valores en el universo, basta que UNO tenga la barra
de ayer para que el maximo diga "ayer", y a partir de ahi todo el que pregunte
"¿estoy al dia?" recibe un si con 622 valores sin descargar.

Paso de verdad: la descarga de precios reventaba a mitad, entraban tres indices
y ni una accion. `MAX(date)` decia 20/08, el lanzador respondia "al dia" y no
volvia a descargar nunca. El dashboard llevaba dias en el 18.

LA DEFINICION

Una sesion esta completa cuando reune al menos el 60 % de los valores del dia
mas poblado de las ultimas 30 sesiones. Es la misma regla que ya usaba la vista
`current_session` para decidir que dia ensena el dashboard; lo que faltaba era
usarla tambien para decidir cuando descargar y cuando calcular.

Y ES UNA REGLA RELATIVA A PROPOSITO. Un umbral absoluto —"600 valores"— se
rompe el dia que el universo cambia de tamano, y encima de forma silenciosa: no
falla, simplemente deja de considerar completa ninguna sesion.

TRES ESTADOS, NO DOS

Con esto, "el dashboard no avanza" deja de ser una sola cosa y se separa en tres
averias con tres arreglos distintos:

  - faltan precios          -> hay que descargar
  - hay precios sin calcular -> hay que calcular
  - hay sesiones a medias    -> hay que volver a descargar ESAS sesiones

La tercera no la reportaba nadie, y es la que tenia parado al usuario.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

# El mismo 0,6 que la vista `current_session` del esquema. Estan en dos sitios
# —uno en SQL y otro aqui— porque la vista tiene que poder consultarse sin
# pasar por Python; hay un test que comprueba que no se separen.
UMBRAL_COMPLETA = 0.6

# Cuantas sesiones hacia atras se miran para saber cual es "el dia mas poblado".
# Treinta cubre mes y medio de mercado: suficiente para que un puente o una
# racha de festivos no muevan la referencia.
VENTANA = 30

TABLAS = {"prices_daily", "indicators_daily"}


def _conteos(conn, tabla: str) -> list[tuple[date, int]]:
    """Valores por fecha en las ultimas sesiones. Solo acciones y ETF.

    Solo acciones y ETF porque son los que definen si una sesion de bolsa esta
    completa. Cripto cotiza los domingos y las divisas casi siempre: contarlos
    haria que un domingo con cuatro pares pareciera una sesion.
    """
    if tabla not in TABLAS:
        raise ValueError(f"tabla no permitida: {tabla}")
    return [
        (pd.Timestamp(f[0]).date(), int(f[1]))
        for f in conn.execute(
            f"""
            SELECT t.date, COUNT(*) FROM {tabla} t
            JOIN instruments i USING (ticker)
            WHERE i.asset_class IN ('equity', 'etf')
            GROUP BY t.date ORDER BY t.date DESC LIMIT {VENTANA}
            """
        ).fetchall()
    ]


def umbral(conteos: list[tuple[date, int]]) -> float:
    if not conteos:
        return 0.0
    return max(n for _, n in conteos) * UMBRAL_COMPLETA


def ultima_completa(conn, tabla: str) -> date | None:
    """La sesion mas reciente que esta ENTERA. None si no hay ninguna."""
    conteos = _conteos(conn, tabla)
    if not conteos:
        return None
    minimo = umbral(conteos)
    completas = [f for f, n in conteos if n >= minimo]
    return max(completas) if completas else None


def incompletas(conn, tabla: str) -> list[tuple[date, int, float]]:
    """Sesiones a medias mas nuevas que la ultima completa.

    Son las que existen en la tabla y el dashboard NO ensena, y desde fuera eso
    se ve exactamente igual que "no se ha descargado" o que "no se ha
    calculado". Sin nombrarlas, el usuario no tiene forma de distinguir las tres
    cosas, y las tres se arreglan distinto.
    """
    conteos = _conteos(conn, tabla)
    if not conteos:
        return []
    minimo = umbral(conteos)
    completa = ultima_completa(conn, tabla)
    return sorted(
        (f, n, minimo) for f, n in conteos
        if n < minimo and (completa is None or f > completa)
    )


def sesiones_de_mercado(desde: date | None, hasta: date) -> int:
    """Dias de mercado entre dos fechas, sin contar `desde`.

    De lunes a viernes. Los festivos inflan la cuenta —no se conocen aqui, y un
    calendario de cinco mercados en cuatro paises seria otra fuente de datos que
    mantener— asi que el numero es un TECHO, no una cifra exacta. Para decidir
    si avisar vale: cero significa "no falta nada" con certeza.
    """
    if desde is None or hasta <= desde:
        return 0
    return sum(
        1 for n in range((hasta - desde).days)
        if (desde + timedelta(days=n + 1)).weekday() < 5
    )

"""Convertir importes a la divisa de la cuenta.

POR QUE EXISTE ESTE MODULO

La cartera se guarda en la divisa de cada valor: AAPL en dolares, SAN.MC en
euros, y `avg_cost` viene del extracto del broker tal cual. Eso esta bien y es
lo unico que se puede hacer sin inventarse un tipo de cambio historico.

Lo que NO se podia hacer era SUMAR. `5_watchlist.py` calculaba el valor total
de la cartera con

    positions["qty"] * positions["close"]

y lo sumaba entero, mezclando dolares con euros como si valieran lo mismo. Con
EUR/USD a 1,17, una cartera mitad y mitad se presentaba un 8 % por encima de lo
que vale. Habia un aviso en pantalla —"los totales se suman sin convertir"— y
un aviso no arregla una cifra: se lee una vez y despues se mira el numero.

Y el peso de cada posicion salia del mismo total. Un valor en dolares se veia
mas grande de lo que es y uno en euros mas pequeno, que es justo la cifra con
la que se decide si una posicion pesa demasiado.

LO QUE HACE Y LO QUE NO

Convierte con el ULTIMO tipo de cambio disponible en el almacen. Eso vale para
valorar hoy —una cartera se valora al cambio de hoy— y NO vale para reconstruir
el coste historico: el euro que pagaste por tus dolares hace dos anos no es el
de hoy. Por eso `avg_cost` convertido es una aproximacion y se dice donde se
usa.

Cuando falta el tipo de cambio, esto devuelve NaN y NO 1,0. Tratar una divisa
desconocida como paridad es exactamente el fallo que se esta arreglando, solo
que sin aviso. Una celda vacia se ve; un numero mal, no.
"""

from __future__ import annotations

import pandas as pd

# De que ticker de Yahoo sale cada par. Yahoo publica `EURUSD=X` como "cuantos
# USD vale 1 EUR", asi que para pasar de USD a EUR se DIVIDE.
#
# Solo estan los pares que puede traer un extracto de un broker europeo. Anadir
# uno son dos lineas: aqui y en `config/universe.yaml` (bloque MACRO), y sin lo
# segundo el tipo no se descarga nunca y la conversion sale vacia.
PARES = {
    "USD": "EURUSD=X",
    "GBP": "EURGBP=X",
    "CHF": "EURCHF=X",
    "JPY": "EURJPY=X",
    "SEK": "EURSEK=X",
    "NOK": "EURNOK=X",
    "DKK": "EURDKK=X",
    "CAD": "EURCAD=X",
    "AUD": "EURAUD=X",
}

BASE = "EUR"

# Sesiones hacia atras que se admiten para dar un tipo por vigente. Un puente
# largo cabe de sobra. Mas alla, el mercado se ha movido y el tipo ya no
# describe el dia que se esta valorando: es mejor no dar cifra que darla vieja.
MAX_DIAS_TIPO = 7


def tipos(conn, hasta: object | None = None) -> dict[str, float]:
    """Cuantas unidades de cada divisa vale un euro, a dia de hoy.

    Devuelve solo los pares que estan en el almacen y son recientes. Lo que no
    esta, no esta: no se rellena con 1,0 ni con el ultimo valor conocido de hace
    un mes.

    `hasta` fija la fecha de referencia; por defecto, la ultima sesion con datos.
    """
    if not PARES:
        return {}

    marcadores = ", ".join("?" for _ in PARES)
    filas = conn.execute(
        f"""
        SELECT ticker, close, date FROM prices_daily
        WHERE ticker IN ({marcadores}) AND close IS NOT NULL AND close > 0
        QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY date DESC) = 1
        """,
        list(PARES.values()),
    ).fetchall()

    referencia = pd.Timestamp(hasta) if hasta is not None else None
    if referencia is None and filas:
        referencia = max(pd.Timestamp(f[2]) for f in filas)

    por_ticker = {}
    for ticker, close, fecha in filas:
        if referencia is not None:
            antiguedad = (referencia - pd.Timestamp(fecha)).days
            if antiguedad > MAX_DIAS_TIPO:
                continue
        por_ticker[ticker] = float(close)

    return {divisa: por_ticker[t] for divisa, t in PARES.items() if t in por_ticker}


def a_base(importes: pd.Series, divisas: pd.Series,
           tipos_cambio: dict[str, float]) -> pd.Series:
    """Pasa cada importe a euros segun la divisa de su fila.

    Lo que no se puede convertir sale NaN, y ese NaN tiene que llegar hasta el
    total: una suma que ignora las filas que no supo convertir da un numero
    menor y con pinta de correcto, que es peor que no dar ninguno.
    """
    importes = pd.to_numeric(importes, errors="coerce")
    codigos = divisas.astype("string").str.upper().fillna("")

    factor = codigos.map(lambda c: 1.0 if c == BASE else tipos_cambio.get(c))
    return importes / pd.to_numeric(factor, errors="coerce")


def total(importes: pd.Series) -> float:
    """Suma que se contagia de los huecos, al reves que `Series.sum()`.

    `Series.sum()` SALTA los NaN por defecto. Sumando una cartera eso es un
    fallo silencioso de manual: la posicion que no se supo convertir sale del
    total y queda un numero mas pequeno, redondo y con toda la pinta de estar
    bien. Nadie mira un total y piensa "le falta una fila".

    Aqui se prefiere el vacio: `format_money` lo pinta como una raya, y una raya
    se pregunta.
    """
    return float(pd.to_numeric(importes, errors="coerce").sum(skipna=False))


def sin_tipo(divisas: pd.Series, tipos_cambio: dict[str, float]) -> list[str]:
    """Las divisas presentes para las que no hay tipo. Para poder decirlo."""
    codigos = set(divisas.astype("string").str.upper().dropna())
    return sorted(c for c in codigos if c != BASE and c not in tipos_cambio)

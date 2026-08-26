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

    # La referencia es HOY, no la fecha mas nueva de los propios tipos.
    #
    # Comparando cada par contra el maximo de los pares, si TODOS estan igual de
    # viejos la guarda no salta nunca: el mas nuevo es su propia referencia,
    # antiguedad cero, y la cartera se valora con tipos de hace un mes sin que
    # nada lo diga. Justo el caso que importa —el programa lleva dias sin
    # descargar— era el unico que la guarda no veia.
    referencia = pd.Timestamp(hasta) if hasta is not None else pd.Timestamp.today()

    por_ticker = {}
    for ticker, close, fecha in filas:
        if (referencia - pd.Timestamp(fecha)).days > MAX_DIAS_TIPO:
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


SIN_DIVISA = "(sin divisa)"


def sin_tipo(divisas: pd.Series, tipos_cambio: dict[str, float]) -> list[str]:
    """Las divisas presentes para las que no hay tipo. Para poder decirlo.

    Una divisa NULL cuenta y se nombra. Antes esto hacia `dropna()` mientras
    `a_base` hacia `fillna("")`, asi que una posicion sin divisa anulaba el
    total pero no aparecia en la lista de lo que falta. Y como la pantalla
    decide entre el aviso y la nota con `if faltan / elif hay varias divisas`,
    el resultado era un total vacio sin una sola linea que lo explicara.
    """
    codigos = divisas.astype("string").str.upper()
    fuera = {c for c in codigos.dropna()
             if c and c != BASE and c not in tipos_cambio}
    if codigos.isna().any() or (codigos.fillna("") == "").any():
        fuera.add(SIN_DIVISA)
    return sorted(fuera)


def tipos_en(conn, fechas: pd.Series | list) -> pd.DataFrame:
    """El tipo de cambio VIGENTE en cada fecha dada, por divisa.

    POR QUE HACE FALTA EL TIPO DE ENTONCES Y NO VALE EL DE HOY

    El resultado en euros de una posicion en dolares tiene dos partes: lo que
    ha hecho la accion y lo que ha hecho el cambio. Convirtiendo el coste y el
    valor con el MISMO tipo -el de hoy- la segunda parte se cancela y
    desaparece de la cifra.

    Reproducido: compras 1.000 USD de AAPL con el EUR/USD a 1,05, o sea 952 EUR
    de verdad. Hoy valen 1.100 USD con el cambio a 1,17, o sea 940 EUR.

        Con el tipo de hoy en los dos lados:  940 - 855 = +85 EUR  (+10 %)
        Con el tipo de cada fecha:            940 - 952 = -12 EUR  (-1,3 %)

    Se declaraba una ganancia donde hay una perdida. Y la segunda cifra es
    ademas la que cuenta para Hacienda, que calcula la ganancia patrimonial en
    euros al tipo de cada fecha —punto a confirmar con un asesor fiscal—.

    Devuelve un DataFrame (fecha, divisa, tipo) con el ultimo tipo conocido en
    esa fecha o antes. Lo que no tenga tipo NO sale: quien lo use tiene que
    quedarse sin cifra, no con una inventada.
    """
    fechas = pd.to_datetime(pd.Series(list(fechas)), errors="coerce").dropna()
    if fechas.empty or not PARES:
        return pd.DataFrame(columns=["fecha", "divisa", "tipo"])

    pedidas = pd.DataFrame({"fecha": sorted(set(fechas.dt.date))})
    marcadores = ", ".join("?" for _ in PARES)
    historico = conn.execute(
        f"SELECT ticker, date, close FROM prices_daily "
        f"WHERE ticker IN ({marcadores}) AND close IS NOT NULL AND close > 0 "
        f"ORDER BY ticker, date",
        list(PARES.values()),
    ).fetchdf()
    if historico.empty:
        return pd.DataFrame(columns=["fecha", "divisa", "tipo"])

    # `astype("datetime64[ns]")` y no solo `to_datetime`: DuckDB devuelve las
    # fechas con resolucion de SEGUNDOS y las construidas aqui salen en
    # microsegundos. `merge_asof` no admite claves de distinta resolucion y
    # aborta con "incompatible merge keys, must be the same type". Se vio al
    # ejecutar los tests, no al escribirlo.
    historico["date"] = pd.to_datetime(historico["date"]).astype("datetime64[ns]")
    pedidas["fecha"] = pd.to_datetime(pedidas["fecha"]).astype("datetime64[ns]")
    por_ticker = {t: d for t, d in PARES.items()}

    fuera = []
    for ticker, grupo in historico.groupby("ticker", sort=False):
        divisa = next((d for d, t in por_ticker.items() if t == ticker), None)
        if divisa is None:
            continue
        # `merge_asof` y no un ASOF de DuckDB: aquel exige comparar dos
        # COLUMNAS y aqui una de las dos partes se construye en memoria.
        casado = pd.merge_asof(
            pedidas.sort_values("fecha"),
            grupo[["date", "close"]].sort_values("date"),
            left_on="fecha", right_on="date", direction="backward",
        ).dropna(subset=["close"])
        if casado.empty:
            continue
        casado = casado.assign(divisa=divisa).rename(columns={"close": "tipo"})
        fuera.append(casado[["fecha", "divisa", "tipo"]])

    if not fuera:
        return pd.DataFrame(columns=["fecha", "divisa", "tipo"])
    return pd.concat(fuera, ignore_index=True)


def a_base_en_fecha(importes: pd.Series, divisas: pd.Series,
                    fechas: pd.Series, tabla: pd.DataFrame) -> pd.Series:
    """Como `a_base`, pero con el tipo de cambio de la fecha de cada fila.

    `tabla` es lo que devuelve `tipos_en`. Igual que en `a_base`, lo que no se
    puede convertir sale NaN y ese NaN tiene que llegar al total.
    """
    importes = pd.to_numeric(importes, errors="coerce")
    codigos = divisas.astype("string").str.upper().fillna("")
    dias = pd.to_datetime(fechas, errors="coerce")

    if tabla is None or tabla.empty:
        indice = {}
    else:
        indice = {
            (pd.Timestamp(f).normalize(), d): float(t)
            for f, d, t in zip(tabla["fecha"], tabla["divisa"], tabla["tipo"],
                               strict=False)
        }

    def factor(codigo, dia) -> float | None:
        if codigo == BASE:
            return 1.0
        if pd.isna(dia):
            return None
        return indice.get((pd.Timestamp(dia).normalize(), codigo))

    factores = [factor(c, d) for c, d in zip(codigos, dias, strict=False)]
    return importes / pd.to_numeric(pd.Series(factores, index=importes.index),
                                    errors="coerce")

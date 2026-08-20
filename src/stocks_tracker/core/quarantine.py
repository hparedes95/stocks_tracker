"""Barras imposibles: apartarlas del calculo sin borrarlas ni inventarselas.

EL PROBLEMA QUE RESUELVE

Yahoo manda, de vez en cuando, una barra que no puede existir: un cierre por
encima del maximo del dia, un maximo por debajo del minimo. En la primera
instalacion real fueron CUATRO barras de 2021 sobre casi un millon. La puerta de
calidad las declaraba bloqueantes y el programa se quedaba sin poder calcular
nada. Y no era un bloqueo que el usuario pudiera resolver: en la siguiente
descarga el proveedor manda exactamente la misma barra mala, asi que el
programa quedaba inutilizado para siempre por cuatro filas de hace cinco anos.

Bloquear todo o tragarselo no son las dos unicas opciones. La tercera es
apartar esas barras del calculo y decirlo.

QUE SE APARTA EXACTAMENTE

Solo `open`, `high` y `low`. El cierre se conserva, y no por comodidad:

- La incoherencia casi siempre esta en el RANGO. Los maximos y minimos
  intradia de Yahoo vienen de un feed distinto al del cierre, y es ese el que
  falla.
- El cierre es ademas el unico campo que tiene otra comprobacion detras: la de
  reescritura del historico lo vigila descarga a descarga.
- Y tirar la barra entera abriria un hueco en la serie, que es un problema
  peor: las medias moviles se calcularian sobre fechas salteadas sin decirlo.

Con `high` y `low` a nulo, el ATR y cualquier cosa que use el rango salen NaN
en esa ventana. Es lo correcto: no sabemos cuanto se movio ese dia, y un numero
inventado se parece demasiado a un numero medido.

LO QUE ESTE MODULO NO HACE

No repara. Nadie sabe cual de los cuatro numeros es el equivocado, y elegir uno
—recortar el cierre contra el maximo, por ejemplo— produciria una serie que
parece coherente y no lo es. Eso es peor que un hueco, porque un hueco se ve.
"""

from __future__ import annotations

import pandas as pd

COLUMNAS_DEL_RANGO = ("open", "high", "low")


def registrar(conn, malas: pd.DataFrame, run_id: str) -> int:
    """Apunta las barras imposibles. Idempotente: el proveedor las repite.

    Se conserva la deteccion mas antigua de cada barra —`detected_at` no se
    pisa— porque saber desde cuando arrastramos una barra mala es justo lo que
    distingue "el proveedor acaba de romper algo" de "esto lleva ahi desde 2021".
    """
    if malas.empty:
        return 0
    from .timeutils import utcnow

    filas = malas[["ticker", "date", "motivo"]].drop_duplicates(
        subset=["ticker", "date"]
    ).copy()
    filas["date"] = pd.to_datetime(filas["date"]).dt.date
    filas["detected_at"] = utcnow()
    filas["run_id"] = run_id

    conn.register("_cuarentena", filas)
    try:
        insertadas = conn.execute(
            """
            INSERT INTO prices_quarantine (ticker, date, motivo, detected_at, run_id)
            SELECT c.ticker, c.date, c.motivo, c.detected_at, c.run_id
            FROM _cuarentena c
            WHERE NOT EXISTS (
                SELECT 1 FROM prices_quarantine q
                WHERE q.ticker = c.ticker AND q.date = c.date
            )
            RETURNING 1
            """
        ).fetchall()
    finally:
        conn.unregister("_cuarentena")
    return len(insertadas)


def barras(conn) -> pd.DataFrame:
    """Las barras apartadas, para aplicarlas a una serie de precios."""
    return conn.execute(
        "SELECT ticker, date FROM prices_quarantine"
    ).fetchdf()


def aplicar(precios: pd.DataFrame, cuarentena: pd.DataFrame) -> pd.DataFrame:
    """Devuelve los precios con el rango del dia a nulo en las barras apartadas.

    No modifica el original: el que llama puede seguir usandolo para las
    comprobaciones de calidad, que tienen que ver los datos como llegaron.
    """
    if precios.empty or cuarentena.empty:
        return precios

    fechas = pd.to_datetime(precios["date"])
    apartadas = set(
        zip(cuarentena["ticker"].astype(str),
            pd.to_datetime(cuarentena["date"]), strict=True)
    )
    # Un par (ticker, fecha) y no dos condiciones sueltas: filtrar por los
    # tickers afectados y por las fechas afectadas por separado borraria el
    # rango de TODOS los dias de esos tickers y de todos los tickers en esos
    # dias, que en un almacen de 600 valores son miles de barras buenas.
    marcadas = pd.Series(
        list(zip(precios["ticker"].astype(str), fechas, strict=True)),
        index=precios.index,
    ).isin(apartadas)
    if not marcadas.any():
        return precios

    salida = precios.copy()
    for columna in COLUMNAS_DEL_RANGO:
        if columna in salida.columns:
            salida.loc[marcadas, columna] = pd.NA
            salida[columna] = pd.to_numeric(salida[columna], errors="coerce")
    return salida


def resumen(conn) -> pd.DataFrame:
    """Cuantas barras apartadas tiene cada valor y desde cuando."""
    return conn.execute(
        """
        SELECT ticker, COUNT(*) AS barras, MIN(date) AS primera,
               MAX(date) AS ultima, ANY_VALUE(motivo) AS motivo
        FROM prices_quarantine
        GROUP BY ticker
        ORDER BY barras DESC, ticker
        """
    ).fetchdf()

"""Splits y dividendos: separar el retorno del precio del retorno total.

`corporate_actions` estaba en el esquema desde el principio y no la escribia
nadie. Este modulo es lo que le da sentido, y sirve para dos cosas que no se
pueden hacer sin ella.

1. SEPARAR PRICE RETURN DE TOTAL RETURN

El `adj_close` de Yahoo mezcla los dos: descuenta hacia atras cada dividendo,
asi que su variacion es el retorno TOTAL, con dividendos reinvertidos. El
`close` da el retorno del PRECIO a secas.

Los dos son correctos y responden a preguntas distintas:

- "¿Cuanto he ganado?" -> total. Los dividendos son dinero que cobraste.
- "¿Cuanto ha subido la accion?" -> precio. Es lo que se ve en el grafico.

Confundirlos no da error. Da un numero que parece razonable y no lo es: una
electrica que reparte el 6 % al ano lleva seis puntos de diferencia anual entre
las dos medidas, y en cinco anos son mas de treinta.

2. COMPROBAR QUE UN SPLIT NO SE HA COMIDO EL VALOR

Un split 4:1 divide el precio entre cuatro y multiplica las acciones por
cuatro. El valor economico no cambia. Si el proveedor aplica el split al precio
y no al historico —o al reves— aparece un salto del 75 % en la serie que
ninguna comprobacion de coherencia OHLC detecta: cada barra suelta es
perfectamente valida, y el salto esta ENTRE dos barras.

Ese salto se propaga a los retornos, a la volatilidad, al drawdown y a
cualquier senal de momentum. Un backtest sobre una serie asi encuentra una
oportunidad espectacular que nunca existio.

LO QUE ESTE MODULO NO HACE

No corrige. Si un split esta mal aplicado, la serie hay que volver a
descargarla: reconstruirla aqui significaria multiplicar por un factor
adivinado, y una serie remendada que parece coherente es peor que una rota que
se ve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

# Cuanto puede desviarse el salto observado del ratio del split sin que cuente
# como mal aplicado. Un split se aplica a un precio que ademas se mueve ese dia
# por razones de mercado, asi que la coincidencia nunca es exacta.
#
# 15 % es ancho a proposito: lo que se busca es el fallo GRANDE —el split que no
# se aplico, o que se aplico dos veces—, no discutir el movimiento del dia. Un
# split 2:1 sin aplicar deja un salto del 50 %, muy lejos de este margen.
TOLERANCIA_SPLIT = 0.15

# Diferencia relativa maxima entre el retorno total calculado a mano —precio mas
# dividendos— y el que sale del `adj_close`. Por encima, las dos fuentes se
# contradicen sobre lo mismo.
TOLERANCIA_RETORNO = 0.01


@dataclass(frozen=True)
class Hallazgo:
    ticker: str
    fecha: date
    tipo: str
    detalle: str


def retorno_precio(precios: pd.DataFrame) -> float | None:
    """Cuanto ha subido la ACCION entre la primera y la ultima sesion.

    Sobre `close`, el precio cotizado. Es lo que se ve en un grafico y lo que
    contesta a "¿cuanto ha subido?".
    """
    return _variacion(precios, "close")


def retorno_total(precios: pd.DataFrame) -> float | None:
    """Cuanto has ganado, contando los dividendos cobrados.

    Sobre `adj_close`, que ya los lleva descontados hacia atras.
    """
    return _variacion(precios, "adj_close")


def _variacion(precios: pd.DataFrame, columna: str) -> float | None:
    if precios.empty or columna not in precios.columns:
        return None
    serie = pd.to_numeric(
        precios.sort_values("date")[columna], errors="coerce"
    ).dropna()
    if len(serie) < 2 or serie.iloc[0] <= 0:
        return None
    return float(serie.iloc[-1] / serie.iloc[0] - 1.0)


def dividendos_cobrados(acciones: pd.DataFrame, ticker: str,
                        desde: date, hasta: date) -> float:
    """Suma de los dividendos por accion pagados en la ventana."""
    if acciones.empty:
        return 0.0
    fechas = pd.to_datetime(acciones["date"]).dt.date
    dentro = (
        (acciones["ticker"] == ticker)
        & (acciones["action_type"] == "dividend")
        & (fechas > desde)
        & (fechas <= hasta)
    )
    return float(pd.to_numeric(acciones.loc[dentro, "value"], errors="coerce").sum())


def comprobar_retorno(precios: pd.DataFrame, acciones: pd.DataFrame,
                      ticker: str) -> Hallazgo | None:
    """Que el retorno total del proveedor cuadre con precio + dividendos.

    Es la unica comprobacion INDEPENDIENTE que se puede hacer sobre el ajustado
    sin una segunda fuente: el `adj_close` y la lista de dividendos los calcula
    el proveedor por caminos distintos, asi que tienen que cuadrar entre si.

    Es una aproximacion y se dice: reinvertir cada dividendo al precio del dia
    da algo ligeramente distinto de sumarlos, y por eso la tolerancia es del
    1 % y no del 0,01 %. Lo que caza es el desacuerdo grande, que es el que
    significa que uno de los dos esta mal.
    """
    serie = precios[precios["ticker"] == ticker].sort_values("date")
    if len(serie) < 2:
        return None

    precio = retorno_precio(serie)
    total = retorno_total(serie)
    if precio is None or total is None:
        return None

    # Un split parte el `close` y no el `adj_close` de la misma manera; mezclar
    # las dos cosas aqui daria un falso positivo garantizado.
    if _hay_split(acciones, ticker, serie):
        return None

    inicio = pd.to_datetime(serie["date"]).dt.date.iloc[0]
    fin = pd.to_datetime(serie["date"]).dt.date.iloc[-1]
    cierre_inicial = float(pd.to_numeric(serie["close"], errors="coerce").iloc[0])
    if cierre_inicial <= 0:
        return None

    cobrado = dividendos_cobrados(acciones, ticker, inicio, fin)
    estimado = precio + cobrado / cierre_inicial

    if abs(estimado - total) <= TOLERANCIA_RETORNO:
        return None
    return Hallazgo(
        ticker, fin, "retorno_no_cuadra",
        f"El retorno total del proveedor es {total:.2%} y sumando el del precio "
        f"({precio:.2%}) mas los dividendos cobrados ({cobrado:,.4g} por accion, "
        f"{cobrado / cierre_inicial:.2%}) sale {estimado:.2%}. Los dos numeros "
        "los calcula el mismo proveedor por caminos distintos: si no cuadran, "
        "uno de los dos esta mal.",
    )


def _hay_split(acciones: pd.DataFrame, ticker: str, serie: pd.DataFrame) -> bool:
    if acciones.empty:
        return False
    fechas = pd.to_datetime(acciones["date"]).dt.date
    propias = pd.to_datetime(serie["date"]).dt.date
    return bool((
        (acciones["ticker"] == ticker)
        & (acciones["action_type"] == "split")
        & (fechas >= propias.min())
        & (fechas <= propias.max())
    ).any())


def comprobar_splits(precios: pd.DataFrame,
                     acciones: pd.DataFrame) -> list[Hallazgo]:
    """Que cada split se refleje en el precio con su ratio.

    Un split 4:1 tiene que dejar el cierre del dia en torno a la cuarta parte
    del anterior. Si el precio no se movio, el proveedor no lo aplico; si se
    movio el doble, lo aplico dos veces.

    Se mira sobre `close` y NO sobre `adj_close`: el ajustado ya reescribe todo
    el historico hacia atras con cada split, asi que ahi el salto no existe por
    construccion y la comprobacion no comprobaria nada.
    """
    fuera: list[Hallazgo] = []
    if acciones.empty or precios.empty:
        return fuera

    splits = acciones[acciones["action_type"] == "split"]
    if splits.empty:
        return fuera

    for fila in splits.itertuples():
        ratio = float(fila.value)
        if not np.isfinite(ratio) or ratio <= 0:
            continue
        serie = precios[precios["ticker"] == fila.ticker].sort_values("date")
        if serie.empty:
            continue

        cuando = pd.Timestamp(fila.date)
        fechas = pd.to_datetime(serie["date"])
        anteriores = serie[fechas < cuando]
        el_dia = serie[fechas == cuando]
        if anteriores.empty or el_dia.empty:
            # Un split anterior al historico que tenemos no se puede comprobar,
            # y decir que "pasa" seria mentir sobre algo que no se ha mirado.
            continue

        antes = float(pd.to_numeric(anteriores["close"], errors="coerce").iloc[-1])
        despues = float(pd.to_numeric(el_dia["close"], errors="coerce").iloc[0])
        if not (np.isfinite(antes) and np.isfinite(despues)) or despues <= 0:
            continue

        esperado = antes / ratio
        desvio = abs(despues - esperado) / esperado
        if desvio <= TOLERANCIA_SPLIT:
            continue

        fuera.append(Hallazgo(
            str(fila.ticker), cuando.date(), "split_mal_aplicado",
            f"Split {ratio:g}:1 el {cuando.date()}. El cierre pasa de {antes:,.4g} "
            f"a {despues:,.4g}, y con el split aplicado deberia rondar "
            f"{esperado:,.4g} ({desvio:.1%} de desvio). Un salto asi no lo "
            "detecta ninguna comprobacion de coherencia: cada barra suelta es "
            "valida y el salto esta ENTRE dos barras. Vuelve a descargar la "
            "serie entera de este valor.",
        ))
    return fuera


def guardar(conn, acciones) -> int:
    """Escribe los eventos, sin duplicar los que ya estaban.

    Acepta un DataFrame o una lista de diccionarios. Los proveedores los mandan
    como lista a proposito: viajan dentro de `df.attrs`, y un DataFrame ahi
    dentro tumba la descarga entera en el primer `merge` que pandas haga
    —compara los `attrs` con `==` y comparar DataFrames no da un booleano—.
    """
    if acciones is None:
        return 0
    if not isinstance(acciones, pd.DataFrame):
        acciones = pd.DataFrame(
            list(acciones), columns=["ticker", "date", "action_type", "value"]
        )
    if acciones.empty:
        return 0
    filas = acciones[["ticker", "date", "action_type", "value"]].copy()
    filas["date"] = pd.to_datetime(filas["date"]).dt.date
    filas = filas.drop_duplicates(subset=["ticker", "date", "action_type"])

    conn.register("_acciones", filas)
    try:
        insertadas = conn.execute(
            """
            INSERT INTO corporate_actions (ticker, date, action_type, value)
            SELECT a.ticker, a.date, a.action_type, a.value FROM _acciones a
            WHERE NOT EXISTS (
                SELECT 1 FROM corporate_actions c
                WHERE c.ticker = a.ticker AND c.date = a.date
                  AND c.action_type = a.action_type
            )
            RETURNING 1
            """
        ).fetchall()
    finally:
        conn.unregister("_acciones")
    return len(insertadas)


def leer(conn, tickers: list[str] | None = None) -> pd.DataFrame:
    if tickers:
        marcas = ", ".join("?" for _ in tickers)
        return conn.execute(
            f"SELECT ticker, date, action_type, value FROM corporate_actions "
            f"WHERE ticker IN ({marcas}) ORDER BY ticker, date",
            list(tickers),
        ).fetchdf()
    return conn.execute(
        "SELECT ticker, date, action_type, value FROM corporate_actions "
        "ORDER BY ticker, date"
    ).fetchdf()

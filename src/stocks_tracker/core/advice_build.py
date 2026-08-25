"""Del almacen a las recomendaciones del dia.

Aqui no hay ninguna regla de decision: todas viven en `advice.py`, que es puro y
se puede testear sin base de datos. Esto solo va a buscar los datos y los pone
en la forma que el motor espera.

La separacion no es ceremonia. La primera version de casi cualquier pantalla de
consejos mezcla las dos cosas, y entonces la unica manera de comprobar una
regla es montar un almacen entero: acaba sin tests y con las reglas escondidas
entre `SELECT`s.

EL ORDEN IMPORTA Y ES DELIBERADO

Primero la cartera, despues los candidatos. Lo que ya tienes puede costarte
dinero hoy; lo que no tienes puede esperar a manana. Ademas, una venta libera
una plaza de las siete, y sin resolver antes la cartera un candidato bueno
saldria VETADA por una plaza que en realidad esta a punto de quedar libre.
"""

from __future__ import annotations

import pandas as pd

from . import advice
from . import deterioration as det
from .advice import Recomendacion
from .flags import red_flags
from .textutils import as_float


def de_la_cartera(salud: pd.DataFrame, posiciones: pd.DataFrame,
                  *, pesos_sector: dict[str, float] | None = None,
                  avisos_fiscales: dict[str, str] | None = None,
                  percentiles: dict[str, float] | None = None,
                  stops: dict[str, float] | None = None) -> list[Recomendacion]:
    """Un veredicto por posicion abierta.

    `salud` es lo que devuelve `get_position_health`: los datos de hoy y los del
    dia de la compra, que es lo que necesita `deterioration.diagnosticar` para
    comparar. Sin la mitad de "entonces", el diagnostico sale GRIS y el
    veredicto SIN_OPINION, que es lo correcto: no se ha podido mirar.
    """
    if posiciones is None or posiciones.empty:
        return []

    pesos_sector = pesos_sector or {}
    avisos_fiscales = avisos_fiscales or {}
    percentiles = percentiles or {}
    stops = stops or {}
    por_ticker = (
        {str(f["ticker"]): f for _, f in salud.iterrows()}
        if salud is not None and not salud.empty else {}
    )

    fuera: list[Recomendacion] = []
    for _, pos in posiciones.iterrows():
        ticker = str(pos["ticker"])
        fila = por_ticker.get(ticker)
        diagnostico = None
        hoy: dict = {}
        if fila is not None:
            # `partir` y no la fila entera por los dos lados: con los datos de
            # hoy tambien como "entonces" no habria nada que comparar y TODO
            # saldria en verde. Es el fallo contra el que avisa el docstring de
            # `deterioration.py`, y es facilisimo cometerlo aqui.
            hoy, entonces = det.partir(fila)
            diagnostico = det.diagnosticar(
                ticker,
                fund_hoy=hoy, fund_entonces=entonces,
                ind_hoy=hoy, ind_entonces=entonces,
                comparado_con=hoy.get("opened_at"),
            )
        fuera.append(advice.sobre_una_posicion(
            ticker,
            diagnostico=diagnostico,
            banderas=red_flags(hoy) if hoy else [],
            precio=as_float(pos.get("close")),
            stop=stops.get(ticker),
            peso_pct=as_float(pos.get("peso_pct")),
            peso_sector_pct=pesos_sector.get(str(pos.get("gics_sector") or "")),
            percentil=percentiles.get(ticker),
            titulos=as_float(pos.get("qty")),
            aviso_fiscal=avisos_fiscales.get(ticker, ""),
        ))
    return fuera


def de_los_candidatos(ranking: pd.DataFrame, *, equity: float, caja: float,
                      regimen: str = "neutral", n_posiciones: int = 0,
                      pesos_actuales: dict[str, float] | None = None,
                      pesos_sector: dict[str, float] | None = None,
                      avisos_fiscales: dict[str, str] | None = None,
                      limite: int = 25) -> list[Recomendacion]:
    """Un veredicto por candidato del ranking.

    `limite` no es paginacion: es que una lista larga no se lee. Con
    `max_positions: 7`, mirar mas de veinticinco candidatos es mirar una lista
    que no cabe en la cartera ni de lejos, y una lista que no cabe se ojea por
    encima —que es como se acaba comprando el primero que suena—.
    """
    if ranking is None or ranking.empty:
        return []

    pesos_actuales = pesos_actuales or {}
    pesos_sector = pesos_sector or {}
    avisos_fiscales = avisos_fiscales or {}

    fuera: list[Recomendacion] = []
    for _, fila in ranking.head(limite).iterrows():
        ticker = str(fila["ticker"])
        precio = as_float(fila.get("close"))
        atr_pct = as_float(fila.get("atr_pct"))
        # `atr_pct` viene en porcentaje del precio; `size_by_atr` quiere el ATR
        # en unidades de precio. Confundirlos daria stops cien veces mas
        # estrechos y tamanos cien veces mayores, y todo con buena pinta.
        atr14 = (precio * atr_pct / 100.0) if (precio and atr_pct) else None

        fuera.append(advice.sobre_un_candidato(
            ticker,
            percentil=as_float(fila.get("composite_pctile")),
            cobertura=as_float(fila.get("coverage")),
            banderas=red_flags(fila),
            precio=precio, atr14=atr14,
            equity=equity, caja=caja, regimen=regimen,
            n_posiciones=n_posiciones,
            peso_actual_pct=pesos_actuales.get(ticker),
            peso_sector_pct=pesos_sector.get(str(fila.get("gics_sector") or "")),
            motivos_ranking=_motivos_del_ranking(fila),
            aviso_fiscal=avisos_fiscales.get(ticker, ""),
        ))
    return fuera


# Los factores que se nombran en los motivos, y como se llaman en castellano.
_NOMBRES = {
    "value_z": "valoracion", "growth_z": "crecimiento", "quality_z": "calidad",
    "momentum_z": "momentum", "lowvol_z": "estabilidad",
    "dividend_z": "dividendo", "technical_z": "tecnico",
}

# A partir de que z-score un factor merece mencionarse. Por debajo de 1 sigma la
# diferencia con la media del sector no se distingue del ruido, y llenar el
# motivo de factores mediocres diluye los dos que de verdad sostienen la idea.
_Z_PARA_MENCIONAR = 1.0


def _motivos_del_ranking(fila) -> list[str]:
    """Los dos factores que mas empujan, en castellano.

    Dos y no siete: un consejo con siete motivos no tiene ninguno. Si hace falta
    enumerar todo lo que sale bien para justificar una compra, lo que hay es una
    empresa del monton.
    """
    puntos = []
    for campo, nombre in _NOMBRES.items():
        z = as_float(fila.get(campo))
        if z is not None and abs(z) >= _Z_PARA_MENCIONAR:
            puntos.append((abs(z), nombre, z))
    puntos.sort(reverse=True)

    fuera = []
    for _, nombre, z in puntos[:2]:
        lado = "destaca en" if z > 0 else "flojea en"
        fuera.append(f"{lado.capitalize()} {nombre} ({z:+.1f} sigma).")
    return fuera

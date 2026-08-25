"""Página 12 — Qué haría hoy.

La página que el resto del programa existe para sostener. Todo lo demás
—ingesta, calidad, factores, deterioro— acaba aquí, en una decisión por valor.

EL ORDEN NO ES CASUAL

Primero tu cartera, después las compras nuevas. Lo que ya tienes puede costarte
dinero hoy; lo que no tienes puede esperar a mañana. Poner las compras arriba
—que es lo que apetece leer— es como se acaba con una cartera llena de aciertos
viejos sin vender.

Y el marcador va ARRIBA DEL TODO, antes que ningún consejo. Es lo único que
distingue a un asesor de un adivino, y ponerlo al final sería esconderlo justo
cuando todavía no dice nada bueno.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components import health_panel
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.app.components.theme import format_money
from stocks_tracker.core.advice import ETIQUETA, Conviccion, Veredicto
from stocks_tracker.core.advice_store import resumen_honesto

st.title("Qué haría hoy")
st.caption(
    "Lo que **tus reglas** implican hoy, no lo que va a hacer el mercado. "
    "Ningún módulo de este programa predice nada: lo que hace es aplicar tus "
    "criterios igual todos los días y enseñar el porqué."
)

COLOR = {
    Veredicto.VENDER: "🔴", Veredicto.REDUCIR: "🟠", Veredicto.COMPRAR: "🟢",
    Veredicto.AMPLIAR: "🟢", Veredicto.MANTENER: "⚪", Veredicto.VETADA: "⛔",
    Veredicto.SIN_OPINION: "❔",
}
FUERZA = {Conviccion.ALTA: "convicción alta",
          Conviccion.MEDIA: "convicción media",
          Conviccion.BAJA: "convicción baja"}


# ===========================================================================
# 1. El marcador. Primero, y sin adornos
# ===========================================================================
st.subheader("¿Acierta este asesor?")

puntuadas, resultados = da.get_advice_scoreboard()
st.info(resumen_honesto(resultados), icon=":material/scoreboard:")

if resultados:
    filas = [
        {
            "Veredicto": r.veredicto,
            "Puntuadas": r.puntuadas,
            "Batieron al índice": r.aciertos,
            # `None` y no un porcentaje: con pocos datos, un numero con pinta de
            # estadistica invita a confiar antes de tiempo.
            "Tasa": (r.tasa * 100) if r.tasa is not None else None,
            "Exceso medio (pp)": r.exceso_medio,
        }
        for r in resultados
    ]
    st.dataframe(
        pd.DataFrame(filas), hide_index=True,
        column_config={
            "Tasa": st.column_config.NumberColumn(
                format="%.0f%%",
                help="Solo aparece cuando hay bastantes datos para que "
                     "signifique algo."),
            "Exceso medio (pp)": st.column_config.NumberColumn(format="%+.1f"),
        },
    )

# La OTRA mitad de la evidencia, y va separada a proposito.
#
# El marcador de arriba mide hacia delante y tarda meses. Esto mide hacia atras
# y esta disponible hoy, pero SOLO sirve para la parte del ranking que sale de
# precios. Juntar las dos cifras en un mismo bloque las haria parecer la misma
# clase de prueba, y no lo son: una es el asesor entero midiendose en real, la
# otra es una regla suelta medida sobre el pasado.
with st.expander("¿Y ha funcionado esto en el pasado?"):
    st.markdown(da.get_advice_calibration())
    st.caption(
        "Mide **una sola cosa**: si el corte del percentil 90 separó a los que "
        "luego lo hicieron mejor que el índice. No simula comprar ni vender. "
        "Un backtest completo tiene decenas de decisiones y cada una es una "
        "oportunidad de ajustar hasta que salga bonito; esto tiene una."
    )

with st.expander("Qué mide y qué NO mide este marcador"):
    st.markdown(
        "- **Se puntúa contra el índice, no contra cero.** Comprar algo que "
        "sube un 3 % mientras el mercado sube un 8 % no es un acierto: es "
        "haber elegido peor que no elegir.\n"
        "- **Solo cuenta lo accionable.** Los `mantener` no entran: nadie tomó "
        "esa decisión ni pagó comisión por ella.\n"
        "- **Empieza vacío y tarda meses.** No hay forma honesta de rellenarlo "
        "con historia: media recomendación se apoya en fundamentales de los "
        "que no existe serie punto-en-el-tiempo, y puntuar 2019 con los "
        "balances de hoy es mirar el futuro.\n"
        "- **Mientras esté vacío, este asesor no está validado.** Es coherente "
        "y consistente, que no es lo mismo que acertado."
    )

st.divider()

# ===========================================================================
# 2. Los consejos GUARDADOS de la ultima sesion
# ===========================================================================
# La pantalla LEE lo que escribio `run_advice`; no vuelve a calcular.
#
# Si recalculara al vuelo y el marcador puntuara lo guardado, los dos podrian
# separarse —basta con que cambie un precio entre una cosa y otra— y el marcador
# estaria puntuando consejos que nadie llego a ver. Que lo que se ve y lo que se
# puntua sean lo mismo no es un detalle: es la condicion para que el marcador
# signifique algo.
guardadas = da.get_advice()

if guardadas.empty:
    st.info(
        "Todavía no hay consejos calculados. Ejecuta `stocks.ps1 consejo` "
        "—o `python -m stocks_tracker.compute.run_advice`— después del cálculo "
        "diario y aparecerán aquí.\n\n"
        "Se calculan en un paso aparte y no al abrir esta página, para que "
        "quede constancia de cada uno: sin esa constancia el marcador de arriba "
        "no podría llenarse nunca.",
        icon=":material/hourglass_empty:",
    )
    st.stop()


def _lista(campo: str, fila) -> list[str]:
    try:
        return json.loads(fila[campo] or "[]")
    except (TypeError, ValueError):
        return []


def _tarjeta(fila) -> None:
    """Una recomendación entera: veredicto, porqué, y qué la desmentiría.

    Las tres cosas juntas y siempre. Un consejo sin motivos no se puede
    discutir, y uno sin condición de error no se puede revisar dentro de seis
    meses: es lo que separa una afirmación comprobable de un horóscopo.
    """
    veredicto = Veredicto(fila["veredicto"])
    conviccion = Conviccion(fila["conviccion"])
    with st.container(border=True):
        st.markdown(
            f"{COLOR[veredicto]} **{fila['ticker']}** — {ETIQUETA[veredicto]}"
            f"  \n:gray[{FUERZA[conviccion]}]"
        )
        for motivo in _lista("motivos", fila):
            st.markdown(f"- {motivo}")

        if fila["importe_eur"] and fila["importe_eur"] > 0:
            cols = st.columns(3)
            cols[0].metric("Importe", format_money(fila["importe_eur"], "EUR"))
            cols[1].metric("Stop", f"{fila['stop']:.2f}" if fila["stop"] else "—")
            cols[2].metric("Arriesgas",
                           format_money(fila["riesgo_eur"], "EUR")
                           if fila["riesgo_eur"] else "—")
        elif fila["titulos"]:
            st.metric("Títulos a soltar", f"{fila['titulos']:.4f}")

        if fila["aviso_fiscal"]:
            st.warning(fila["aviso_fiscal"], icon=":material/gavel:")

        desmiente = _lista("desmentiria", fila)
        if desmiente:
            with st.expander("Qué haría que esto fuera un error"):
                for d in desmiente:
                    st.markdown(f"- {d}")


cartera = set(da.get_positions()["ticker"]) if not da.get_positions().empty else set()
mias = guardadas[guardadas["ticker"].isin(cartera)]
nuevas = guardadas[~guardadas["ticker"].isin(cartera)]

fecha = pd.Timestamp(guardadas["fecha"].iloc[0])
st.caption(f"Consejos de la sesión del {fecha:%d/%m/%Y}.")

st.divider()
st.subheader("Tu cartera")

# EL SILENCIO HAY QUE CONTARLO, Y AQUI MAS QUE EN NINGUN SITIO
#
# Solo se guarda lo accionable, asi que una cartera entera sobre la que el
# programa NO HA PODIDO OPINAR se veia exactamente igual que una cartera sana:
# "nada que hacer hoy". Es el mismo verde tranquilizador por falta de datos que
# `deterioration.py` existe para evitar, un piso mas arriba.
#
# El caso llega del uso real: con las fechas de compra puestas al importar el
# extracto, el diagnostico compara hoy contra hoy y no puede encontrar nada.
_mudas = []
try:
    _salud = da.get_position_health()
    if not _salud.empty:
        _mudas = [d for d in health_panel.diagnosticos(_salud) if d.espejo]
except Exception:  # noqa: BLE001 - una pantalla no se cae por un aviso
    _mudas = []

if _mudas:
    st.warning(
        f"**No he podido opinar sobre {len(_mudas)} de tus posiciones** "
        f"({', '.join(d.ticker for d in _mudas)}). La fecha de compra que "
        "tengo de ellas es la de hoy —la que se pone al importar el extracto, "
        "porque el extracto no la trae—, así que compararía los datos de hoy "
        "con los datos de hoy y no encontraría nada nunca. Ponles la fecha "
        "real en **Cartera y watchlist** y vuelven a juzgarse.",
        icon=":material/event_busy:",
    )

if mias.empty:
    st.success(
        "Nada que hacer hoy con lo que tienes. Los `mantener` no se guardan: "
        "solo aparecen aquí las posiciones que piden una acción."
        + (" Ojo: esto NO incluye las posiciones de arriba, sobre las que no "
           "he podido opinar." if _mudas else ""),
        icon=":material/check_circle:",
    )
else:
    for _, fila in mias.iterrows():
        _tarjeta(fila)

st.divider()
st.subheader("Compras nuevas")
if nuevas.empty:
    st.info(
        "Ningún candidato pasa hoy tus filtros. Es lo normal la mayoría de los "
        "días: con siete plazas y el percentil 90 como listón, no hay una "
        "compra cada mañana.",
        icon=":material/inbox:",
    )
else:
    for _, fila in nuevas.iterrows():
        _tarjeta(fila)

# ---------------------------------------------------------------------------
# El encuadre, al final y sin suavizar
# ---------------------------------------------------------------------------
st.divider()
huella = guardadas["universe_hash"].iloc[0]
if huella:
    st.caption(
        f"Calculado contra el universo con huella `{huella}`. El ranking es "
        "relativo: con otro universo, estos consejos cambian sin que las "
        "empresas hayan hecho nada."
    )
render_disclaimer()

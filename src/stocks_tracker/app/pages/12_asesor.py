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

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.core import advice, advice_build, fx
from stocks_tracker.core.advice import Conviccion, Veredicto
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
# 2. Lo que el programa NO sabe y hay que decirle
# ===========================================================================
posiciones = da.get_positions()
tipos = da.get_fx_rates()

valor_cartera = 0.0
if not posiciones.empty:
    en_euros = fx.a_base(posiciones["qty"] * posiciones["close"],
                         posiciones["currency"], tipos)
    valor_cartera = fx.total(en_euros)

# El efectivo NO se puede saber: no hay conexion con el banco ni con el broker,
# y el extracto que se importa solo trae posiciones. Inventarlo —suponer que
# siempre hay caja— produciria recomendaciones de compra que no se pueden
# ejecutar, que es la forma mas rapida de que una pantalla deje de usarse.
caja = st.number_input(
    "Efectivo disponible para invertir (EUR)",
    min_value=0.0, value=0.0, step=100.0,
    help="El programa no puede saberlo: no habla con tu banco ni con tu "
         "broker, y el extracto solo trae posiciones. Sin este dato no se "
         "puede calcular cuánto comprar, así que las compras saldrán vetadas.",
)

if valor_cartera and valor_cartera == valor_cartera:
    st.caption(
        f"Cartera valorada en **{valor_cartera:,.2f} EUR** + "
        f"{caja:,.2f} EUR de efectivo declarado."
        .replace(",", " ")
    )

equity = (valor_cartera if valor_cartera == valor_cartera else 0.0) + caja

st.divider()

# ===========================================================================
# 3. Tu cartera
# ===========================================================================
st.subheader("Tu cartera")

if posiciones.empty:
    st.info(
        "No tienes posiciones registradas. Impórtalas o añádelas en la página "
        "de Cartera y aquí aparecerá qué hacer con cada una.",
        icon=":material/inbox:",
    )
    recomendaciones_cartera: list = []
else:
    pos = posiciones.copy()
    pos["valor_eur"] = fx.a_base(pos["qty"] * pos["close"], pos["currency"], tipos)
    total = fx.total(pos["valor_eur"])
    pos["peso_pct"] = (pos["valor_eur"] / total * 100.0) if total else None
    pesos_sector = (
        pos.groupby(pos["gics_sector"].fillna(""))["peso_pct"].sum().to_dict()
    )

    recomendaciones_cartera = advice.ordenar(advice_build.de_la_cartera(
        da.get_position_health(), pos, pesos_sector=pesos_sector,
    ))


def _tarjeta(r: advice.Recomendacion) -> None:
    """Una recomendación entera: veredicto, porqué, y qué la desmentiría.

    Las tres cosas juntas y siempre. Un consejo sin motivos no se puede
    discutir, y uno sin condición de error no se puede revisar dentro de seis
    meses: es lo que separa una afirmación comprobable de un horóscopo.
    """
    cabecera = f"{COLOR[r.veredicto]} **{r.ticker}** — {r.etiqueta}"
    with st.container(border=True):
        st.markdown(f"{cabecera}  \n:gray[{FUERZA[r.conviccion]}]")

        for motivo in r.motivos:
            st.markdown(f"- {motivo}")

        if r.importe_eur:
            cols = st.columns(3)
            cols[0].metric("Comprar", f"{r.importe_eur:,.0f} EUR".replace(",", " "))
            cols[1].metric("Stop", f"{r.stop:.2f}")
            cols[2].metric("Arriesgas", f"{r.riesgo_eur:,.2f} EUR".replace(",", " "))
        if r.titulos_a_soltar:
            st.metric("Soltar", f"{r.titulos_a_soltar:.4f} títulos")

        if r.aviso_fiscal:
            st.warning(r.aviso_fiscal, icon=":material/gavel:")

        if r.desmentiria:
            with st.expander("Qué haría que esto fuera un error"):
                for d in r.desmentiria:
                    st.markdown(f"- {d}")


for r in recomendaciones_cartera:
    _tarjeta(r)

st.divider()

# ===========================================================================
# 4. Compras nuevas
# ===========================================================================
st.subheader("Compras nuevas")

ranking = da.get_candidates(limit=60)
if ranking.empty:
    st.info(
        "No hay ranking calculado todavía. Ejecuta el cálculo y vuelve.",
        icon=":material/hourglass_empty:",
    )
else:
    pesos_actuales = {}
    pesos_sector_cartera: dict = {}
    if not posiciones.empty:
        pesos_actuales = dict(zip(pos["ticker"], pos["peso_pct"], strict=False))
        pesos_sector_cartera = pesos_sector

    nuevas = advice.ordenar(advice_build.de_los_candidatos(
        ranking, equity=equity, caja=caja,
        n_posiciones=len(posiciones),
        pesos_actuales=pesos_actuales,
        pesos_sector=pesos_sector_cartera,
    ))
    # Se ensenan tambien las VETADAS y las SIN_OPINION, y no solo las compras.
    # Una lista que solo muestra lo que el motor sabe hacer parece mas lista de
    # lo que es, y esconde justo la informacion util: por que NO se compra algo
    # que estaba arriba del ranking.
    for r in nuevas[:15]:
        _tarjeta(r)

# ---------------------------------------------------------------------------
# El encuadre, al final y sin suavizar
# ---------------------------------------------------------------------------
st.divider()
universo = da.scoring_universe(None)
if universo:
    st.caption(
        f"Calculado contra **{universo['n_tickers']} valores** · huella "
        f"`{universo['huella']}`. El ranking es relativo: con otro universo, "
        "estos consejos cambian sin que las empresas hayan hecho nada."
    )
render_disclaimer()

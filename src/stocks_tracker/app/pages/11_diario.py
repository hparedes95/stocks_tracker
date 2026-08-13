"""Pagina 11 — Diario de decisiones.

El orden de esta pantalla es su unica caracteristica importante: al revisar una
decision se lee **primero lo que escribiste** y solo despues, y con un clic de
por medio, lo que paso. Al reves no sirve de nada: con el resultado delante, la
tesis se relee a traves de el y siempre parece que ya se sabia.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app import journal_store as store
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.core.journal import (
    DESCRIPCION_VEREDICTO,
    ETIQUETA_ACCION,
    Accion,
    Balance,
    Veredicto,
    calibracion_por_conviccion,
    pendientes,
)

st.title("Diario de decisiones")
st.caption(
    "Por que compraste, **escrito antes de saber si salio bien**. Cuando algo "
    "sale bien, el recuerdo del motivo se reescribe solo para que encaje y se "
    "aprende una leccion que nunca ocurrio. Lo unico que lo frena es dejarlo "
    "por escrito antes y releerlo despues sin retocarlo."
)

datos = store.leer()
entradas = store.a_entradas(datos)
hoy = store.hoy()
precios = store.precios_para(entradas)

tab_nueva, tab_revisar, tab_historial, tab_balance = st.tabs(
    ["Anotar una decision", "Revisar", "Historial", "Que dice el diario"]
)

# ===========================================================================
# Anotar
# ===========================================================================
with tab_nueva:
    st.caption(
        "Anota tambien lo que decides **no** hacer. No comprar y esperar son la "
        "mitad de las decisiones que tomas y no dejan rastro en ningun sitio: "
        "sin ellas, el diario solo puede guardar aciertos."
    )

    with st.form("nueva_decision", clear_on_submit=True):
        arriba = st.columns([2, 2, 1])
        with arriba[0]:
            ticker = st.selectbox("Valor", options=da.all_tickers(), index=None,
                                  placeholder="Busca un ticker")
        with arriba[1]:
            accion = st.selectbox(
                "Que has decidido", options=list(Accion),
                format_func=lambda a: ETIQUETA_ACCION[a],
            )
        with arriba[2]:
            conviccion = st.slider("Conviccion", 1, 5, 3,
                                   help="Del 1 (dudo) al 5 (lo tengo claro). "
                                        "Sirve para comprobar despues si tus "
                                        "decisiones mas convencidas salen mejor.")

        tesis = st.text_area(
            "Por que", height=110,
            placeholder="Con tus palabras. Lo que de verdad te ha hecho "
                        "decidirlo, no lo que suena bien.",
        )
        salida = st.text_area(
            "Que tendria que pasar para que cambiara de idea", height=90,
            placeholder="Lo mas util del diario: si los ingresos vuelven a "
                        "caer, si pierde la MM200, si el margen baja del 15 %...",
            help="Es el campo que la memoria reescribe con mas facilidad. "
                 "Escrito antes, no hay forma de discutirlo despues.",
        )
        horizonte = st.number_input(
            "En cuantos dias quieres revisar esto", min_value=1, max_value=3650,
            value=90, step=30,
            help="El plazo lo pones tu: revisar a los 90 dias una tesis a tres "
                 "anos solo produce ruido.",
        )

        enviado = st.form_submit_button("Anotar", type="primary")
        if enviado:
            if not ticker or not tesis.strip():
                st.error("Hace falta el valor y el motivo. Una decision sin "
                         "motivo escrito no se puede revisar despues.")
            else:
                store.anotar(
                    ticker=ticker, accion=accion, tesis=tesis.strip(),
                    que_me_haria_salir=salida.strip(),
                    horizonte_dias=int(horizonte), conviccion=int(conviccion),
                    foto=store.foto_de(ticker),
                )
                st.success(
                    f"Anotado. Se guarda tambien el precio, el percentil y el "
                    f"RSI de hoy: cuando revises {ticker} veras lo que de "
                    "verdad sabias, no lo que recuerdes haber sabido."
                )

# ===========================================================================
# Revisar
# ===========================================================================
with tab_revisar:
    toca = pendientes(entradas, hoy)
    if not toca:
        st.info(
            "Nada pendiente de revisar. Aparecen aqui solas cuando se cumple "
            "el plazo que les pusiste.",
            icon=":material/check_circle:",
        )
    else:
        st.caption(
            f"**{len(toca)}** decision(es) han cumplido su plazo. Lee primero "
            "lo que escribiste; el resultado esta escondido a proposito, "
            "porque con el delante la tesis siempre parece que ya lo sabia."
        )

    for e in toca:
        with st.expander(
            f"{e.ticker} · {ETIQUETA_ACCION[e.accion]} el "
            f"{e.fecha:%d/%m/%Y} ({e.dias_desde(hoy)} dias)",
            expanded=len(toca) == 1,
        ):
            st.markdown("**Lo que escribiste entonces**")
            st.info(e.tesis or "_(sin motivo escrito)_")
            if e.que_me_haria_salir:
                st.markdown("**Lo que dijiste que te haria cambiar de idea**")
                st.warning(e.que_me_haria_salir)
            st.caption(f"Conviccion: {e.conviccion}/5 · plazo: "
                       f"{e.horizonte_dias} dias")

            # El resultado, detras de un clic. Es la unica linea de esta pagina
            # que de verdad importa: leerlo antes contamina la relectura.
            with st.expander("Ver que paso", expanded=False):
                precio = precios.get(e.ticker)
                resultado = e.resultado(precio)
                relativo = e.resultado_relativo(precio,
                                                precios.get(store.MERCADO))
                if resultado is None:
                    st.caption("Sin precio para calcular el resultado.")
                else:
                    m = st.columns(2)
                    m[0].metric("Resultado de la decision",
                                f"{resultado * 100:+.1f} %")
                    if relativo is not None:
                        m[1].metric("Descontando el mercado",
                                    f"{relativo * 100:+.1f} pp")
                    if e.accion in (Accion.NO_COMPRAR, Accion.ESPERAR,
                                    Accion.VENDER):
                        st.caption(
                            "El signo esta invertido a proposito: si el valor "
                            "se hundio despues de que decidieras no tenerlo, "
                            "la decision te ahorro ese dinero."
                        )

            st.markdown("**Y ahora lo importante: ¿por que salio asi?**")
            elegido = st.radio(
                "Veredicto", options=list(Veredicto), key=f"ver_{e.id}",
                format_func=lambda v: DESCRIPCION_VEREDICTO[v][0],
                index=None,
            )
            if elegido is not None:
                st.caption(DESCRIPCION_VEREDICTO[elegido][1])
            nota = st.text_area(
                "Que te llevas de aqui", key=f"nota_{e.id}", height=80,
                placeholder="Lo que harias distinto, o lo que confirmarias.",
            )
            if st.button("Guardar la revision", key=f"btn_{e.id}",
                         type="primary", disabled=elegido is None):
                store.revisar(e.id, elegido, nota.strip())
                st.rerun()

# ===========================================================================
# Historial
# ===========================================================================
with tab_historial:
    if not entradas:
        st.caption("Todavia no hay ninguna decision anotada.")
    else:
        tabla = pd.DataFrame([
            {
                "Fecha": e.fecha,
                "Valor": e.ticker,
                "Decision": ETIQUETA_ACCION[e.accion],
                "Conviccion": e.conviccion,
                "Por que": e.tesis,
                "Revisar el": e.vence_el(),
                "Veredicto": (DESCRIPCION_VEREDICTO[e.veredicto][0]
                              if e.veredicto else "— pendiente —"),
            }
            for e in entradas
        ])
        st.dataframe(
            tabla, hide_index=True, height=min(500, 42 + 35 * len(tabla)),
            column_config={
                "Fecha": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Revisar el": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Por que": st.column_config.TextColumn(width="large"),
            },
        )

# ===========================================================================
# Balance
# ===========================================================================
with tab_balance:
    revisadas = [e for e in entradas if e.revisada]
    balance = Balance(revisadas)

    if balance.total < 5:
        st.info(
            f"Hay {balance.total} decision(es) revisada(s). Con tan pocas, "
            "cualquier porcentaje de aciertos es ruido. Esta pantalla empieza "
            "a decir algo a partir de una decena, y no hay atajo.",
            icon=":material/hourglass_empty:",
        )

    if revisadas:
        reparto = balance.reparto
        cols = st.columns(4)
        for col, v in zip(cols, Veredicto, strict=False):
            col.metric(DESCRIPCION_VEREDICTO[v][0], reparto[v])

        if balance.por_suerte is not None:
            pct = balance.por_suerte * 100
            texto = (
                f"De las {balance.buenos_resultados} decisiones que salieron "
                f"bien, **{pct:.0f} %** fue por algo que no habias escrito."
            )
            if pct >= 50:
                st.warning(
                    texto + " Es el numero mas incomodo del diario: lo que "
                    "esta funcionando no es tu metodo. Repetirlo esperando el "
                    "mismo resultado es apostar a que la suerte se mantenga.",
                    icon=":material/casino:",
                )
            else:
                st.success(texto, icon=":material/psychology:")

        st.caption(
            f"Buen proceso en {balance.buen_proceso} de {balance.total}: "
            "cuenta los aciertos por el motivo previsto **y** los fallos que no "
            "podias ver venir. Es la cifra que conviene subir; la de resultados "
            "depende ademas del mercado."
        )

        cal = calibracion_por_conviccion(revisadas)
        if len(cal) >= 2:
            st.markdown("**¿Sirve de algo tu conviccion?**")
            st.dataframe(
                pd.DataFrame([
                    {"Conviccion": nivel, "Decisiones": d["n"],
                     "Salieron bien": d["acierta"] * 100,
                     "Buen proceso": d["proceso"] * 100}
                    for nivel, d in sorted(cal.items())
                ]),
                hide_index=True,
                column_config={
                    "Salieron bien": st.column_config.NumberColumn(format="%.0f%%"),
                    "Buen proceso": st.column_config.NumberColumn(format="%.0f%%"),
                },
            )
            st.caption(
                "Si las de conviccion 5 no salen mejor que las de conviccion 2, "
                "tu conviccion no esta midiendo nada — y es justo la que te hace "
                "apostar mas fuerte."
            )

st.divider()
render_disclaimer()

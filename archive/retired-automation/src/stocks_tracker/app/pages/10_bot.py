"""Página 10 — El bot: que tiene, que hizo y que espera tu visto bueno.

Esta página es un REGISTRO, no una lista de recomendaciones. Muestra lo que el
bot ya hizo con su propio dinero asignado y lo que quedo retenido por el freno
de mano. Nada de lo que aparece aquí es una sugerencia para tu cartera.

Existe porque con el freno puesto hace falta: una orden retenida que no sale
en ninguna pantalla es una orden pérdida.
"""

from __future__ import annotations

import streamlit as st

from stocks_tracker.app import bot_view
from stocks_tracker.app.components.common import render_disclaimer

st.title("El bot")

st.info(
    "Esto es el **registro** del bot, no una lista de recomendaciones. Son "
    "operaciones que ya ha hecho con el capital que tiene asignado, y órdenes "
    "que esperan tu confirmación. Nada de lo que aparece aquí es una "
    "sugerencia para tu cartera.",
    icon=":material/smart_toy:",
)

# ---------------------------------------------------------------------------
# El veredicto de la puerta
# ---------------------------------------------------------------------------
# Estaba en la pagina de validacion de senales, que es analisis y se usa a
# diario. Aqui encaja mejor: es la respuesta a "¿puede este bot operar?", no a
# "¿sirve esta senal?". Y se ejecuta solo, los domingos.
from stocks_tracker.trading import gate as _gate  # noqa: E402

_report = _gate.latest_report()
st.subheader("¿Esta validado?")

if _report is None:
    st.info(
        "La estrategia no se ha validado todavía. Se examina sola los domingos, "
        "después de la actualización nocturna. Hasta entonces el bot no opera "
        "con dinero, aunque tenga credenciales.",
        icon=":material/schedule:",
    )
elif _report["blockers"]:
    st.error(
        "**No se puede certificar.** El resultado del backtest no sería "
        "interpretable:\n\n"
        + "\n\n".join(f"- {b}" for b in _report["blockers"]),
        icon=":material/block:",
    )
elif _report["passed"]:
    st.success(
        "**Validada.** Esto NO dice que vaya a ganar dinero: dice que no ha "
        "fallado ninguna comprobación que sepamos hacer. Es condición "
        "necesaria, nunca suficiente.",
        icon=":material/verified:",
    )
else:
    _fallan = [c["name"] for c in _report["checks"] if not c["passed"]]
    st.warning(
        f"**No validada.** Falla: {', '.join(_fallan)}. El bot no opera con "
        "dinero: la estrategia se ajusta o se descarta. Es el sistema "
        "funcionando, no una averia.",
        icon=":material/gpp_maybe:",
    )

if _report and _report["checks"]:
    with st.expander("Ver las comprobaciones"):
        import pandas as _pd

        st.dataframe(
            _pd.DataFrame([
                {"": "OK" if c["passed"] else "FALLA", "Comprobación": c["name"],
                 "Observado": c["observed"], "Umbral": c["required"]}
                for c in _report["checks"]
            ]),
            hide_index=True, width='stretch',
        )

st.divider()

carteras = bot_view.modes()
if not carteras:
    st.warning(
        "El bot no ha ejecutado ningún ciclo todavía. Cuando lo haga, aquí "
        "apareceran sus posiciones, sus órdenes y el motivo de cada decisión.",
        icon=":material/hourglass_empty:",
    )
    render_disclaimer()
    st.stop()

# ---------------------------------------------------------------------------
# Lo que espera confirmacion, arriba del todo
# ---------------------------------------------------------------------------
# Va lo primero porque es lo unico de esta pagina que requiere que hagas algo,
# y porque caduca: enterrarlo debajo de las tablas seria la forma de que
# caducase sin que nadie lo viera.
pendientes = bot_view.pending()
if not pendientes.empty:
    st.subheader(f"Esperando tu confirmación ({len(pendientes)})")
    st.warning(
        "El bot opera solo, pero estas órdenes han cruzado un freno y no "
        "saldran hasta que las apruebes. **Caducan**: si nadie decide, se "
        "descartan y el bot volvera a proponerlas con el precio de entonces.",
        icon=":material/pan_tool:",
    )
    st.dataframe(
        pendientes.rename(columns={
            "created_at": "propuesta", "expires_at": "caduca",
            "ticker": "valor", "side": "lado",
            "notional_approved": "importe", "qty_approved": "cantidad",
            "ref_price": "precio ref.", "decision_note": "por que espera",
        }).drop(columns=["intent_id"]),
        hide_index=True, width='stretch',
    )
    st.caption(
        "Para decidir, en la ventana del programa:  "
        "`stocks.ps1 pendientes`  —  aprobar no se salta el riesgo: la orden "
        "ya paso por el mandato, y el precio se comprueba otra vez antes de "
        "enviarla."
    )
    st.divider()

# ---------------------------------------------------------------------------
cartera = st.selectbox(
    "Cartera", carteras,
    help="Cada mercado lleva su contabilidad aparte: nunca un bote comun.",
)

estado = bot_view.kill_switch(cartera)
run = bot_view.last_run(cartera)

col1, col2, col3 = st.columns(3)
with col1:
    # "Sin arrancar" habiendo un ciclo registrado seria falso: lo que falta no
    # es el bot, es la fila del kill switch, que solo se crea en los modos que
    # gestionan estado.
    bruto = (estado or {}).get("state")
    if bruto == "RUNNING":
        st.metric("Estado", "En marcha")
    elif bruto:
        st.metric("Estado", str(bruto))
    elif run:
        st.metric("Estado", "Sin registro")
    else:
        st.metric("Estado", "Sin arrancar")
with col2:
    st.metric("Último ciclo",
              str((run or {}).get("started_at") or "—")[:16])
with col3:
    equity = (run or {}).get("equity_end")
    st.metric("Equity", f"{equity:.2f}" if equity else "—")

if run and not estado:
    st.caption(
        "El kill switch no tiene registro para esta cartera. Solo se crea en "
        "los modos que gestionan estado, así que un ciclo de simulación "
        "antiguo puede aparecer sin el."
    )

if estado and estado.get("state") not in (None, "RUNNING"):
    st.error(
        f"**El bot esta parado.** Regla: {estado.get('halt_rule') or '—'}. "
        f"{estado.get('halt_detail') or ''}\n\n"
        "El rearme es **manual siempre**: un kill switch que se rearma solo no "
        "es un kill switch, es una pausa.",
        icon=":material/block:",
    )

# ---------------------------------------------------------------------------
tab_pos, tab_ord, tab_dec = st.tabs(
    ["Posiciones abiertas", "Órdenes", "Por qué hizo cada cosa"]
)

with tab_pos:
    posiciones = bot_view.positions(cartera)
    if posiciones.empty:
        st.caption("Sin posiciones abiertas.")
    else:
        st.dataframe(
            posiciones.rename(columns={
                "ticker": "valor", "qty": "cantidad",
                "avg_entry_price": "entrada media", "stop_price": "stop",
                "opened_at": "abierta el",
                "highest_close_since_entry": "máximo desde entrada",
            }),
            hide_index=True, width='stretch',
        )
        st.caption(
            "El stop es **sintético**: lo vigila este programa, no el "
            "exchange. Con el ordenador apagado no se dispara."
        )

with tab_ord:
    ordenes = bot_view.orders(cartera)
    if ordenes.empty:
        st.caption("Todavía no ha enviado ninguna orden.")
    else:
        st.dataframe(
            ordenes.rename(columns={
                "submitted_at": "enviada", "ticker": "valor", "side": "lado",
                "qty": "cantidad", "notional": "importe", "status": "estado",
            }),
            hide_index=True, width='stretch',
        )

with tab_dec:
    st.caption(
        "Incluye lo que NO hizo y por que. Es la respuesta a \"por que no "
        "compro X el martes\", que sin esto no la tiene nadie."
    )
    decisiones = bot_view.recent_decisions(cartera)
    if decisiones.empty:
        st.caption("Sin decisiones registradas.")
    else:
        st.dataframe(
            decisiones.rename(columns={
                "logged_at": "cuando", "ticker": "valor",
                "decision": "decisión", "reason_code": "código",
                "reason_text": "motivo",
            }),
            hide_index=True, width='stretch',
        )

render_disclaimer()

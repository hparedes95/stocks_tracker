"""Pagina 7 — Validacion de senales.

Esta pagina **no predice** nada. Sirve para descartar senales que se comportan
igual que el azar.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.app.components.theme import (
    STATUS,
    apply_layout,
    palette,
    series_color,
)
from stocks_tracker.backtest import engine as eng
from stocks_tracker.backtest import metrics as mx
from stocks_tracker.backtest import run_backtest as runner
from stocks_tracker.core.config import get_explanations

st.title("Validacion de senales")

st.error(
    "**Esta seccion no predice nada.** Sirve para **descartar** senales que se "
    "comportan igual que el azar. Una senal solo permanece en el dashboard si, "
    "despues de costes, bate a la referencia con muestra suficiente. Que haya "
    "funcionado no garantiza que funcione.",
    icon=":material/warning:",
)


labels = get_explanations().get("signal_labels", {})
EVIDENCE_LABEL = {
    eng.VALIDATED: "Validada", eng.WEAK: "Debil",
    eng.NOT_VALIDATED: "No validada", eng.NO_DATA: "Sin datos suficientes",
}
EVIDENCE_COLOR = {
    eng.VALIDATED: STATUS["good"], eng.WEAK: STATUS["warning"],
    eng.NOT_VALIDATED: STATUS["critical"], eng.NO_DATA: palette()["muted"],
}

if not da.validation_available():
    st.warning(
        "Todavia no se ha ejecutado ninguna validacion.\n\n"
        "Ejecuta `make validate` (o "
        "`python -m stocks_tracker.backtest.run_backtest --tag-signals`) "
        "y vuelve a esta pagina.",
        icon=":material/science:",
    )
    render_disclaimer()
    st.stop()

# ---------------------------------------------------------------------------
# Advertencias metodologicas: van arriba a proposito
# ---------------------------------------------------------------------------
with st.expander("Que sesgos tienen estos resultados", expanded=False):
    st.markdown(
        """
**Sesgo de supervivencia.** El universo son los constituyentes de **hoy**. Las
empresas que quebraron o salieron del indice no aparecen, asi que los resultados
estan sesgados al alza. Trata cualquier cifra como una **cota superior
optimista**. La composicion se registra a diario, de modo que el sesgo ira
desapareciendo hacia adelante.

**Solo senales tecnicas.** Los fundamentales que guardamos son una foto actual,
no una serie historica: no sabemos que PER tenia una empresa hace tres anos.
Validar factores fundamentales con los datos de hoy seria hacer trampa, asi que
aqui solo se validan senales tecnicas.

**Entrada retardada.** Una senal detectada al cierre no se puede comprar a ese
cierre. Todos los retornos se miden entrando al cierre del dia **siguiente**.

**Costes incluidos.** Se descuentan comision y deslizamiento en la ida y en la
vuelta. Sin costes, cualquier estrategia de alta rotacion parece rentable.

**Referencia obligatoria.** El exceso se mide contra el **universo
equiponderado**, no contra un indice. Comparar contra un indice mezclaria el
aporte de la senal con la diferencia estructural entre estas acciones y ese
indice — un error con firma reconocible: senales opuestas saliendo ambas
ganadoras.
        """
    )

# ---------------------------------------------------------------------------
# Controles
# ---------------------------------------------------------------------------
control_left, control_right = st.columns(2)
with control_left:
    scope = st.selectbox(
        "Ambito",
        options=[eng.SCOPE_EQUITY_US, eng.SCOPE_EQUITY_EU, eng.SCOPE_CRYPTO],
        format_func=lambda s: {
            eng.SCOPE_EQUITY_US: "Acciones EE.UU.",
            eng.SCOPE_EQUITY_EU: "Acciones Europa",
            eng.SCOPE_CRYPTO: "Cripto",
        }[s],
        help=(
            "Una senal validada en un ambito NO esta validada en otro: los "
            "regimenes y la microestructura no se parecen."
        ),
    )
with control_right:
    horizon = st.selectbox(
        "Horizonte (sesiones)", options=list(eng.DEFAULT_HORIZONS), index=2,
        help="Cuantas sesiones se mantiene la posicion tras la senal.",
    )

evidence = da.get_signal_evidence(scope)
if evidence.empty:
    st.info(f"Sin resultados para el ambito seleccionado. Ejecuta `make validate --scope {scope}`.")
    render_disclaimer()
    st.stop()

subset = evidence[evidence["horizon_days"] == horizon].copy()
subset = subset.sort_values("avg_excess_ret", ascending=False)

# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------
counts = subset["evidence"].value_counts()
summary = st.columns(4)
for i, key in enumerate([eng.VALIDATED, eng.WEAK, eng.NOT_VALIDATED, eng.NO_DATA]):
    summary[i].metric(EVIDENCE_LABEL[key], int(counts.get(key, 0)))

if int(counts.get(eng.VALIDATED, 0)) == 0:
    st.info(
        "Ninguna senal supera la validacion en este ambito y horizonte. **Es un "
        "resultado, no un fallo**: significa que, con los datos disponibles, "
        "ninguna se distingue del azar despues de costes. Operar cualquiera de "
        "ellas seria apostar.",
        icon=":material/info:",
    )

# ---------------------------------------------------------------------------
# Tabla de resultados
# ---------------------------------------------------------------------------
st.subheader("Resultados por senal")

table = pd.DataFrame(
    {
        "Senal": subset["signal_id"].map(lambda s: labels.get(s, s)),
        "Eventos": subset["n_obs"],
        "Acierto": subset["hit_rate"] * 100,
        "Exceso medio": subset["avg_excess_ret"] * 100,
        "Evidencia": subset["evidence"].map(EVIDENCE_LABEL),
    }
)
st.dataframe(
    table, hide_index=True, height=min(430, 42 + 35 * len(table)),
    column_config={
        "Eventos": st.column_config.NumberColumn(
            format="%d", help="Por debajo de 100 cualquier conclusion es anecdota."
        ),
        "Acierto": st.column_config.ProgressColumn(
            min_value=0.0, max_value=100.0, format="%.0f%%",
            help="Porcentaje de eventos con retorno positivo. Por si solo no dice nada: "
                 "hay que compararlo con el exceso.",
        ),
        "Exceso medio": st.column_config.NumberColumn(
            format="%+.2f%%", help="Frente al universo equiponderado, tras costes."
        ),
    },
)
st.caption(
    "El **acierto** no basta: en un mercado alcista casi cualquier senal acierta "
    "mas de la mitad de las veces. Lo que importa es el **exceso** sobre lo que "
    "habria dado comprar cualquier valor del universo ese mismo dia."
)

# ---------------------------------------------------------------------------
# Detalle de una senal
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Detalle de una senal")

options = subset["signal_id"].tolist()
chosen = st.selectbox("Senal", options=options, format_func=lambda s: labels.get(s, s))
row = subset[subset["signal_id"] == chosen].iloc[0]

badge_col, metrics_col = st.columns([1, 3])
with badge_col:
    color = EVIDENCE_COLOR.get(row["evidence"], palette()["muted"])
    st.markdown(
        f"<div style='padding:12px;border-radius:8px;border:2px solid {color}'>"
        f"<div style='font-size:0.8em;color:{palette()['muted']}'>Evidencia</div>"
        f"<div style='font-size:1.3em;color:{color};font-weight:600'>"
        f"{EVIDENCE_LABEL.get(row['evidence'], row['evidence'])}</div></div>",
        unsafe_allow_html=True,
    )

with metrics_col:
    cols = st.columns(4)
    cols[0].metric("Eventos", int(row["n_obs"]))
    cols[1].metric(
        "Acierto",
        f"{row['hit_rate']:.0%}" if pd.notna(row["hit_rate"]) else "—",
    )
    cols[2].metric(
        "Exceso medio",
        f"{row['avg_excess_ret']:+.2%}" if pd.notna(row["avg_excess_ret"]) else "—",
    )
    cols[3].metric("Coste asumido", f"{row['costs_bps_assumed']:.0f} pb")

if int(row["n_obs"]) < mx.MIN_OBSERVATIONS:
    st.warning(
        f"Solo {int(row['n_obs'])} eventos. Por debajo de "
        f"{mx.MIN_OBSERVATIONS} la muestra es insuficiente para concluir nada.",
        icon=":material/warning:",
    )

period = ""
if pd.notna(row.get("oos_from")) and pd.notna(row.get("oos_to")):
    period = f"Periodo analizado: {row['oos_from']} a {row['oos_to']}."
st.caption(period)

# ---------------------------------------------------------------------------
# Distribucion de retornos: senal frente al resto
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Distribucion de retornos posteriores")


@st.cache_data(ttl=900, show_spinner="Calculando el estudio de eventos...")
def _event_detail(signal_id: str, scope_key: str, horizon_days: int) -> pd.DataFrame:
    """Recalcula el detalle evento a evento para el histograma.

    No se guarda en el almacen: es mucho volumen para algo que solo se mira
    cuando alguien abre esta pagina.
    """
    prices, signals = runner.load_data(scope_key)
    if prices.empty or signals.empty:
        return pd.DataFrame()
    fwd = eng.forward_returns(prices, (horizon_days,))
    bench = eng.universe_forward_returns(fwd, (horizon_days,))
    _, detail = eng.event_study(
        signals[signals["signal_id"] == signal_id], fwd, horizon_days,
        bench, runner.DEFAULT_COST_BPS,
    )
    return detail


detail = _event_detail(chosen, scope, int(horizon))

if detail.empty:
    st.caption("Sin detalle disponible para esta senal.")
else:
    p = palette()
    fig = go.Figure()
    fig.add_trace(
        go.Histogram(
            x=detail["retorno"] * 100, nbinsx=60, name="Tras la senal",
            marker=dict(color=series_color(0), line=dict(width=0)), opacity=0.75,
            hovertemplate="Retorno %{x:.1f}%: %{y} eventos<extra></extra>",
        )
    )
    fig.add_trace(
        go.Histogram(
            x=detail["referencia"] * 100, nbinsx=60, name="Universo equiponderado",
            marker=dict(color=series_color(1), line=dict(width=0)), opacity=0.55,
            hovertemplate="Retorno %{x:.1f}%: %{y} eventos<extra></extra>",
        )
    )
    fig.add_vline(x=0, line=dict(color=p["axis"], width=1))
    fig.add_vline(
        x=float(detail["retorno"].mean() * 100),
        line=dict(color=STATUS["good"], width=2, dash="dash"),
        annotation_text="media tras la senal", annotation_position="top",
        annotation_font=dict(size=10, color=p["text_secondary"]),
    )
    fig = apply_layout(fig, height=320, showlegend=True, barmode="overlay")
    fig.update_xaxes(ticksuffix="%")
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption(
        "Si las dos distribuciones se solapan casi por completo, la senal no "
        "aporta informacion: lo que ocurre despues es lo mismo que habria "
        "ocurrido sin ella."
    )

    # -----------------------------------------------------------------------
    # Estabilidad entre ventanas
    # -----------------------------------------------------------------------
    st.subheader("Estabilidad a lo largo del tiempo")
    folds = eng.walk_forward(detail, n_folds=3)
    if not folds:
        st.caption("Muestra insuficiente para dividir en ventanas.")
    else:
        fold_table = pd.DataFrame(
            [
                {
                    "Ventana": f.label,
                    "Desde": f.start.date(),
                    "Hasta": f.end.date(),
                    "Eventos": f.n_obs,
                    "Exceso medio": f.avg_excess * 100,
                    "Acierto": f.hit_rate * 100,
                }
                for f in folds
            ]
        )
        st.dataframe(
            fold_table, hide_index=True,
            column_config={
                "Exceso medio": st.column_config.NumberColumn(format="%+.2f%%"),
                "Acierto": st.column_config.ProgressColumn(
                    min_value=0.0, max_value=100.0, format="%.0f%%"
                ),
            },
        )
        positive = sum(1 for f in folds if f.avg_excess > 0)
        st.caption(
            f"Positiva en **{positive} de {len(folds)}** ventanas. Una senal que "
            "solo funciona en un tramo del historico no es una senal: es una "
            "fotografia de ese tramo."
        )

# ---------------------------------------------------------------------------
# Comparativa de horizontes
# ---------------------------------------------------------------------------
st.divider()
st.subheader("La misma senal a distintos horizontes")

across = evidence[evidence["signal_id"] == chosen].sort_values("horizon_days")
if len(across) > 1:
    p = palette()
    values = across["avg_excess_ret"] * 100
    fig = go.Figure(
        go.Bar(
            x=across["horizon_days"].astype(str) + " ses.",
            y=values,
            marker=dict(
                color=[STATUS["good"] if v >= 0 else STATUS["critical"] for v in values],
                line=dict(width=0),
            ),
            text=[f"{v:+.2f}%" for v in values], textposition="outside",
            textfont=dict(size=11, color=p["text_secondary"]),
            hovertemplate="%{x}: %{y:+.2f}%<extra></extra>",
        )
    )
    fig = apply_layout(fig, height=260)
    fig.update_yaxes(ticksuffix="%", zeroline=True, zerolinecolor=p["axis"])
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption(
        "Una senal que solo funciona en un horizonte muy concreto es "
        "sospechosa: suele ser casualidad de la muestra mas que un efecto real."
    )

st.divider()
render_disclaimer()

"""Página 7 — Validación de señales.

Esta página **no predice** nada. Sirve para descartar señales que se comportan
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

st.title("Validación de señales")

st.error(
    "**Esta sección no predice nada.** Sirve para **descartar** señales que se "
    "comportan igual que el azar. Una señal solo permanece en el dashboard si, "
    "después de costes, bate a la referencia con muestra suficiente. Que haya "
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
        "Todavía no se ha ejecutado ninguna validación.\n\n"
        "Ejecuta `make validate` (o "
        "`python -m stocks_tracker.backtest.run_backtest --tag-signals`) "
        "y vuelve a esta página.",
        icon=":material/science:",
    )
    render_disclaimer()
    st.stop()

# ---------------------------------------------------------------------------
# Advertencias metodologicas: van arriba a proposito
# ---------------------------------------------------------------------------
with st.expander("Qué sesgos tienen estos resultados", expanded=False):
    st.markdown(
        """
**Sesgo de supervivencia.** El universo son los constituyentes de **hoy**. Las
empresas que quebraron o salieron del índice no aparecen, así que los resultados
están sesgados al alza. Trata cualquier cifra como una **cota superior
optimista**. La composición se registra a diario, de modo que el sesgo ira
desapareciendo hacia adelante.

**Solo señales técnicas.** Los fundamentales que guardamos son una foto actual,
no una serie histórica: no sabemos que PER tenía una empresa hace tres años.
Validar factores fundamentales con los datos de hoy sería hacer trampa, así que
aquí solo se validan señales técnicas.

**Entrada retardada.** Una señal detectada al cierre no se puede comprar a ese
cierre. Todos los retornos se miden entrando al cierre del día **siguiente**.

**Costes incluidos.** Se descuentan comisión y deslizamiento en la ida y en la
vuelta. Sin costes, cualquier estrategia de alta rotación parece rentable.

**Referencia obligatoria.** El exceso se mide contra el **universo
equiponderado**, no contra un índice. Comparar contra un índice mezclaría el
aporte de la señal con la diferencia estructural entre estas acciones y ese
índice — un error con firma reconocible: señales opuestas saliendo ambas
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
            "Una señal validada en un ámbito NO esta validada en otro: los "
            "regimenes y la microestructura no se parecen."
        ),
    )
with control_right:
    horizon = st.selectbox(
        "Horizonte (sesiones)", options=list(eng.DEFAULT_HORIZONS), index=2,
        help="Cuántas sesiones se mantiene la posición tras la señal.",
    )

evidence = da.get_signal_evidence(scope)
if evidence.empty:
    st.info(f"Sin resultados para el ámbito seleccionado. Ejecuta `make validate --scope {scope}`.")
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
        "Ninguna señal supera la validación en este ámbito y horizonte. **Es un "
        "resultado, no un fallo**: significa que, con los datos disponibles, "
        "ninguna se distingue del azar después de costes. Operar cualquiera de "
        "ellas sería apostar.",
        icon=":material/info:",
    )

# ---------------------------------------------------------------------------
# Tabla de resultados
# ---------------------------------------------------------------------------
st.subheader("Resultados por señal")

table = pd.DataFrame(
    {
        "Señal": subset["signal_id"].map(lambda s: labels.get(s, s)),
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
            format="%d", help="Por debajo de 100 cualquier conclusión es anecdota."
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
    "El **acierto** no basta: en un mercado alcista casi cualquier señal acierta "
    "más de la mitad de las veces. Lo que importa es el **exceso** sobre lo que "
    "habría dado comprar cualquier valor del universo ese mismo día."
)

# ---------------------------------------------------------------------------
# Detalle de una senal
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Detalle de una señal")

options = subset["signal_id"].tolist()
chosen = st.selectbox("Señal", options=options, format_func=lambda s: labels.get(s, s))
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
st.subheader("Distribución de retornos posteriores")


@st.cache_data(ttl=900, show_spinner="Calculando el estudio de eventos...")
def _event_detail(signal_id: str, scope_key: str, horizon_days: int) -> pd.DataFrame:
    """Recalcula el detalle evento a evento para el histograma.

    No se guarda en el almacen: es mucho volumen para algo que solo se mira
    cuando alguien abre esta página.
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
    st.caption("Sin detalle disponible para esta señal.")
else:
    p = palette()
    fig = go.Figure()
    fig.add_trace(
        go.Histogram(
            x=detail["retorno"] * 100, nbinsx=60, name="Tras la señal",
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
        annotation_text="media tras la señal", annotation_position="top",
        annotation_font=dict(size=10, color=p["text_secondary"]),
    )
    fig = apply_layout(fig, height=320, showlegend=True, barmode="overlay")
    fig.update_xaxes(ticksuffix="%")
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption(
        "Si las dos distribuciones se solapan casi por completo, la señal no "
        "aporta información: lo que ocurre después es lo mismo que habría "
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
            f"Positiva en **{positive} de {len(folds)}** ventanas. Una señal que "
            "solo funciona en un tramo del histórico no es una señal: es una "
            "fotografía de ese tramo."
        )

# ---------------------------------------------------------------------------
# Comparativa de horizontes
# ---------------------------------------------------------------------------
st.divider()
st.subheader("La misma señal a distintos horizontes")

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
        "Una señal que solo funciona en un horizonte muy concreto es "
        "sospechosa: suele ser casualidad de la muestra más que un efecto real."
    )

st.divider()
render_disclaimer()

"""Página 3 — Oportunidades.

La página central del proyecto. La salida no es un score, es un candidato con
sus motivos escritos en castellano y con sus pegas al lado.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components import charts, tv_widgets
from stocks_tracker.app.components.common import (
    render_disclaimer,
    render_flags,
    render_reasons,
    sidebar_filters,
)
from stocks_tracker.app.components.theme import (
    format_market_cap,
    format_pct,
)
from stocks_tracker.core.config import get_factor_config
from stocks_tracker.core.explain import build_reasons
from stocks_tracker.core.flags import red_flags
from stocks_tracker.core.scoring import PRESET_DESCRIPTIONS, preset_label
from stocks_tracker.core.textutils import as_float, as_text

st.title("Oportunidades")
st.caption(
    "Ranking relativo dentro del universo filtrado. Indica que valores cumplen "
    "más criterios ahora mismo, no que vayan a subir."
)

filters = sidebar_filters("oport")
cfg = get_factor_config()

# ---------------------------------------------------------------------------
# Controles
# ---------------------------------------------------------------------------
with st.sidebar:
    st.subheader("Estilo de inversión")
    presets = da.available_presets()
    if presets:
        style = st.selectbox(
            "Qué factores pesan más",
            options=presets,
            index=presets.index(da.default_preset()) if da.default_preset() in presets
            else 0,
            format_func=preset_label,
            help=(
                "Cambia el reparto de pesos entre los siete factores y, con el, "
                "todo el ranking. No filtra: reordena."
            ),
        )
        st.caption(PRESET_DESCRIPTIONS.get(style, ""))
    else:
        style = None
        st.caption(
            "Solo hay un juego de pesos calculado. Ejecuta "
            "`make compute-presets` para poder comparar estilos."
        )

    st.subheader("Perfil de riesgo")
    profile = st.radio(
        "Exigencia de las guardas",
        options=["conservador", "equilibrado", "agresivo"],
        index=1,
        help=(
            "Conservador exige tendencia alcista, estabilidad y dividendo. "
            "Agresivo relaja las guardas y permite momentum puro."
        ),
    )

    st.subheader("Guardas de sensatez")
    guards = cfg.guards
    require_uptrend = st.checkbox(
        "Solo por encima de la MM200",
        value=profile != "agresivo",
        help="Evita comprar en tendencia bajista estructural.",
    )
    max_rsi = st.slider(
        "RSI máximo", min_value=50, max_value=100,
        value=int(guards.get("max_rsi14", 75)),
        help="Evita entrar en zona de euforia.",
    )
    min_coverage = st.slider(
        "Cobertura mínima de datos", min_value=0.0, max_value=1.0,
        value=float(guards.get("min_coverage", 0.5)), step=0.05,
        help="Fracción de campos fundamentales disponibles.",
    )
    exclude_deep_drawdown = st.checkbox(
        "Excluir caídas superiores al 50%", value=True,
        help="Un valor en colapso puede seguir cayendo.",
    )

candidates = da.get_candidates(
    filters["universe"], filters["sectors"], limit=400, preset=style
)

# Etiquetas de validacion historica: sin ellas, una senal sin evidencia se
# mostraria con la misma autoridad que una validada.
evidence_map = da.evidence_by_signal("equity_us", 21)

if candidates.empty:
    st.warning("No hay valores puntuados. Ejecuta `make compute`.")
    st.stop()

# ---------------------------------------------------------------------------
# Aplicacion de guardas
# ---------------------------------------------------------------------------
before = len(candidates)
filtered = candidates.copy()
applied: list[str] = []

if require_uptrend:
    filtered = filtered[filtered["above_sma200"].fillna(False).astype(bool)]
    applied.append("por encima de la MM200")
if max_rsi < 100:
    filtered = filtered[(filtered["rsi14"].isna()) | (filtered["rsi14"] <= max_rsi)]
    applied.append(f"RSI ≤ {max_rsi}")
if min_coverage > 0:
    filtered = filtered[filtered["coverage"].fillna(0) >= min_coverage]
    applied.append(f"cobertura ≥ {min_coverage:.0%}")
if exclude_deep_drawdown:
    filtered = filtered[(filtered["drawdown"].isna()) | (filtered["drawdown"] > -0.50)]
    applied.append("sin caídas superiores al 50%")

if profile == "conservador":
    filtered = filtered[filtered["lowvol_z"].fillna(0) > -0.5]
    applied.append("volatilidad no elevada para su sector")

filtered = filtered.sort_values("composite", ascending=False).reset_index(drop=True)

st.caption(
    f"**{len(filtered)}** de {before} valores pasan los filtros"
    + (f" · {', '.join(applied)}" if applied else "")
)

if filtered.empty:
    st.info("Ningún valor cumple todos los criterios. Prueba a relajar las guardas.")
    render_disclaimer()
    st.stop()

# ---------------------------------------------------------------------------
# Vista
# ---------------------------------------------------------------------------
view_mode = st.segmented_control(
    "Vista", options=["Tarjetas", "Tabla"], default="Tarjetas",
    label_visibility="collapsed",
)

FACTOR_LABELS = {
    "value_z": "Valor", "growth_z": "Crecimiento", "quality_z": "Calidad",
    "momentum_z": "Momentum", "lowvol_z": "Estabilidad",
    "dividend_z": "Dividendo", "technical_z": "Técnico",
}


def _render_card(row: pd.Series) -> None:
    ticker = row["ticker"]
    with st.container(border=True):
        head_left, head_right = st.columns([2, 1])
        with head_left:
            st.markdown(f"### {ticker}")
            st.caption(
                f"{as_text(row.get('name'))} · "
                f"{as_text(row.get('gics_sector')) or 'Sin sector'}"
            )
        with head_right:
            pctile = row.get("composite_pctile")
            st.metric(
                "Percentil",
                f"{pctile:.0%}" if pd.notna(pctile) else "—",
                delta=format_pct(row.get("ret_1d")),
            )

        price_cols = st.columns(4)
        price_cols[0].caption(f"**Precio** {row.get('close', float('nan')):,.2f}")
        price_cols[1].caption(f"**Cap.** {format_market_cap(row.get('market_cap'))}")
        rsi = row.get("rsi14")
        price_cols[2].caption(f"**RSI** {rsi:.0f}" if pd.notna(rsi) else "**RSI** —")
        cov = row.get("coverage")
        price_cols[3].caption(f"**Datos** {cov:.0%}" if pd.notna(cov) else "**Datos** —")

        # Con el perfil: si el ranking se ordena con unos pesos y los
        # motivos se explican con otros, la explicacion no explica nada.
        contributions = da.get_contributions(ticker, preset=style)
        signals = da.get_active_signals(ticker)
        # as_text y no `or ""`: un sector ausente llega como NaN, que es
        # VERDADERO, asi que `or` lo dejaba pasar. DuckDB recibia un numero
        # donde esperaba texto e intentaba convertir la columna entera a
        # DOUBLE: "Could not convert string 'Industrials' to DOUBLE". La
        # pagina entera se caia por un valor sin sector.
        medians = da.get_sector_medians(as_text(row.get("gics_sector")))
        zscores = {
            f.replace("_z", ""): row.get(f)
            for f in FACTOR_LABELS
            if pd.notna(row.get(f))
        }

        reasons = build_reasons(
            row, contributions=contributions, active_signals=signals,
            sector_medians=medians, zscores=zscores,
        )

        if reasons.is_empty:
            st.caption(
                "Aparece por su puntuación técnica agregada; datos "
                "fundamentales insuficientes para justificarlo mejor."
            )
        else:
            render_reasons(reasons, evidence=evidence_map, signal_ids=signals)

        flags = red_flags(row)
        if flags:
            st.markdown("**Banderas rojas**")
            render_flags(flags)

        # El desglose de factores va plegado: dentro de una tarjeta estrecha las
        # etiquetas se solapan y el grafico deja de leerse.
        if not contributions.empty:
            with st.expander("Desglose por factor"):
                st.plotly_chart(
                    charts.contribution_bars(contributions, height=240),
                    width="stretch",
                    config={"displayModeBar": False},
                    key=f"contrib_{ticker}",
                )

        action_cols = st.columns(2)
        if action_cols[0].button("Ficha", key=f"ficha_{ticker}", width="stretch"):
            st.session_state["selected_ticker"] = ticker
            st.switch_page("pages/4_ficha_valor.py")
        if action_cols[1].button("Guardar", key=f"wl_{ticker}", width="stretch"):
            da.add_to_watchlist(ticker, price=as_float(row.get("close")))
            st.toast(f"{ticker} añadido a la watchlist")


if view_mode == "Tarjetas":
    top_n = st.slider("Candidatos a mostrar", 2, 30, 8, step=2)
    subset = filtered.head(top_n)
    # Dos por fila: con tres, el percentil y los botones se cortan.
    for start in range(0, len(subset), 2):
        cols = st.columns(2)
        for offset, (_, row) in enumerate(subset.iloc[start:start + 2].iterrows()):
            with cols[offset]:
                _render_card(row)

else:
    table = pd.DataFrame(
        {
            "Ticker": filtered["ticker"],
            "Nombre": filtered["name"].fillna(""),
            "Sector": filtered["gics_sector"].fillna("—"),
            "Precio": filtered["close"],
            "Día": filtered["ret_1d"] * 100,
            "Score": filtered["composite"],
            "Percentil": filtered["composite_pctile"] * 100,
            **{
                FACTOR_LABELS[f]: filtered[f]
                for f in FACTOR_LABELS if f in filtered.columns
            },
            "Datos": filtered["coverage"] * 100,
            "PER": filtered.get("trailing_pe"),
            "Div.": filtered.get("dividend_yield", pd.Series(dtype=float)) * 100,
        }
    )
    st.dataframe(
        table, hide_index=True, height=620,
        column_config={
            "Precio": st.column_config.NumberColumn(format="%.2f"),
            "Día": st.column_config.NumberColumn(format="%+.2f%%"),
            "Score": st.column_config.NumberColumn(format="%+.2f"),
            "Percentil": st.column_config.ProgressColumn(
                min_value=0.0, max_value=100.0, format="%.0f%%"
            ),
            "Datos": st.column_config.ProgressColumn(
                min_value=0.0, max_value=100.0, format="%.0f%%"
            ),
            "PER": st.column_config.NumberColumn(format="%.1f"),
            "Div.": st.column_config.NumberColumn(format="%.1f%%"),
            **{
                FACTOR_LABELS[f]: st.column_config.NumberColumn(format="%+.2f")
                for f in FACTOR_LABELS
            },
        },
    )
    st.download_button(
        "Descargar CSV",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name="oportunidades.csv",
        mime="text/csv",
        help="Incluye el aviso: ranking relativo, no recomendación de compra.",
    )

# ---------------------------------------------------------------------------
# Distribucion y complemento externo
# ---------------------------------------------------------------------------
st.divider()
dist_col, screener_col = st.columns([1, 1])

with dist_col:
    st.subheader("Distribución de scores")
    st.plotly_chart(
        charts.score_distribution(candidates["composite"]),
        width="stretch",
        config={"displayModeBar": False},
    )
    st.caption(
        "Los scores son relativos: la mitad del universo siempre estara por "
        "debajo de la mediana, también en un buen mercado."
    )

with screener_col:
    if tv_widgets.enabled():
        st.subheader("Screener de TradingView")
        st.caption("Complemento externo. No explica por que aparece cada valor.")
        with st.expander("Abrir screener"):
            tv_widgets.screener("america", height=460)

del np
st.divider()
render_disclaimer()

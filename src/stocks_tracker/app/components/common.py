"""Piezas de interfaz compartidas: aviso legal, filtros, tarjetas, tablas."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ... import DISCLAIMER
from ...core.explain import Reasons
from .. import data_access as da
from .theme import STATUS, format_market_cap, format_pct


def render_disclaimer(compact: bool = True) -> None:
    """Pie legal. Va en TODAS las paginas, sin excepcion."""
    if compact:
        st.caption(f":grey[{DISCLAIMER}]")
    else:
        st.info(DISCLAIMER, icon=":material/info:")


def render_freshness_badge() -> None:
    """Estado de los datos. Si estan viejos hay que decirlo, no disimularlo."""
    info = da.data_freshness()
    last_date = info["last_price_date"]
    if last_date is None:
        st.warning(
            "No hay datos cargados. Ejecuta `make ingest` (datos reales) o "
            "`make ingest-demo` (datos sinteticos) y despues `make compute`.",
            icon=":material/database_off:",
        )
        return

    hours = info["hours_since_run"]
    detail = f"Datos hasta el {last_date:%d/%m/%Y}"
    if hours is not None:
        detail += f" · ultima actualizacion hace {hours:.0f} h"

    if info["is_stale"]:
        st.warning(f"{detail}. Conviene actualizar.", icon=":material/schedule:")
    elif info["failures"]:
        st.caption(f":orange[{detail} · {info['failures']} descargas fallidas]")
    else:
        st.caption(f":grey[{detail}]")


def sidebar_filters(key_prefix: str = "") -> dict:
    """Filtros comunes de la barra lateral."""
    with st.sidebar:
        st.subheader("Filtros")
        universes = da.universe_options()
        universe = st.selectbox(
            "Mercado",
            options=list(universes),
            format_func=lambda k: universes[k],
            key=f"{key_prefix}_universe",
        )
        sectors = st.multiselect(
            "Sectores",
            options=da.get_sectors(),
            default=[],
            help="Vacio = todos los sectores",
            key=f"{key_prefix}_sectors",
        )
    return {"universe": universe, "sectors": tuple(sectors)}


def metric_row(items: list[dict], columns: int = 5) -> None:
    """Fila de indicadores clave.

    Cada variacion lleva signo explicito: el color refuerza, no comunica.
    """
    cols = st.columns(min(columns, max(1, len(items))))
    for i, item in enumerate(items):
        with cols[i % len(cols)]:
            st.metric(
                label=item["label"],
                value=item["value"],
                delta=item.get("delta"),
                help=item.get("help"),
            )


def render_reasons(reasons: Reasons, show_signals: bool = True) -> None:
    """Motivos a favor y en contra, en lenguaje llano."""
    if reasons.pros:
        st.markdown("**A favor**")
        for phrase in reasons.pros:
            st.markdown(f"<span style='color:{STATUS['good']}'>✓</span> {phrase}",
                        unsafe_allow_html=True)
    if reasons.cons:
        st.markdown("**A vigilar**")
        for phrase in reasons.cons:
            st.markdown(f"<span style='color:{STATUS['warning']}'>▲</span> {phrase}",
                        unsafe_allow_html=True)
    if show_signals and reasons.signals:
        st.markdown("**Senales activas hoy**")
        st.markdown(" · ".join(f"`{s}`" for s in reasons.signals))


def render_flags(flags: list[str]) -> None:
    """Banderas rojas. Se muestran aunque el score sea alto."""
    if not flags:
        return
    for flag in flags:
        st.markdown(
            f"<span style='color:{STATUS['critical']}'>■</span> "
            f"<span style='font-size:0.9em'>{flag}</span>",
            unsafe_allow_html=True,
        )


def movers_table(df: pd.DataFrame, height: int = 320) -> None:
    """Tabla de movimientos con formato consistente."""
    if df.empty:
        st.caption("Sin datos.")
        return

    view = pd.DataFrame(
        {
            "Ticker": df["ticker"],
            "Nombre": df["name"].fillna(""),
            "Sector": df["gics_sector"].fillna("—"),
            "Precio": df["close"],
            "Dia": df["ret_1d"],
            "Vol. rel.": df.get("rel_volume_20"),
            "Percentil": df.get("composite_pctile"),
        }
    )
    st.dataframe(
        view,
        hide_index=True,
        height=height,
        column_config={
            "Precio": st.column_config.NumberColumn(format="%.2f"),
            "Dia": st.column_config.NumberColumn(format="%+.2f%%"),
            "Vol. rel.": st.column_config.NumberColumn(format="%.1fx"),
            "Percentil": st.column_config.ProgressColumn(
                min_value=0.0, max_value=1.0, format="%.0f%%"
            ),
        },
    )


def prepare_percent_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Pasa fracciones a puntos porcentuales, para el formato de las tablas."""
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce") * 100
    return out


def signal_chips(signals: list[str], labels: dict[str, str]) -> str:
    if not signals:
        return "—"
    return " · ".join(labels.get(s, s) for s in signals)


__all__ = [
    "render_disclaimer", "render_freshness_badge", "sidebar_filters",
    "metric_row", "render_reasons", "render_flags", "movers_table",
    "prepare_percent_columns", "signal_chips", "format_pct", "format_market_cap",
]

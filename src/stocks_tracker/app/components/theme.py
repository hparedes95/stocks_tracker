"""Paleta y estilo comun de los graficos.

Un unico sitio donde viven los colores, para que todo el dashboard se lea como
un mismo sistema y para poder cambiar de tema sin tocar cada grafico.

La paleta categorica esta validada para daltonismo: el ORDEN de los colores es
el mecanismo de seguridad, no una decision estetica. Nunca reordenar los slots
ni generar colores nuevos por codigo; a partir del noveno, agrupar en "Otros".
"""

from __future__ import annotations

import streamlit as st

# Paleta categorica, en orden fijo. Verificada en claro y oscuro.
CATEGORICAL_LIGHT = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
    "#e87ba4", "#008300", "#4a3aa7", "#e34948",
]
CATEGORICAL_DARK = [
    "#3987e5", "#d95926", "#199e70", "#c98500",
    "#d55181", "#008300", "#9085e9", "#e66767",
]

# Estados. Fijos, nunca se usan como color de serie.
# En un dashboard financiero verde/rojo es la convencion, pero es justo el par
# peor para daltonismo: por eso SIEMPRE van acompanados de signo o flecha, y el
# color nunca es el unico portador del significado.
STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

# Rampa secuencial de un solo tono (magnitud continua).
SEQUENTIAL_BLUE = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec",
    "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab",
]

# Divergente general: dos polos opuestos con gris neutro en el centro.
DIVERGING = [
    [0.0, "#d03b3b"], [0.5, "#f0efec"], [1.0, "#2a78d6"],
]

# Divergente para RENDIMIENTO. Rojo y verde son el peor par posible para el
# daltonismo, pero aqui pesa mas la coherencia: en el resto del dashboard verde
# es subida y rojo bajada, y usar azul solo en los mapas de calor obligaria a
# recordar dos convenciones distintas dentro de la misma pantalla.
#
# La mitigacion es la misma que en el resto: **cada celda lleva su valor con
# signo impreso encima**, asi que el color refuerza pero nunca comunica en
# solitario. Si en algun momento se quitan esas etiquetas, hay que volver al
# divergente azul-rojo.
DIVERGING_PERFORMANCE = [
    [0.0, "#d03b3b"], [0.5, "#f0efec"], [1.0, "#0ca30c"],
]

LIGHT = {
    "surface": "#fcfcfb",
    "plane": "#f9f9f7",
    "text_primary": "#0b0b0b",
    "text_secondary": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "categorical": CATEGORICAL_LIGHT,
}

DARK = {
    "surface": "#1a1a19",
    "plane": "#0d0d0d",
    "text_primary": "#ffffff",
    "text_secondary": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "categorical": CATEGORICAL_DARK,
}

FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def is_dark() -> bool:
    """Tema activo de Streamlit.

    `st.context.theme` es la fuente fiable: refleja el tema que el navegador
    esta usando de verdad, incluido el heredado del sistema operativo.
    `st.get_option("theme.base")` solo devuelve algo si hay un tema declarado en
    `config.toml`, y es None en la configuracion por defecto.

    Ante la duda se asume tema CLARO. Equivocarse hacia oscuro pinta texto
    blanco sobre fondo blanco y hace desaparecer cifras enteras del grafico;
    equivocarse hacia claro deja texto oscuro, que al menos se lee.
    """
    try:
        theme_type = getattr(st.context.theme, "type", None)
        if theme_type:
            return str(theme_type).lower() == "dark"
    except Exception:  # noqa: BLE001
        pass

    try:
        base = st.get_option("theme.base")
        if base:
            return str(base).lower() == "dark"
    except Exception:  # noqa: BLE001
        pass

    return False


def palette() -> dict:
    return DARK if is_dark() else LIGHT


def series_color(index: int) -> str:
    """Color de la serie n, en orden fijo. A partir del octavo, se repite el
    ultimo en lugar de inventar un tono nuevo: si hay tantas series, el problema
    es el grafico, no la paleta."""
    colors = palette()["categorical"]
    return colors[index] if index < len(colors) else colors[-1]


def change_color(value: float | None) -> str:
    """Color para una variacion. Siempre acompanado de signo en el texto."""
    if value is None:
        return palette()["muted"]
    if value > 0:
        return STATUS["good"]
    if value < 0:
        return STATUS["critical"]
    return palette()["muted"]


def apply_layout(fig, height: int = 320, showlegend: bool = False, **kwargs):
    """Estilo comun: rejilla discreta, ejes tenues, sin ruido visual."""
    p = palette()
    fig.update_layout(
        height=height,
        showlegend=showlegend,
        margin=dict(l=8, r=8, t=28, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT_FAMILY, size=12, color=p["text_secondary"]),
        hoverlabel=dict(font_size=12, font_family=FONT_FAMILY),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            font=dict(size=11),
        ),
        **kwargs,
    )
    fig.update_xaxes(
        showgrid=False, zeroline=False,
        linecolor=p["axis"], tickfont=dict(color=p["muted"], size=11),
    )
    fig.update_yaxes(
        showgrid=True, gridcolor=p["grid"], gridwidth=1,
        zeroline=False, linecolor="rgba(0,0,0,0)",
        tickfont=dict(color=p["muted"], size=11),
    )
    return fig


def format_pct(value, decimals: int = 1, with_sign: bool = True) -> str:
    """Porcentaje con signo explicito. El signo es el canal accesible que
    acompana al color: sin el, un daltonico no distingue subida de bajada."""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v != v:  # NaN
        return "—"
    sign = "+" if (with_sign and v > 0) else ""
    return f"{sign}{v * 100:.{decimals}f}%"


def format_num(value, decimals: int = 2) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v != v:
        return "—"
    return f"{v:,.{decimals}f}".replace(",", " ")


def format_money(value, currency: str = "USD", decimals: int = 2) -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency or "USD", "")
    return f"{format_num(value, decimals)} {symbol}".strip()


def format_market_cap(value) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v != v:
        return "—"
    for threshold, suffix in ((1e12, "B"), (1e9, "MM"), (1e6, "M")):
        if abs(v) >= threshold:
            return f"{v / threshold:.1f} {suffix}"
    return format_num(v, 0)

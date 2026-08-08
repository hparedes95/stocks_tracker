"""Graficos propios (Plotly).

Ambito deliberadamente reducido: aqui solo va lo que TradingView no puede
mostrar porque son NUESTROS calculos (amplitud, factores, contribuciones,
rendimiento por sector) o lo que no es una serie de precio.

Regla de accesibilidad que atraviesa el modulo: el color nunca es el unico
portador del significado. Toda subida o bajada lleva signo explicito, y las
series categoricas van con leyenda o etiqueta directa.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .theme import (
    DIVERGING,
    SEQUENTIAL_BLUE,
    STATUS,
    apply_layout,
    palette,
    series_color,
)


def sparkline(values: pd.Series, height: int = 60, positive: bool | None = None) -> go.Figure:
    """Mini-grafico sin ejes, para ir dentro de una tarjeta o una celda."""
    p = palette()
    if positive is None:
        positive = len(values) > 1 and float(values.iloc[-1]) >= float(values.iloc[0])
    color = STATUS["good"] if positive else STATUS["critical"]

    fig = go.Figure(
        go.Scatter(
            y=values.to_numpy(), mode="lines",
            line=dict(color=color, width=2),
            fill="tozeroy", fillcolor="rgba(0,0,0,0)",
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        height=height, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False, xaxis=dict(visible=False), yaxis=dict(visible=False),
    )
    del p
    return fig


def risk_gauge(score: float, regime: str, height: int = 220) -> go.Figure:
    """Semaforo risk-on / risk-off.

    Un unico numero con contexto. El texto del regimen acompana siempre al color:
    "risk_off" en letra es lo que hace legible el grafico sin depender del tono.
    """
    p = palette()
    color = (
        STATUS["good"] if score > 30 else
        STATUS["critical"] if score < -30 else
        STATUS["warning"]
    )
    label = {"risk_on": "Apetito por riesgo", "risk_off": "Aversion al riesgo",
             "neutral": "Neutral"}.get(regime, regime)

    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=float(score),
            # El medidor ocupa la mitad superior y el numero cae debajo del arco.
            # Sin reservar ese espacio, Plotly recorta la cifra y el grafico se
            # queda sin el dato que precisamente venia a comunicar.
            domain=dict(x=[0, 1], y=[0.25, 1]),
            # Entero: el decimal sugiere una precision que este indicador no tiene.
            number=dict(valueformat=".0f", font=dict(size=34, color=p["text_primary"])),
            title=dict(text=label, font=dict(size=13, color=p["text_secondary"])),
            gauge=dict(
                axis=dict(range=[-100, 100], tickwidth=1, tickcolor=p["muted"],
                          tickfont=dict(size=10, color=p["muted"])),
                bar=dict(color=color, thickness=0.7),
                bgcolor="rgba(0,0,0,0)",
                borderwidth=0,
                steps=[
                    dict(range=[-100, -30], color=p["grid"]),
                    dict(range=[-30, 30], color="rgba(0,0,0,0)"),
                    dict(range=[30, 100], color=p["grid"]),
                ],
            ),
        )
    )
    fig.update_layout(
        height=height, margin=dict(l=16, r=16, t=40, b=16),
        paper_bgcolor="rgba(0,0,0,0)", font=dict(color=p["text_secondary"]),
    )
    return fig


def sector_bars(df: pd.DataFrame, value_col: str, height: int = 380) -> go.Figure:
    """Rendimiento por sector: barras divergentes ordenadas.

    Las barras llevan el valor etiquetado al extremo, asi que el color solo
    refuerza el signo, no lo comunica.
    """
    p = palette()
    data = df.dropna(subset=[value_col]).sort_values(value_col)
    if data.empty:
        return apply_layout(go.Figure(), height=height)

    values = data[value_col].astype(float) * 100
    colors = [STATUS["good"] if v >= 0 else STATUS["critical"] for v in values]
    labels = [f"{v:+.1f}%" for v in values]

    fig = go.Figure(
        go.Bar(
            x=values, y=data["sector"], orientation="h",
            marker=dict(color=colors, line=dict(width=0)),
            text=labels, textposition="outside",
            textfont=dict(size=11, color=p["text_secondary"]),
            hovertemplate="%{y}: %{x:.2f}%<extra></extra>",
            width=0.62,
        )
    )
    fig = apply_layout(fig, height=height)
    fig.update_xaxes(showgrid=True, gridcolor=p["grid"], zeroline=True,
                     zerolinecolor=p["axis"], zerolinewidth=1, ticksuffix="%")
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11, color=p["text_secondary"]))
    max_abs = float(np.abs(values).max()) if len(values) else 1.0
    fig.update_xaxes(range=[-max_abs * 1.35, max_abs * 1.35])
    return fig


def breadth_lines(df: pd.DataFrame, height: int = 300) -> go.Figure:
    """Porcentaje de valores sobre sus medias.

    Debajo del 30% el mercado esta deteriorado por dentro; por encima del 80%,
    eufórico. Las bandas de referencia hacen legible esa lectura sin explicarla.
    """
    p = palette()
    if df.empty:
        return apply_layout(go.Figure(), height=height)

    fig = go.Figure()
    for i, (col, label) in enumerate(
        [("pct_above_sma200", "Sobre la MM200"), ("pct_above_sma50", "Sobre la MM50")]
    ):
        if col not in df.columns:
            continue
        fig.add_trace(
            go.Scatter(
                x=pd.to_datetime(df["date"]), y=df[col], mode="lines", name=label,
                line=dict(color=series_color(i), width=2),
                hovertemplate="%{x|%d %b %Y}<br>" + label + ": %{y:.0f}%<extra></extra>",
            )
        )

    # Las etiquetas van DENTRO del area de dibujo: fuera se recortan contra el
    # margen derecho y se quedan en una letra suelta.
    for level, note, position in (
        (30, "deterioro", "top left"), (80, "euforia", "bottom left")
    ):
        fig.add_hline(
            y=level, line=dict(color=p["axis"], width=1, dash="dot"),
            annotation_text=note, annotation_position=position,
            annotation_font=dict(size=10, color=p["muted"]),
        )

    fig = apply_layout(fig, height=height, showlegend=True, hovermode="x unified")
    fig.update_yaxes(range=[0, 100], ticksuffix="%")
    return fig


def advance_decline(df: pd.DataFrame, height: int = 260) -> go.Figure:
    """Linea avance-descenso acumulada.

    Su divergencia con el indice es el aviso clasico de que una subida se esta
    quedando sin participantes.
    """
    if df.empty or "ad_line" not in df.columns:
        return apply_layout(go.Figure(), height=height)

    fig = go.Figure(
        go.Scatter(
            x=pd.to_datetime(df["date"]), y=df["ad_line"], mode="lines",
            name="Linea A-D", line=dict(color=series_color(0), width=2),
            hovertemplate="%{x|%d %b %Y}<br>A-D acumulada: %{y:,.0f}<extra></extra>",
        )
    )
    return apply_layout(fig, height=height, hovermode="x unified")


def factor_radar(scores: dict[str, float], height: int = 320) -> go.Figure:
    """Perfil factorial de un valor. Da la forma de un vistazo."""
    p = palette()
    labels = {
        "value_z": "Valor", "growth_z": "Crecimiento", "quality_z": "Calidad",
        "momentum_z": "Momentum", "lowvol_z": "Estabilidad",
        "dividend_z": "Dividendo", "technical_z": "Tecnico",
    }
    keys = [k for k in labels if k in scores and scores[k] is not None
            and np.isfinite(scores.get(k, np.nan))]
    if len(keys) < 3:
        return apply_layout(go.Figure(), height=height)

    values = [float(np.clip(scores[k], -3, 3)) for k in keys]
    names = [labels[k] for k in keys]
    # Cerrar el poligono
    values.append(values[0])
    names.append(names[0])

    fig = go.Figure(
        go.Scatterpolar(
            r=values, theta=names, fill="toself",
            line=dict(color=series_color(0), width=2),
            fillcolor="rgba(42,120,214,0.18)",
            hovertemplate="%{theta}: %{r:+.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=height, margin=dict(l=40, r=40, t=30, b=30),
        paper_bgcolor="rgba(0,0,0,0)", showlegend=False,
        font=dict(size=11, color=p["text_secondary"]),
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(range=[-3, 3], showticklabels=True, gridcolor=p["grid"],
                            tickfont=dict(size=9, color=p["muted"])),
            angularaxis=dict(gridcolor=p["grid"],
                             tickfont=dict(size=11, color=p["text_secondary"])),
        ),
    )
    return fig


def contribution_bars(contributions: pd.DataFrame, height: int = 260) -> go.Figure:
    """Que factores suman y cuales restan al score. La respuesta a "por que"."""
    p = palette()
    if contributions.empty:
        return apply_layout(go.Figure(), height=height)

    labels = {
        "value": "Valor", "growth": "Crecimiento", "quality": "Calidad",
        "momentum": "Momentum", "lowvol": "Estabilidad",
        "dividend": "Dividendo", "technical": "Tecnico",
    }
    data = contributions.dropna(subset=["contribution"]).copy()
    data["nombre"] = data["factor"].map(lambda f: labels.get(f, f))
    data = data.sort_values("contribution")

    colors = [
        STATUS["good"] if v >= 0 else STATUS["critical"]
        for v in data["contribution"]
    ]
    fig = go.Figure(
        go.Bar(
            x=data["contribution"], y=data["nombre"], orientation="h",
            marker=dict(color=colors, line=dict(width=0)),
            text=[f"{v:+.2f}" for v in data["contribution"]],
            textposition="outside",
            textfont=dict(size=11, color=p["text_secondary"]),
            hovertemplate="%{y}: %{x:+.3f}<extra></extra>",
            width=0.6,
        )
    )
    fig = apply_layout(fig, height=height)
    fig.update_xaxes(showgrid=True, gridcolor=p["grid"], zeroline=True,
                     zerolinecolor=p["axis"], zerolinewidth=1)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11, color=p["text_secondary"]))
    span = float(data["contribution"].abs().max() or 0.1)
    fig.update_xaxes(range=[-span * 1.5, span * 1.5])
    return fig


def price_with_signals(
    prices: pd.DataFrame, indicators: pd.DataFrame,
    signals: pd.DataFrame | None = None, height: int = 460,
) -> go.Figure:
    """Velas con NUESTRAS medias y NUESTRAS senales marcadas.

    Esto es lo que ningun widget de TradingView puede dar: sus graficos son una
    caja negra con sus datos, y no admiten que dibujemos nada encima.
    """
    p = palette()
    if prices.empty:
        return apply_layout(go.Figure(), height=height)

    dates = pd.to_datetime(prices["date"])
    fig = go.Figure()

    fig.add_trace(
        go.Candlestick(
            x=dates, open=prices["open"], high=prices["high"],
            low=prices["low"], close=prices["close"], name="Precio",
            increasing=dict(line=dict(color=STATUS["good"], width=1),
                            fillcolor=STATUS["good"]),
            decreasing=dict(line=dict(color=STATUS["critical"], width=1),
                            fillcolor=STATUS["critical"]),
            showlegend=False,
        )
    )

    if not indicators.empty:
        ind_dates = pd.to_datetime(indicators["date"])
        for i, (col, label) in enumerate(
            [("sma50", "MM50"), ("sma200", "MM200")]
        ):
            if col in indicators.columns and indicators[col].notna().any():
                fig.add_trace(
                    go.Scatter(
                        x=ind_dates, y=indicators[col], mode="lines", name=label,
                        line=dict(color=series_color(i), width=2),
                        hovertemplate=label + ": %{y:.2f}<extra></extra>",
                    )
                )

    if signals is not None and not signals.empty:
        price_by_date = dict(zip(dates, prices["close"], strict=False))
        for direction, symbol, color, pos in (
            ("bullish", "triangle-up", STATUS["good"], -0.03),
            ("bearish", "triangle-down", STATUS["critical"], 0.03),
        ):
            subset = signals[signals["direction"] == direction]
            if subset.empty:
                continue
            sig_dates = pd.to_datetime(subset["date"])
            # El marcador debe caer sobre una fecha que exista en la serie; si
            # no, queda huerfano y confunde mas que ayuda.
            y_vals, x_vals, texts = [], [], []
            for d, sid in zip(sig_dates, subset["signal_id"], strict=False):
                price = price_by_date.get(d)
                if price is None or not np.isfinite(price):
                    continue
                x_vals.append(d)
                y_vals.append(price * (1 + pos))
                texts.append(sid)
            if not x_vals:
                continue
            fig.add_trace(
                go.Scatter(
                    x=x_vals, y=y_vals, mode="markers",
                    name="Senales alcistas" if direction == "bullish" else "Senales bajistas",
                    marker=dict(symbol=symbol, size=9, color=color,
                                line=dict(width=1.5, color=p["surface"])),
                    text=texts,
                    hovertemplate="%{x|%d %b %Y}<br>%{text}<extra></extra>",
                )
            )

    fig = apply_layout(fig, height=height, showlegend=True)
    fig.update_layout(xaxis_rangeslider_visible=False, hovermode="x unified")
    return fig


def oscillator_panel(indicators: pd.DataFrame, column: str, title: str,
                     bands: tuple[float, float] | None = None,
                     height: int = 170) -> go.Figure:
    """Panel inferior para RSI, MACD y similares."""
    p = palette()
    if indicators.empty or column not in indicators.columns:
        return apply_layout(go.Figure(), height=height)

    dates = pd.to_datetime(indicators["date"])
    fig = go.Figure(
        go.Scatter(
            x=dates, y=indicators[column], mode="lines", name=title,
            line=dict(color=series_color(0), width=2),
            hovertemplate=title + ": %{y:.2f}<extra></extra>",
        )
    )
    if bands:
        for level in bands:
            fig.add_hline(y=level, line=dict(color=p["axis"], width=1, dash="dot"))

    fig = apply_layout(fig, height=height, hovermode="x unified")
    fig.update_layout(margin=dict(l=8, r=8, t=24, b=8), title=dict(
        text=title, font=dict(size=12, color=p["text_secondary"]), x=0, xanchor="left"))
    return fig


def heatmap_sector_horizon(df: pd.DataFrame, height: int = 380) -> go.Figure:
    """Sector por horizonte temporal. Escala divergente centrada en cero."""
    p = palette()
    horizons = [
        ("ret_1d", "1 dia"), ("ret_5d", "1 semana"), ("ret_1m", "1 mes"),
        ("ret_3m", "3 meses"), ("ret_12m", "12 meses"),
    ]
    cols = [c for c, _ in horizons if c in df.columns]
    if df.empty or not cols:
        return apply_layout(go.Figure(), height=height)

    matrix = df.set_index("sector")[cols].astype(float) * 100
    labels = [name for c, name in horizons if c in cols]
    span = float(np.nanmax(np.abs(matrix.to_numpy()))) or 1.0

    fig = go.Figure(
        go.Heatmap(
            z=matrix.to_numpy(), x=labels, y=matrix.index.tolist(),
            colorscale=DIVERGING, zmid=0, zmin=-span, zmax=span,
            text=[[f"{v:+.1f}%" if np.isfinite(v) else "" for v in row]
                  for row in matrix.to_numpy()],
            texttemplate="%{text}",
            textfont=dict(size=10),
            hovertemplate="%{y} · %{x}: %{z:+.2f}%<extra></extra>",
            xgap=2, ygap=2,
            colorbar=dict(title="", ticksuffix="%", thickness=10,
                          tickfont=dict(size=9, color=p["muted"])),
        )
    )
    fig = apply_layout(fig, height=height)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11, color=p["text_secondary"]))
    fig.update_xaxes(side="top")
    return fig


def score_distribution(scores: pd.Series, highlight: float | None = None,
                       height: int = 200) -> go.Figure:
    """Donde cae un valor dentro de la distribucion de scores del universo."""
    p = palette()
    data = scores.dropna()
    if data.empty:
        return apply_layout(go.Figure(), height=height)

    fig = go.Figure(
        go.Histogram(
            x=data, nbinsx=40, marker=dict(color=SEQUENTIAL_BLUE[6], line=dict(width=0)),
            hovertemplate="Score %{x:.2f}: %{y} valores<extra></extra>",
        )
    )
    if highlight is not None and np.isfinite(highlight):
        fig.add_vline(
            x=highlight, line=dict(color=STATUS["warning"], width=2),
            annotation_text="Este valor", annotation_position="top",
            annotation_font=dict(size=10, color=p["text_secondary"]),
        )
    return apply_layout(fig, height=height)

"""Gráficos propios (Plotly).

Ámbito deliberadamente reducido: aquí solo va lo que TradingView no puede
mostrar porque son NUESTROS cálculos (amplitud, factores, contribuciones,
rendimiento por sector) o lo que no es una serie de precio.

Regla de accesibilidad que atraviesa el módulo: el color nunca es el único
portador del significado. Toda subida o bajada lleva signo explicito, y las
series categóricas van con leyenda o etiqueta directa.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .theme import (
    DIVERGING_PERFORMANCE,
    FONT_FAMILY,
    SEQUENTIAL_BLUE,
    STATUS,
    apply_layout,
    palette,
    series_color,
)


def sparkline(values: pd.Series, height: int = 60, positive: bool | None = None) -> go.Figure:
    """Mini-gráfico sin ejes, para ir dentro de una tarjeta o una celda."""
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
    """Semáforo risk-on / risk-off.

    Un único número con contexto. El texto del regimen acompaña siempre al color:
    "risk_off" en letra es lo que hace legible el gráfico sin depender del tono.
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
    # Margenes laterales generosos: con 16 px, Plotly recorta las etiquetas de
    # los extremos del arco y "-100" se lee "00", que es peor que no ponerla.
    fig.update_layout(
        height=height, margin=dict(l=44, r=44, t=40, b=16),
        paper_bgcolor="rgba(0,0,0,0)", font=dict(color=p["text_secondary"]),
    )
    return fig


def fear_greed_strip(value: float, bands: list[tuple[float, float, str]],
                     height: int = 120) -> go.Figure:
    """Miedo y codicia como una franja 0-100 con un marcador.

    Deliberadamente NO es un medidor. Es la misma cifra que el semáforo de
    riesgo en otra escala, y ponerle un segundo dial al lado la convertiría
    visualmente en un segundo indicador: dos agujas que no coinciden del todo
    no informan más, solo siembran la duda de a cual hacer caso.

    Los tramos van rotulados con su nombre, así que el color no carga con el
    significado el solo.
    """
    p = palette()
    shades = [
        STATUS["critical"], STATUS["warning"], p["grid"],
        SEQUENTIAL_BLUE[4], STATUS["good"],
    ]

    fig = go.Figure()
    for i, (start, end, name) in enumerate(bands):
        fig.add_shape(
            type="rect", x0=start, x1=end, y0=0, y1=1,
            fillcolor=shades[i % len(shades)], opacity=0.28, line=dict(width=0),
        )
        fig.add_annotation(
            x=(start + end) / 2, y=-0.55, text=name, showarrow=False,
            font=dict(size=9, color=p["text_secondary"]),
        )

    fig.add_shape(
        type="line", x0=value, x1=value, y0=-0.12, y1=1.12,
        line=dict(color=p["text_primary"], width=3),
    )
    fig.add_annotation(
        x=value, y=1.7, text=f"<b>{value:.0f}</b>", showarrow=False,
        font=dict(size=22, color=p["text_primary"]),
    )

    fig = apply_layout(fig, height=height)
    fig.update_xaxes(range=[0, 100], showgrid=False, zeroline=False,
                     tickvals=[0, 25, 45, 55, 75, 100],
                     tickfont=dict(size=9, color=p["muted"]))
    fig.update_yaxes(range=[-1.1, 2.2], visible=False)
    fig.update_layout(margin=dict(l=8, r=8, t=8, b=4))
    return fig


def sector_bars(df: pd.DataFrame, value_col: str, height: int = 380) -> go.Figure:
    """Rendimiento por sector: barras divergentes ordenadas.

    Las barras llevan el valor etiquetado al extremo, así que el color solo
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


def weight_bars(weights: pd.Series, height: int = 240) -> go.Figure:
    """Exposición por categoría: barras desde cero, de un solo color.

    Deliberadamente distinto de `sector_bars`: un peso no puede ser negativo,
    así que un eje divergente y el par rojo/verde sugerirían una lectura de
    rendimiento que aquí no existe.
    """
    p = palette()
    data = weights.dropna().sort_values()
    if data.empty:
        return apply_layout(go.Figure(), height=height)

    values = data.astype(float) * 100
    fig = go.Figure(
        go.Bar(
            x=values, y=[str(i) for i in data.index], orientation="h",
            marker=dict(color=SEQUENTIAL_BLUE[6], line=dict(width=0)),
            text=[f"{v:.1f}%" for v in values], textposition="outside",
            textfont=dict(size=11, color=p["text_secondary"]),
            hovertemplate="%{y}: %{x:.1f}%<extra></extra>",
            width=0.62,
        )
    )
    fig = apply_layout(fig, height=height)
    fig.update_xaxes(showgrid=True, gridcolor=p["grid"], ticksuffix="%",
                     range=[0, float(values.max()) * 1.25])
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11, color=p["text_secondary"]))
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

    Su divergencia con el índice es el aviso clásico de que una subida se esta
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
        "dividend_z": "Dividendo", "technical_z": "Técnico",
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
    """Qué factores suman y cuáles restan al score. La respuesta a "por que"."""
    p = palette()
    if contributions.empty:
        return apply_layout(go.Figure(), height=height)

    labels = {
        "value": "Valor", "growth": "Crecimiento", "quality": "Calidad",
        "momentum": "Momentum", "lowvol": "Estabilidad",
        "dividend": "Dividendo", "technical": "Técnico",
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
    """Velas con NUESTRAS medias y NUESTRAS señales marcadas.

    Esto es lo que ningún widget de TradingView puede dar: sus gráficos son una
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
                    name="Señales alcistas" if direction == "bullish" else "Señales bajistas",
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
        ("ret_1d", "1 día"), ("ret_5d", "1 semana"), ("ret_1m", "1 mes"),
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
            colorscale=DIVERGING_PERFORMANCE, zmid=0, zmin=-span, zmax=span,
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


def rotation_chart(df: pd.DataFrame, height: int = 520,
                   trails_for: list[str] | None = None) -> go.Figure:
    """Gráfico de rotación sectorial (estilo RRG).

    Eje X: fuerza relativa frente al índice. Eje Y: si esa ventaja se acelera o
    se agota. Los cuadrantes describen DONDE ESTA cada sector ahora; la estela
    muestra por donde ha pasado. Nada de esto dice hacia donde ira.

    Cada punto lleva su etiqueta directa: con once sectores, una leyenda de once
    colores sería indescifrable.
    """
    p = palette()
    if df.empty:
        return apply_layout(go.Figure(), height=height)

    fig = go.Figure()

    # Cuadrantes: fondos muy tenues, solo para orientar la lectura.
    span = 3.5
    quadrants = [
        (100, 100 + span, 100, 100 + span, "Lidera"),
        (100, 100 + span, 100 - span, 100, "Se debilita"),
        (100 - span, 100, 100 - span, 100, "Rezagado"),
        (100 - span, 100, 100, 100 + span, "Mejora"),
    ]
    for x0, x1, y0, y1, label in quadrants:
        fig.add_shape(
            type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
            fillcolor=p["grid"], opacity=0.28, line=dict(width=0), layer="below",
        )
        fig.add_annotation(
            x=(x0 + x1) / 2, y=y1 - 0.12, text=label, showarrow=False,
            font=dict(size=10, color=p["muted"]),
        )

    # Estelas: solo las pedidas explicitamente.
    #
    # Dibujar las once a la vez produce una marana de lineas cruzadas donde no
    # se distingue ninguna. La estela solo aporta cuando se sigue UN sector, asi
    # que por defecto no se muestra ninguna.
    wanted = set(trails_for or [])
    for i, row in enumerate(df.itertuples()):
        if getattr(row, "etf", None) not in wanted:
            continue
        trail_x = getattr(row, "estela_ratio", None) or []
        trail_y = getattr(row, "estela_momentum", None) or []
        if len(trail_x) > 1:
            fig.add_trace(
                go.Scatter(
                    x=trail_x, y=trail_y, mode="lines+markers",
                    line=dict(color=series_color(i), width=2, dash="dot"),
                    marker=dict(size=4, color=series_color(i)),
                    opacity=0.8, hoverinfo="skip", showlegend=False,
                )
            )

    # Las etiquetas alternan arriba y abajo: dos sectores en posiciones
    # parecidas escribirian su nombre uno encima del otro y quedarian ilegibles.
    ordered = df.sort_values("ratio").index
    positions = pd.Series("top center", index=df.index)
    positions.loc[ordered[1::2]] = "bottom center"

    fig.add_trace(
        go.Scatter(
            x=df["ratio"], y=df["momentum"], mode="markers+text",
            text=df["etf"], textposition=positions.tolist(),
            textfont=dict(size=10, color=p["text_secondary"]),
            marker=dict(
                size=13,
                color=[series_color(i) for i in range(len(df))],
                line=dict(width=1.5, color=p["surface"]),
            ),
            customdata=df[["sector", "cuadrante"]].to_numpy(),
            hovertemplate=(
                "%{customdata[0]}<br>Fuerza relativa: %{x:.2f}"
                "<br>Momentum: %{y:.2f}<br>%{customdata[1]}<extra></extra>"
            ),
            showlegend=False,
        )
    )

    fig.add_hline(y=100, line=dict(color=p["axis"], width=1))
    fig.add_vline(x=100, line=dict(color=p["axis"], width=1))

    fig = apply_layout(fig, height=height)
    fig.update_xaxes(title="Fuerza relativa frente al índice", showgrid=False,
                     title_font=dict(size=11, color=p["muted"]))
    fig.update_yaxes(title="Momentum de esa fuerza", showgrid=False,
                     title_font=dict(size=11, color=p["muted"]))
    return fig


def sector_treemap(df: pd.DataFrame, group_col: str = "gics_sector",
                   height: int = 480) -> go.Figure:
    """Mapa de superficie: tamaño por capitalización, color por rendimiento.

    Complementa al mapa de TradingView porque puede agrupar por **tipo de
    inversión**, dimensión que aquel no ofrece.
    """
    p = palette()
    needed = {group_col, "ticker", "market_cap", "ret_1d"}
    if df.empty or not needed.issubset(df.columns):
        return apply_layout(go.Figure(), height=height)

    data = df.dropna(subset=[group_col, "market_cap", "ret_1d"]).copy()
    data = data[data["market_cap"] > 0]
    if data.empty:
        return apply_layout(go.Figure(), height=height)

    data["ret_pct"] = data["ret_1d"] * 100
    span = float(data["ret_pct"].abs().quantile(0.95)) or 1.0

    fig = go.Figure(
        go.Treemap(
            labels=data["ticker"],
            parents=data[group_col],
            values=data["market_cap"],
            marker=dict(
                colors=data["ret_pct"], colorscale=DIVERGING_PERFORMANCE,
                cmid=0, cmin=-span, cmax=span,
                line=dict(width=2, color=p["surface"]),
            ),
            texttemplate="%{label}<br>%{color:+.1f}%",
            textfont=dict(size=11),
            hovertemplate="%{label}<br>%{color:+.2f}%<extra></extra>",
            branchvalues="remainder",
            tiling=dict(pad=2),
        )
    )
    fig.update_layout(
        height=height, margin=dict(l=4, r=4, t=8, b=4),
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT_FAMILY, color=p["text_secondary"]),
    )
    return fig


def correlation_line(df: pd.DataFrame, height: int = 260) -> go.Figure:
    """Correlación media entre pares.

    Cuando sube, el mercado se mueve en bloque por razones macro y elegir
    valores concretos aporta poco: casi todo sube o baja junto.
    """
    p = palette()
    if df.empty or "avg_pairwise_corr" not in df.columns:
        return apply_layout(go.Figure(), height=height)

    data = df.dropna(subset=["avg_pairwise_corr"])
    if data.empty:
        return apply_layout(go.Figure(), height=height)

    fig = go.Figure(
        go.Scatter(
            x=pd.to_datetime(data["date"]), y=data["avg_pairwise_corr"],
            mode="lines", name="Correlación media",
            line=dict(color=series_color(0), width=2),
            hovertemplate="%{x|%d %b %Y}<br>Correlación media: %{y:.2f}<extra></extra>",
        )
    )
    fig.add_hline(
        y=0.5, line=dict(color=p["axis"], width=1, dash="dot"),
        annotation_text="mercado en bloque", annotation_position="top left",
        annotation_font=dict(size=10, color=p["muted"]),
    )
    fig = apply_layout(fig, height=height, hovermode="x unified")
    fig.update_yaxes(range=[-0.2, 1.0])
    return fig


def macro_series(df: pd.DataFrame, title: str, zero_line: bool = False,
                 mark_negative: bool = False, height: int = 240) -> go.Figure:
    """Serie macro simple.

    `zero_line` dibuja la referencia del cero. `mark_negative` marca además los
    tramos por debajo, y solo se activa donde ese tramo significa algo concreto
    (la curva de tipos invertida). Aplicarlo a cualquier serie que cruce el cero
    llena el gráfico de puntos rojos que no dicen nada.
    """
    p = palette()
    if df.empty:
        return apply_layout(go.Figure(), height=height)

    fig = go.Figure(
        go.Scatter(
            x=pd.to_datetime(df["date"]), y=df["value"], mode="lines",
            line=dict(color=series_color(0), width=2), name=title,
            hovertemplate="%{x|%b %Y}<br>%{y:.2f}<extra></extra>",
        )
    )
    if zero_line:
        fig.add_hline(y=0, line=dict(color=p["axis"], width=1, dash="dash"))
    if mark_negative:
        negative = df[df["value"] < 0]
        if not negative.empty:
            fig.add_trace(
                go.Scatter(
                    x=pd.to_datetime(negative["date"]), y=negative["value"],
                    mode="markers", marker=dict(size=3, color=STATUS["critical"]),
                    name="Curva invertida", hoverinfo="skip",
                )
            )
    fig = apply_layout(fig, height=height, hovermode="x unified")
    fig.update_layout(title=dict(text=title, font=dict(size=12, color=p["text_secondary"]),
                                 x=0, xanchor="left"))
    return fig


def score_distribution(scores: pd.Series, highlight: float | None = None,
                       height: int = 200) -> go.Figure:
    """Donde cae un valor dentro de la distribución de scores del universo."""
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

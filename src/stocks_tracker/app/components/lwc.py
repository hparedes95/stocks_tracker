"""Gráficos con `lightweight-charts` de TradingView, sobre NUESTROS datos.

Es lo que ningún widget embebido puede dar: los widgets son cajas negras con
datos de TradingView y no admiten que dibujemos nada encima. Aquí podemos
superponer nuestras señales, nuestros niveles y nuestras medias.

El JS va **vendorizado** en `app/static/` (Apache 2.0) en lugar de cargarse de
un CDN: así los gráficos funcionan sin conexion, que es justo la limitación que
tienen los widgets.

Notas de la API v5 (todos los tutoriales que circulan siguen en v4):

- `chart.addSeries(LightweightCharts.CandlestickSeries, {...})`.
  `addCandlestickSeries()` YA NO EXISTE.
- `LightweightCharts.createSeriesMarkers(series, markers)`.
  `series.setMarkers()` YA NO EXISTE: los marcadores son un primitive.
- **El `time` de un marcador debe coincidir EXACTAMENTE con el de un punto de
  la serie; si no, se descarta en silencio.** Es el fallo más probable de este
  módulo, y por eso todo pasa por `to_lwc_time()` y `snap_to_sessions()`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd
import streamlit as st

from .theme import STATUS, palette, series_color

_STATIC = Path(__file__).resolve().parents[1] / "static"
_JS_FILE = _STATIC / "lightweight-charts.standalone.production.js"


@lru_cache(maxsize=1)
def _library_js() -> str:
    """La librería, leida una sola vez.

    Se incrusta en cada iframe. Son ~190 KB por gráfico, que en localhost no
    viaja por ninguna red: es el precio de no depender de un CDN externo.
    """
    if not _JS_FILE.exists():
        raise FileNotFoundError(
            f"Falta la librería vendorizada en {_JS_FILE}. "
            "Descargala del paquete npm 'lightweight-charts'."
        )
    return _JS_FILE.read_text(encoding="utf-8")


def library_available() -> bool:
    return _JS_FILE.exists()


# --------------------------------------------------------------------------
# Tipos
# --------------------------------------------------------------------------
@dataclass
class Marker:
    time: str
    position: str = "belowBar"      # aboveBar | belowBar | inBar
    color: str = "#0ca30c"
    shape: str = "arrowUp"          # arrowUp | arrowDown | circle | square
    text: str = ""


@dataclass
class PriceLine:
    price: float
    color: str = "#898781"
    title: str = ""
    lineStyle: int = 2              # 0 solido, 1 puntos, 2 discontinuo
    lineWidth: int = 1


@dataclass
class Pane:
    """Panel inferior (RSI, MACD, volumen)."""
    name: str
    series: dict[str, list[dict]] = field(default_factory=dict)
    kind: str = "line"              # line | histogram
    height: int = 110
    levels: list[float] = field(default_factory=list)


# --------------------------------------------------------------------------
# Conversion de fechas: el punto delicado
# --------------------------------------------------------------------------
def to_lwc_time(value) -> str | None:
    """Convierte una fecha al formato que espera la librería: 'YYYY-MM-DD'.

    Se usa la MISMA función para los datos y para los marcadores. Si cada uno
    usara su formato, los marcadores no encontrarían su punto y desaparecerían
    sin ningún error.
    """
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, str):
        try:
            value = pd.Timestamp(value)
        except (ValueError, TypeError):
            return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def snap_to_sessions(target, sessions: list[str]) -> str | None:
    """Lleva una fecha a la sesión disponible más cercana hacia atrás.

    Una señal puede caer en un día sin cotización (festivo, o una fecha que la
    ingesta no trajo). Sin este ajuste el marcador se descartaría en silencio y
    la señal simplemente no aparaceria en el gráfico.
    """
    stamp = to_lwc_time(target)
    if stamp is None or not sessions:
        return None
    if stamp in set(sessions):
        return stamp
    earlier = [s for s in sessions if s <= stamp]
    return earlier[-1] if earlier else None


# Senales que marcan un cambio de estado y merecen ocupar sitio en el grafico.
# Las que quedan fuera (MACD_BULL_CROSS, BB_SQUEEZE, VOLUME_SPIKE) se disparan
# cada pocas sesiones: dibujarlas todas llena el grafico y no deja leer ninguna.
SIGNIFICANT_SIGNALS = frozenset(
    {
        "GOLDEN_CROSS", "DEATH_CROSS", "NEW_DOWNTREND",
        "HIGH_52W_BREAKOUT", "LOW_52W_BREAKDOWN",
        "PULLBACK_IN_UPTREND", "RSI_OVERSOLD_REVERSAL",
    }
)

# Por encima de este numero, los marcadores dejan de llevar texto: se solapan
# entre si y el resultado es ilegible.
MAX_LABELLED_MARKERS = 10
MAX_MARKERS = 24


def markers_from_signals(
    signals: pd.DataFrame,
    sessions: list[str],
    labels: dict[str, str] | None = None,
    only_significant: bool = True,
    max_markers: int = MAX_MARKERS,
) -> list[Marker]:
    """Marcadores a partir de la tabla de señales, alineados con la serie.

    Se filtra y se limita a propósito. Un año de histórico produce del orden de
    ochenta señales; pintarlas todas con su etiqueta tapa las velas y convierte
    el gráfico en ruido. Se conservan las más recientes de las señales que
    indican cambio de estado, y solo llevan texto si son pocas.
    """
    if signals is None or signals.empty or not sessions:
        return []

    labels = labels or {}
    session_set = set(sessions)
    data = signals.copy()

    if only_significant and "signal_id" in data.columns:
        filtered = data[data["signal_id"].isin(SIGNIFICANT_SIGNALS)]
        # Si el filtro se lo lleva todo, es mejor mostrar algo que nada.
        data = filtered if not filtered.empty else data

    if "date" in data.columns:
        data = data.sort_values("date")
    if len(data) > max_markers:
        data = data.tail(max_markers)

    show_text = len(data) <= MAX_LABELLED_MARKERS
    out: list[Marker] = []

    for row in data.itertuples():
        stamp = snap_to_sessions(getattr(row, "date", None), sessions)
        if stamp is None or stamp not in session_set:
            continue
        signal_id = str(getattr(row, "signal_id", ""))
        text = labels.get(signal_id, signal_id) if show_text else ""
        direction = str(getattr(row, "direction", "neutral"))

        if direction == "bullish":
            out.append(Marker(stamp, "belowBar", STATUS["good"], "arrowUp", text))
        elif direction == "bearish":
            out.append(Marker(stamp, "aboveBar", STATUS["critical"], "arrowDown", text))
        else:
            out.append(Marker(stamp, "inBar", STATUS["warning"], "circle", text))
    return out


# --------------------------------------------------------------------------
# Preparacion de datos
# --------------------------------------------------------------------------
def _candles(ohlcv: pd.DataFrame) -> tuple[list[dict], list[str]]:
    rows, sessions = [], []
    for row in ohlcv.itertuples():
        stamp = to_lwc_time(getattr(row, "date", None))
        if stamp is None:
            continue
        try:
            candle = {
                "time": stamp,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
            }
        except (TypeError, ValueError):
            continue
        if any(v != v for v in candle.values() if isinstance(v, float)):
            continue
        rows.append(candle)
        sessions.append(stamp)
    return rows, sessions


def _line_points(dates, values) -> list[dict]:
    out = []
    for d, v in zip(dates, values, strict=False):
        stamp = to_lwc_time(d)
        if stamp is None or v is None:
            continue
        try:
            value = float(v)
        except (TypeError, ValueError):
            continue
        if value != value:
            continue
        out.append({"time": stamp, "value": value})
    return out


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------
_TEMPLATE = """
<div id="chart" style="width:100%;height:{height}px"></div>
<script>{library}</script>
<script>
const CFG = {config};
const el = document.getElementById('chart');

const chart = LightweightCharts.createChart(el, {{
  layout: {{
    background: {{ type: 'solid', color: CFG.colors.surface }},
    textColor: CFG.colors.text,
    fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
    fontSize: 11,
    panes: {{ separatorColor: CFG.colors.grid, separatorHoverColor: CFG.colors.axis }},
  }},
  grid: {{
    vertLines: {{ visible: false }},
    horzLines: {{ color: CFG.colors.grid }},
  }},
  rightPriceScale: {{ borderColor: CFG.colors.axis, scaleMargins: {{ top: 0.12, bottom: 0.12 }} }},
  timeScale: {{ borderColor: CFG.colors.axis, rightOffset: 4, fixLeftEdge: true }},
  crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
  localization: {{ locale: 'es-ES' }},
  autoSize: true,
}});

// API v5: addSeries(TipoDeSerie, opciones). addCandlestickSeries() ya no existe.
let mainSeries;
if (CFG.candles && CFG.candles.length) {{
  mainSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {{
    upColor: CFG.colors.up, downColor: CFG.colors.down,
    borderUpColor: CFG.colors.up, borderDownColor: CFG.colors.down,
    wickUpColor: CFG.colors.up, wickDownColor: CFG.colors.down,
  }});
  mainSeries.setData(CFG.candles);
}}

(CFG.overlays || []).forEach(function (ov) {{
  const s = chart.addSeries(LightweightCharts.LineSeries, {{
    color: ov.color, lineWidth: 2, title: ov.name,
    priceLineVisible: false, lastValueVisible: false,
  }});
  s.setData(ov.data);
}});

// API v5: los marcadores son un primitive independiente.
if (mainSeries && CFG.markers && CFG.markers.length) {{
  LightweightCharts.createSeriesMarkers(mainSeries, CFG.markers);
}}

if (mainSeries) {{
  (CFG.priceLines || []).forEach(function (pl) {{
    mainSeries.createPriceLine({{
      price: pl.price, color: pl.color, lineWidth: pl.lineWidth,
      lineStyle: pl.lineStyle, axisLabelVisible: true, title: pl.title,
    }});
  }});
}}

(CFG.panes || []).forEach(function (pane, i) {{
  const paneIndex = i + 1;
  Object.keys(pane.series).forEach(function (name, j) {{
    const type = pane.kind === 'histogram'
      ? LightweightCharts.HistogramSeries : LightweightCharts.LineSeries;
    const s = chart.addSeries(type, {{
      color: pane.colors[j], lineWidth: 2, title: name,
      priceLineVisible: false, lastValueVisible: false,
    }}, paneIndex);
    s.setData(pane.series[name]);
    (pane.levels || []).forEach(function (level) {{
      s.createPriceLine({{
        price: level, color: CFG.colors.axis, lineWidth: 1,
        lineStyle: 1, axisLabelVisible: false, title: '',
      }});
    }});
  }});
  const panes = chart.panes();
  if (panes[paneIndex]) {{ panes[paneIndex].setHeight(pane.height); }}
}});

chart.timeScale().fitContent();
</script>
"""


def _colors() -> dict:
    p = palette()
    return {
        "surface": p["surface"],
        "text": p["text_secondary"],
        "grid": p["grid"],
        "axis": p["axis"],
        "up": STATUS["good"],
        "down": STATUS["critical"],
    }


def price_chart(
    ohlcv: pd.DataFrame,
    overlays: dict[str, pd.Series] | None = None,
    markers: list[Marker] | None = None,
    price_lines: list[PriceLine] | None = None,
    panes: list[Pane] | None = None,
    height: int = 460,
    key: str = "",
) -> bool:
    """Velas con nuestras medias, señales y niveles. Devuelve False si no hay datos."""
    if ohlcv is None or ohlcv.empty or not library_available():
        return False

    candles, sessions = _candles(ohlcv)
    if not candles:
        return False

    overlay_payload = []
    for i, (name, series) in enumerate((overlays or {}).items()):
        aligned = series.reindex(pd.to_datetime(ohlcv["date"]).to_numpy()) \
            if isinstance(series.index, pd.DatetimeIndex) else series
        points = _line_points(ohlcv["date"], aligned.to_numpy() if hasattr(aligned, "to_numpy") else aligned)
        if points:
            overlay_payload.append({"name": name, "color": series_color(i), "data": points})

    # Solo se conservan los marcadores cuya fecha existe en la serie: la
    # libreria descarta los demas sin avisar, y un marcador ausente es peor que
    # ninguno porque nadie se entera de que falta.
    session_set = set(sessions)
    marker_payload = [
        asdict(m) for m in sorted(markers or [], key=lambda m: m.time)
        if m.time in session_set
    ]

    pane_payload = []
    for pane in panes or []:
        colors = [series_color(i) for i in range(len(pane.series))]
        pane_payload.append(
            {"name": pane.name, "series": pane.series, "kind": pane.kind,
             "height": pane.height, "levels": pane.levels, "colors": colors}
        )

    config = {
        "colors": _colors(),
        "candles": candles,
        "overlays": overlay_payload,
        "markers": marker_payload,
        "priceLines": [asdict(pl) for pl in (price_lines or [])],
        "panes": pane_payload,
    }

    html = _TEMPLATE.format(
        height=height, library=_library_js(), config=json.dumps(config)
    )
    st.iframe(html, height=height + 8, width="stretch")
    return True


def equity_chart(curves: dict[str, pd.Series], height: int = 340, key: str = "") -> bool:
    """Varias curvas comparables (backtest, cartera frente a referencia)."""
    if not curves or not library_available():
        return False

    payload = []
    for i, (name, series) in enumerate(curves.items()):
        if series is None or series.empty:
            continue
        points = _line_points(series.index, series.to_numpy())
        if points:
            payload.append({"name": name, "color": series_color(i), "data": points})

    if not payload:
        return False

    config = {
        "colors": _colors(), "candles": [], "overlays": payload,
        "markers": [], "priceLines": [], "panes": [],
    }
    html = _TEMPLATE.format(
        height=height, library=_library_js(), config=json.dumps(config)
    )
    st.iframe(html, height=height + 8, width="stretch")
    return True


def series_to_points(dates, values) -> list[dict]:
    """Ayuda para construir paneles desde las páginas."""
    return _line_points(dates, values)


def sessions_of(ohlcv: pd.DataFrame) -> list[str]:
    """Fechas de las velas, en el formato de la librería.

    Es lo que hay que pasar a `markers_from_signals` para que cada marcador
    caiga sobre un punto existente.
    """
    if ohlcv is None or ohlcv.empty:
        return []
    _, sessions = _candles(ohlcv)
    return sessions


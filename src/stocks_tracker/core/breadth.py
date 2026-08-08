"""Amplitud de mercado: la salud interna detras del indice.

Un indice puede subir sostenido por cuatro valores enormes mientras el resto
cae. La amplitud es lo que revela esa diferencia, y es de lo poco que avisa de
un deterioro antes de que se vea en el precio del indice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_breadth(indicators: pd.DataFrame, scope: str) -> pd.DataFrame:
    """Serie diaria de amplitud para un conjunto de valores.

    `indicators` es el DataFrame largo (ticker, date, ...) de todos los valores
    del ambito. Devuelve una fila por fecha.
    """
    if indicators.empty:
        return pd.DataFrame()

    df = indicators.copy()
    df["date"] = pd.to_datetime(df["date"])

    grouped = df.groupby("date")
    rows = []

    for dt, g in grouped:
        n = len(g)
        above200 = g["above_sma200"].astype("boolean")
        above50 = g["above_sma50"].astype("boolean")
        ret = pd.to_numeric(g.get("ret_1d"), errors="coerce")
        rsi = pd.to_numeric(g.get("rsi14"), errors="coerce")
        dist_high = pd.to_numeric(g.get("dist_52w_high"), errors="coerce")
        dist_low = pd.to_numeric(g.get("dist_52w_low"), errors="coerce")

        rows.append(
            {
                "date": dt.date(),
                "scope": scope,
                "n_constituents": n,
                "pct_above_sma50": float(above50.mean() * 100) if above50.notna().any() else np.nan,
                "pct_above_sma200": float(above200.mean() * 100) if above200.notna().any() else np.nan,
                "advances": int((ret > 0).sum()),
                "declines": int((ret < 0).sum()),
                "new_highs_52w": int((dist_high >= -0.002).sum()),
                "new_lows_52w": int((dist_low <= 0.002).sum()),
                "pct_rsi_overbought": float((rsi > 70).mean() * 100) if rsi.notna().any() else np.nan,
                "pct_rsi_oversold": float((rsi < 30).mean() * 100) if rsi.notna().any() else np.nan,
                "median_ret_1d": float(ret.median()) if ret.notna().any() else np.nan,
                "median_ret_1m": float(
                    pd.to_numeric(g.get("roc_1m"), errors="coerce").median()
                ),
            }
        )

    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    # Linea avance-descenso acumulada: su divergencia con el indice es el aviso
    # clasico de que la subida se esta quedando sin participantes.
    out["ad_line"] = (out["advances"] - out["declines"]).cumsum().astype(float)
    return out


def sector_performance(
    indicators: pd.DataFrame, instruments: pd.DataFrame, as_of: pd.Timestamp
) -> pd.DataFrame:
    """Rendimiento por sector a varios horizontes, en la fecha dada.

    Se usa la MEDIANA, no la media: es robusta a que un valor se dispare por una
    OPA y arrastre a todo el sector en la tabla.
    """
    if indicators.empty or instruments.empty:
        return pd.DataFrame()

    snap = indicators[pd.to_datetime(indicators["date"]) == pd.to_datetime(as_of)].copy()
    if snap.empty:
        return pd.DataFrame()

    merged = snap.merge(
        instruments[["ticker", "gics_sector"]], on="ticker", how="left"
    )
    merged = merged[merged["gics_sector"].notna() & (merged["gics_sector"] != "")]
    if merged.empty:
        return pd.DataFrame()

    horizons = {
        "ret_1d": "1 dia",
        "ret_5d": "1 semana",
        "roc_1m": "1 mes",
        "roc_3m": "3 meses",
        "roc_6m": "6 meses",
        "roc_12m": "12 meses",
    }
    agg = {col: "median" for col in horizons if col in merged.columns}
    agg["ticker"] = "count"

    out = merged.groupby("gics_sector").agg(agg).reset_index()
    out = out.rename(columns={"ticker": "n_valores"})

    if "above_sma200" in merged.columns:
        pct = (
            merged.groupby("gics_sector")["above_sma200"]
            .apply(lambda s: float(pd.Series(s).astype("boolean").mean() * 100))
            .reset_index(name="pct_above_sma200")
        )
        out = out.merge(pct, on="gics_sector", how="left")

    return out.sort_values("ret_1d", ascending=False).reset_index(drop=True)

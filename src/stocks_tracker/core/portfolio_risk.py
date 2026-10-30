"""Riesgo conjunto y escenarios explícitos, no pérdidas máximas prometidas."""
from __future__ import annotations

import numpy as np
import pandas as pd


def analyse(returns: pd.DataFrame, values: dict[str, float], *, cash=0.0,
            currencies: dict[str, str] | None = None, equity_override=None) -> dict:
    if cash < 0 or not np.isfinite(cash):
        raise ValueError("Efectivo inválido.")
    if any(not np.isfinite(v) or v < 0 for v in values.values()):
        return {"reason": "No se puede valorar toda la cartera; riesgo conjunto no disponible."}
    total = sum(values.values()) + cash if equity_override is None else equity_override
    if not np.isfinite(total) or total <= 0:
        return {"reason": "Añade posiciones y efectivo para evaluar el riesgo conjunto."}
    weights = pd.Series(values, dtype=float) / total
    exposure = {}
    for ticker, weight in weights.items():
        curr = (currencies or {}).get(ticker, "desconocida")
        exposure[curr] = exposure.get(curr, 0.0) + float(weight)
    exposure['EUR'] = exposure.get('EUR', 0.) + cash / total
    result = {"equity": total, "currency_exposure": exposure,
              "shock_equity_30_eur": -sum(values.values()) * .30,
              "shock_foreign_fx_10_eur": -sum(v for k, v in values.items()
                          if (currencies or {}).get(k) not in (None, "EUR")) * .10,
              "reason": ""}
    if not set(values).issubset(returns.columns):
        result['reason'] = "Falta histórico en EUR de alguna posición; solo se muestran escenarios."
        return result
    sample = returns[list(values)].replace([np.inf, -np.inf], np.nan).dropna().tail(252)
    if len(sample) < 60:
        result['reason'] = "Menos de 60 sesiones comunes; no se estima correlación ni riesgo histórico."
        return result
    joint = sample.mul(weights).sum(axis=1)
    path = pd.concat([pd.Series([1.]), (1 + joint).cumprod().reset_index(drop=True)], ignore_index=True)
    q = joint.quantile(.05)
    result.update({"sessions": len(sample), "correlations": sample.corr(),
                   "annual_volatility": float(joint.std() * np.sqrt(returns.attrs.get('annual_periods', 252))),
                   "historical_drawdown": float((path / path.cummax() - 1).min()),
                   "mean_worst_5pct_day_eur": float(joint[joint <= q].mean() * total)})
    return result


def returns_in_eur(conn, tickers: list[str], *, as_of) -> pd.DataFrame:
    """No rellena huecos de activos. FX arrastrado como máximo siete días."""
    from . import fx
    if not tickers:
        return pd.DataFrame()
    instruments = conn.execute("SELECT ticker,currency,asset_class,country,exchange FROM instruments").fetchdf()
    metadata = instruments.set_index('ticker').to_dict('index')
    currencies = dict(zip(instruments.ticker, instruments.currency, strict=False))
    frame = conn.execute("SELECT p.ticker,p.date,p.close,p.adj_close,p.source,q.date IS NOT NULL AS quarantined "
                         "FROM prices_daily p LEFT JOIN prices_quarantine q ON q.ticker=p.ticker AND q.date=p.date "
                         "WHERE p.date <= ? ORDER BY p.ticker,p.date", [as_of]).fetchdf()
    if frame.empty:
        return pd.DataFrame()
    frame['date'] = pd.to_datetime(frame['date'])
    rates = fx.tipos_en(conn, sorted(frame['date'].unique()))
    prices = {}
    for ticker in tickers:
        p = frame[frame.ticker == ticker].copy()
        if (p.empty or p.quarantined.tail(253).any() or p.source.isna().any() or
                p.source.astype(str).str.strip().str.lower().isin(['synthetic', '']).any() or
                p.source.nunique() != 1 or not np.isfinite(p.adj_close).all() or
                (p.adj_close <= 0).any()):
            continue
        p = p.reset_index(drop=True)
        curr = currencies.get(ticker)
        if not curr:
            continue
        converted = fx.a_base_en_fecha(p.adj_close, pd.Series([curr] * len(p)), p.date, rates)
        if curr != 'EUR':
            r = frame[frame.ticker == fx.PARES.get(curr)]
            if r.empty:
                continue
            matched = pd.merge_asof(p[['date']].astype({'date': 'datetime64[ns]'}),
                r[['date', 'close', 'source']].rename(columns={'date': 'fx_date'}).astype({'fx_date': 'datetime64[ns]'}),
                left_on='date', right_on='fx_date', direction='backward', tolerance=pd.Timedelta(days=7))
            invalid = (matched.fx_date.isna() | matched.source.isna() |
                       matched.source.astype(str).str.strip().str.lower().isin(['synthetic', '']) |
                       ~np.isfinite(matched.close) | (matched.close <= 0))
            converted[invalid] = np.nan
        prices[ticker] = pd.Series(converted.to_numpy(), index=p.date)
    crypto = {t for t in prices if metadata[t]['asset_class'] == 'crypto' or t in ('BTC-USD', 'ETH-USD')}
    securities = set(prices) - crypto
    grid = None
    if crypto and securities:
        from .trading_calendar import calendar_name, sessions
        calendars = {calendar_name(metadata[t]['country'], metadata[t]['exchange']) for t in securities}
        if None in calendars:
            # A missing calendar is not permission to compare a 24h crypto
            # return with a stock return accumulated over a weekend.
            prices = {t: p for t, p in prices.items() if t not in crypto}
        else:
            for name in sorted(calendars):
                dates = sessions(name, frame.date.min(), frame.date.max())
                grid = dates if grid is None else grid.intersection(dates)
    output = {}
    for ticker, series in prices.items():
        sampled = series.reindex(grid) if grid is not None else series
        returns = sampled.pct_change(fill_method=None)
        returns.loc[sampled.index.to_series().diff().dt.days.to_numpy() > 7] = np.nan
        output[ticker] = returns
    result = pd.DataFrame(output).sort_index()
    result.attrs['annual_periods'] = 365 if crypto and not securities else 252
    return result

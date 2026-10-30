"""Current holding research, independent of purchase dates and order eligibility.

Reads the current book and dated market observations. Does not write trades,
recommendations or the forward scoreboard. The interpretable trend rules are
research heuristics, not a claim of validated predictive advantage.
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from . import snaptrade_portfolio as snap
from .broker_symbols import research_target
from .flags import red_flags
from .forecast import scenarios
from .indicators import rsi
from .trading_calendar import calendar_name, check_history


def fundamental_metrics(fund):
    """Warehouse margins/growth are fractions; P/E and leverage are multiples."""
    fields = (('trailing_pe', 'PER (veces)', 1),
              ('profit_margin', 'Margen neto (%)', 100),
              ('revenue_growth_yoy', 'Crecimiento de ingresos (%)', 100),
              ('net_debt_to_ebitda', 'Deuda neta / EBITDA (veces)', 1))
    return {label: round(fund[key] * scale, 2) for key, label, scale in fields if key in fund}


def holdings(conn):
    """One row per actual position; never merge CFD and spot quantities."""
    if snap.active(conn):
        metadata = conn.execute('SELECT ticker,currency,exchange,mic FROM instruments').fetchdf()
        result = []
        for pos in json.loads(snap.state(conn)['snapshot'])['positions']:
            if pos['cash_equivalent'] or not pos['qty']:
                continue
            target = research_target(pos, metadata)
            result.append({'id': pos['id'], 'symbol': pos['symbol'], 'kind': pos['kind'],
                           'qty': pos['qty'], 'ticker': target.ticker,
                           'currency': target.currency, 'asset_class': target.asset_class,
                           'identity_note': target.reason})
        return result
    return conn.execute("SELECT p.id,p.ticker AS symbol,p.ticker,p.qty,"
                        "COALESCE(i.asset_class, 'equity') AS asset_class,"
                        "'spot' AS kind,p.currency,'' AS identity_note FROM positions p "
                        "LEFT JOIN instruments i ON i.ticker=p.ticker "
                        "WHERE p.closed_at IS NULL AND p.qty>0 ORDER BY p.ticker,p.id").fetchdf().to_dict('records')


def _market(conn, ticker, cutoff):
    # Keep sufficient observations for independent historical scenarios; bounded
    # to 30 years and only the user's positions, not the entire scoring universe.
    frame = conn.execute('SELECT p.date,p.adj_close,p.source,q.date IS NOT NULL AS quarantined '
                        'FROM prices_daily p LEFT JOIN prices_quarantine q '
                        'ON q.ticker=p.ticker AND q.date=p.date '
                        'WHERE p.ticker=? AND p.date<=? AND p.date>=? ORDER BY p.date',
                        [ticker, cutoff.date(), (cutoff - pd.DateOffset(years=30)).date()]).fetchdf()
    meta = conn.execute('SELECT country,exchange,mic,asset_class FROM instruments WHERE ticker=?', [ticker]).fetchone()
    calendar = calendar_name(meta[0], meta[2] or meta[1]) if meta else None
    if meta and meta[3] == 'crypto' or ticker in ('BTC-USD', 'ETH-USD'):
        calendar = '24/7'
    if ticker in ('CIBR', 'ITA'):
        calendar = 'XNYS'
    frame.attrs['calendar'] = calendar
    return frame


def _technical(prices, cutoff):
    """Validate the analysis window before deriving any opinion, no stale fallback."""
    if prices.empty:
        return {}, 'No hay histórico descargado. Pulsa Actualizar datos y análisis.'
    window = prices.tail(253).copy()
    last = pd.Timestamp(window.date.iloc[-1])
    if (cutoff - last).days > 7:
        return {}, f'Precios desactualizados: último cierre {last.date()}. Actualiza los datos.'
    sources = set(window.source.fillna('').astype(str).str.strip().str.lower())
    if len(sources) != 1 or '' in sources or any('synthetic' in s for s in sources):
        return {}, 'Procedencia ausente, sintética o mezclada: no se emite una opinión.'
    if window.quarantined.any():
        return {}, 'Hay precios en cuarentena en la ventana de análisis; revisa la calidad.'
    values = pd.to_numeric(window.adj_close, errors='coerce')
    if not np.isfinite(values).all() or (values <= 0).any():
        return {}, 'Hay precios ajustados inválidos; no se emite una opinión.'
    dates = pd.to_datetime(window.date)
    if dates.duplicated().any() or dates.diff().dt.days.dropna().gt(7).any():
        return {}, 'El histórico tiene sesiones duplicadas o huecos de más de siete días.'
    if prices.attrs.get('calendar'):
        issue = check_history(window, prices.attrs['calendar'])['reason']
        if issue:
            return {}, issue
    if len(values) < 200:
        return {}, f'Solo hay {len(values)} cierres; hacen falta 200 para analizar la tendencia.'
    latest = float(values.iloc[-1])
    return {'adjusted_close': latest, 'sma50': float(values.tail(50).mean()),
            'sma200': float(values.tail(200).mean()),
            'momentum_3m_pct': float((latest / values.iloc[-64] - 1) * 100),
            'rsi14': float(rsi(values).iloc[-1]),
            'drawdown_pct': float((latest / values.max() - 1) * 100),
            'daily_vol_pct': float(values.pct_change().dropna().tail(60).std() * 100)}, ''


def _opinion(metrics, *, short=False):
    close, fast, slow = (metrics[k] for k in ('adjusted_close', 'sma50', 'sma200'))
    momentum = metrics['momentum_3m_pct']
    reasons = [f'Cierre ajustado {close:.2f}; media de 50 sesiones {fast:.2f} '
               f'y de 200 sesiones {slow:.2f}.',
               f'Retorno ajustado a 63 sesiones: {momentum:+.2f} %.']
    if close < slow and fast < slow and momentum < 0:
        stance = 'Revisar reducción; no ampliar'
        reasons.append('Precio y media de 50 bajo la media de 200, con momentum negativo: debilidad coincidente.')
    elif close > fast > slow and momentum > 0:
        stance = 'Mantener en vigilancia'
        reasons.append('Tendencia y momentum positivos; esto no demuestra que esté barato ni justifica ampliar por sí solo.')
    else:
        stance = 'Vigilar; no ampliar por tendencia'
        reasons.append('Las señales de tendencia y momentum no coinciden; no hay una dirección técnica clara.')
    if metrics['rsi14'] >= 70:
        reasons.append('RSI de 14 sesiones elevado: evita perseguir la subida sin revisar valoración y riesgo.')
    if metrics['drawdown_pct'] <= -20:
        reasons.append('Caída de al menos 20 % desde el máximo de la ventana: revisa la tesis y la exposición.')
    if short:
        stance = 'Revisar posición corta por separado'
        reasons.append('La lectura anterior describe el subyacente al alza/baja, no la conveniencia del contrato corto.')
    return stance, reasons


def analyse(conn, *, as_of=None, horizon=126):
    """Dated, read-only reports. Missing data on one holding cannot hide others."""
    cutoff = pd.Timestamp(as_of or date.today()).normalize()
    result = []
    # Varios lotes/contratos pueden referirse al mismo subyacente. Reutilizar
    # solo las observaciones de esta lectura, nunca cantidades ni opiniones.
    markets, technicals, funds, forecasts, unsafe_history = {}, {}, {}, {}, {}
    account_issue = snap.readiness(conn) if snap.active(conn) else ''
    for pos in holdings(conn):
        report = {**pos, 'cutoff': str(cutoff.date()), 'price_date': None, 'source': None,
                  'stance': 'Pendiente de datos', 'metrics': {}, 'reasons': [],
                  'limitations': [], 'fundamentals': {}, 'scenario': None,
                  'review_if': []}
        result.append(report)
        if account_issue:
            report['limitations'].append(account_issue)
        if pos['kind'] == 'cfd':
            report['limitations'].append('Se analiza la referencia del subyacente, no la rentabilidad del CFD. '
                'Su exposición y el encaje de otras compras se comprueban aparte con los datos del bróker. '
                'Este análisis no estima margen, financiación ni pérdida máxima del contrato.')
        if not pos['ticker']:
            report['limitations'].append(pos['identity_note'] or 'No se identifica el instrumento con seguridad.')
            continue
        ticker = pos['ticker']
        if ticker not in markets:
            markets[ticker] = _market(conn, ticker, cutoff)
            technicals[ticker] = _technical(markets[ticker], cutoff)
            unsafe_history[ticker] = (markets[ticker].quarantined.any() or
                                     any('synthetic' in str(s).lower() for s in markets[ticker].source))
        prices = markets[ticker]
        if not prices.empty:
            report.update(price_date=str(pd.Timestamp(prices.date.iloc[-1]).date()),
                          source=str(prices.source.iloc[-1] or 'sin fuente'))
        metrics, issue = technicals[ticker]
        if issue:
            report['limitations'].append(issue)
            continue
        if not prices.attrs.get('calendar'):
            report['limitations'].append('Calendario bursátil no identificado: continuidad comprobada solo por huecos de fechas.')
        report['metrics'] = metrics.copy()
        report['stance'], report['reasons'] = _opinion(metrics, short=pos['qty'] < 0)
        if pos['kind'] == 'cfd':
            report['stance'] = {
                'Mantener en vigilancia': 'Subyacente favorable; vigilar el CFD',
                'Revisar reducción; no ampliar': 'Subyacente débil; revisar el CFD',
                'Vigilar; no ampliar por tendencia': 'Subyacente mixto; no ampliar por esta señal',
            }.get(report['stance'], report['stance'])
            report['limitations'].append('La debilidad del subyacente perjudica una exposición larga y favorece una corta; '
                'esta lectura de tendencia no se traduce automáticamente en vender el CFD.')
        report['review_if'] = [f'Revisar si el cierre cruza la media de 200 sesiones '
                               f'(hoy {metrics["sma200"]:.2f}; nivel móvil, no stop garantizado).',
                               'Revisar si cambia el signo del retorno a 63 sesiones.']
        if account_issue:
            report['stance'] = 'Lectura de mercado; cartera sin sincronizar'
        if pos['asset_class'] in ('equity', 'stock', 'adr'):
            if ticker not in funds:
                funds[ticker] = conn.execute('SELECT * FROM fundamentals_snapshot WHERE ticker=? AND as_of<=? '
                                            'ORDER BY as_of DESC LIMIT 1', [ticker, cutoff.date()]).fetchdf()
            fund = funds[ticker]
            if not fund.empty:
                f = fund.iloc[0]
                source = str(f.source or '').strip()
                age = (cutoff - pd.Timestamp(f.as_of)).days
                if age <= 90 and source and 'synthetic' not in source.lower():
                    report['fundamentals'] = {k: float(f[k]) for k in
                        ('trailing_pe', 'profit_margin', 'revenue_growth_yoy', 'net_debt_to_ebitda')
                        if pd.notna(f[k]) and np.isfinite(f[k])}
                    if report['fundamentals']:
                        report['fundamentals'].update(date=str(pd.Timestamp(f.as_of).date()), source=source)
                        flags = red_flags(f)
                        if flags:
                            report['reasons'].extend(flags)
                            if not account_issue and pos['qty'] > 0:
                                report['stance'] = 'Revisar riesgos fundamentales; no ampliar'
            if not report['fundamentals']:
                report['limitations'].append('Sin fundamentales recientes verificables; la lectura es técnica, no una valoración de la empresa.')
        else:
            report['limitations'].append('Análisis de precios; no se aplican márgenes o deuda de empresa a un ETF o una criptomoneda.')
        # A quarantined bar anywhere in the longer series invalidates scenarios,
        # without invalidating an otherwise clean recent technical window.
        if unsafe_history[ticker]:
            report['limitations'].append('Escenarios omitidos: hay precios en cuarentena o sintéticos en el histórico largo.')
        else:
            if ticker not in forecasts:
                forecasts[ticker] = scenarios(prices, horizon=horizon, as_of=cutoff)
            forecast = forecasts[ticker]
            if forecast.reason:
                report['limitations'].append('Escenarios: ' + forecast.reason)
            else:
                report['scenario'] = {'low_pct': forecast.low * 100, 'median_pct': forecast.median * 100,
                                      'high_pct': forecast.high * 100, 'episodes': forecast.observations,
                                      'horizon': horizon}
    return result

"""Current-position rules when the purchase-date comparison is unavailable.

Not a forecast of profit or a claim that a business thesis has broken.
Unknown contracts are never turned into spot trades. Risk alerts already
produced by the original policy take precedence.
"""
from dataclasses import replace
from datetime import date

import pandas as pd

from .advice import Conviccion, Veredicto
from .holding_analysis import _market, _technical


def complete(conn, recommendations, positions, *, as_of, horizon_months, decision_date=None):
    result = []
    fresh = 0 <= (pd.Timestamp(decision_date or date.today()) - pd.Timestamp(as_of)).days <= 7
    cache = {}
    for rec in recommendations:
        if rec.veredicto != Veredicto.SIN_OPINION or not fresh:
            result.append(rec)
            continue
        lots = positions[positions.ticker == rec.ticker]
        if (lots.empty or not (lots.qty > 0).all()
                or ('supported' in lots and not lots.supported.all())
                or ('analysis_supported' in lots and not lots.analysis_supported.all())):
            result.append(rec)
            continue
        if rec.ticker not in cache:
            prices = _market(conn, rec.ticker, pd.Timestamp(as_of))
            metrics, issue = _technical(prices, pd.Timestamp(as_of))
            cache[rec.ticker] = prices, metrics, issue
        prices, metrics, issue = cache[rec.ticker]
        if issue or not prices.attrs.get('calendar'):
            result.append(replace(rec, motivos=[issue or 'Calendario no identificado.']))
            continue
        weak = (metrics['adjusted_close'] < metrics['sma200']
                and metrics['sma50'] < metrics['sma200'] and metrics['momentum_3m_pct'] < 0)
        verdict = Veredicto.REDUCIR if weak else Veredicto.MANTENER
        reasons = [
            'Regla técnica de gestión de la posición actual, independiente de la fecha de compra.',
            f"Cierre ajustado {metrics['adjusted_close']:.2f}; media 50: {metrics['sma50']:.2f}; "
            f"media 200: {metrics['sma200']:.2f}; retorno a 63 sesiones: {metrics['momentum_3m_pct']:+.2f} %.",
            ('Tres señales de debilidad coinciden: se propone reducir exposición, no vender toda la posición.'
             if weak else 'No coinciden las tres señales de reducción; mantener en vigilancia no equivale a recomendar ampliar.'),
            'No demuestra deterioro del negocio ni anticipa el próximo precio. Revisa costes y fiscalidad antes de actuar.',
        ]
        result.append(replace(rec, veredicto=verdict, conviccion=Conviccion.BAJA,
            motivos=reasons, horizonte_meses=horizon_months,
            # No arbitrary "sell half" and no fabricated portfolio weights.
            titulos_a_soltar=None, limitado_por='regla técnica; sin importe de reducción',
            desmentiria=['La señal de reducción deja de cumplirse si el precio o la media de 50 '
                        'recuperan la media de 200, o el retorno de 63 sesiones pasa a ser positivo.'],
            assessment={'decision_basis': 'position_rules',
                        'datos': f"Cierre {prices.date.iloc[-1]}; fuente {prices.source.iloc[-1]}.",
                        'evidencia': 'Regla técnica no validada como ventaja predictiva.',
                        'encaje': 'No se calcula cuántos títulos vender; no se supone conocida la valoración global.'}))
    # Several lots must not write the same ticker twice.
    priority = {Veredicto.VENDER: 0, Veredicto.REDUCIR: 1,
                Veredicto.MANTENER: 2, Veredicto.SIN_OPINION: 3}
    unique = {}
    for rec in result:
        if rec.ticker not in unique or priority.get(rec.veredicto, 4) < priority.get(unique[rec.ticker].veredicto, 4):
            unique[rec.ticker] = rec
    return list(unique.values())

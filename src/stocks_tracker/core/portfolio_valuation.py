"""Explain unvalued positions without inventing prices, FX or CFD exposure."""
from __future__ import annotations

import math

import pandas as pd


def _finite(value, *, positive=False):
    try:
        return not isinstance(value, bool) and math.isfinite(float(value)) and (
            float(value) > 0 if positive else float(value) >= 0)
    except (TypeError, ValueError):
        return False


def issues(positions, rates):
    """Only valuation blockers, not every limitation of the predictive model."""
    rows = []
    if positions is None:
        positions = pd.DataFrame()
    for pos in positions.to_dict('records'):
        symbol = next((pos[k] for k in ('broker_symbol', 'ticker')
                       if isinstance(pos.get(k), str) and pos[k].strip()), 'Sin identificar')
        currency = pos.get('currency')
        currency = currency.strip().upper() if isinstance(currency, str) else ''
        reason, action = '', ''
        if not pos.get('supported', True):
            reason = pos.get('reason') or 'Producto o identidad no admitidos para valoración.'
            action = ('Un CFD necesita valoración y exposición del contrato; el precio del ETF no las sustituye.'
                      if 'cfd' in str(reason).lower() else
                      'Revisar producto, mercado y divisa informados por el bróker; no forzar un símbolo.')
        elif not _finite(pos.get('qty')):
            reason = 'Cantidad negativa o no válida; no se trata como una posición larga al contado.'
            action = 'Revisar las unidades y el tipo de posición en el origen.'
        elif not _finite(pos.get('close'), positive=True):
            reason = 'Falta un precio válido para esta posición.'
            action = 'Actualizar datos y análisis; revisar el símbolo y la última consulta del bróker.'
        elif currency != 'EUR' and not _finite(rates.get(currency), positive=True):
            reason = f'Falta un tipo de cambio vigente {currency or "desconocido"}/EUR.'
            action = 'Actualizar las divisas; no se aplica paridad ni un cambio antiguo.'
        elif 'valor_eur' in pos and not _finite(pos['valor_eur']):
            reason = 'El valor en EUR no es finito o no es válido.'
            action = 'Revisar cantidad, precio y divisa antes de calcular los pesos.'
        if reason:
            rows.append({'Símbolo':str(symbol), 'Divisa':currency or 'Desconocida',
                         'Motivo':reason, 'Qué falta resolver':action})
    return pd.DataFrame(rows, columns=['Símbolo', 'Divisa', 'Motivo', 'Qué falta resolver'])

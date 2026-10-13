"""Sensibilidad del valor a supuestos explícitos, no un precio objetivo."""
import math

import pandas as pd


def cashflow_sensitivity(free_cash_flow: float, market_cap: float) -> pd.DataFrame:
    if not all(math.isfinite(v) and v > 0 for v in (free_cash_flow, market_cap)):
        return pd.DataFrame()
    # Caja para el accionista aproximada por CFO-capex. No apto para bancos,
    # aseguradoras o negocios con caja anual atípica; los supuestos se muestran.
    rows = []
    for growth in (-.05, 0., .05, .10):
        for discount in (.08, .10, .12):
            cash = free_cash_flow
            present = 0.
            for year in range(1, 6):
                cash *= 1 + growth
                present += cash / (1 + discount) ** year
            terminal = cash * 1.02 / (discount - .02)
            value = present + terminal / (1 + discount) ** 5
            rows.append({'crecimiento_5_anos': growth, 'descuento': discount,
                         'crecimiento_terminal': .02, 'valor_hipotetico': value,
                         'diferencia_vs_capitalizacion': value / market_cap - 1})
    return pd.DataFrame(rows)

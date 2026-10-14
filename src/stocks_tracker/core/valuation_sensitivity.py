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


def verified_sensitivity(annual: pd.DataFrame, market_cap, *, currency, sector, as_of):
    """FCFE aproximado con entradas verificables; no resta otra vez deuda neta."""
    def missing(reason):
        return {'reason': reason, 'scenarios': pd.DataFrame(), 'normalized_cash': None}
    if (currency != 'USD' or not sector or str(sector).lower() in ('none', 'nan', '<na>') or
            any(s in str(sector).lower() for s in ('financ', 'real estate', 'reit', 'inmobili', 'insurance'))):
        return missing('Sector o moneda no compatibles con esta fórmula de caja al accionista.')
    if annual.empty or len(annual) < 3:
        return missing('Se necesitan tres ejercicios anuales comparables y trazables.')
    selected = annual.sort_values('fin').tail(3)
    if (pd.Timestamp(as_of) - pd.Timestamp(selected.fin.iloc[-1])).days > 550:
        return missing('Cuentas demasiado antiguas para esta sensibilidad.')
    if not selected.fin.diff().dropna().dt.days.between(330, 400).all():
        return missing('Los tres ejercicios no son consecutivos.')
    values = selected['caja_accionista_aproximada']
    if not values.map(lambda v: pd.notna(v) and math.isfinite(v) and v > 0).all():
        return missing('Faltan deuda emitida/amortizada, financiación corta o preferentes, '
                       'o hay caja no positiva. Una ausencia no se sustituye por cero.')
    cash = float(values.median())
    if values.max() > 3 * values.min():
        return missing('Caja plurianual inestable: revisar partidas extraordinarias antes de valorar.')
    try:
        scenarios = cashflow_sensitivity(cash, float(market_cap))
    except (TypeError, ValueError):
        return missing('Capitalización no disponible en USD.')
    if scenarios.empty:
        return missing('Capitalización no válida.')
    return {'reason': '', 'scenarios': scenarios, 'normalized_cash': cash}

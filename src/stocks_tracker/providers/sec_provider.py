"""Estados oficiales SEC/EDGAR: sin API key, con fecha de publicación preservada."""
from __future__ import annotations

import time
from datetime import date

import numpy as np
import pandas as pd
import requests

USER_AGENT = "StocksTracker/0.2 personal-research (https://github.com/hparedes95/stocks_tracker)"
CONCEPTS = {
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "NetIncomeLoss",
    "NetCashProvidedByUsedInOperatingActivities", "PaymentsToAcquirePropertyPlantAndEquipment",
    "LongTermDebtCurrent", "LongTermDebtNoncurrent", "LongTermDebt",
    "CashAndCashEquivalentsAtCarryingValue", "OperatingIncomeLoss", "InterestExpense",
    "PaymentsOfDividends", "PaymentsOfDividendsCommonStock", "CommonStockSharesOutstanding",
    "StockBasedCompensation",
}


def _get(url: str) -> dict:
    # Máximo cinco peticiones/s por este proceso, por debajo del límite SEC.
    time.sleep(.21)
    response = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                            timeout=(10, 45), stream=True)
    try:
        response.raise_for_status()
        payload = bytearray()
        for chunk in response.iter_content(65536):
            payload.extend(chunk)
            if len(payload) > 30 * 1024 * 1024:
                raise ValueError("Respuesta SEC demasiado grande.")
        import json
        return json.loads(payload)
    finally:
        response.close()


def ticker_map() -> dict[str, str]:
    rows = _get("https://www.sec.gov/files/company_tickers.json")
    return {str(r['ticker']).upper(): str(r['cik_str']).zfill(10) for r in rows.values()}


def parse_facts(payload: dict, *, ticker: str, cik: str) -> pd.DataFrame:
    rows = []
    for concept, body in payload.get('facts', {}).get('us-gaap', {}).items():
        if concept not in CONCEPTS:
            continue
        for unit, observations in body.get('units', {}).items():
            if unit not in ('USD', 'shares'):
                continue
            for item in observations:
                try:
                    end, filed = date.fromisoformat(item['end']), date.fromisoformat(item['filed'])
                    start = date.fromisoformat(item.get('start', '1900-01-01'))
                    value = float(item['val'])
                    accession = str(item['accn'])
                    if not np.isfinite(value) or filed < end or item['form'] not in ('10-K', '10-K/A', '10-Q', '10-Q/A'):
                        continue
                    if not accession.replace('-', '').isdigit():
                        continue
                    rows.append({'cik': cik, 'ticker': ticker, 'concept': concept, 'unit': unit,
                                 'period_start': start, 'period_end': end, 'filed': filed,
                                 'accession': accession, 'form': item['form'], 'value': value,
                                 'source_url': f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                                               f"{accession.replace('-', '')}/{accession}-index.html"})
                except (KeyError, ValueError, TypeError):
                    continue
    return pd.DataFrame(rows).drop_duplicates(
        ['cik', 'concept', 'unit', 'period_start', 'period_end', 'accession']) if rows else pd.DataFrame()


def fetch_company(ticker: str, cik: str) -> pd.DataFrame:
    if not cik.isdigit() or len(cik) != 10:
        raise ValueError("CIK inválido.")
    return parse_facts(_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"),
                       ticker=ticker, cik=cik)


def annual_review(facts: pd.DataFrame, *, as_of) -> dict:
    """Comparaciones anuales homogéneas, sin tratar ausencia como cero."""
    if facts.empty:
        return {"reason": "Sin estados oficiales SEC disponibles.", "facts": facts, "warnings": []}
    f = facts.copy()
    for col in ('filed', 'period_start', 'period_end'):
        f[col] = pd.to_datetime(f[col])
    f = f[(f.filed <= pd.Timestamp(as_of)) & f.form.isin(['10-K', '10-K/A'])]
    duration = (f.period_end - f.period_start).dt.days
    f = f[duration.between(300, 380) | (f.period_start == pd.Timestamp('1900-01-01'))]
    if f.empty:
        return {"reason": "Sin cuentas anuales publicadas en la fecha de corte.", "facts": f, "warnings": []}
    # La fecha de publicación, no el año del balance, gobierna las revisiones.
    f = f.sort_values(['filed', 'accession']).drop_duplicates(['concept', 'unit', 'period_end'], keep='last')
    ends = sorted(f.loc[f.concept.eq('NetIncomeLoss'), 'period_end'].unique())
    if not ends:
        return {"reason": "No hay periodos comparables de resultados US-GAAP.", "facts": f, "warnings": []}
    end = pd.Timestamp(ends[-1])
    current = f[f.period_end == end]
    def value(*concepts):
        for concept in concepts:
            values = current[current.concept == concept]
            if not values.empty:
                return float(values.iloc[-1]['value'])
        return None
    income = value('NetIncomeLoss')
    operating_cash = value('NetCashProvidedByUsedInOperatingActivities')
    capex = value('PaymentsToAcquirePropertyPlantAndEquipment')
    dividends = value('PaymentsOfDividendsCommonStock', 'PaymentsOfDividends')
    revenue = value('RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues')
    fcf = operating_cash - capex if operating_cash is not None and capex is not None else None
    operating_income = value('OperatingIncomeLoss')
    interest = value('InterestExpense')
    coverage = operating_income / interest if interest and interest > 0 and operating_income is not None else None
    debt_current, debt_long = value('LongTermDebtCurrent'), value('LongTermDebtNoncurrent')
    debt = debt_current + debt_long if debt_current is not None and debt_long is not None else None
    cash = value('CashAndCashEquivalentsAtCarryingValue')
    net_debt = debt - cash if debt is not None and cash is not None else None
    warnings = []
    if coverage is not None and coverage < 2:
        warnings.append("El beneficio operativo cubre menos de dos veces los intereses anuales; revisar deuda y vencimientos.")
    if income is not None and income > 0 and operating_cash is not None and operating_cash < income * .5:
        warnings.append("El flujo operativo es inferior a la mitad del beneficio anual; revisar conversión a caja.")
    if fcf is not None and dividends is not None and dividends > max(0, fcf):
        warnings.append("Los dividendos anuales superan el flujo de caja libre calculado.")
    shares = f[f.concept.eq('CommonStockSharesOutstanding')].sort_values('period_end')
    dilution = None
    if len(shares) >= 2:
        last_shares = shares.iloc[-1]
        prior = shares[(shares.period_end >= last_shares.period_end - pd.Timedelta(days=420)) &
                       (shares.period_end <= last_shares.period_end - pd.Timedelta(days=300))]
        if not prior.empty and prior.iloc[-1]['value'] > 0:
            dilution = float(last_shares['value'] / prior.iloc[-1]['value'] - 1)
            if dilution > .05:
                warnings.append("Las acciones en circulación aumentaron más del 5 % entre los últimos periodos.")
    stale = (pd.Timestamp(as_of) - end).days > 550
    return {"reason": "Cuentas anuales antiguas; no validan la situación actual." if stale else "",
            "period_end": end, "income": income, "operating_cash": operating_cash,
            "free_cash_flow": fcf, "margin": income / revenue if revenue and income is not None else None,
            "dilution": dilution, "interest_coverage": coverage, "net_debt": net_debt,
            "warnings": warnings, "facts": current}

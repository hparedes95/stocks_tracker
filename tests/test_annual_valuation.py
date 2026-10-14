import pandas as pd
import pytest

from stocks_tracker.core.valuation_sensitivity import verified_sensitivity
from stocks_tracker.providers.sec_provider import annual_cashflows


def facts():
    rows = []
    for year in (2022, 2023, 2024):
        for concept, value in {'NetCashProvidedByUsedInOperatingActivities':100,
                               'PaymentsToAcquirePropertyPlantAndEquipment':20,
                               'ProceedsFromIssuanceOfLongTermDebt':10,
                               'RepaymentsOfLongTermDebt':5,
                               'ProceedsFromRepaymentsOfShortTermDebt':-2,
                               'PaymentsOfDividendsPreferredStock':3}.items():
            rows.append({'concept': concept, 'value':value, 'unit':'USD', 'form':'10-K',
                         'period_start':f'{year}-01-01', 'period_end':f'{year}-12-31',
                         'filed':f'{year+1}-02-01', 'accession':str(year), 'source_url':f'https://www.sec.gov/{year}'})
    return pd.DataFrame(rows)


def test_fcfe_bridge_and_three_year_median():
    annual = annual_cashflows(facts(), as_of='2025-03-01')
    assert annual.caja_accionista_aproximada.tolist() == [80.,80.,80.]
    result = verified_sensitivity(annual, 1000, currency='USD', sector='Technology', as_of='2025-03-01')
    assert result['normalized_cash'] == 80
    assert len(result['scenarios']) == 12
    case = result['scenarios'].query('crecimiento_5_anos == 0 and descuento == .1').iloc[0]
    expected = sum(80 / 1.1 ** year for year in range(1,6)) + 80 * 1.02 / .08 / 1.1 ** 5
    assert case.valor_hipotetico == pytest.approx(expected)


@pytest.mark.parametrize('problem', ['missing','currency','quarter','future','different_filing'])
def test_incompatible_facts_are_not_zero_or_mixed(problem):
    frame = facts()
    mask = frame.concept.eq('RepaymentsOfLongTermDebt') & frame.period_end.eq('2024-12-31')
    if problem == 'missing':
        frame = frame[~mask]
    elif problem == 'currency':
        frame.loc[mask,'unit'] = 'EUR'
    elif problem == 'quarter':
        frame.loc[mask,'period_start'] = '2024-10-01'
    elif problem == 'future':
        frame.loc[mask,'filed'] = '2026-01-01'
    else:
        frame.loc[mask,'accession'] = 'incompatible'
    annual = annual_cashflows(frame, as_of='2025-03-01')
    result = verified_sensitivity(annual, 1000, currency='USD', sector='Technology', as_of='2025-03-01')
    assert result['reason']
    assert result['scenarios'].empty


def test_bank_and_old_accounts_do_not_receive_valuation():
    annual = annual_cashflows(facts(), as_of='2025-03-01')
    assert verified_sensitivity(annual,1000,currency='USD',sector='Financial Services',as_of='2025-03-01')['reason']
    assert verified_sensitivity(annual,1000,currency='USD',sector='Technology',as_of='2027-03-01')['reason']

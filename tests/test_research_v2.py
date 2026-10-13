import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core.portfolio_risk import analyse
from stocks_tracker.core.predictive_validation import validate_models
from stocks_tracker.core.valuation_sensitivity import cashflow_sensitivity
from stocks_tracker.providers.sec_provider import annual_review, parse_facts


def history(n=7000):
    rng = np.random.default_rng(71)
    return pd.DataFrame({'date': pd.bdate_range(end='2026-09-25', periods=n),
                         'adj_close': 100*np.exp(np.cumsum(rng.normal(.0003,.009,n))), 'source':'real-test'})


def test_walkforward_predictions_do_not_use_future_prices():
    prices = history()
    cutoff = prices.date.iloc[-800]
    a = validate_models(prices, horizon=21, as_of=cutoff)
    prices.loc[prices.date > cutoff, 'adj_close'] *= 100
    b = validate_models(prices, horizon=21, as_of=cutoff)
    assert not a.trials.empty
    pd.testing.assert_frame_equal(a.trials, b.trials)
    assert (a.trials.exit <= cutoff).all()
    assert not a.qualified  # Sin benchmark no hay una ventaja que certificar.


def test_oos_periods_do_not_overlap_and_append_does_not_rewrite_them():
    prices = history()
    cutoff = prices.date.iloc[-800]
    a = validate_models(prices, horizon=21, as_of=cutoff)
    b = validate_models(prices, horizon=21, as_of=prices.date.iloc[-1])
    prior = b.trials[b.trials.exit <= cutoff].reset_index(drop=True)
    pd.testing.assert_frame_equal(a.trials.reset_index(drop=True), prior)
    selected = a.trials[a.trials.model == 'tendencia_volatilidad']
    assert (selected.entry.iloc[1:].to_numpy() > selected.exit.iloc[:-1].to_numpy()).all()


def test_bad_sources_or_costs_cannot_validate():
    prices = history(500)
    prices['source'] = 'synthetic'
    assert not validate_models(prices).qualified
    with pytest.raises(ValueError):
        validate_models(prices, roundtrip_cost=float('nan'))


def test_portfolio_uses_cash_and_correlation():
    rng = np.random.default_rng(2)
    r = rng.normal(0, .01, 100)
    result = analyse(pd.DataFrame({'A':r, 'B':r}), {'A':1000., 'B':1000.}, cash=2000.,
                     currencies={'A':'EUR','B':'USD'})
    assert result['equity'] == 4000
    assert result['correlations'].loc['A','B'] == pytest.approx(1)
    assert result['shock_equity_30_eur'] == -600
    assert result['shock_foreign_fx_10_eur'] == -100
    assert result['annual_volatility'] == pytest.approx(pd.Series(r).std()*np.sqrt(252)*.5)


def test_missing_prices_are_not_zero_risk():
    result = analyse(pd.DataFrame({'A':[.01]*100}), {'A':100., 'B':300.})
    assert 'volatility' not in result
    assert result['reason']
    assert analyse(pd.DataFrame(), {'A':float('nan')})['reason']


def payload():
    def metric(value, **extra):
        return {'units': {'USD': [{'start':'2024-01-01','end':'2024-12-31','filed':'2025-02-01',
                                  'accn':'0000000001-25-000001','form':'10-K','val':value, **extra}]}}
    return {'facts': {'us-gaap': {'NetIncomeLoss': metric(100),
            'NetCashProvidedByUsedInOperatingActivities': metric(30),
            'PaymentsToAcquirePropertyPlantAndEquipment': metric(20),
            'PaymentsOfDividends': metric(40)}}}


def test_official_accounts_use_publication_date_and_link_to_document():
    facts = parse_facts(payload(), ticker='A', cik='0000000001')
    assert annual_review(facts, as_of='2025-01-31')['facts'].empty
    report = annual_review(facts, as_of='2025-03-01')
    assert report['free_cash_flow'] == 10
    assert len(report['warnings']) == 2
    assert all(url.startswith('https://www.sec.gov/Archives/edgar/data/1/') for url in facts.source_url)


def test_restatements_cannot_leak_back_in_time():
    facts = parse_facts(payload(), ticker='A', cik='0000000001')
    revised = facts.copy()
    revised['filed'] = '2026-02-01'
    revised['accession'] = '0000000001-26-000002'
    revised['value'] = 999
    mixed = pd.concat([facts,revised], ignore_index=True)
    assert annual_review(mixed, as_of='2025-03-01')['income'] == 100


def test_valuation_is_sensitivity_not_a_single_target():
    assert cashflow_sensitivity(-10,100).empty
    table = cashflow_sensitivity(10,100)
    assert len(table) == 12
    assert table.diferencia_vs_capitalizacion.nunique() == 12
    conservative = table[table.crecimiento_5_anos == -.05].sort_values('descuento')
    assert conservative.valor_hipotetico.is_monotonic_decreasing

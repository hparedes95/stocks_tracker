"""Blockers are named, never discarded to manufacture a usable portfolio value."""
import pandas as pd

from stocks_tracker.core.portfolio_valuation import issues


def test_cfd_missing_fx_missing_price_and_good_positions_are_distinguished():
    positions = pd.DataFrame([
        {'ticker':'CIBR', 'broker_symbol':'CIBR', 'supported':False,
         'reason':'Producto cfd: no se interpreta como una posición al contado.', 'qty':2, 'currency':'USD'},
        {'ticker':'AAPL', 'supported':True, 'qty':1, 'currency':'USD', 'close':100},
        {'ticker':'SAN.MC', 'supported':True, 'qty':1, 'currency':'EUR', 'close':5},
        {'ticker':'MSFT', 'supported':True, 'qty':1, 'currency':'EUR', 'close':None},
    ])
    report = issues(positions, {})
    assert set(report['Símbolo']) == {'CIBR', 'AAPL', 'MSFT'}
    indexed = report.set_index('Símbolo')
    assert 'cfd' in indexed.loc['CIBR', 'Motivo']
    assert 'contrato' in indexed.loc['CIBR', 'Qué falta resolver']
    assert 'USD/EUR' in indexed.loc['AAPL', 'Motivo']
    assert 'precio' in indexed.loc['MSFT', 'Motivo']
    assert 'AAPL' not in set(issues(positions, {'USD':1.1})['Símbolo'])


def test_empty_and_finite_values_do_not_claim_a_block_and_frames_are_not_mutated():
    assert issues(None, {}).empty
    frame = pd.DataFrame([{'ticker':'ABC', 'qty':1, 'currency':'EUR', 'close':10, 'valor_eur':10}])
    before = frame.copy(deep=True)
    assert issues(frame, {}).empty
    pd.testing.assert_frame_equal(frame, before)
    frame.loc[0, 'valor_eur'] = float('nan')
    assert 'EUR' in issues(frame, {}).iloc[0]['Motivo']


def test_unknown_currency_and_shorts_stay_explicitly_blocked():
    frame = pd.DataFrame([{'ticker':'ABC', 'qty':1, 'currency':pd.NA, 'close':10},
                          {'ticker':'SHORT', 'qty':-1, 'currency':'EUR', 'close':10}])
    result = issues(frame, {})
    assert len(result) == 2
    assert 'desconocido' in result.iloc[0]['Motivo']
    assert 'negativa' in result.iloc[1]['Motivo']

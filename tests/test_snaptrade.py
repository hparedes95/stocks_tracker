import base64
import copy
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import duckdb
import pytest
import requests

from stocks_tracker.core import snaptrade_credentials as vault
from stocks_tracker.core import snaptrade_portfolio as snap
from stocks_tracker.core.db import schema_path
from stocks_tracker.providers.snaptrade import Client, SnapTradeError

ACCOUNT = '12345678-1234-1234-1234-123456789012'


@pytest.fixture
def conn():
    c = duckdb.connect(':memory:')
    c.execute(schema_path().read_text(encoding='utf-8'))
    c.execute("INSERT INTO instruments(ticker,currency,exchange,gics_sector) VALUES ('AAPL','USD','NMS','Technology')")
    c.execute("INSERT INTO positions(id,ticker,qty,avg_cost,currency) VALUES ('manual','AAPL',500,99,'USD')")
    yield c
    c.close()


@pytest.fixture
def payload():
    now = datetime.now(UTC).isoformat()
    return {'account': {'id': ACCOUNT, 'sync_status': {'balances': now}},
            'positions': {'data_freshness': {'as_of': now}, 'results': [
                {'instrument': {'id':'apple', 'kind':'stock', 'symbol':'AAPL', 'currency':'USD', 'exchange':'XNAS'},
                 'units':'2.5', 'price':'200', 'cost_basis':'150', 'currency':'USD'}]},
            'balances': [{'currency':{'code':'EUR'}, 'cash':100, 'buying_power':1000}]}


@pytest.mark.parametrize('path', ['/accounts/all', '/authorizations',
    f'/accounts/{ACCOUNT}/positions/all', f'/accounts/{ACCOUNT}/balances'])
def test_signature_and_personal_scope(monkeypatch, path):
    monkeypatch.setattr('stocks_tracker.providers.snaptrade.time.time', lambda: 1715123456)
    captured = {}

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            yield b'{"results": []}'

    def get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    key = 'private-test-key'
    client = Client('personal-client', key, session=SimpleNamespace(get=get))
    assert client.get(path) == {'results':[]}
    parsed = urlsplit(captured['url'])
    assert parsed.netloc == 'api.snaptrade.com'
    assert parsed.path == path
    assert parsed.query == 'clientId=personal-client&timestamp=1715123456'
    # Official SDK request_after_hook.compute_request_signature signs the exact
    # resource_path sent on the wire, with no invented /api/v1 prefix.
    expected = json.dumps({'content': None, 'path': parsed.path, 'query': parsed.query},
                          sort_keys=True, separators=(',', ':')).encode()
    assert captured['headers']['Signature'] == base64.b64encode(hmac.new(key.encode(), expected, hashlib.sha256).digest()).decode()
    assert not captured['allow_redirects']
    assert key not in captured['url']
    assert 'userSecret' not in captured['url'] and 'userId' not in captured['url']


@pytest.mark.parametrize('path', ['/trade', '/accounts/../orders', 'https://evil.invalid', '/authorizations/x/refresh', '/accounts/x/balances'])
def test_http_allowlist(path):
    with pytest.raises(SnapTradeError, match='lista permitida'):
        Client('client-id', 'secret-key').get(path)


def test_transport_error_is_redacted():
    def get(*args, **kwargs):
        raise requests.ConnectionError('secret-key client-id')
    with pytest.raises(SnapTradeError) as error:
        Client('client-id', 'secret-key', session=SimpleNamespace(get=get)).get('/accounts/all')
    assert 'secret-key' not in str(error.value) and error.value.__suppress_context__


@pytest.mark.parametrize('disabled,permission', [(True,'read'), (False,'trade'), (None,'read')])
def test_disabled_or_trade_connection_rejected(monkeypatch, disabled, permission):
    client = Client('client-id', 'secret-key')
    def get(path):
        if path == '/authorizations':
            return [{'id':'link', 'brokerage':{'slug':'ETORO'}, 'disabled':disabled, 'type':permission}]
        if path == '/accounts/all':
            return {'results':[{'id':ACCOUNT, 'kind':'investment', 'connection_id':'link', 'is_paper':False}]}
        pytest.fail('Must reject before reading portfolio')
    monkeypatch.setattr(client, 'get', get)
    with pytest.raises(SnapTradeError):
        client.snapshot(ACCOUNT)


@pytest.mark.parametrize('field,value', [('units',None), ('units','NaN'), ('price','inf'), ('price','-1'), ('currency',None), ('cash_equivalent','false')])
def test_incomplete_positions_rejected(payload, field, value):
    payload['positions']['results'][0][field] = value
    with pytest.raises(SnapTradeError):
        snap.normalise(payload)


def test_cash_is_not_buying_power_and_no_fake_dates(payload, conn):
    data = snap.normalise(payload)
    snap.store_snapshot(conn, data)
    snap.enable(conn, True)
    assert snap.cash_eur(conn) == 100
    frame = snap.portfolio(conn)
    assert frame.qty.tolist() == [2.5]
    assert frame.opened_at.isna().all()
    assert frame.supported.all()
    assert conn.execute('SELECT qty FROM positions').fetchone()[0] == 500
    assert snap.readiness(conn) == ''


def test_idempotent_snapshot_and_manual_preserved(payload, conn):
    data = snap.normalise(payload)
    snap.store_snapshot(conn, data)
    snap.enable(conn, True)
    for _ in range(3):
        snap.store_snapshot(conn, data)
    assert len(snap.portfolio(conn)) == 1
    assert conn.execute('SELECT COUNT(*) FROM snaptrade_state').fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM positions').fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0
    snap.enable(conn, False)
    assert snap.portfolio(conn) is None
    assert conn.execute('SELECT qty FROM positions').fetchone()[0] == 500


@pytest.mark.parametrize('change', ['cfd','short','unknown','venue','cash_equivalent'])
def test_no_silent_conversion_of_unsupported_products(payload, conn, change):
    row = payload['positions']['results'][0]
    if change == 'cfd':
        row['instrument']['kind'] = 'cfd'
    elif change == 'short':
        row['units'] = '-2'
    elif change == 'unknown':
        row['instrument']['symbol'] = 'UNRECOGNISED'
    elif change == 'venue':
        row['instrument']['exchange'] = 'XLON'
    else:
        row['cash_equivalent'] = True
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    frame = snap.portfolio(conn)
    assert frame.empty if change == 'cash_equivalent' else not frame.supported.any()


def test_empty_portfolio_does_not_create_a_sale(payload, conn):
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    payload['positions']['results'] = []
    snap.store_snapshot(conn, snap.normalise(payload))
    assert snap.portfolio(conn).empty
    assert conn.execute('SELECT closed_at FROM positions').fetchone()[0] is None


def test_other_account_requires_explicit_deactivation(payload, conn):
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    before = snap.state(conn)['snapshot']
    payload['account']['id'] = 'different-account'
    with pytest.raises(SnapTradeError):
        snap.store_snapshot(conn, snap.normalise(payload))
    assert snap.state(conn)['snapshot'] == before


@pytest.mark.parametrize('field', ['as_of','cash_as_of'])
def test_stale_or_missing_provider_timestamps_block_advice(payload, conn, field):
    data = snap.normalise(payload)
    data[field] = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    snap.store_snapshot(conn, data)
    snap.enable(conn, True)
    assert '36 horas' in snap.readiness(conn)
    if field == 'cash_as_of':
        data[field] = None
        snap.store_snapshot(conn, data)
        assert 'antigüedad' in snap.readiness(conn)


def test_snapshots_are_excluded_from_market_export():
    from stocks_tracker.core.market_bundle import TABLES
    assert 'snaptrade_state' not in TABLES


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_credentials_are_encrypted_and_local(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, 'project_root', lambda: tmp_path)
    vault.save('personal-client-id', 'private-secret-123456789')
    raw = vault._path().read_bytes()
    assert b'private-secret' not in raw and b'personal-client' not in raw
    assert vault.load() == ('personal-client-id', 'private-secret-123456789')
    vault.forget()
    assert not vault.exists()


def test_snapshot_is_not_changed_by_normalise(payload):
    original = copy.deepcopy(payload)
    snap.normalise(payload)
    assert original == payload


def test_advisor_uses_live_quantities_without_splits(payload, conn):
    from stocks_tracker.compute.run_advice import _cartera
    day = datetime.now(UTC).date()
    conn.execute("INSERT INTO indicators_daily(ticker,date,close) VALUES ('AAPL',?,200)", [day])
    conn.execute("INSERT INTO prices_daily(ticker,date,close,source) VALUES ('EURUSD=X',?,2,'ecb')", [day])
    conn.execute("INSERT INTO corporate_actions(ticker,date,action_type,value) VALUES ('AAPL',?,'split',4)", [day])
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    frame, _, weights = _cartera(conn, day)
    assert frame.qty.tolist() == [2.5]
    assert frame.valor_eur.tolist() == [250]
    assert weights == {'AAPL':100}
    manual, _, _ = _cartera(conn, day, use_live=False)
    assert manual.qty.iloc[0] != 2.5


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    from stocks_tracker.core import db
    monkeypatch.setattr(db, 'get_settings', lambda: SimpleNamespace(warehouse_path=tmp_path / 'local.duckdb'))
    db.migrate()
    return db


def test_failed_sync_preserves_snapshot_and_blocks_advisor(local_db, payload, monkeypatch):
    with local_db.connect() as c:
        snap.store_snapshot(c, snap.normalise(payload))
        snap.enable(c, True)
        before = snap.state(c)['snapshot']
    monkeypatch.setattr(vault, 'load', lambda: ('client-id', 'secret-key'))
    def fail(*args):
        raise SnapTradeError('Conexión caducada.')
    monkeypatch.setattr(Client, 'snapshot', fail)
    with pytest.raises(SnapTradeError, match='caducada'):
        snap.sync(ACCOUNT)
    with local_db.connect(read_only=True) as c:
        assert snap.state(c)['snapshot'] == before
        assert snap.state(c)['last_attempt'] is not None
        assert 'falló' in snap.readiness(c)


def test_auto_sync_is_throttled_across_sessions(local_db, payload, monkeypatch):
    with local_db.connect() as c:
        snap.store_snapshot(c, snap.normalise(payload))
        snap.enable(c, True)
    calls = []
    monkeypatch.setattr(snap, 'sync', lambda account: calls.append(account))
    assert snap.auto_sync() is False
    with local_db.connect() as c:
        c.execute('UPDATE snaptrade_state SET last_attempt=?', [datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=6)])
    assert snap.auto_sync() is True
    assert calls == [ACCOUNT]
    with local_db.connect() as c:
        snap.enable(c, False)
    assert snap.auto_sync() is False


def test_configuration_form_validates_and_saves_locally(local_db, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from stocks_tracker.core.config import project_root
    saved = []
    monkeypatch.setattr(vault, 'exists', lambda: bool(saved))
    monkeypatch.setattr(vault, 'save', lambda *values: saved.append(values))
    monkeypatch.setattr(Client, 'accounts', lambda self: [{'id': ACCOUNT, 'display_name': 'eToro'}])
    app = AppTest.from_file(str(project_root() / 'src/stocks_tracker/app/pages/15_snaptrade.py'), default_timeout=60).run()
    assert not app.exception
    app.text_input[0].input('personal-client')
    app.text_input[1].input('private-test-key')
    app.checkbox[0].check()
    app.button[0].click().run()
    assert not app.exception
    assert saved == [('personal-client', 'private-test-key')]
    assert app.selectbox[0].value == ACCOUNT
    with local_db.connect(read_only=True) as c:
        assert not snap.active(c)  # saving credentials is not consent to replace the portfolio


def test_policy_uses_broker_cash_not_manual_input(payload, conn, monkeypatch):
    from stocks_tracker.compute import run_advice
    from stocks_tracker.core import advisor_evidence
    day = datetime.now(UTC).date()
    conn.execute("UPDATE instruments SET asset_class='equity'")
    conn.execute("INSERT INTO indicators_daily(ticker,date,close) VALUES ('AAPL',?,200)", [day])
    conn.execute("INSERT INTO prices_daily(ticker,date,close,source) VALUES ('EURUSD=X',?,2,'ecb')", [day])
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    observed = {}
    def candidates(ranking, **kwargs):
        observed.update(kwargs)
        return []
    monkeypatch.setattr(run_advice.advice_build, 'de_los_candidatos', candidates)
    monkeypatch.setattr(run_advice.advice_build, 'de_la_cartera', lambda *a, **k: [])
    monkeypatch.setattr(advisor_evidence, 'apply_gate', lambda conn, recs, **k: recs)
    run_advice.calculate_policy(conn, 'test-profile', 999999, use_live_portfolio=True)
    assert observed['caja'] == 100
    assert observed['equity'] == 350
    run_advice.calculate_policy(conn, 'test-profile', 70)  # historical replay never reads live account
    assert observed['caja'] == 70
    assert observed['equity'] != 350


@pytest.mark.parametrize('symbol,kind,currency,exchange,ticker', [
    ('BTC', 'crypto', 'USD', None, 'BTC-USD'),
    ('ETH', 'crypto', None, None, 'ETH-USD'),
    ('TSM', 'adr', 'USD', 'XNYS', 'TSM'),
    ('ASML', 'stock', 'USD', 'NASDAQ', 'ASML'),
    ('ASML', 'stock', 'EUR', 'XAMS', 'ASML.AS'),
    ('CIBR', 'etf', 'USD', 'XNAS', 'CIBR'),
    ('ITA', 'etf', 'USD', 'BATS', 'ITA'),
    ('ITA', 'etf', 'USD', 'ARCX', 'ITA'),
])
def test_requested_holdings_work_before_local_history_exists(payload, conn, symbol, kind, currency, exchange, ticker):
    row = payload['positions']['results'][0]
    row['instrument'].update(symbol=symbol, kind=kind, currency=currency, exchange=exchange)
    row['currency'] = currency or 'USD'
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    frame = snap.portfolio(conn)
    assert frame.supported.all() and frame.analysis_supported.all()
    assert frame.ticker.tolist() == [ticker]
    assert frame.qty.tolist() == [2.5]  # no ADR/share or currency ratio applied to quantities
    assert frame.valuation_source.iloc[0].startswith('SnapTrade')
    assert 'pendiente' in frame.reason.iloc[0]
    assert conn.execute('SELECT COUNT(*) FROM prices_daily').fetchone()[0] == 0  # quote is not fabricated history


def test_all_six_holdings_count_in_advisor_equity(payload, conn):
    from stocks_tracker.compute.run_advice import _cartera
    day = datetime.now(UTC).date()
    conn.execute("INSERT INTO prices_daily(ticker,date,close,source) VALUES ('EURUSD=X',?,2,'ecb')", [day])
    positions = []
    for symbol, kind, market in [('BTC','crypto',None), ('ETH','crypto',None),
                                ('TSM','adr','XNYS'), ('ASML','stock','XNAS'),
                                ('CIBR','etf','XNAS'), ('ITA','etf','BATS')]:
        row = copy.deepcopy(payload['positions']['results'][0])
        row['instrument'].update(id=symbol, symbol=symbol, kind=kind, exchange=market)
        positions.append(row)
    payload['positions']['results'] = positions
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    frame, sectors, weights = _cartera(conn, day)
    assert frame.supported.all() and len(frame) == 6
    assert frame.valor_eur.sum() == 1500
    assert sum(weights.values()) == pytest.approx(100)
    assert sectors['Criptomonedas'] == pytest.approx(100 / 3)
    assert '' not in sectors  # unknown ETF/equity sectors are not one fictitious sector
    assert conn.execute('SELECT qty FROM positions').fetchone()[0] == 500
    conn.execute("UPDATE instruments SET asset_class='equity' WHERE ticker='AAPL'")
    conn.execute("INSERT INTO indicators_daily(ticker,date,close) VALUES ('AAPL',?,200)", [day])
    from stocks_tracker.compute.run_advice import calculate_policy
    recs, portfolio, _, _, _ = calculate_policy(conn, 'test', 999999, use_live_portfolio=True)
    assert len(portfolio) == 6 and portfolio.valor_eur.sum() == 1500
    assert {r.ticker for r in recs} == set(weights)
    assert not any(r.veredicto in ('comprar', 'ampliar') for r in recs)
    assert conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0


@pytest.mark.parametrize('market,currency', [(None,'USD'), ('XAMS','USD'), ('XNAS','EUR')])
def test_ambiguous_asml_is_valued_but_never_uses_other_listing(payload, conn, market, currency):
    row = payload['positions']['results'][0]
    row['instrument'].update(symbol='ASML', exchange=market, currency=currency)
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    frame = snap.portfolio(conn)
    assert frame.supported.all() and not frame.analysis_supported.any()
    assert frame.ticker.iloc[0].startswith('SnapTrade:ASML:')
    assert frame.currency.iloc[0] == 'USD'  # price currency, not an inferred listing currency
    assert frame.close.iloc[0] == 200
    assert frame.reason.iloc[0]


def test_cross_currency_broker_price_is_converted_without_changing_units(payload, conn):
    day = datetime.now(UTC).date()
    conn.execute("INSERT INTO prices_daily(ticker,date,close,source) VALUES ('EURUSD=X',?,2,'ecb')", [day])
    payload['positions']['results'][0]['instrument'].update(symbol='ASML', exchange='XAMS', currency='EUR')
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    frame = snap.portfolio(conn, day)
    assert frame.ticker.iloc[0] == 'ASML.AS' and frame.currency.iloc[0] == 'EUR'
    assert frame.close.iloc[0] == 100 and frame.qty.iloc[0] == 2.5
    assert frame.avg_cost.isna().all()


@pytest.mark.parametrize('kind,qty', [('cfd',2.5), ('crypto',-2.5)])
def test_crypto_support_does_not_disguise_cfds_or_shorts(payload, conn, kind, qty):
    payload['positions']['results'][0]['instrument'].update(symbol='BTC', kind=kind)
    payload['positions']['results'][0]['units'] = qty
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    assert not snap.portfolio(conn).supported.any()


def test_missing_fx_does_not_turn_broker_usd_into_listing_eur(payload, conn):
    payload['positions']['results'][0]['instrument'].update(symbol='ASML', exchange='XAMS', currency='EUR')
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    frame = snap.portfolio(conn)
    assert frame.supported.all() and frame.close.isna().all()
    assert 'tipo de cambio' in frame.reason.iloc[0]


def test_conflicting_local_currency_is_not_used_for_a_catalogue_listing(payload, conn):
    conn.execute("INSERT INTO instruments(ticker,currency,exchange) VALUES ('ASML','EUR','NMS')")
    payload['positions']['results'][0]['instrument'].update(symbol='ASML', exchange='XNAS', currency='USD')
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    frame = snap.portfolio(conn)
    assert not frame.analysis_supported.any()
    assert frame.currency.iloc[0] == 'USD' and frame.close.iloc[0] == 200
    assert 'contradice' in frame.reason.iloc[0]


def test_cibr_cfd_does_not_abort_analysis_of_other_holdings(payload, conn):
    from stocks_tracker.compute.run_advice import _cartera, calculate_policy
    day = datetime.now(UTC).date()
    conn.execute("UPDATE instruments SET asset_class='equity'")
    conn.execute("INSERT INTO indicators_daily(ticker,date,close) VALUES ('AAPL',?,200)", [day])
    conn.execute("INSERT INTO prices_daily(ticker,date,close,source) VALUES ('EURUSD=X',?,2,'ecb')", [day])
    bad = copy.deepcopy(payload['positions']['results'][0])
    bad['instrument'].update(id='cibr-cfd', symbol='CIBR', kind='cfd')
    payload['positions']['results'].append(bad)
    snap.store_snapshot(conn, snap.normalise(payload))
    snap.enable(conn, True)
    recs, positions, _, _, _ = calculate_policy(conn, 'test', 0, use_live_portfolio=True)
    assert {r.ticker for r in recs} == {'AAPL', 'CIBR'}
    unsupported = next(r for r in recs if r.ticker == 'CIBR')
    assert not unsupported.accionable and any('cfd' in reason.lower() for reason in unsupported.motivos)
    assert len(positions) == 2  # the unresolved holding was not removed or valued as zero
    frame, sectors, weights = _cartera(conn, day)
    assert frame.valor_eur.isna().sum() == 1
    assert not sectors and not weights  # no percentages from an incomplete denominator
    assert conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0


def test_partial_advisor_persists_diagnostics_instead_of_failing(local_db, payload):
    from stocks_tracker.compute.run_advice import calcular_y_guardar
    day = datetime.now(UTC).date()
    broken = copy.deepcopy(payload['positions']['results'][0])
    broken['instrument'].update(id='cibr-cfd', symbol='CIBR', kind='cfd')
    payload['positions']['results'].append(broken)
    with local_db.connect() as c:
        c.execute("INSERT INTO instruments(ticker,currency,exchange,asset_class) VALUES ('AAPL','USD','NMS','equity')")
        c.execute("INSERT INTO indicators_daily(ticker,date,close) VALUES ('AAPL',?,200)", [day])
        c.execute("INSERT INTO prices_daily(ticker,date,close,source) VALUES ('EURUSD=X',?,2,'ecb')", [day])
        snap.store_snapshot(c, snap.normalise(payload))
        snap.enable(c, True)
    assert calcular_y_guardar() == 0
    with local_db.connect(read_only=True) as c:
        diagnostics = dict(c.execute('SELECT ticker,reason FROM advisor_diagnostics').fetchall())
        assert 'AAPL' in diagnostics and 'CIBR' in diagnostics
        assert 'cfd' in diagnostics['CIBR'].lower()
        assert c.execute('SELECT COUNT(*) FROM advisor_runs').fetchone()[0] == 1
        assert len(snap.portfolio(c, day)) == 2
        assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0

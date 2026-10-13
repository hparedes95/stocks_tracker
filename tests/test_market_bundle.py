import io
import json
import zipfile

import duckdb
import pandas as pd
import pytest

from stocks_tracker.core import market_bundle as mb
from stocks_tracker.core.db import schema_path


@pytest.fixture
def warehouses():
    a, b = duckdb.connect(':memory:'), duckdb.connect(':memory:')
    for c in (a, b):
        c.execute(schema_path().read_text(encoding='utf-8'))
    a.execute("INSERT INTO instruments (ticker, asset_class, currency) VALUES ('AAA','equity','EUR')")
    a.execute("INSERT INTO prices_daily(ticker,date,adj_close,close,source) VALUES ('AAA','2026-09-25',100,100,'yfinance')")
    a.execute("INSERT INTO indicators_daily(ticker,date,close) VALUES ('AAA','2026-09-25',100)")
    a.execute("INSERT INTO factor_scores(ticker,date,weights_hash,composite) VALUES ('AAA','2026-09-25','w1',2)")
    for c, symbol in ((a, 'PRIVATE_A'), (b, 'PRIVATE_B')):
        c.execute("INSERT INTO positions (id,ticker,qty) VALUES ('private',?,12)", [symbol])
    yield a, b
    a.close()
    b.close()


def test_fresh_install_and_old_pc_have_identical_market_not_portfolio(warehouses):
    a, b = warehouses
    b.execute("INSERT INTO prices_daily(ticker,date,close) VALUES ('OLD','2020-01-01',7)")
    payload = mb.export_bundle(a)
    manifest = mb.import_bundle(b, payload)
    for table in mb.TABLES:
        pd.testing.assert_frame_equal(a.execute(f'SELECT * FROM {table} ORDER BY ALL').fetchdf(),
                                      b.execute(f'SELECT * FROM {table} ORDER BY ALL').fetchdf())
    assert b.execute('SELECT ticker FROM positions').fetchone()[0] == 'PRIVATE_B'
    assert mb.state(b)['bundle_id'] == manifest['bundle_id']
    assert mb.state(b)['frozen']
    assert mb.inspect_bundle(mb.export_bundle(b))[0]['bundle_id'] == manifest['bundle_id']
    mb.unlock(b)
    assert not mb.state(b)['frozen']


def mutate(payload, transform):
    target = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as source, zipfile.ZipFile(target, 'w') as out:
        files = {name: source.read(name) for name in source.namelist()}
        transform(files)
        for name, body in files.items():
            out.writestr(name, body)
    return target.getvalue()


@pytest.mark.parametrize('attack', ['extra_table', 'traversal', 'corruption', 'manifest'])
def test_invalid_archives_never_change_market_or_private_data(warehouses, attack):
    a, b = warehouses
    def change(files):
        if attack == 'extra_table':
            files['positions.parquet'] = b'private'
        elif attack == 'traversal':
            files['../.env'] = b'no'
        elif attack == 'corruption':
            files['prices_daily.parquet'] += b'changed'
        else:
            m = json.loads(files['manifest.json'])
            m['as_of'] = '2099-01-01'
            files['manifest.json'] = json.dumps(m).encode()
    payload = mutate(mb.export_bundle(a), change)
    with pytest.raises(ValueError):
        mb.import_bundle(b, payload)
    assert b.execute('SELECT COUNT(*) FROM prices_daily').fetchone()[0] == 0
    assert b.execute('SELECT ticker FROM positions').fetchone()[0] == 'PRIVATE_B'


def test_wrong_engine_rejected_before_writing(warehouses, monkeypatch):
    a, b = warehouses
    payload = mb.export_bundle(a)
    monkeypatch.setattr(mb, 'engine_fingerprint', lambda: 'another-version')
    with pytest.raises(ValueError, match='código/configuración'):
        mb.import_bundle(b, payload)


def test_export_never_contains_private_tables(warehouses):
    a, _ = warehouses
    with zipfile.ZipFile(io.BytesIO(mb.export_bundle(a))) as archive:
        assert set(archive.namelist()) == {'manifest.json', *(t + '.parquet' for t in mb.TABLES)}
        assert all(word not in archive.namelist() for word in ('positions.parquet', 'recommendations.parquet', '.env'))


def test_synthetic_cannot_be_imported(warehouses):
    a, b = warehouses
    a.execute("UPDATE prices_daily SET source='synthetic'")
    with pytest.raises(ValueError, match='sintético'):
        mb.import_bundle(b, mb.export_bundle(a))

from contextlib import contextmanager

import duckdb
import pandas as pd
import pytest

from stocks_tracker.core.db import schema_path
from stocks_tracker.ingest import sec_fundamentals as service
from stocks_tracker.providers import sec_provider as provider


@pytest.fixture
def connection(monkeypatch):
    c = duckdb.connect(':memory:')
    c.execute(schema_path().read_text(encoding='utf-8'))
    @contextmanager
    def connect(**kwargs):
        yield c
    monkeypatch.setattr(service,'connect',connect)
    monkeypatch.setattr(service,'frozen_market',lambda:False)
    yield c
    c.close()


def test_sec_ingestion_preserves_filing_and_uses_daily_cache(connection, monkeypatch):
    frame = pd.DataFrame([{'cik':'0000000001','ticker':'A','concept':'NetIncomeLoss','unit':'USD',
              'period_start':'2024-01-01','period_end':'2024-12-31','filed':'2025-02-01',
              'accession':'0000000001-25-000001','form':'10-K','value':100.,
              'source_url':'https://www.sec.gov/Archives/edgar/data/1/'}])
    calls = []
    monkeypatch.setattr(service,'ticker_map',lambda:{'A':'0000000001'})
    def fetch(*args):
        calls.append(args)
        return frame
    monkeypatch.setattr(service,'fetch_company',fetch)
    assert service.refresh('A')['updated'] == 1
    assert service.refresh('A')['updated'] == 0
    assert len(calls) == 1
    assert connection.execute('SELECT COUNT(*) FROM sec_facts').fetchone()[0] == 1
    assert connection.execute("SELECT rows_written FROM ingest_log WHERE task='sec'").fetchone()[0] == 1


def test_optional_source_failure_and_frozen_copy_do_not_invent_rows(connection,monkeypatch):
    def fail():
        raise RuntimeError('unavailable')
    monkeypatch.setattr(service,'ticker_map',fail)
    assert 'no disponible' in service.refresh('A')['reason']
    monkeypatch.setattr(service,'frozen_market',lambda:True)
    assert 'compartida' in service.refresh('A')['reason']
    assert connection.execute('SELECT COUNT(*) FROM sec_facts').fetchone()[0] == 0


def test_http_boundary_has_identity_timeout_and_closes_response(monkeypatch):
    calls = []
    class Response:
        closed = False
        def raise_for_status(self):
            pass
        def iter_content(self,size):
            yield b'{"ok":true}'
        def close(self):
            self.closed = True
    response = Response()
    def get(url,**kwargs):
        calls.append(kwargs)
        return response
    monkeypatch.setattr(provider.requests,'get',get)
    monkeypatch.setattr(provider.time,'sleep',lambda _:None)
    assert provider._get('https://data.sec.gov/example') == {'ok':True}
    assert response.closed
    assert 'StocksTracker' in calls[0]['headers']['User-Agent']
    assert calls[0]['timeout'] == (10,45)


def test_invalid_cik_does_not_make_a_request(monkeypatch):
    monkeypatch.setattr(provider,'_get',lambda _:pytest.fail('unexpected network'))
    with pytest.raises(ValueError):
        provider.fetch_company('A','../other')


def test_blocked_provider_has_bounded_retry_pause(connection, monkeypatch):
    calls = []
    def fail():
        calls.append(1)
        raise RuntimeError('blocked')
    monkeypatch.setattr(service, 'ticker_map', fail)
    service.refresh('A')
    assert '24 horas' in service.refresh('A')['reason']
    assert len(calls) == 1

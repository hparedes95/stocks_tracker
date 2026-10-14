from datetime import timedelta

import duckdb
import pytest

from stocks_tracker.core.advice import Conviccion, Recomendacion, Veredicto
from stocks_tracker.core.db import schema_path
from stocks_tracker.core.investor_profile import DEFAULTS, save
from stocks_tracker.core.prediction_plans import prepare, record_outcomes
from stocks_tracker.core.timeutils import utcnow


@pytest.fixture
def warehouse():
    c = duckdb.connect(':memory:')
    c.execute(schema_path().read_text(encoding='utf-8'))
    c.execute("INSERT INTO instruments(ticker,country,asset_class) VALUES ('A','US','equity')")
    save(c, {**DEFAULTS, 'confirmed': True})
    yield c
    c.close()


def test_preregistered_holdout_is_future_and_idempotent(warehouse):
    prepare(warehouse)
    first = warehouse.execute('SELECT * FROM prediction_plans').fetchall()
    prepare(warehouse)
    assert warehouse.execute('SELECT * FROM prediction_plans').fetchall() == first
    assert first[0][2] == (utcnow() + timedelta(days=1)).date()
    assert first[0][3] == 'preinscrita'


def test_failure_cannot_be_replaced_with_later_success(warehouse):
    prepare(warehouse)
    identity = warehouse.execute('SELECT plan_id FROM prediction_plans').fetchone()[0]
    def recommendation(state):
        return Recomendacion('A', Veredicto.SIN_OPINION, Conviccion.BAJA,
                             assessment={'plan_id': identity, 'validation_state': state})
    record_outcomes(warehouse, [recommendation('refutada')])
    record_outcomes(warehouse, [recommendation('confirmada')])
    assert warehouse.execute('SELECT status FROM prediction_plans').fetchone()[0] == 'refutada'


def test_changed_profile_creates_new_attempt_without_erasing_failure(warehouse):
    prepare(warehouse)
    warehouse.execute("UPDATE prediction_plans SET status='refutada'")
    save(warehouse, {**DEFAULTS, 'confirmed': True, 'horizon_months': 1})
    prepare(warehouse)
    assert sorted(r[0] for r in warehouse.execute('SELECT status FROM prediction_plans').fetchall()) == ['preinscrita', 'refutada']


def test_model_protocol_contains_no_private_risk_profile(warehouse):
    prepare(warehouse)
    raw = warehouse.execute('SELECT protocol FROM prediction_plans').fetchone()[0]
    assert 'max_loss_pct' not in raw and 'confirmed' not in raw and 'max_cluster_pct' not in raw
    save(warehouse, {**DEFAULTS, 'confirmed': True, 'max_loss_pct': 10.})
    prepare(warehouse)
    assert warehouse.execute('SELECT COUNT(*) FROM prediction_plans').fetchone()[0] == 1

"""Protocolo prospectivo preinscrito. Un resultado refutado no se reintenta."""
import hashlib
import json
from datetime import timedelta

from .evaluation import BENCHMARKS
from .investor_profile import load
from .market_bundle import engine_fingerprint
from .predictive_validation import VERSION
from .timeutils import utcnow


def key(ticker, benchmark, profile, engine):
    # Solo supuestos del modelo, no tolerancia a pérdidas, caja o datos personales.
    model_parameters = {k: profile[k] for k in ('horizon_months', 'roundtrip_pct')}
    protocol = {'ticker': ticker, 'benchmark': benchmark, 'parameters': model_parameters,
                'engine': engine, 'candidate': VERSION, 'minimum_episodes': 30,
                'cost_stress': 2, 'temporal_error': 'max(HAC, iid)',
                'multiple_testing': 'Bonferroni: universo por tres horizontes',
                'stability': 'tres tercios no solapados', 'execution': 'cierre siguiente'}
    raw = json.dumps(protocol, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest(), raw


def prepare(conn):
    """No borra ni retrasa el corte inicial al actualizar. Cambiar motor crea otro intento."""
    now, profile, engine = utcnow(), load(conn), engine_fingerprint()
    if not profile['confirmed']:
        return
    for ticker, country in conn.execute("SELECT ticker,country FROM instruments WHERE asset_class IN ('equity','etf')").fetchall():
        identity, raw = key(ticker, BENCHMARKS.get(country, 'VT'), profile, engine)
        conn.execute('INSERT INTO prediction_plans(plan_id,created_at,holdout_start,status,protocol,outcome) '
                     'VALUES (?, ?, ?, ?, ?, NULL) ON CONFLICT DO NOTHING',
                     [identity, now, (now + timedelta(days=1)).date(), 'preinscrita', raw])


def lookup(conn, ticker, benchmark, profile, engine):
    identity, _ = key(ticker, benchmark, profile, engine)
    row = conn.execute('SELECT holdout_start,status FROM prediction_plans WHERE plan_id=?', [identity]).fetchone()
    return identity, row


def record_outcomes(conn, recommendations):
    for rec in recommendations:
        assessment = rec.assessment
        status = assessment.get('validation_state')
        if status in ('confirmada', 'refutada') and assessment.get('plan_id'):
            # Primera evaluación final: no cambiar una refutación por ensayos posteriores.
            conn.execute("UPDATE prediction_plans SET status=?,outcome=?,decided_at=? WHERE plan_id=? AND status='preinscrita'",
                         [status, json.dumps({'status': status, 'evidence': assessment.get('evidencia', '')}, sort_keys=True),
                          utcnow(), assessment['plan_id']])

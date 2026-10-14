"""Replay aislado de la política real; investigación, nunca órdenes de bróker.

No reconstruye fundamentales actuales en el pasado. La cobertura histórica de
metadatos, exclusiones del universo y anuncios de resultados no está certificada:
el informe es parcial y no habilita modelos para recomendar compras.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field

import duckdb
import pandas as pd

from ..compute.run_advice import calculate_policy
from ..core import fx
from ..core.db import schema_path
from ..core.market_bundle import engine_fingerprint
from ..core.trading_calendar import calendar_name, sessions


@dataclass(frozen=True)
class ReplaySpec:
    start: str
    end: str
    weights_hash: str
    initial_cash: float = 10000.
    benchmark: str = 'VT'
    roundtrip_cost: float = .01
    slippage: float = .001
    delay_sessions: int = 0
    rebalance_sessions: int = 21

    def validate(self):
        if not (pd.Timestamp(self.start) <= pd.Timestamp(self.end)):
            raise ValueError('Fechas invertidas')
        if not math.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError('Capital inicial inválido')
        if not (0 <= self.roundtrip_cost < .2 and 0 <= self.slippage < .1):
            raise ValueError('Costes inválidos')
        if self.delay_sessions not in (0, 1, 2) or self.rebalance_sessions < 1:
            raise ValueError('Retraso o frecuencia inválidos')
        if (pd.Timestamp(self.end) - pd.Timestamp(self.start)).days > 3653:
            raise ValueError('Máximo diez años por simulación')


@dataclass
class Ledger:
    cash: float
    holdings: dict = field(default_factory=dict)
    receivables: float = 0.
    movements: list = field(default_factory=list)
    cost_basis: dict = field(default_factory=dict)
    opened: dict = field(default_factory=dict)

    def trade(self, day, ticker, qty, price_eur, cost_rate, *, price_native=None):
        """Cantidad positiva compra, negativa vende; fracciones permitidas, sin crédito."""
        if not all(math.isfinite(v) for v in (qty, price_eur, cost_rate)) or price_eur <= 0 or not 0 <= cost_rate < 1:
            raise ValueError('Operación simulada inválida')
        held = self.holdings.get(ticker, 0.)
        if qty >= 0:
            qty = min(qty, self.cash / (price_eur * (1 + cost_rate)))
        else:
            qty = -min(-qty, held)
        if abs(qty) < 1e-12:
            return
        gross = qty * price_eur
        fee = abs(gross) * cost_rate
        self.cash -= gross + fee
        self.holdings[ticker] = held + qty
        if qty > 0:
            native = price_eur if price_native is None else price_native
            self.cost_basis[ticker] = (self.cost_basis.get(ticker, 0.) * held + qty * native) / (held + qty)
            self.opened.setdefault(ticker, pd.Timestamp(day).date())
        if self.holdings[ticker] < 1e-10:
            self.holdings.pop(ticker)
            self.cost_basis.pop(ticker, None)
            self.opened.pop(ticker, None)
        if self.cash < -1e-7:
            raise ArithmeticError('La simulación ha creado crédito')
        self.movements.append({'date': str(pd.Timestamp(day).date()), 'ticker': ticker,
                               'type': 'buy' if qty > 0 else 'sell', 'qty': qty,
                               'price_eur': price_eur, 'cost_eur': fee, 'cash_eur': self.cash})

    def split(self, day, ticker, ratio):
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError('Ratio de split inválido')
        if ticker in self.holdings:
            self.holdings[ticker] *= ratio
            self.cost_basis[ticker] /= ratio
            self.movements.append({'date': str(pd.Timestamp(day).date()), 'ticker': ticker,
                                   'type': 'split', 'ratio': ratio, 'cost_eur': 0.})

    def dividend(self, day, ticker, per_share_eur):
        if not math.isfinite(per_share_eur) or per_share_eur < 0:
            raise ValueError('Dividendo inválido')
        amount = self.holdings.get(ticker, 0.) * per_share_eur
        if amount:
            # Ex-date no es fecha de cobro: no financiar compras con ese dinero.
            self.receivables += amount
            self.movements.append({'date': str(pd.Timestamp(day).date()), 'ticker': ticker,
                                   'type': 'dividend_receivable', 'amount_eur': amount, 'cost_eur': 0.})

    def nav(self, prices):
        if any(t not in prices or not math.isfinite(prices[t]) or prices[t] <= 0 for t in self.holdings):
            raise ValueError('Falta precio o FX para valorar íntegramente la cartera simulada')
        return self.cash + self.receivables + sum(q * prices[t] for t, q in self.holdings.items())


DATED = {'prices_daily': 'date', 'indicators_daily': 'date', 'factor_scores': 'date',
         'fundamentals_snapshot': 'as_of', 'corporate_actions': 'date', 'regime_daily': 'date',
         'scoring_runs': 'date', 'sec_facts': 'filed', 'prediction_plans': 'created_at'}


def read_inputs(conn):
    """Una sola lectura consistente; no copia cartera, secretos ni recomendaciones."""
    conn.execute('BEGIN TRANSACTION')
    try:
        frames = {t: conn.execute(f'SELECT * FROM "{t}" ORDER BY ALL').fetchdf()
                  for t in (*DATED, 'instruments', 'universe_membership', 'investor_preferences')}
        conn.execute('COMMIT')
    except Exception:  # noqa: BLE001 — lectura consistente
        conn.execute('ROLLBACK')
        raise
    return frames


def snapshot(frames, day, ledger):
    """Base desechable: ni una fila fechada después de la decisión."""
    c = duckdb.connect(':memory:')
    try:
        c.execute(schema_path().read_text(encoding='utf-8'))
        for table, original in frames.items():
            frame = original.copy()
            if table in DATED:
                frame = frame[pd.to_datetime(frame[DATED[table]]) <= day]
            if table == 'scoring_runs':
                # Un ranking recalculado hoy no se conocía hace cuatro años.
                frame = frame[pd.to_datetime(frame.computed_at) < day + pd.Timedelta(days=1)]
            if table == 'universe_membership':
                frame = frame[pd.to_datetime(frame.valid_from) <= day]
            if table == 'prediction_plans':
                unknown_outcome = pd.to_datetime(frame.decided_at).isna() | (pd.to_datetime(frame.decided_at) > day)
                frame.loc[unknown_outcome, 'status'] = 'preinscrita'
                frame.loc[unknown_outcome, 'outcome'] = None
            c.register('_replay_input', frame)
            c.execute(f'INSERT INTO "{table}" BY NAME SELECT * FROM _replay_input')
            c.unregister('_replay_input')
        c.execute('DELETE FROM factor_scores WHERE NOT EXISTS (SELECT 1 FROM scoring_runs r '
                  'WHERE r.date=factor_scores.date AND r.weights_hash=factor_scores.weights_hash)')
        for ticker, qty in ledger.holdings.items():
            currency = c.execute('SELECT currency FROM instruments WHERE ticker=?', [ticker]).fetchone()
            c.execute('INSERT INTO positions(id,ticker,qty,avg_cost,currency,opened_at,updated_at) '
                      'VALUES (?,?,?,?,?,?,?)', [ticker, ticker, qty, ledger.cost_basis.get(ticker),
                      currency[0] if currency else None, ledger.opened.get(ticker), day.to_pydatetime()])
        return c
    except Exception:  # noqa: BLE001 — no dejar conexiones de simulación abiertas
        c.close()
        raise


def run_replay(conn, spec: ReplaySpec, *, policy=None):
    spec.validate()
    policy = policy or calculate_policy
    frames = read_inputs(conn)
    from ..core.investor_profile import DEFAULTS
    preferences = dict(DEFAULTS)
    if not frames['investor_preferences'].empty:
        preferences.update(json.loads(frames['investor_preferences'].iloc[0].preferences))
    preferences['roundtrip_pct'] = spec.roundtrip_cost * 100
    frames['investor_preferences'] = pd.DataFrame([{'singleton': 1, 'preferences': json.dumps(preferences, sort_keys=True)}])
    h = hashlib.sha256()
    for table, frame in sorted(frames.items()):
        h.update(table.encode())
        h.update(frame.to_json(date_format='iso', orient='split').encode())
    identity = {'spec': asdict(spec), 'engine': engine_fingerprint(), 'inputs': h.hexdigest()}
    identity['run_id'] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    meta = frames['instruments'].set_index('ticker')
    prices = frames['prices_daily'].copy()
    prices.date = pd.to_datetime(prices.date)
    series = {t: f.set_index('date').sort_index() for t, f in prices.groupby('ticker')}
    calendars = {t: calendar_name(row.country, row.exchange) for t, row in meta.iterrows()}
    if spec.benchmark not in series or not calendars.get(spec.benchmark):
        raise ValueError('Falta la referencia prefijada o su calendario; no se sustituye por otra favorable.')
    days = sessions(calendars[spec.benchmark], spec.start, spec.end)
    if len(days) > 2600 or len(days) < 2:
        raise ValueError('Ventana sin sesiones suficientes o demasiado extensa')
    ledger, pending, history, decisions = Ledger(spec.initial_cash), [], [], []
    actions = frames['corporate_actions'].copy()
    actions.date = pd.to_datetime(actions.date)
    previous_day = days[0] - pd.Timedelta(days=1)
    invalid = []
    cost = spec.roundtrip_cost / 2 + spec.slippage

    def price(ticker, day, *, adjusted=False, exact=False):
        data = series.get(ticker, pd.DataFrame())
        if data.empty:
            raise ValueError(f'{ticker}: no hay precios')
        before = data.loc[data.index <= day]
        if before.empty or ticker not in meta.index or not calendars.get(ticker):
            raise ValueError(f'{ticker}: precio, moneda o calendario ausente')
        row = before.iloc[-1]
        expected = sessions(calendars[ticker], day - pd.Timedelta(days=14), day)
        if len(expected) == 0 or before.index[-1] != expected[-1] or (exact and before.index[-1] != day):
            raise ValueError(f'{ticker}: falta una sesión de precio')
        value = float(row.adj_close if adjusted else row.close)
        if not math.isfinite(value) or value <= 0 or pd.isna(row.source) or str(row.source).lower().strip() in ('', 'synthetic'):
            raise ValueError(f'{ticker}: cotización no utilizable')
        currency = meta.loc[ticker, 'currency']
        if currency == 'EUR':
            return value
        fx_data = series.get(fx.PARES.get(currency), pd.DataFrame())
        if fx_data.empty:
            raise ValueError(f'{ticker}: falta conversión a EUR')
        rates = fx_data.loc[(fx_data.index <= day) & (fx_data.index >= day - pd.Timedelta(days=7))]
        if rates.empty:
            raise ValueError(f'{ticker}: cambio de divisa antiguo')
        rate = rates.iloc[-1]
        if pd.isna(rate.source) or str(rate.source).lower().strip() in ('', 'synthetic') or not math.isfinite(rate.close) or rate.close <= 0:
            raise ValueError(f'{ticker}: cambio de divisa inválido')
        return value / rate.close

    initial_benchmark = price(spec.benchmark, days[0], adjusted=True, exact=True)
    # Incluir días de otros mercados donde haya ejecución/eventos entre cierres del benchmark.
    timeline = pd.DatetimeIndex(sorted(set(days) | {d for s in series.values() for d in s.index
                                    if days[0] <= d <= days[-1]}))
    decision_days = set(days[::spec.rebalance_sessions])
    for day in timeline:
        for action in actions[(actions.date > previous_day) & (actions.date <= day)].itertuples():
            if action.ticker not in ledger.holdings:
                continue
            if action.action_type == 'split':
                # La base heredada no declara si close ya incorpora splits.
                # No multiplicar títulos a ciegas y duplicar patrimonio.
                invalid.append(f'{action.ticker}: split sin contrato de ajuste verificado; simulación interrumpida')
                break
            if action.action_type == 'dividend':
                try:
                    raw_close = float(series[action.ticker].loc[day, 'close'])
                    conversion = price(action.ticker, day, exact=True) / raw_close
                    ledger.dividend(day, action.ticker, action.value * conversion)
                except (ValueError, KeyError):
                    invalid.append(f'{action.ticker}: dividendo sin precio/FX verificable')
        if invalid:
            break
        for due, rec in list(pending):
            if due > day:
                continue
            pending.remove((due, rec))
            try:
                p = price(rec.ticker, due, exact=True)
                if rec.veredicto in ('comprar', 'ampliar'):
                    qty = max(0., float(rec.importe_eur or 0.)) / p
                else:
                    held = ledger.holdings.get(rec.ticker, 0.)
                    qty = -held if rec.veredicto == 'vender' else -min(held, float(rec.titulos_a_soltar or 0.))
                ledger.trade(due, rec.ticker, qty, p, cost,
                             price_native=float(series[rec.ticker].loc[due, 'close']))
            except ValueError as exc:
                decisions.append({'date': str(day.date()), 'ticker': rec.ticker, 'verdict': 'sin_ejecucion', 'reason': str(exc)})
        try:
            nav = ledger.nav({t: price(t, day) for t in ledger.holdings})
            if day in days:
                bench = spec.initial_cash / (1 + cost) * price(spec.benchmark, day, adjusted=True) / initial_benchmark
                history.append({'date': day, 'nav_eur': nav, 'cash_eur': ledger.cash,
                                'receivables_eur': ledger.receivables, 'benchmark_eur': bench,
                                'exposure': (nav - ledger.cash - ledger.receivables) / nav if nav else 0.})
        except ValueError as exc:
            invalid.append(str(exc))
            break
        if day in decision_days:
            with snapshot(frames, day, ledger) as isolated:
                recs, _, _, signal_day, _ = policy(isolated, spec.weights_hash, ledger.cash, decision_date=day.date())
            if signal_day is None or pd.Timestamp(signal_day) != day:
                decisions.append({'date': str(day.date()), 'ticker': '', 'verdict': 'sin_opinion',
                                  'reason': 'No hay sesión completa de análisis disponible en esa fecha.'})
            else:
                if not recs:
                    decisions.append({'date': str(day.date()), 'ticker': '', 'verdict': 'sin_opinion',
                                      'reason': 'No hay candidatos evaluables con la información registrada.'})
                for rec in recs:
                    decisions.append({'date': str(day.date()), 'ticker': rec.ticker,
                                      'verdict': str(rec.veredicto), 'reason': ' · '.join(rec.motivos)})
                    if not rec.accionable or not calendars.get(rec.ticker):
                        continue
                    future = sessions(calendars[rec.ticker], day + pd.Timedelta(days=1), day + pd.Timedelta(days=20))
                    due = future[spec.delay_sessions]
                    pending.append((due, rec))
        previous_day = day
    navs = pd.DataFrame(history)
    summary = {'status': 'datos_no_utilizables' if invalid else 'investigacion_parcial',
               'qualified': False, 'pending_orders': len(pending)}
    if not navs.empty and not invalid:
        summary.update(net_return=navs.nav_eur.iloc[-1] / spec.initial_cash - 1,
                       excess=(navs.nav_eur.iloc[-1] - navs.benchmark_eur.iloc[-1]) / spec.initial_cash,
                       cash_return=0., max_drawdown=float((navs.nav_eur / navs.nav_eur.cummax() - 1).min()),
                       mean_exposure=float(navs.exposure.mean()),
                       turnover=sum(abs(m.get('qty', 0.) * m.get('price_eur', 0.)) for m in ledger.movements) / spec.initial_cash,
                       costs=sum(m['cost_eur'] for m in ledger.movements),
                       abstentions=sum(d['verdict'] in ('sin_opinion', 'vetada', 'sin_ejecucion') for d in decisions))
    return {**identity, 'summary': summary, 'nav': navs, 'movements': pd.DataFrame(ledger.movements),
            'decisions': pd.DataFrame(decisions), 'limitations': [*invalid,
             'Sin certificación histórica de metadatos, bajas del universo ni anuncios de resultados.',
             'Solo rankings registrados en su fecha; no se inventan fundamentales anteriores a su observación.',
             'Precios históricos revisables: no equivale a una evaluación prospectiva independiente.',
             'Dividendos reconocidos como derechos no reinvertibles; fecha de pago e impuestos desconocidos.',
             'Splits sin contrato verificable de precios interrumpen el informe; no se adivinan ajustes.',
             'Cierres siguientes, títulos fraccionarios y costes supuestos. No son precios de ejecución garantizados.']}


def run_recorded(conn, spec):
    """Registrar también errores y resultados desfavorables, sin borrar intentos."""
    import uuid

    from ..core.timeutils import utcnow
    from . import experiments

    spec.validate()
    attempt = uuid.uuid4().hex
    params = {**asdict(spec), 'engine': engine_fingerprint()}
    experiment = experiments.Spec('advisor-full-policy', 'research-only', spec.rebalance_sessions,
                                  spec.benchmark, spec.roundtrip_cost * 10000, params=params)
    experiments.congelar(conn, experiment, 'Investigación retrospectiva; no habilita recomendaciones.')
    conn.execute('INSERT INTO advisor_replay_runs VALUES (?, ?, ?, ?, NULL)',
                 [attempt, utcnow(), json.dumps(params, sort_keys=True), 'running'])
    try:
        report = run_replay(conn, spec)
        payload = {k: (json.loads(v.to_json(orient='records', date_format='iso'))
                       if isinstance(v, pd.DataFrame) else v) for k, v in report.items()}
        conn.execute('UPDATE advisor_replay_runs SET status=?, report=? WHERE attempt_id=?',
                     [report['summary']['status'], json.dumps(payload, default=str, sort_keys=True), attempt])
        experiments.registrar(conn, experiment, fase=experiments.DESCUBRIMIENTO,
                              estado=experiments.SIN_DATOS, split_at=pd.Timestamp(spec.end).date(),
                              data_from=spec.start, data_to=spec.end, n_obs=len(report['movements']),
                              n_dates=len(report['nav']), motivo='Política completa: cobertura parcial; no certificable.')
        return report
    except Exception as exc:  # noqa: BLE001 — conservar el intento fallido y propagarlo
        conn.execute('UPDATE advisor_replay_runs SET status=?, report=? WHERE attempt_id=?',
                     ['failed', json.dumps({'error_type': type(exc).__name__}), attempt])
        experiments.registrar(conn, experiment, fase=experiments.DESCUBRIMIENTO,
                              estado=experiments.SIN_DATOS, split_at=pd.Timestamp(spec.end).date(),
                              motivo=f'Replay fallido: {type(exc).__name__}')
        raise

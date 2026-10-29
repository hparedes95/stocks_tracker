"""Puerta final de compras: costes, evidencia y encaje conjunto.

Se ejecuta desde el cálculo real del asesor, no solo desde una pantalla de
investigación. Nunca bloquea advertencias de deterioro de posiciones abiertas.
"""
import math
from dataclasses import replace
from datetime import date

import pandas as pd

from .advice import Conviccion, Veredicto
from .evaluation import BENCHMARKS
from .investor_profile import load
from .market_bundle import engine_fingerprint, state
from .portfolio_risk import returns_in_eur
from .predictive_validation import validate_models
from .trading_calendar import calendar_name


def finite_number(value):
    try:
        return value is not None and math.isfinite(value)
    except (TypeError, ValueError):
        return False


def build_gate(conn, *, as_of, positions, cash, tickers=(), decision_date=None,
               equity_override=None, portfolio_note=''):
    """Build one read-only assessment; reserve cash only for accepted proposals."""
    profile = load(conn)
    from .prediction_plans import lookup
    engine = engine_fingerprint()
    shared = state(conn)
    meta = conn.execute("SELECT ticker, currency, country, exchange, mic FROM instruments").fetchdf().set_index('ticker')
    raw = conn.execute("SELECT p.ticker, p.date, p.adj_close, p.source, q.date IS NOT NULL AS quarantined "
                       "FROM prices_daily p LEFT JOIN prices_quarantine q ON q.ticker=p.ticker AND q.date=p.date "
                       "WHERE p.date <= ? ORDER BY p.ticker, p.date", [as_of]).fetchdf()
    histories = {t: p.drop(columns='ticker') for t, p in raw.groupby('ticker')}
    invalid_positions = not positions.empty and (
        ('supported' in positions and not positions.supported.all()) or
        positions.ticker.isna().any() or
        positions.ticker.astype(str).str.strip().eq('').any() or
        any(not finite_number(v) or v < 0 for v in positions.valor_eur))
    values = (positions.groupby('ticker')['valor_eur'].sum().to_dict()
              if not positions.empty and not invalid_positions else {})
    valid_portfolio = not invalid_positions and finite_number(cash) and cash >= 0
    equity = sum(values.values()) + cash if valid_portfolio else float('nan')
    if equity_override is not None:
        equity = equity_override if valid_portfolio and finite_number(equity_override) and equity_override > 0 else float('nan')
    tickers = sorted(set(values) | set(tickers))
    # Reutilizar una matriz para la correlación y para evaluar en la misma divisa.
    eur_returns = returns_in_eur(conn, tickers + list(set(BENCHMARKS.values()) | {'VT'}), as_of=as_of)
    costs = profile['roundtrip_pct'] / 100
    projected = dict(values)
    available = cash if valid_portfolio else 0.0
    from .snaptrade_portfolio import limitations
    pending = limitations(positions)
    partial = invalid_positions or bool(pending)
    unresolved = set()
    for row in positions.to_dict('records'):
        if (not row.get('supported', True) or not row.get('analysis_supported', True)
                or not finite_number(row.get('valor_eur')) or row['valor_eur'] < 0):
            unresolved.update(str(row.get(key) or '') for key in ('ticker', 'broker_symbol'))
    def review(rec):
        nonlocal available
        if rec.veredicto not in (Veredicto.COMPRAR, Veredicto.AMPLIAR):
            return rec
        reasons = []
        conditional = partial and rec.ticker not in unresolved
        age = (pd.Timestamp(decision_date or date.today()) - pd.Timestamp(as_of)).days
        if age < 0:
            reasons.append("La sesión está en el futuro: no se puede recomendar con ella.")
        if age > 7:
            reasons.append("La base tiene más de siete días: no permite proponer una compra actual.")
        if shared.get('frozen') and shared.get('engine') != engine:
            reasons.append("La copia pertenece a otra versión del motor; importa una copia compatible.")
        if not finite_number(cash) or cash < 0 or (conditional and cash == 0):
            reasons.append("No hay efectivo positivo verificado para plantear una compra.")
        if not conditional and (not math.isfinite(equity) or equity <= 0):
            reasons.append("No se puede valorar íntegramente la cartera y el efectivo.")
        if partial and not conditional:
            reasons.append('Análisis parcial: ' + ' · '.join(pending) +
                           ' No se puede identificar o valorar íntegramente esta posición.')
        if not profile['confirmed']:
            reasons.append("Confirma horizonte, costes y tolerancia a pérdidas en el perfil del asesor.")
        ref = BENCHMARKS.get(meta.loc[rec.ticker, 'country'], 'VT') if rec.ticker in meta.index else 'VT'
        p = histories.get(rec.ticker, pd.DataFrame())
        b = histories.get(ref, pd.DataFrame())
        from .holding_analysis import _technical
        calendar = (calendar_name(meta.loc[rec.ticker, 'country'],
                    meta.loc[rec.ticker, 'mic'] or meta.loc[rec.ticker, 'exchange'])
                    if rec.ticker in meta.index else None)
        p.attrs['calendar'] = calendar
        _, quality_issue = _technical(p, pd.Timestamp(as_of))
        if quality_issue:
            reasons.append(quality_issue)
        # Reconstrucción EUR desde retornos con huecos preservados. No compara
        # rentabilidad de USD contra EUR como si fueran la misma inversión.
        def euro_history(ticker, original):
            if ticker not in eur_returns or original.empty:
                return pd.DataFrame()
            returns = eur_returns[ticker].dropna()
            if returns.empty:
                return pd.DataFrame()
            frame = pd.DataFrame({'date': returns.index, 'adj_close': (1 + returns).cumprod().to_numpy(),
                                  'source': 'derived-eur'})
            if ticker in meta.index:
                frame.attrs['calendar'] = calendar_name(meta.loc[ticker, 'country'], meta.loc[ticker, 'exchange'])
            return frame
        vp, vb = euro_history(rec.ticker, p), euro_history(ref, b)
        if any(t not in meta.index or not calendar_name(meta.loc[t, 'country'], meta.loc[t, 'exchange'])
               for t in (rec.ticker, ref)):
            reasons.append("No se ha identificado el calendario bursátil del valor o de su referencia.")
        plan_id, plan = lookup(conn, rec.ticker, ref, profile, engine)
        validation = validate_models(vp, benchmark=vb, as_of=as_of,
                         horizon=profile['horizon_months'] * 21, roundtrip_cost=costs,
                         family_size=max(1, len(meta)), holdout_start=plan[0] if plan else None)
        strict = profile['recommendation_mode'] == 'validated'
        if strict and plan and plan[1] == 'refutada':
            reasons.append('El protocolo ya fue refutado en el periodo reservado; no se reintenta hasta conseguir un acierto.')
        if strict and not validation.qualified:
            reasons.append(validation.reason)
        elif strict and (not finite_number(validation.lower_excess) or validation.lower_excess <= costs):
            reasons.append("La ventaja estimada no supera costes e incertidumbre.")
        personal_start = len(reasons)
        valid_size = all(finite_number(v) and v > 0 for v in
                         (rec.importe_eur, rec.titulos, rec.stop))
        if not valid_size and not conditional:
            reasons.append('El importe, los títulos o el stop son inválidos; no se propone una compra.')
        amount = rec.importe_eur if valid_size and not conditional else 0.0
        # Reserva coste de entrada: la mitad del supuesto ida/vuelta.
        if amount * (1 + costs / 2) > available:
            reasons.append("El efectivo no cubre el importe propuesto y el coste de entrada estimado.")
        if not conditional and equity > 0 and (sum(projected.values()) + amount) * .30 > equity * profile['max_loss_pct'] / 100:
            shock_pct = (sum(projected.values()) + amount) * .30 / equity * 100
            reasons.append(f"Una caída del 30 % en las posiciones supondría {shock_pct:.1f} % "
                           f"del patrimonio, por encima de tu límite del {profile['max_loss_pct']:.1f} %.")
        cluster = amount
        for held, value in (projected.items() if not conditional else ()):
            if held == rec.ticker:
                cluster += value
            elif held in eur_returns and rec.ticker in eur_returns:
                pair = eur_returns[[held, rec.ticker]].dropna().tail(252)
                if len(pair) < 60:
                    reasons.append(f"Falta histórico común suficiente para medir correlación con {held}.")
                else:
                    correlation = pair.corr().iloc[0, 1]
                    if pd.isna(correlation):
                        reasons.append(f"Correlación no estimable con {held}; no se interpreta como riesgo cero.")
                    elif correlation >= .80:
                        cluster += value
            else:
                reasons.append(f"No se puede contrastar el riesgo conjunto con {held}.")
        if not conditional and equity > 0 and cluster / equity * 100 > profile['max_cluster_pct']:
            reasons.append(f"La exposición conjunta correlacionada sería {cluster / equity * 100:.1f} %, "
                           f"por encima de tu límite del {profile['max_cluster_pct']:.1f} %.")
        assessment = {
            'plan_id': plan_id, 'validation_state': validation.status,
            'decision_basis': profile['recommendation_mode'],
            'datos': f"Corte {as_of}; {len(p)} precios disponibles. Calendario "
                     f"{vp.attrs.get('calendar') or 'desconocido'}. Cobertura no equivale a verificación independiente.",
            'evidencia': ('' if strict else 'Propuesta manual por reglas; sin ventaja predictiva demostrada. ') +
                         validation.reason + ' La simulación del asesor completo sigue en investigación.',
            'encaje': ('Análisis parcial: no se ha podido verificar el encaje conjunto de la cartera. '
                       'Pendiente: ' + ' · '.join(pending) if partial else
                       'Perfil sin confirmar.' if not profile['confirmed'] else
                       ' · '.join(reasons[personal_start:]) or
                       'Dentro de los límites comprobados de efectivo y exposición; no son garantías de pérdida máxima.'),
        }
        if portfolio_note:
            assessment['encaje'] += ' ' + portfolio_note
        earnings = conn.execute("SELECT MIN(report_date) FROM earnings_events WHERE ticker=? "
                               "AND report_date BETWEEN ? AND ?", [rec.ticker, as_of,
                               (pd.Timestamp(as_of) + pd.Timedelta(days=7)).date()]).fetchone()[0]
        if earnings:
            reasons.append(f"Publicación de resultados prevista el {earnings}: riesgo de salto de cotización.")
        from ..providers.sec_provider import annual_review
        official = conn.execute("SELECT * FROM sec_facts WHERE ticker=?", [rec.ticker]).fetchdf()
        review = annual_review(official, as_of=as_of)
        reasons.extend(review['warnings'])
        if reasons:
            return replace(rec, veredicto=Veredicto.SIN_OPINION, conviccion=Conviccion.BAJA,
                                  motivos=list(dict.fromkeys(reasons)), importe_eur=None, titulos=None,
                                  stop=None, riesgo_eur=None, limitado_por='evidencia',
                                  assessment=assessment,
                                  horizonte_meses=profile['horizon_months'])
        else:
            accepted = replace(rec, benchmark=ref, roundtrip_cost=costs,
                                  conviccion=rec.conviccion if strict else Conviccion.BAJA,
                                  assessment=assessment,
                                  horizonte_meses=profile['horizon_months'],
                                  motivos=rec.motivos + ([portfolio_note] if portfolio_note else []) + [assessment['evidencia'],
                                  "Contraste oficial: " + (review['reason'] or "cuentas SEC disponibles; revisar ficha"),
                                  f"Coste de ida y vuelta supuesto: {costs:.2%}; no incluye impuestos.",
                                  "La ausencia de eventos en el calendario no confirma que no los haya."])
            if conditional:
                condition = ('Análisis parcial: ' + ' · '.join(pending) +
                    ' El valor cumple las reglas de selección, pero falta comprobar el peso, '
                    'la concentración y el riesgo total antes de decidir cuánto comprar. '
                    'No es una compra lista para ejecutar y no se asignan importes ni títulos.')
                return replace(accepted,
                    veredicto=(Veredicto.AMPLIACION_CONDICIONADA if rec.veredicto == Veredicto.AMPLIAR
                               else Veredicto.COMPRA_CONDICIONADA),
                    importe_eur=None, titulos=None, stop=None, riesgo_eur=None,
                    limitado_por='valoración pendiente',
                    assessment={**assessment, 'sizing': 'pending'},
                    motivos=[condition] + accepted.motivos)
            projected[rec.ticker] = projected.get(rec.ticker, 0.0) + amount
            available -= amount * (1 + costs / 2)
            return accepted
    return review


def apply_gate(conn, recommendations, *, as_of, positions, cash, decision_date=None):
    review = build_gate(conn, as_of=as_of, positions=positions, cash=cash,
                        tickers=[r.ticker for r in recommendations], decision_date=decision_date)
    return [review(rec) for rec in recommendations]

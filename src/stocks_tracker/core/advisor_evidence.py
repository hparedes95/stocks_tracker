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


def apply_gate(conn, recommendations, *, as_of, positions, cash):
    profile = load(conn)
    shared = state(conn)
    meta = conn.execute("SELECT ticker, currency, country FROM instruments").fetchdf().set_index('ticker')
    raw = conn.execute("SELECT ticker, date, adj_close, source FROM prices_daily "
                       "WHERE date <= ? ORDER BY ticker, date", [as_of]).fetchdf()
    histories = {t: p.drop(columns='ticker') for t, p in raw.groupby('ticker')}
    values = (positions.groupby('ticker')['valor_eur'].sum(min_count=1).to_dict()
              if not positions.empty else {})
    equity = sum(values.values()) + cash
    tickers = sorted(set(values) | {r.ticker for r in recommendations if r.veredicto in ('comprar', 'ampliar')})
    # Reutilizar una matriz para la correlación y para evaluar en la misma divisa.
    eur_returns = returns_in_eur(conn, tickers + list(set(BENCHMARKS.values()) | {'VT'}), as_of=as_of)
    costs = profile['roundtrip_pct'] / 100
    output = []
    projected = dict(values)
    available = cash
    for rec in recommendations:
        if rec.veredicto not in (Veredicto.COMPRAR, Veredicto.AMPLIAR):
            output.append(rec)
            continue
        reasons = []
        if (pd.Timestamp(date.today()) - pd.Timestamp(as_of)).days > 7:
            reasons.append("La base tiene más de siete días: no permite proponer una compra actual.")
        if shared.get('frozen') and shared.get('engine') != engine_fingerprint():
            reasons.append("La copia pertenece a otra versión del motor; importa una copia compatible.")
        if not math.isfinite(equity) or equity <= 0:
            reasons.append("No se puede valorar íntegramente la cartera y el efectivo.")
        if not profile['confirmed']:
            reasons.append("Confirma horizonte, costes y tolerancia a pérdidas en el perfil del asesor.")
        ref = BENCHMARKS.get(meta.loc[rec.ticker, 'country'], 'VT') if rec.ticker in meta.index else 'VT'
        p = histories.get(rec.ticker, pd.DataFrame())
        b = histories.get(ref, pd.DataFrame())
        # Reconstrucción EUR desde retornos con huecos preservados. No compara
        # rentabilidad de USD contra EUR como si fueran la misma inversión.
        def euro_history(ticker, original):
            if ticker not in eur_returns or original.empty:
                return pd.DataFrame()
            returns = eur_returns[ticker].dropna()
            if returns.empty:
                return pd.DataFrame()
            return pd.DataFrame({'date': returns.index, 'adj_close': (1 + returns).cumprod().to_numpy(),
                                 'source': 'derived-eur'})
        vp, vb = euro_history(rec.ticker, p), euro_history(ref, b)
        validation = validate_models(vp, benchmark=vb, as_of=as_of,
                         horizon=profile['horizon_months'] * 21, roundtrip_cost=costs,
                         family_size=max(1, len(meta)))
        if not validation.qualified:
            reasons.append(validation.reason)
        elif validation.lower_excess is None or validation.lower_excess <= costs:
            reasons.append("La ventaja estimada no supera costes e incertidumbre.")
        amount = rec.importe_eur or 0.0
        # Reserva coste de entrada: la mitad del supuesto ida/vuelta.
        if amount * (1 + costs / 2) > available:
            reasons.append("El efectivo no cubre el importe propuesto y el coste de entrada estimado.")
        if equity > 0 and (sum(projected.values()) + amount) * .30 > equity * profile['max_loss_pct'] / 100:
            reasons.append("Un escenario explícito de caída del 30 % superaría tu tolerancia declarada.")
        cluster = amount
        for held, value in projected.items():
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
        if equity > 0 and cluster / equity * 100 > profile['max_cluster_pct']:
            reasons.append("La exposición a posiciones muy correlacionadas supera tu límite conjunto.")
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
            output.append(replace(rec, veredicto=Veredicto.SIN_OPINION, conviccion=Conviccion.BAJA,
                                  motivos=list(dict.fromkeys(reasons)), importe_eur=None, titulos=None,
                                  stop=None, riesgo_eur=None, limitado_por='evidencia',
                                  horizonte_meses=profile['horizon_months']))
        else:
            output.append(replace(rec, benchmark=ref, roundtrip_cost=costs,
                                  horizonte_meses=profile['horizon_months'],
                                  motivos=rec.motivos + [validation.reason,
                                  "Contraste oficial: " + (review['reason'] or "cuentas SEC disponibles; revisar ficha"),
                                  f"Coste de ida y vuelta supuesto: {costs:.2%}; no incluye impuestos.",
                                  "La ausencia de eventos en el calendario no confirma que no los haya."]))
            projected[rec.ticker] = projected.get(rec.ticker, 0.0) + amount
            available -= amount * (1 + costs / 2)
    return output

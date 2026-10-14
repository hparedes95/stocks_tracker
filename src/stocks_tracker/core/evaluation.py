"""Evaluación temporal, en EUR y sin convertir datos ausentes en aciertos."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from . import fx

MODEL_VERSION = "advisor-evidence-v3-sessions"
BENCHMARKS = {"US": "SPY", "ES": "EWP", "DE": "EWG", "FR": "EWQ", "GB": "EWU",
              "United States": "SPY", "Spain": "EWP", "Germany": "EWG", "France": "EWQ",
              "United Kingdom": "EWU"}


def _calculate(conn, *, hasta=None, benchmark=None) -> pd.DataFrame:
    """Fija la salida al vencimiento; entrada al cierre posterior a la señal.

    Se comparan retornos totales ajustados de la misma pareja de sesiones.
    No se sustituye un ETF de retorno total por un índice de precio ausente.
    Las recomendaciones heredadas quedan separadas de la versión nueva.
    """
    cutoff = pd.Timestamp(hasta or pd.Timestamp.today().date()).normalize()
    rows = conn.execute("SELECT * FROM recommendations ORDER BY fecha, ticker").fetchdf()
    ledger = conn.execute("SELECT payload FROM recommendation_ledger ORDER BY recorded_at, observation_id").fetchall()
    if ledger:
        frozen = pd.DataFrame([json.loads(r[0]) for r in ledger])
        frozen['fecha'] = pd.to_datetime(frozen['fecha'])
        keys = ['fecha', 'ticker', 'weights_hash', 'model_version']
        frozen = frozen.drop_duplicates(keys, keep='first')
        if not rows.empty:
            # Las versiones anteriores al registro inmutable siguen accesibles.
            known = set(map(tuple, frozen[['fecha', 'ticker', 'weights_hash']].to_numpy()))
            rows = rows[[tuple(r) not in known for r in rows[['fecha', 'ticker', 'weights_hash']].to_numpy()]]
        rows = pd.concat([rows, frozen], ignore_index=True)
    if rows.empty:
        return rows
    prices = conn.execute(
        "SELECT ticker, date, close, adj_close, source FROM prices_daily WHERE date <= ? "
        "ORDER BY ticker, date", [cutoff.date()]).fetchdf()
    meta = conn.execute("SELECT ticker, currency, country FROM instruments").fetchdf()
    if prices.empty or meta.empty:
        return rows.iloc[:0]
    meta = meta.drop_duplicates("ticker").set_index("ticker")
    prices["date"] = pd.to_datetime(prices["date"])
    series = {t: p.set_index("date") for t, p in prices.groupby("ticker")}
    output = []
    for _, row in rows.iterrows():
        ticker = row["ticker"]
        if ticker not in meta.index or ticker not in series:
            continue
        currency = meta.loc[ticker, "currency"]
        recorded_benchmark = row.get("benchmark")
        ref = benchmark or (recorded_benchmark if pd.notna(recorded_benchmark) else None)
        ref = ref or BENCHMARKS.get(meta.loc[ticker, "country"], "VT")
        if ref not in series or ref not in meta.index or not currency:
            continue
        ref_currency = meta.loc[ref, "currency"]
        if pd.isna(currency) or pd.isna(ref_currency) or not ref_currency:
            continue
        start = pd.Timestamp(row["fecha"])
        issued = pd.to_datetime(row.get('emitida_at'), errors='coerce', utc=True)
        if pd.notna(issued):
            # Recalcular hoy sobre una copia antigua no crea un consejo pasado.
            start = max(start, issued.tz_localize(None).normalize())
        months = row.get("horizonte_meses", 6)
        if pd.isna(months) or not 1 <= months <= 120:
            continue
        session_horizon = row.get('horizon_sessions')
        if pd.notna(session_horizon):
            from .trading_calendar import dates_for_horizon
            if not row.get('calendar_name') or pd.isna(row.get('calendar_name')):
                continue
            try:
                expected_entry, maturity = dates_for_horizon(row['calendar_name'], start, int(session_horizon))
            except ValueError:
                continue  # Una fila incompatible no invalida las otras recomendaciones.
        else:
            maturity = start + pd.DateOffset(months=int(months))
        if maturity > cutoff:
            continue
        asset, bench = series[ticker], series[ref]
        common = asset.index.intersection(bench.index).sort_values()
        entries = common[(common > start) & (common <= start + pd.Timedelta(days=7))]
        exits = common[(common >= maturity) & (common <= maturity + pd.Timedelta(days=7))]
        if entries.empty or exits.empty:
            continue
        entry, end = entries[0], exits[0]
        if pd.notna(session_horizon) and (entry != expected_entry or end != maturity):
            continue  # Una cotización ausente no desplaza la ejecución ni el vencimiento.
        pair = [entry, end]
        samples = [asset.loc[pair], bench.loc[pair]]
        if any(s["source"].isna().any() or s["source"].nunique() != 1 or
               s["source"].astype(str).str.strip().str.lower().isin(["synthetic", ""]).any() or
               not np.isfinite(s["adj_close"]).all() or (s["adj_close"] <= 0).any()
               for s in samples):
            continue
        rates = fx.tipos_en(conn, pair)
        # tipos_en permite arrastre; aquí no se acepta FX antiguo.
        valid_fx = True
        for curr in {currency, ref_currency} - {"EUR"}:
            fx_ticker = fx.PARES.get(curr)
            for day in pair:
                p = series.get(fx_ticker, pd.DataFrame())
                if p.empty or p.loc[(p.index <= day) &
                                   (p.index >= day - pd.Timedelta(days=7))].empty:
                    valid_fx = False
                    continue
                latest = p.loc[p.index <= day].iloc[-1]
                if (pd.isna(latest['source']) or str(latest['source']).strip().lower() in ('', 'synthetic') or
                        pd.isna(latest['close']) or not np.isfinite(latest['close']) or latest['close'] <= 0):
                    valid_fx = False
        if not valid_fx:
            continue
        def converted(sample, curr, pair=pair, rates=rates):
            return fx.a_base_en_fecha(sample["adj_close"].reset_index(drop=True),
                                      pd.Series([curr, curr]), pd.Series(pair), rates)
        a, b = converted(samples[0], currency), converted(samples[1], ref_currency)
        if a.isna().any() or b.isna().any() or (a <= 0).any() or (b <= 0).any():
            continue
        ret, market = float(a.iloc[1] / a.iloc[0] - 1), float(b.iloc[1] / b.iloc[0] - 1)
        cost = row.get("roundtrip_cost", np.nan)
        cost = float(cost) if pd.notna(cost) and 0 <= cost < 1 else np.nan
        excess = ret - market
        buy = row["veredicto"] in ("comprar", "ampliar")
        output.append({**row.to_dict(), "vence": maturity, "entrada": entry, "salida": end,
                       "benchmark": ref, "retorno": ret, "retorno_indice": market,
                       "retorno_neto": ret - cost, "exceso": excess,
                       "ventaja_bruta": excess if buy else -excess,
                       "ventaja_neta": (excess if buy else -excess) - cost,
                       "acierto": excess > 0 if buy else excess < 0,
                       "model_version": (str(row['model_version']) if pd.notna(row.get('model_version')) else "legacy"),
                       "metodo": "retorno total; cierre siguiente; EUR; costes estimados, sin impuestos"})
    return pd.DataFrame(output)


def observation_key(row):
    fields = ('fecha', 'ticker', 'weights_hash', 'model_version', 'emitida_at', 'benchmark')
    values = {}
    for key in fields:
        value = row.get(key)
        if value is None or pd.isna(value):
            values[key] = None
        elif key in ('fecha', 'emitida_at'):
            values[key] = pd.Timestamp(value).isoformat()
        else:
            values[key] = str(value)
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def evaluate(conn, *, hasta=None, benchmark=None):
    """Primera evaluación cerrada inmutable; revisiones de precios no reescriben aciertos."""
    current = _calculate(conn, hasta=hasta, benchmark=benchmark)
    cutoff = pd.Timestamp(hasta or pd.Timestamp.today().date())
    stored = conn.execute('SELECT payload FROM evaluation_ledger ORDER BY evaluated_at').fetchall()
    result = {observation_key(r): r for r in current.to_dict('records')}
    for payload, in stored:
        row = json.loads(payload)
        if pd.Timestamp(row['salida']) > cutoff or (benchmark and row['benchmark'] != benchmark):
            continue
        for field in ('fecha', 'entrada', 'salida', 'vence', 'emitida_at'):
            if row.get(field) is not None:
                row[field] = pd.Timestamp(row[field])
        result[observation_key(row)] = row
    return pd.DataFrame(result.values())


def freeze_evaluations(conn, *, hasta=None):
    from .timeutils import utcnow
    frame = _calculate(conn, hasta=hasta)
    for row in frame.to_dict('records'):
        conn.execute('INSERT INTO evaluation_ledger VALUES (?, ?, ?) ON CONFLICT DO NOTHING',
                     [observation_key(row), utcnow(), json.dumps(row, default=str, sort_keys=True)])
    return len(frame)


def independent_episodes(frame: pd.DataFrame) -> pd.DataFrame:
    """Una exposición no solapada por acción/modelo/perfil, no un voto por día.

    No elimina la correlación entre acciones: nunca se etiqueta independencia
    estadística total. Los bloques temporales se usan al validar estrategias.
    """
    if frame.empty or not {"entrada", "salida", "ticker"}.issubset(frame):
        return frame
    keys = [k for k in ("ticker", "model_version", "weights_hash", "benchmark", "horizonte_meses") if k in frame]
    chosen = []
    for _, group in frame.groupby(keys, dropna=False, sort=True):
        last_exit = pd.Timestamp.min
        for idx, row in group.sort_values(["entrada", "salida"]).iterrows():
            if row["entrada"] > last_exit:
                chosen.append(idx)
                last_exit = row["salida"]
    return frame.loc[chosen].copy()

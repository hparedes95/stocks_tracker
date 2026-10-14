"""Complemento oficial acotado: cartera y candidatos, sin requerir claves."""
from datetime import timedelta

from stocks_tracker.core.db import connect, upsert_df
from stocks_tracker.core.market_bundle import frozen_market
from stocks_tracker.core.timeutils import utcnow
from stocks_tracker.providers.sec_provider import fetch_company, ticker_map


def _log(target, status, rows=0):
    now = utcnow()
    with connect() as conn:
        conn.execute("INSERT INTO ingest_log (run_id,task,target,status,started_at,finished_at,rows_written) "
                     "VALUES (?, 'sec', ?, ?, ?, ?, ?)",
                     [f"sec-{target}-{now.isoformat()}", target, status, now, now, rows])


def refresh(ticker: str | None = None, *, limit=15) -> dict:
    if frozen_market():
        return {"updated": 0, "reason": "Copia compartida: no se modifica el histórico importado."}
    with connect(read_only=True) as conn:
        if ticker:
            tickers = [ticker]
        else:
            rows = conn.execute("SELECT ticker FROM positions WHERE closed_at IS NULL "
                                "UNION SELECT ticker FROM factor_scores "
                                "WHERE date=(SELECT MAX(date) FROM factor_scores) AND composite_pctile>=0.9 "
                                "ORDER BY ticker").fetchall()
            tickers = [r[0] for r in rows][:limit]
        # Los estados no cambian diariamente: caché local de solicitudes exitosas.
        fresh = {r[0] for r in conn.execute("SELECT target FROM ingest_log WHERE task='sec' "
                 "AND status IN ('OK','UNAVAILABLE') AND finished_at>=?",
                 [utcnow() - timedelta(hours=24)]).fetchall()}
    if '__ticker_map__' in fresh:
        return {"updated": 0, "reason": "SEC no disponible recientemente; pausa de 24 horas para no insistir ante bloqueos."}
    wanted = [t for t in tickers if t not in fresh]
    if not wanted:
        return {"updated": 0, "reason": "Datos oficiales consultados recientemente."}
    try:
        mapping = ticker_map()
    except Exception:  # noqa: BLE001 — proveedor opcional, ausencia explícita sin datos inventados
        _log('__ticker_map__', 'UNAVAILABLE')
        return {"updated": 0, "reason": "SEC no disponible; no se inventan datos oficiales."}
    updated, errors = 0, []
    for symbol in wanted:
        cik = mapping.get(symbol.replace('-', '.')) or mapping.get(symbol)
        if not cik:
            _log(symbol, 'UNAVAILABLE')
            errors.append(symbol)
            continue
        try:
            frame = fetch_company(symbol, cik)
            if not frame.empty:
                with connect() as conn:
                    updated += upsert_df(conn, 'sec_facts', frame,
                         keys=['cik', 'concept', 'unit', 'period_start', 'period_end', 'accession'])
                _log(symbol, 'OK', len(frame))
            else:
                _log(symbol, 'UNAVAILABLE')
                errors.append(symbol)
        except Exception:  # noqa: BLE001 — aislar un emisor sin ocultar los no disponibles
            _log(symbol, 'UNAVAILABLE')
            errors.append(symbol)
    return {"updated": updated, "reason": "Consulta oficial completada.", "unavailable": errors}


def main():
    import json

    from stocks_tracker.core.db import migrate
    migrate()
    print(json.dumps(refresh(), ensure_ascii=False))


if __name__ == '__main__':
    main()

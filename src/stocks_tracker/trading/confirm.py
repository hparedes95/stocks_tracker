"""Lo que el freno de mano dejo esperando, y que hacer con ello.

    python -m stocks_tracker.trading.confirm              # ver que hay
    python -m stocks_tracker.trading.confirm --aprobar ID
    python -m stocks_tracker.trading.confirm --rechazar ID

Sin esto, `guarded` seria peor que `auto`: las ordenes que cruzan un freno se
quedarian retenidas y nadie podria ni aprobarlas ni rechazarlas, o sea que se
perderian en silencio. El freno solo tiene sentido si hay una forma de
levantarlo.

**Una confirmacion caduca.** Aprobar dos dias despues una orden calculada con
el precio del lunes no es aprobar lo que se propuso: es mandar al mercado una
decision que ya no corresponde a ningun precio. Por eso las pendientes tienen
`expires_at` y las caducadas se descartan solas, sin preguntar.

**Aprobar aqui NO se salta el riesgo.** La orden ya paso por `risk.py` cuando
se propuso; lo que estaba en pausa era solo el ultimo paso. Y se vuelve a
comprobar el precio antes de enviarla: si se ha movido mas de lo que el
mandato tolera, se rechaza aunque la hayas aprobado, porque lo que aprobaste
era otra cosa.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from ..core.config import get_trading_config
from ..core.db import connect
from ..core.textutils import as_float, as_text


@dataclass(frozen=True)
class Pending:
    """Una orden retenida, con lo necesario para decidir sin abrir la base."""

    intent_id: str
    created_at: datetime
    expires_at: datetime | None
    ticker: str
    side: str
    intent_type: str
    qty: float | None
    notional: float | None
    ref_price: float
    stop_price: float | None
    reason: str
    rationale: dict

    @property
    def short_id(self) -> str:
        """Los ultimos seis caracteres bastan para teclearlo sin equivocarse."""
        return self.intent_id[-6:]

    def expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or datetime.now()) >= self.expires_at

    def hours_left(self, now: datetime | None = None) -> float:
        if self.expires_at is None:
            return float("inf")
        return (self.expires_at - (now or datetime.now())).total_seconds() / 3600.0


def _row_to_pending(row) -> Pending:
    try:
        rationale = json.loads(row[11] or "{}")
    except (ValueError, TypeError):
        rationale = {}
    return Pending(
        intent_id=as_text(row[0]), created_at=row[1], expires_at=row[2],
        ticker=as_text(row[3]), side=as_text(row[4]), intent_type=as_text(row[5]),
        qty=as_float(row[6]) or None, notional=as_float(row[7]) or None,
        ref_price=as_float(row[8]), stop_price=as_float(row[9]) or None,
        reason=as_text(row[10]), rationale=rationale,
    )


_SELECT = (
    "SELECT intent_id, created_at, expires_at, ticker, side, intent_type, "
    "qty_approved, notional_approved, ref_price, stop_price, decision_note, "
    "rationale FROM intents WHERE status = 'PENDING_CONFIRMATION'"
)


def pending(include_expired: bool = False) -> list[Pending]:
    """Ordenes esperando confirmacion, la mas antigua primero."""
    with connect(read_only=True) as conn:
        filas = conn.execute(_SELECT + " ORDER BY created_at").fetchall()
    out = [_row_to_pending(f) for f in filas]
    return out if include_expired else [p for p in out if not p.expired()]


def expire_stale(now: datetime | None = None) -> int:
    """Descarta las caducadas. Devuelve cuantas.

    Se hace solo y sin preguntar: una orden calculada con el precio de hace
    dos dias no es la orden que se propuso, y aprobarla tarde manda al mercado
    una decision que ya no corresponde a ningun precio.
    """
    ahora = now or datetime.now()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE intents SET status = 'EXPIRED', decided_by = 'sistema', "
            "decided_at = ?, decision_note = 'Caducada sin confirmar' "
            "WHERE status = 'PENDING_CONFIRMATION' AND expires_at IS NOT NULL "
            "AND expires_at <= ?",
            [ahora, ahora],
        )
        return int(cur.fetchall()[0][0]) if cur.description else 0


def find(short_or_full: str) -> Pending | None:
    """Busca por identificador completo o por sus ultimos caracteres."""
    clave = short_or_full.strip()
    if not clave:
        return None
    for p in pending(include_expired=True):
        if p.intent_id == clave or p.intent_id.endswith(clave):
            return p
    return None


def reject(intent_id: str, note: str = "Rechazada a mano") -> bool:
    with connect() as conn:
        conn.execute(
            "UPDATE intents SET status = 'REJECTED', decided_by = 'humano', "
            "decided_at = ?, decision_note = ? WHERE intent_id = ? "
            "AND status = 'PENDING_CONFIRMATION'",
            [datetime.now(), note, intent_id],
        )
    return True


def price_drift_pct(p: Pending, current_price: float) -> float:
    """Cuanto se ha movido el precio desde que se propuso, en porcentaje."""
    if p.ref_price <= 0:
        return 0.0
    return abs(current_price - p.ref_price) / p.ref_price * 100.0


def approve(intent_id: str, broker, current_price: float | None = None,
            max_drift_pct: float | None = None) -> tuple[bool, str]:
    """Envia al broker una orden retenida. Devuelve (enviada, motivo).

    Comprueba el precio antes de mandarla. Si se ha movido mas de lo que el
    mandato tolera, se rechaza aunque la hayas aprobado: lo que aprobaste era
    una orden a otro precio, y ejecutarla igual seria darle a tu confirmacion
    un significado que no tenia.
    """
    from .brokers.base import OrderRequest

    p = find(intent_id)
    if p is None:
        return False, f"No hay ninguna orden pendiente con '{intent_id}'."
    if p.expired():
        expire_stale()
        return False, (
            f"{p.ticker}: la orden caduco hace "
            f"{-p.hours_left():.1f} h. Se propondra de nuevo en el proximo "
            "ciclo con el precio de entonces."
        )

    if max_drift_pct is None:
        max_drift_pct = as_float(
            get_trading_config().execution.get("max_price_drift_pct"), 2.0
        )
    if current_price is not None:
        drift = price_drift_pct(p, current_price)
        if drift > max_drift_pct:
            reject(p.intent_id, f"Precio movido {drift:.1f}% desde la propuesta")
            return False, (
                f"{p.ticker}: el precio se ha movido un {drift:.1f}% desde que "
                f"se propuso (limite {max_drift_pct:.1f}%). No se envia: lo que "
                "aprobaste era una orden a otro precio."
            )

    request = OrderRequest(
        symbol=p.ticker, side=p.side, client_order_id=f"st-{p.intent_id}",
        qty=p.qty if p.side == "sell" else None,
        notional=p.notional if p.side == "buy" else None,
    )
    try:
        broker.submit_order(request)
    except Exception as exc:  # noqa: BLE001 — el motivo es lo unico que importa
        return False, f"{p.ticker}: el broker la ha rechazado: {exc}"

    with connect() as conn:
        conn.execute(
            "UPDATE intents SET status = 'SUBMITTED', decided_by = 'humano', "
            "decided_at = ?, decision_note = 'Confirmada a mano' "
            "WHERE intent_id = ?",
            [datetime.now(), p.intent_id],
        )
    return True, f"{p.ticker}: orden enviada."


# ---------------------------------------------------------------------------
def render(pendientes: list[Pending]) -> str:
    lineas = ["", "  Ordenes esperando tu confirmacion", "  " + "=" * 64, ""]
    if not pendientes:
        lineas += ["  No hay ninguna.", ""]
        return "\n".join(lineas)

    for p in pendientes:
        importe = (f"{p.notional:.2f} EUR" if p.notional
                   else f"{p.qty:.6f} unidades" if p.qty else "?")
        lineas.append(f"  [{p.short_id}]  {p.side.upper():4s} {p.ticker}  {importe}")
        lineas.append(f"           precio de referencia {p.ref_price:.2f}"
                      + (f", stop {p.stop_price:.2f}" if p.stop_price else ""))
        lineas.append(f"           caduca en {p.hours_left():.1f} h")
        lineas.append(f"           por que espera: {p.reason}")
        for razon in (p.rationale.get("reasons") or [])[:3]:
            lineas.append(f"           - {razon}")
        lineas.append("")

    lineas += [
        "  Para decidir:",
        "    python -m stocks_tracker.trading.confirm --aprobar <id>",
        "    python -m stocks_tracker.trading.confirm --rechazar <id>",
        "",
        "  Aprobar no se salta el riesgo: la orden ya paso por el mandato. Lo",
        "  que estaba en pausa era el ultimo paso, y el precio se comprueba de",
        "  nuevo antes de enviarla.",
        "",
    ]
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Ordenes retenidas por el freno de mano."
    )
    parser.add_argument("--aprobar", metavar="ID", default=None)
    parser.add_argument("--rechazar", metavar="ID", default=None)
    parser.add_argument("--venue", default="kraken",
                        help="Mercado al que enviar la orden aprobada")
    args = parser.parse_args(argv)

    caducadas = expire_stale()

    if args.rechazar:
        p = find(args.rechazar)
        if p is None:
            print(f"\n  No hay ninguna orden pendiente con '{args.rechazar}'.\n")
            return 1
        reject(p.intent_id)
        print(f"\n  {p.ticker}: rechazada. No se enviara.\n")
        return 0

    if args.aprobar:
        from .brokers.registry import build_broker

        p = find(args.aprobar)
        if p is None:
            print(f"\n  No hay ninguna orden pendiente con '{args.aprobar}'.\n")
            return 1
        broker = build_broker(args.venue)
        precio = None
        try:
            precios = broker.get_latest_price([p.ticker])
            precio = precios.get(p.ticker)
        except Exception as exc:  # noqa: BLE001
            print(f"\n  No se ha podido comprobar el precio actual: {exc}")
            print("  No se envia: aprobar sin saber el precio es firmar en "
                  "blanco.\n")
            return 1
        enviada, motivo = approve(p.intent_id, broker, current_price=precio)
        print(f"\n  {motivo}\n")
        return 0 if enviada else 1

    pendientes = pending()
    print(render(pendientes))
    if caducadas:
        print(f"  ({caducadas} caducada(s) descartada(s) por el camino)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

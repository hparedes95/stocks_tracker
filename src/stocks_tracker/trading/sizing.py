"""Cuanto comprar. Dimensionamiento por volatilidad.

La idea: arriesgar siempre la misma cantidad de dinero, no comprar siempre la
misma cantidad de dinero. Un valor que se mueve un 6 % al dia y otro que se
mueve un 1 % no pueden llevar el mismo importe si el stop esta a la misma
distancia en ATR, porque el primero saltaria el stop con el ruido de un martes
cualquiera.

    riesgo          = equity x risk_per_trade_pct / 100
    distancia_stop  = atr_stop_mult x ATR14
    qty_teorica     = riesgo / distancia_stop
    notional        = min(qty_teorica x precio,
                          tope por activo,
                          objetivo x factor de regimen,
                          efectivo disponible - reserva minima)

Con 50 EUR y un ATR tipico del 2,5 % esto da posiciones de 8-12 EUR, o sea 4-6
posiciones, que es el mandato.
"""

from __future__ import annotations

from dataclasses import dataclass

# El regimen no cambia QUE se compra, cambia CUANTO. En un mercado hostil la
# misma idea merece menos dinero: es la forma barata de equivocarse menos.
REGIME_FACTOR = {"risk_on": 1.0, "neutral": 0.8, "risk_off": 0.5}


@dataclass(frozen=True)
class SizingResult:
    notional: float
    qty: float
    stop_price: float
    risk_amount: float
    reason_code: str = "OK"
    capped_by: str = ""

    @property
    def ok(self) -> bool:
        return self.reason_code == "OK"


def regime_factor(regime: str) -> float:
    return REGIME_FACTOR.get(regime, 0.8)


def size_by_atr(
    *,
    equity: float,
    price: float,
    atr14: float,
    cash_available: float,
    regime: str,
    risk_per_trade_pct: float,
    atr_stop_mult: float,
    max_position_pct: float,
    target_position_pct: float,
    min_cash_pct: float,
    min_notional: float,
) -> SizingResult:
    """Tamano de una compra nueva. Nunca relaja un limite para que quepa."""
    if price <= 0 or atr14 is None or atr14 <= 0 or equity <= 0:
        return SizingResult(0.0, 0.0, 0.0, 0.0, "NO_SIZING_INPUTS")

    risk_amount = equity * risk_per_trade_pct / 100.0
    stop_distance = atr_stop_mult * atr14
    stop_price = price - stop_distance
    if stop_price <= 0:
        # Un stop en negativo significa que el ATR es tan grande frente al
        # precio que la posicion no se puede proteger. No es un caso raro en
        # valores de pocos euros, y es exactamente donde no hay que entrar.
        return SizingResult(0.0, 0.0, 0.0, 0.0, "STOP_BELOW_ZERO")

    theoretical = (risk_amount / stop_distance) * price
    reserve = equity * min_cash_pct / 100.0
    caps = {
        "riesgo_por_operacion": theoretical,
        "tope_por_activo": equity * max_position_pct / 100.0,
        "objetivo_por_regimen": equity * target_position_pct / 100.0 * regime_factor(regime),
        "efectivo_disponible": max(cash_available - reserve, 0.0),
    }
    capped_by = min(caps, key=caps.get)
    notional = caps[capped_by]

    if notional < min_notional:
        # Excepcion de escala: con 50 EUR el calculo puede dar menos del minimo
        # del broker (1 $). Se sube al minimo SOLO si eso no rompe el tope por
        # activo. Si lo rompe, se veta: nunca se relaja un limite para que
        # quepa una orden.
        if min_notional <= caps["tope_por_activo"] and min_notional <= caps[
            "efectivo_disponible"
        ]:
            notional = min_notional
            capped_by = "minimo_del_broker"
        else:
            return SizingResult(
                0.0, 0.0, stop_price, 0.0, "POSITION_TOO_SMALL_FOR_RISK", capped_by
            )

    qty = notional / price
    return SizingResult(
        notional=notional,
        qty=qty,
        stop_price=stop_price,
        # El riesgo real es el de la posicion que de verdad se abre, no el
        # teorico: si el tamano se ha recortado por un tope, se arriesga menos.
        risk_amount=qty * stop_distance,
        capped_by=capped_by,
    )


def trailing_stop(
    entry_price: float, highest_close: float, atr14: float, atr_stop_mult: float
) -> float:
    """Stop que sube con el precio y nunca baja.

    Que no baje es la propiedad importante: un stop que retrocede cuando el
    precio retrocede no protege de nada, solo retrasa la perdida.
    """
    base = entry_price - atr_stop_mult * atr14
    trailed = highest_close - atr_stop_mult * atr14
    return max(base, trailed)

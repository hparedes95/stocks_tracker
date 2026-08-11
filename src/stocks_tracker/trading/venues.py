"""Que venues estan listos, cuales no, y por que.

Este modulo existe para que la respuesta a "¿ya puedo usarlo?" sea una frase y
no una traza. El camino que pidio el usuario —poner las claves y usarlo— se
rompe si al primer intento sale un `KeyError` en mitad de un adaptador: no
dice que falta, ni donde ponerlo, ni si el problema es suyo o mio.

Un venue esta listo cuando se cumplen TRES cosas, y las tres se comprueban por
separado porque fallan por motivos distintos:

1. **Configurado**: existe en `trading.yaml` y esta `enabled`.
2. **Con credenciales**: las claves estan en `.env`.
3. **Validado**: su estrategia ha superado la puerta. Sin esto se puede
   simular, pero no operar con dinero.

Las tres son necesarias. La tercera es la que la gente se salta.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import secrets
from ..core.config import ConfigError, VenueConfig, get_trading_config

# Que credenciales necesita cada venue. El nombre del venue en `trading.yaml`
# es el mismo que agrupa las credenciales en `core.secrets`.
VENUE_MODES = {
    "kraken": ("simulated", "paper", "live"),
    # Polymarket no tiene entorno de pruebas oficial: o simulas contra datos
    # historicos, o operas con dinero de verdad. No hay punto intermedio, y
    # conviene saberlo antes de disenar la progresion.
    "polymarket": ("simulated", "live"),
}


@dataclass(frozen=True)
class VenueStatus:
    """Estado de un venue, en terminos de lo que el usuario puede hacer."""

    key: str
    label: str
    configured: bool
    enabled: bool
    credentials_ok: bool
    missing_credentials: tuple[str, ...]
    validated: bool
    capital_cap: float = 0.0
    currency: str = ""

    @property
    def can_simulate(self) -> bool:
        """Simular no necesita ni claves ni validacion: no toca dinero."""
        return self.configured

    @property
    def can_trade(self) -> bool:
        return self.configured and self.enabled and self.credentials_ok and self.validated

    def why_not(self) -> str:
        """Una frase que explica que falta. Vacia si no falta nada."""
        if not self.configured:
            return f"'{self.key}' no esta en config/trading.yaml"
        if self.missing_credentials:
            faltan = ", ".join(self.missing_credentials)
            return (f"faltan credenciales en .env: {faltan}. "
                    "Mira `stocks.ps1 claves`")
        if not self.enabled:
            return (f"'{self.key}' esta configurado pero desactivado. "
                    f"Pon `enabled: true` en su bloque de config/trading.yaml")
        if not self.validated:
            return ("su estrategia no ha superado la validacion. Se puede "
                    "simular, pero no operar")
        return ""


def _is_validated(venue: str) -> bool:
    """Si existe un informe de puerta superado para este venue.

    Se consulta la base y no un fichero de configuracion a proposito: la
    validacion es un hecho medido, no una opcion que se pueda poner a mano.
    """
    from ..core.db import connect

    try:
        with connect(read_only=True) as conn:
            row = conn.execute(
                "SELECT passed FROM gate_reports WHERE strategy_id LIKE ? "
                "ORDER BY logged_at DESC LIMIT 1",
                [f"%{venue}%"],
            ).fetchone()
    except Exception:  # noqa: BLE001 — sin almacen todavia
        return False
    return bool(row and row[0])


def status(venue: str) -> VenueStatus:
    cfg = get_trading_config()
    try:
        vcfg: VenueConfig | None = cfg.venue(venue)
    except ConfigError:
        vcfg = None

    ok, faltan = secrets.venue_ready(venue)
    return VenueStatus(
        key=venue,
        label=vcfg.label if vcfg else venue,
        configured=vcfg is not None,
        enabled=bool(vcfg and vcfg.enabled),
        credentials_ok=ok,
        missing_credentials=tuple(faltan),
        validated=_is_validated(venue),
        capital_cap=vcfg.capital_cap if vcfg else 0.0,
        currency=vcfg.quote_currency if vcfg else "",
    )


def all_status() -> list[VenueStatus]:
    return [status(v) for v in sorted(get_trading_config().venues)]


def require_tradeable(venue: str, mode: str) -> VenueStatus:
    """Comprueba que se puede operar, o explica por que no.

    Falla cerrado y con un mensaje accionable. La alternativa —dejar que el
    adaptador reviente cuando pida el saldo— produce un error que no dice ni
    que falta ni donde ponerlo.
    """
    st = status(venue)

    if mode not in VENUE_MODES.get(venue, ()):  # noqa: SIM118
        permitidos = ", ".join(VENUE_MODES.get(venue, ())) or "ninguno"
        raise ConfigError(
            f"El modo '{mode}' no existe en {venue}. Modos: {permitidos}."
            + ("\n  Polymarket no tiene entorno de pruebas: o se simula contra "
               "historico, o se opera con dinero real." if venue == "polymarket" else "")
        )

    if mode == "simulated":
        if not st.configured:
            raise ConfigError(st.why_not())
        return st

    if not st.can_trade:
        raise ConfigError(f"No se puede operar en {st.label}: {st.why_not()}")
    return st


# ---------------------------------------------------------------------------
def main() -> int:
    """`python -m stocks_tracker.trading.venues` — estado de cada mercado."""
    print()
    print("  Mercados del bot")
    print("  " + "=" * 62)
    for st in all_status():
        marca = "LISTO" if st.can_trade else "no"
        print(f"  [{marca:5s}] {st.label}")
        print(f"          tope {st.capital_cap:.0f} {st.currency}")
        if st.can_trade:
            print("          credenciales OK, validado, activado")
        else:
            print(f"          falta: {st.why_not()}")
        print()
    print("  Simular no necesita nada de esto: no toca dinero.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

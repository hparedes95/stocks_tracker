"""Cuanto puede hacer el bot sin preguntar, y donde se para a preguntarlo.

Tres niveles:

| nivel     | que hace                                                        |
|-----------|-----------------------------------------------------------------|
| `semi`    | Todo pide confirmacion. Ninguna orden sale sola.                 |
| `guarded` | Automatico, salvo lo que cruce un freno. **El de dinero real.**  |
| `auto`    | Nada pide confirmacion.                                          |

Esto vive ENTRE el riesgo y el broker, y no dentro de ninguno de los dos. El
riesgo responde a "¿esta orden respeta el mandato?" y su respuesta no depende
de quien la mire. La autonomia responde a "¿hace falta que un humano lo vea
antes?", que es otra pregunta y con otra respuesta segun el modo. Mezclarlas
haria que el mismo limite se comportase distinto en simulacion y en real, y
entonces el backtest dejaria de describir lo que va a pasar.

**Por que existe `guarded` y por que es el nivel de dinero real.** Los frenos
no estan puestos donde la estrategia puede equivocarse —para eso esta la
puerta de validacion— sino donde puede equivocarse el PROGRAMA. Una estrategia
mediocre pierde unos euros despacio; un fallo de dimensionamiento o de
duplicacion de ordenes se los lleva de golpe, y en automatico nadie lo ve
hasta despues. Los tres frenos corresponden a los tres sitios donde ese tipo
de fallo aparece primero:

1. **Importe.** Un error de calculo del tamano se manifiesta como una orden
   mucho mayor de lo normal. Es el sintoma mas fiable y el mas caro.
2. **Primera orden en real.** Es el unico momento en que el codigo toca dinero
   por primera vez. Todo lo que estaba mal y las pruebas no vieron sale aqui.
3. **Estando en perdidas.** Abrir posiciones nuevas mientras la cartera cae es
   doblar la apuesta justo cuando algo va mal, y "algo va mal" incluye "hay un
   fallo que todavia no hemos visto".

Ninguno de los tres es un limite de riesgo: los limites ya los aplico
`risk.py` y siguen aplicandose igual. Un freno solo decide si la orden sale
sola o espera a que la mires.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..core.config import ConfigError, TradingConfig

# Valores por defecto si el mandato no dice otra cosa. Conservadores a
# proposito: lo que no se ha decidido no se decide solo, y menos hacia el lado
# que gasta dinero sin preguntar.
DEFAULT_BRAKES = {
    "confirm_above_eur": 10.0,
    "confirm_first_live_order": True,
    "confirm_when_drawdown_over_pct": 8.0,
}


class Autonomy(StrEnum):
    SEMI = "semi"
    GUARDED = "guarded"
    AUTO = "auto"


@dataclass(frozen=True)
class Brake:
    """Un motivo por el que una orden espera. `code` para filtrar, `text` para leer."""

    code: str
    text: str


def parse(level: str) -> Autonomy:
    try:
        return Autonomy(str(level))
    except ValueError:
        permitidos = ", ".join(a.value for a in Autonomy)
        raise ConfigError(
            f"Nivel de autonomia '{level}' desconocido. Permitidos: {permitidos}."
        ) from None


def _stricter_threshold(general: float, propio: float) -> float:
    """El mas estricto de dos umbrales, donde 0 significa "freno apagado".

    Sin esta regla, `{**general, **propio}` dejaria que un venue subiera su
    tope a 1000 EUR y se saltara el freno del importe por completo. Los frenos
    existen para cazar fallos del programa, y el programa es el mismo en todos
    los venues: uno concreto puede necesitar mas cuidado, nunca menos.
    """
    if general <= 0:
        return propio
    if propio <= 0:
        return general
    return min(general, propio)


def brake_settings(cfg: TradingConfig, venue: str | None = None) -> dict:
    """Ajustes de los frenos. Un venue solo puede APRETARLOS, nunca aflojarlos."""
    base = {**DEFAULT_BRAKES, **(cfg.raw.get("brakes") or {})}
    if not venue:
        return base

    try:
        propios = cfg.venue(venue).raw.get("brakes") or {}
    except ConfigError:
        # Venue inexistente o mal configurado: se queda con los generales, que
        # son los conservadores. Un nombre mal escrito no puede dejar una
        # orden sin frenos.
        return base

    out = dict(base)
    for clave in ("confirm_above_eur", "confirm_when_drawdown_over_pct"):
        if clave in propios:
            out[clave] = _stricter_threshold(
                float(base.get(clave) or 0.0), float(propios.get(clave) or 0.0)
            )
    if "confirm_first_live_order" in propios:
        # Booleano: activarlo es apretar, asi que basta con que lo pida uno.
        out["confirm_first_live_order"] = bool(
            base.get("confirm_first_live_order") or propios["confirm_first_live_order"]
        )
    return out


def brakes_for(
    *,
    notional: float,
    is_opening: bool,
    drawdown_pct: float,
    live_orders_so_far: int,
    settings: dict | None = None,
) -> list[Brake]:
    """Frenos que cruza una orden concreta. Lista vacia = sale sola.

    Se reciben primitivos y no objetos del dominio a proposito: asi esta
    funcion —que es la que decide si algo sale sin que nadie lo mire— se puede
    probar entera sin base de datos, sin contexto y sin broker.
    """
    s = {**DEFAULT_BRAKES, **(settings or {})}
    out: list[Brake] = []

    tope = float(s.get("confirm_above_eur") or 0.0)
    if tope > 0 and notional > tope:
        out.append(Brake(
            "importe",
            f"La orden es de {notional:.2f} EUR, por encima de los "
            f"{tope:.2f} EUR que salen solos. Un error de calculo del tamano "
            "se ve asi antes de ejecutarse."
        ))

    if s.get("confirm_first_live_order") and live_orders_so_far <= 0:
        out.append(Brake(
            "primera",
            "Es la primera orden con dinero real. Es el unico momento en que "
            "el programa toca dinero por primera vez, y ahi sale todo lo que "
            "las pruebas no vieron."
        ))

    limite_dd = float(s.get("confirm_when_drawdown_over_pct") or 0.0)
    if is_opening and limite_dd > 0 and drawdown_pct >= limite_dd:
        out.append(Brake(
            "perdidas",
            f"La cartera esta un {drawdown_pct:.1f}% por debajo de su maximo, "
            f"y a partir del {limite_dd:.1f}% abrir posiciones nuevas espera "
            "confirmacion: doblar la apuesta mientras algo va mal incluye el "
            "caso de que lo que va mal sea un fallo que aun no hemos visto."
        ))

    return out


def requires_confirmation(
    level: str | Autonomy,
    *,
    notional: float,
    is_opening: bool,
    drawdown_pct: float = 0.0,
    live_orders_so_far: int = 1,
    settings: dict | None = None,
) -> list[Brake]:
    """Motivos por los que esta orden NO puede salir sola. Vacio = puede."""
    nivel = parse(level) if not isinstance(level, Autonomy) else level

    if nivel is Autonomy.AUTO:
        return []
    if nivel is Autonomy.SEMI:
        return [Brake("semi", "El modo pide confirmacion para todo.")]

    return brakes_for(
        notional=notional, is_opening=is_opening, drawdown_pct=drawdown_pct,
        live_orders_so_far=live_orders_so_far, settings=settings,
    )


def live_orders_so_far(mode: str = "live") -> int:
    """Cuantas ordenes se han enviado ya en este modo.

    En su propia funcion para que `brakes_for` siga sin tocar la base: es la
    unica pieza de todo esto que necesita el almacen.
    """
    from ..core.db import connect

    try:
        with connect(read_only=True) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE mode = ?", [mode]
            ).fetchone()
    except Exception:  # noqa: BLE001 — sin almacen, es la primera
        return 0
    return int(row[0]) if row else 0


def explain(brakes: list[Brake]) -> str:
    """Las razones en una frase para el registro y para el aviso al movil."""
    if not brakes:
        return ""
    return " ".join(b.text for b in brakes)

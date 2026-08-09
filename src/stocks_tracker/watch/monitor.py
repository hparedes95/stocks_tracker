"""Deteccion de desplomes sobre cotizaciones en vivo.

Todo el modulo gira alrededor de una idea: **avisar solo al empeorar**. Un
mercado parado en -3,2% cumple el umbral de -3% cada minuto durante seis horas.
Sin control de escalado eso son 360 mensajes, y a la tercera vez silenciarias
el canal, que es exactamente lo contrario de lo que quieres el dia que de
verdad haga falta.

Asi que se recuerda el peor nivel alcanzado hoy por cada simbolo y solo se
avisa cuando se cruza uno peor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from ..alerts.evaluate import Alert
from ..core.timeutils import utcnow
from .config import Threshold, WatchConfig, get_watch_config
from .state import WatchState

CRYPTO_GROUP = "crypto"
VOL_GROUP = "volatility"


@dataclass(frozen=True)
class Breach:
    """Un umbral cruzado por un simbolo."""

    key: str
    ticker: str
    label: str
    severity: str
    level: float
    change_pct: float
    price: float
    previous_close: float
    kind: str


def _worst_crossed(value: float, thresholds: list[Threshold],
                   descending: bool) -> Threshold | None:
    """Umbral mas grave que `value` ha cruzado, o None.

    `descending=True` para caidas (cruzar es ir por debajo); False para niveles
    absolutos como el VIX (cruzar es ir por encima).
    """
    crossed = None
    for threshold in thresholds:
        if descending and value <= threshold.value:
            crossed = threshold
        elif not descending and value >= threshold.value:
            crossed = threshold
    return crossed


def _severity_rank(severity: str) -> int:
    return {"baja": 0, "media": 1, "alta": 2, "critica": 3}.get(severity, 1)


def evaluate_quotes(quotes: pd.DataFrame, portfolio: pd.DataFrame | None = None,
                    cfg: WatchConfig | None = None) -> list[Breach]:
    """Umbrales cruzados AHORA, sin tener en cuenta lo ya avisado."""
    cfg = cfg or get_watch_config()
    breaches: list[Breach] = []
    if quotes.empty:
        return breaches

    positions = set()
    if portfolio is not None and not portfolio.empty:
        positions = set(portfolio["ticker"])

    for row in quotes.itertuples():
        ticker = str(row.ticker)
        change = getattr(row, "change_pct", None)
        if change is None or pd.isna(change):
            continue
        change_pct = float(change) * 100.0
        price = float(row.price)
        previous = float(row.previous_close) if pd.notna(row.previous_close) else 0.0
        group = cfg.group_of(ticker)

        if group == VOL_GROUP:
            level = _worst_crossed(price, cfg.thresholds("vix_level"), descending=False)
            if level:
                breaches.append(
                    Breach(f"{ticker}:vix_level", ticker, level.label, level.severity,
                           level.value, change_pct, price, previous, "vix_level")
                )
            jump = _worst_crossed(change_pct, cfg.thresholds("vix_jump_pct"),
                                  descending=False)
            if jump:
                breaches.append(
                    Breach(f"{ticker}:vix_jump", ticker, jump.label, jump.severity,
                           jump.value, change_pct, price, previous, "vix_jump")
                )
            continue

        name = {
            CRYPTO_GROUP: "crypto_drop",
            "indices": "index_drop",
        }.get(group, "position_drop" if ticker in positions else "index_drop")

        crossed = _worst_crossed(change_pct, cfg.thresholds(name), descending=True)
        if crossed:
            breaches.append(
                Breach(f"{ticker}:{name}", ticker, crossed.label, crossed.severity,
                       crossed.value, change_pct, price, previous, name)
            )

    portfolio_breach = _portfolio_breach(quotes, portfolio, cfg)
    if portfolio_breach:
        breaches.append(portfolio_breach)

    return breaches


def _portfolio_breach(quotes: pd.DataFrame, portfolio: pd.DataFrame | None,
                      cfg: WatchConfig) -> Breach | None:
    """Caida de la cartera entera, ponderada por el valor de cada posicion.

    Solo se calcula si hay cotizacion para TODAS las posiciones. Con una
    faltando, el porcentaje saldria del resto y diria que la cartera cae menos
    de lo que cae: preferimos no dar el dato a darlo mal.
    """
    if portfolio is None or portfolio.empty:
        return None

    merged = portfolio.merge(
        quotes[["ticker", "price", "previous_close", "change_pct"]],
        on="ticker", how="left",
    )
    if merged["price"].isna().any():
        return None

    value_now = float((merged["qty"] * merged["price"]).sum())
    value_prev = float((merged["qty"] * merged["previous_close"]).sum())
    if value_prev <= 0:
        return None

    change_pct = (value_now / value_prev - 1.0) * 100.0
    crossed = _worst_crossed(change_pct, cfg.thresholds("portfolio_drop"),
                             descending=True)
    if not crossed:
        return None
    return Breach("PORTFOLIO:portfolio_drop", "PORTFOLIO", crossed.label,
                  crossed.severity, crossed.value, change_pct, value_now,
                  value_prev, "portfolio_drop")


def to_alerts(breaches: list[Breach], state: WatchState,
              cfg: WatchConfig | None = None,
              now: datetime | None = None) -> list[Alert]:
    """Filtra por escalado y convierte en avisos enviables.

    Aqui es donde un vigilante util se distingue de uno que se acaba
    silenciando.
    """
    cfg = cfg or get_watch_config()
    moment = now or utcnow()
    out: list[Alert] = []

    for breach in breaches:
        previous_level = state.level_of(breach.key)

        # Para caidas, "peor" es mas negativo; para el VIX, mas alto.
        worse = previous_level is None or (
            breach.level < previous_level
            if breach.level < 0
            else breach.level > previous_level
        )

        if cfg.escalation_only and not worse:
            continue

        # El intervalo minimo frena la repeticion del MISMO nivel. Un nivel
        # peor sale siempre, sin esperar: pasar de -7% a -13% es pasar de
        # cortacircuitos nivel 1 a nivel 2, y hacerte esperar diez minutos para
        # contartelo seria tapar justo el caso para el que existe todo esto.
        if not worse:
            elapsed = state.minutes_since(breach.key, moment)
            if elapsed is not None and elapsed < cfg.min_minutes_between:
                continue

        out.append(
            Alert(
                id=str(uuid.uuid4()),
                rule_id=f"watch_{breach.kind}",
                ticker=None if breach.ticker == "PORTFOLIO" else breach.ticker,
                triggered_at=moment,
                message=_message(breach),
                severity=breach.severity,
                payload={
                    "change_pct": round(breach.change_pct, 2),
                    "price": round(breach.price, 4),
                    "level": breach.level,
                    "kind": breach.kind,
                    "live": True,
                },
            )
        )
        state.record(breach.key, breach.level, moment)

    return out


def recovery_alerts(quotes: pd.DataFrame, state: WatchState,
                    cfg: WatchConfig | None = None,
                    now: datetime | None = None) -> list[Alert]:
    """Un unico aviso al volver por encima del primer umbral.

    Sin esto, te quedas con el mensaje de panico en el movil y sin saber que
    media hora despues estaba recuperado.
    """
    cfg = cfg or get_watch_config()
    if not cfg.notify_recovery or quotes.empty:
        return []

    moment = now or utcnow()
    out: list[Alert] = []
    current = dict(zip(quotes["ticker"], quotes["change_pct"], strict=False))

    for key in list(state.levels):
        ticker, _, kind = key.partition(":")
        if ticker == "PORTFOLIO" or kind.startswith("vix"):
            continue
        change = current.get(ticker)
        if change is None or pd.isna(change):
            continue

        thresholds = cfg.thresholds(
            "crypto_drop" if cfg.group_of(ticker) == CRYPTO_GROUP else "index_drop"
        )
        if not thresholds:
            continue

        first = thresholds[0].value
        if float(change) * 100.0 > first:
            out.append(
                Alert(
                    id=str(uuid.uuid4()), rule_id="watch_recovery", ticker=ticker,
                    triggered_at=moment,
                    message=(
                        f"{ticker} se recupera: {float(change) * 100:+.1f}% "
                        "en el dia. Vigilancia rearmada."
                    ),
                    severity="baja",
                    payload={"change_pct": round(float(change) * 100, 2),
                             "live": True},
                )
            )
            state.clear(key)
    return out


def _sentence(text: str) -> str:
    """Primera letra en mayuscula sin tocar el resto.

    `str.capitalize()` pasa a minusculas todo lo demas y convierte "VIX en zona
    de tension" en "Vix en zona de tension".
    """
    return text[:1].upper() + text[1:] if text else text


def _message(breach: Breach) -> str:
    if breach.kind == "portfolio_drop":
        return (
            f"TU CARTERA {breach.change_pct:+.1f}% en el dia "
            f"({breach.price:,.0f} frente a {breach.previous_close:,.0f} "
            f"al cierre anterior). {_sentence(breach.label)}."
        )
    if breach.kind == "vix_level":
        return (
            f"VIX en {breach.price:.1f} ({breach.change_pct:+.1f}% hoy). "
            f"{_sentence(breach.label)}."
        )
    if breach.kind == "vix_jump":
        return f"VIX {breach.change_pct:+.1f}% hoy, en {breach.price:.1f}. {_sentence(breach.label)}."
    return (
        f"{breach.ticker} {breach.change_pct:+.1f}% en el dia, a "
        f"{breach.price:,.2f}. {_sentence(breach.label)}."
    )


def summarize(breaches: list[Breach]) -> str:
    """Una linea para el log de consola."""
    if not breaches:
        return "sin incidencias"
    worst = max(breaches, key=lambda b: _severity_rank(b.severity))
    return f"{len(breaches)} umbrales cruzados · mas grave: {worst.ticker} ({worst.severity})"

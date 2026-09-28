"""¿Se equivoca Polymarket lo bastante como para apostar contra el?

Este es el examen de Polymarket, y su logica esta invertida respecto al de
acciones. Alli se mide si una estrategia gano dinero en el pasado. Aqui se
mide algo mas basico, porque en un mercado de prediccion el precio ES la
probabilidad: un contrato a 0,30 no esta barato, es que el mercado cree que
pasa el 30 % de las veces.

Si ese 30 % acierta —de cada cien contratos a 0,30, treinta acaban valiendo
1— no hay ninguna ventaja que explotar. Se gana 0,70 el 30 % de las veces y
se pierde 0,30 el 70 %: exactamente cero, y en negativo despues de la
horquilla. **Un mercado bien calibrado es un mercado en el que no se debe
operar.** Por eso "aprobar" aqui significa haber encontrado una desviacion
medible, consistente y mayor que los costes; no lo contrario.

La desviacion que se busca tiene nombre y esta documentada en mercados de
apuestas desde los anos setenta: el *sesgo favorito-outsider*. Lo improbable
se paga de mas —la gente compra billetes de loteria— y lo casi seguro se paga
de menos. Si aparece aqui, la forma de aprovecharlo es vender lo improbable,
no comprarlo barato.

Cuatro trampas que harian que saliera ventaja donde no la hay:

1. **Mirar el precio final.** El dia antes de resolverse, un mercado ya vale
   casi 1 o casi 0. Medir ahi no mide una prediccion, mide un hecho ya
   ocurrido. Se toma el precio de N dias antes.
2. **Contar los anulados.** Un mercado cerrado sin ganador no es un "no".
   `PredictionMarket.is_resolved` ya los deja fuera.
3. **Buscar en diez tramos y quedarse con el que salga.** Con diez tramos,
   uno parece significativo por puro azar. Se exige que la desviacion sea
   consistente en tramos contiguos, no un pico suelto.
4. **Olvidar la horquilla.** Una ventaja del 2 % con una horquilla del 5 % no
   es una ventaja: es una perdida con pasos intermedios.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

from ..core.config import get_trading_config
from .brokers.polymarket_public import PolymarketPublic, PredictionMarket
from .gate import GateReport

# Tramos de precio. Mas estrechos en los extremos, que es donde vive el sesgo
# favorito-outsider y donde un punto porcentual cambia mas el resultado.
BUCKET_EDGES = (0.0, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 1.0)

# Cuantos dias antes de la resolucion se lee el precio. Una semana es el
# equilibrio: lo bastante lejos para que sea una prediccion y no una cronica,
# lo bastante cerca para que el mercado ya tenga liquidez.
DEFAULT_DAYS_BEFORE = 7

MIN_SAMPLE = 200          # por debajo de esto no se distingue senal de ruido
MIN_PER_BUCKET = 25       # un tramo con cuatro casos no dice nada
Z = 1.96                  # 95 %


@dataclass(frozen=True)
class Observation:
    """Lo que el mercado predijo y lo que acabo pasando."""

    market_id: str
    question: str
    predicted: float
    happened: bool


@dataclass(frozen=True)
class Bucket:
    low: float
    high: float
    n: int
    predicted_mean: float
    observed_rate: float
    ci_low: float
    ci_high: float

    @property
    def gap(self) -> float:
        """Realidad menos precio. Positivo = el mercado se quedaba corto."""
        return self.observed_rate - self.predicted_mean

    @property
    def significant(self) -> bool:
        """Si el intervalo de confianza deja fuera al precio medio.

        Sin esto, cualquier diferencia parece una senal. Con veinte mercados
        en un tramo, una desviacion de diez puntos es lo normal por azar.
        """
        return not (self.ci_low <= self.predicted_mean <= self.ci_high)

    @property
    def label(self) -> str:
        return f"{self.low:.0%}-{self.high:.0%}"


def wilson_interval(exitos: int, n: int, z: float = Z) -> tuple[float, float]:
    """Intervalo de confianza de una proporcion, metodo de Wilson.

    No se usa la formula normal (`p ± z·sqrt(p(1-p)/n)`) porque en los
    extremos —que es justo donde se busca el sesgo— da intervalos que se salen
    de [0,1] y, con cero exitos, un intervalo de anchura cero: diria que la
    probabilidad es exactamente 0 con total certeza.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = exitos / n
    denom = 1.0 + z * z / n
    centro = (p + z * z / (2 * n)) / denom
    margen = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centro - margen), min(1.0, centro + margen))


def brier_score(observaciones: list[Observation]) -> float:
    """Error cuadratico medio de la prediccion. 0 es perfecto, 0,25 es azar.

    Por si solo no dice si hay ventaja —un mercado puede tener buen Brier y
    aun asi estar sesgado en un tramo— pero un Brier malo indica que el
    mercado no sabe nada, y eso tambien es informacion.
    """
    if not observaciones:
        return float("nan")
    total = sum((o.predicted - (1.0 if o.happened else 0.0)) ** 2 for o in observaciones)
    return total / len(observaciones)


def base_rate(observaciones: list[Observation]) -> float:
    if not observaciones:
        return float("nan")
    return sum(1 for o in observaciones if o.happened) / len(observaciones)


def brier_skill_score(observaciones: list[Observation]) -> float:
    """Brier comparado con predecir siempre la frecuencia base.

    Es la comparacion que importa: acertar el 90 % de las veces no tiene
    merito si el 90 % de los mercados resuelven que si.
    """
    if not observaciones:
        return float("nan")
    tasa = base_rate(observaciones)
    referencia = sum((tasa - (1.0 if o.happened else 0.0)) ** 2 for o in observaciones)
    if referencia <= 0:
        return 0.0
    return 1.0 - (brier_score(observaciones) * len(observaciones)) / referencia


def calibration_buckets(
    observaciones: list[Observation], edges: tuple[float, ...] = BUCKET_EDGES
) -> list[Bucket]:
    """Agrupa por precio y compara lo prometido con lo ocurrido."""
    out: list[Bucket] = []
    for low, high in zip(edges, edges[1:], strict=False):
        # El ultimo tramo incluye su extremo derecho; si no, un precio de 1,0
        # no caeria en ninguno.
        dentro = [
            o for o in observaciones
            if low <= o.predicted < high or (high == edges[-1] and o.predicted == high)
        ]
        if not dentro:
            continue
        exitos = sum(1 for o in dentro if o.happened)
        n = len(dentro)
        ci_low, ci_high = wilson_interval(exitos, n)
        out.append(
            Bucket(
                low=low, high=high, n=n,
                predicted_mean=sum(o.predicted for o in dentro) / n,
                observed_rate=exitos / n,
                ci_low=ci_low, ci_high=ci_high,
            )
        )
    return out


def expected_calibration_error(buckets: list[Bucket]) -> float:
    """Desviacion media entre precio y realidad, ponderada por tamano.

    Cerca de cero significa que el mercado acierta, o sea que NO hay nada que
    explotar. Es la unica metrica de este fichero donde un buen numero es una
    mala noticia para el bot.
    """
    total = sum(b.n for b in buckets)
    if not total:
        return float("nan")
    return sum(b.n * abs(b.gap) for b in buckets) / total


def exploitable_runs(buckets: list[Bucket], min_gap: float) -> list[list[Bucket]]:
    """Rachas de tramos contiguos con desviacion significativa y del mismo signo.

    Un solo tramo desviado no vale: con diez tramos, que uno se salga del
    intervalo es lo esperable por azar. Dos o mas contiguos y en la misma
    direccion es lo que produce un sesgo real —el favorito-outsider afecta a
    todo un extremo de la escala, no a un tramo suelto—.
    """
    rachas: list[list[Bucket]] = []
    actual: list[Bucket] = []
    for b in buckets:
        util = b.significant and abs(b.gap) >= min_gap
        if util and actual and (b.gap > 0) == (actual[-1].gap > 0):
            actual.append(b)
            continue
        if len(actual) >= 2:
            rachas.append(actual)
        actual = [b] if util else []
    if len(actual) >= 2:
        rachas.append(actual)
    return rachas


# ---------------------------------------------------------------------------
# Recogida de datos
# ---------------------------------------------------------------------------
def collect_observations(
    reader: PolymarketPublic,
    *,
    days_before: int = DEFAULT_DAYS_BEFORE,
    min_volume: float = 1000.0,
    limit: int = 1000,
) -> list[Observation]:
    """Precio de hace N dias frente a lo que acabo pasando.

    El precio se toma ANTES de la resolucion, nunca el ultimo: el ultimo ya
    incorpora el resultado y mediria una obviedad con aspecto de acierto.
    """
    out: list[Observation] = []
    for mercado in reader.resolved_markets(min_volume=min_volume, limit=limit):
        if not mercado.token_ids or mercado.end_date is None:
            continue
        momento = mercado.end_date - timedelta(days=days_before)
        precio = reader.price_at(mercado.token_ids[0], momento)
        if precio is None or not 0.0 < precio < 1.0:
            # Sin precio en esa fecha, o ya resuelto de hecho: fuera. Rellenar
            # con el precio final seria meter el resultado en la prediccion.
            continue
        out.append(
            Observation(
                market_id=mercado.market_id,
                question=mercado.question,
                predicted=_yes_price_of(mercado, precio),
                happened=_resolved_yes(mercado),
            )
        )
    return out


def _yes_price_of(mercado: PredictionMarket, precio_token: float) -> float:
    """El historico es del PRIMER token, que no siempre es el "si".

    Si el primer token es el "no", su precio es el complementario. Confundirlo
    invierte la mitad de la muestra y el estudio da justo lo contrario.
    """
    if mercado.outcomes and mercado.outcomes[0].strip().lower() in ("yes", "si", "sí", "true"):
        return precio_token
    return 1.0 - precio_token


def _resolved_yes(mercado: PredictionMarket) -> bool:
    return mercado.resolved_outcome.strip().lower() in ("yes", "si", "sí", "true")


# ---------------------------------------------------------------------------
# Veredicto
# ---------------------------------------------------------------------------
def evaluate(
    observaciones: list[Observation], *, max_spread_pct: float | None = None
) -> GateReport:
    """Decide si hay una desviacion que merezca arriesgar dinero.

    Aprobar aqui NO es "el mercado esta bien calibrado". Es lo contrario: que
    se ha encontrado una desviacion grande, repetida y mayor que los costes.
    Un mercado que acierta suspende este examen, y eso es el sistema
    funcionando.
    """
    report = GateReport()

    if max_spread_pct is None:
        try:
            max_spread_pct = get_trading_config().venue("polymarket").execution.get(
                "max_spread_pct", 5.0
            )
        except Exception:  # noqa: BLE001 — sin configuracion, valor del mandato
            max_spread_pct = 5.0
    coste = float(max_spread_pct) / 100.0

    n = len(observaciones)
    report.add("Mercados resueltos en la muestra", n >= MIN_SAMPLE, n,
               f">= {MIN_SAMPLE}",
               "Por debajo no se distingue una senal del ruido")

    if n < MIN_SAMPLE:
        # Sin muestra, el resto de numeros existirian pero no significarian
        # nada. Se corta aqui en vez de imprimir cifras que invitan a mirarlas.
        report.blockers.append(
            f"Solo {n} mercados resueltos utilizables. Hacen falta {MIN_SAMPLE} "
            "para que la calibracion signifique algo."
        )
        return report

    buckets = [b for b in calibration_buckets(observaciones) if b.n >= MIN_PER_BUCKET]
    report.add("Tramos de precio con datos suficientes", len(buckets) >= 3,
               len(buckets), ">= 3",
               f"Cada tramo necesita {MIN_PER_BUCKET} mercados")

    brier = brier_score(observaciones)
    bss = brier_skill_score(observaciones)
    report.add("Brier score", brier < 0.25, round(brier, 4), "< 0,25 (azar)",
               "Solo dice que el mercado sabe algo; no que podamos ganarle")
    report.add("Brier frente a predecir la frecuencia base", bss > 0.0,
               round(bss, 4), "> 0",
               "Acertar el 90 % no tiene merito si el 90 % resuelve que si")

    ece = expected_calibration_error(buckets)
    report.add("Desviacion media (precio vs realidad)", ece > coste,
               f"{ece:.1%}", f"> {coste:.1%} (la horquilla)",
               "Si el mercado acierta, no hay nada que explotar: esto DEBE "
               "superar el coste para que operar tenga sentido")

    rachas = exploitable_runs(buckets, min_gap=coste)
    report.add("Tramos contiguos desviados en la misma direccion",
               bool(rachas), len(rachas), ">= 1 racha de 2 tramos",
               "Un tramo suelto se sale del intervalo por azar cuando se "
               "miran diez")

    if rachas:
        mejor = max(rachas, key=lambda r: sum(abs(b.gap) * b.n for b in r))
        peso = sum(b.n for b in mejor)
        ventaja = sum(abs(b.gap) * b.n for b in mejor) / peso
        report.add("Ventaja de la mejor racha, neta de horquilla",
                   ventaja - coste > 0.0, f"{ventaja - coste:.1%}", "> 0",
                   f"Tramos {mejor[0].label} a {mejor[-1].label}, "
                   f"{peso} mercados")

    return report


def render(report: GateReport, observaciones: list[Observation],
           buckets: list[Bucket] | None = None) -> str:
    """El informe en texto. Se lee entero o no se lee."""
    lineas: list[str] = []
    add = lineas.append

    add("")
    add("  Calibracion de Polymarket")
    add("  " + "=" * 66)
    add("")
    add("  En un mercado de prediccion el precio ES la probabilidad. Si acierta,")
    add("  no hay ventaja que explotar y NO se debe operar. Este examen busca lo")
    add("  contrario: una desviacion medible, repetida y mayor que la horquilla.")
    add("")

    if report.blockers:
        add("  BLOQUEADO")
        for b in report.blockers:
            add(f"    - {b}")
        add("")
        return "\n".join(lineas)

    for c in report.checks:
        marca = "OK  " if c.passed else "NO  "
        add(f"  [{marca}] {c.name}")
        add(f"          observado {c.observed}   se pedia {c.required}")
        if c.detail:
            add(f"          {c.detail}")
    add("")

    buckets = buckets if buckets is not None else [
        b for b in calibration_buckets(observaciones) if b.n >= MIN_PER_BUCKET
    ]
    if buckets:
        add("  Tramo      n    precio   realidad   desvio   IC 95%")
        add("  " + "-" * 66)
        for b in buckets:
            marca = " *" if b.significant else "  "
            add(f"  {b.label:>9} {b.n:>5}   {b.predicted_mean:>6.1%}   "
                f"{b.observed_rate:>7.1%}   {b.gap:>+6.1%}   "
                f"[{b.ci_low:.1%}, {b.ci_high:.1%}]{marca}")
        add("")
        add("  * el intervalo de confianza deja fuera al precio: desviacion real")
        add("")

    if report.passed:
        add("  HAY una desviacion aprovechable. Sigue siendo una medida del")
        add("  pasado, no una promesa: el sesgo puede desaparecer en cuanto")
        add("  suficiente gente lo explote.")
    else:
        add("  NO hay desviacion aprovechable. El mercado acierta lo bastante")
        add("  como para que operar por precio sea perder por la horquilla.")
        add("  Es el sistema funcionando: no se opera.")
    add("")
    return "\n".join(lineas)


def main() -> int:
    """`python -m stocks_tracker.trading.polymarket_calibration`"""
    reader = PolymarketPublic()
    print("  Descargando mercados resueltos de Polymarket (unos minutos)...")
    try:
        observaciones = collect_observations(reader)
    except Exception as exc:  # noqa: BLE001 — el motivo es lo unico que importa
        print(f"\n  No se ha podido leer Polymarket: {exc}\n")
        return 1

    report = evaluate(observaciones)
    print(render(report, observaciones))

    try:
        from .gate import save_report

        save_report(
            report,
            {"sessions": len(observaciones), "operaciones": 0,
             "equity_inicial": 0.0, "equity_final": 0.0, "curva": []},
            strategy_id="polymarket_calibration",
            preset="n/a",
        )
    except Exception as exc:  # noqa: BLE001 — sin almacen se muestra igual
        print(f"  (no se ha podido guardar el informe: {exc})")

    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Lectura de miedo y codicia sobre el semaforo de riesgo.

**No es un indicador nuevo.** Es la misma cifra que ya calcula
`compute_regime`, releida en la escala 0-100 a la que la gente esta
acostumbrada por el indice de CNN.

Merece la pena explicar por que no se ha construido un segundo indicador. La
tentacion era hacer un "Fear & Greed" propio con sus siete componentes; pero
cinco de los siete ya estaban en el semaforo, asi que habrian salido dos
numeros midiendo casi lo mismo en escalas distintas. Dos termometros que no
coinciden del todo no dan mas informacion: dan la duda de a cual hacer caso.

Lo que si faltaba eran los dos componentes que el semaforo no tenia
—maximos frente a minimos anuales, y momentum del indice frente a su media de
medio ano— y esos se han anadido al calculo del propio semaforo.
"""

from __future__ import annotations

# Umbrales sobre la escala 0-100, los mismos tramos que usa CNN.
_BANDS = [
    (25, "Miedo extremo"),
    (45, "Miedo"),
    (55, "Neutral"),
    (75, "Codicia"),
    (101, "Codicia extrema"),
]

# Lo que cada tramo significa, sin convertirlo en una recomendacion. El sesgo
# contrario existe pero es debil y lento: sirve para calibrar la prisa, no para
# decidir la operacion.
_READINGS = {
    "Miedo extremo": (
        "El mercado esta descontando lo peor. Historicamente estos momentos "
        "han ofrecido mejores puntos de entrada que los de euforia, pero "
        "pueden prolongarse meses y agravarse antes de girar."
    ),
    "Miedo": (
        "Predomina la cautela. Ni panico ni confianza: el tipo de fase en que "
        "conviene mirar la lista de candidatos sin prisa."
    ),
    "Neutral": (
        "Sin sesgo claro. El termometro no aporta informacion accionable en "
        "esta zona."
    ),
    "Codicia": (
        "Apetito por el riesgo por encima de lo normal. Buen momento para "
        "revisar cuanto has ganado y si tu exposicion sigue siendo la que "
        "querias, no para ampliarla sin pensar."
    ),
    "Codicia extrema": (
        "Euforia. No significa que el mercado vaya a caer manana, pero si que "
        "el margen de error es pequeno: comprar aqui es pagar el precio de la "
        "confianza de todos los demas."
    ),
}


def to_fear_greed(risk_score: float | None) -> float | None:
    """Pasa el semaforo (-100..+100) a la escala 0-100 de miedo y codicia."""
    if risk_score is None:
        return None
    try:
        value = float(risk_score)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return max(0.0, min(100.0, (value + 100.0) / 2.0))


def label(value: float | None) -> str:
    """Tramo al que pertenece una lectura 0-100."""
    if value is None:
        return "Sin datos"
    for upper, name in _BANDS:
        if value < upper:
            return name
    return _BANDS[-1][1]


def reading(value: float | None) -> str:
    """Que significa el tramo, en castellano y sin recomendar nada."""
    if value is None:
        return "No hay datos suficientes para leer el termometro."
    return _READINGS.get(label(value), "")


def bands() -> list[tuple[float, float, str]]:
    """Tramos como (desde, hasta, nombre), para pintar la escala."""
    out = []
    low = 0.0
    for upper, name in _BANDS:
        high = min(float(upper), 100.0)
        out.append((low, high, name))
        low = high
    return out

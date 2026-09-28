"""Contrato de las estrategias.

Una estrategia responde a "esto es buena idea?". No responde a "me lo puedo
permitir?": eso es del riesgo. Mezclar las dos preguntas produce estrategias
que se autocensuran de forma opaca, y entonces no hay forma de saber por que no
se compro algo.

Por eso una estrategia **solo lee del `StrategyContext`**: ni base de datos, ni
red, ni broker. Asi se puede probar entera con un contexto de mentira.
"""

from __future__ import annotations

from typing import Protocol

from ..context import StrategyContext
from ..intents import Intent


class Strategy(Protocol):
    strategy_id: str

    def should_run_today(self, ctx: StrategyContext) -> bool: ...
    def propose(self, ctx: StrategyContext) -> list[Intent]: ...

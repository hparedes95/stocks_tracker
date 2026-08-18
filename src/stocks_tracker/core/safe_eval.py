"""Evaluador seguro de expresiones para las condiciones de los YAML.

Las reglas de `explanations.yaml` (y mas adelante las de alertas) vienen del
usuario en texto. Usar `eval()` sobre eso seria una puerta abierta a ejecutar
codigo arbitrario, asi que se recorre el AST y solo se permite una lista blanca
de nodos: comparaciones, booleanos, aritmetica simple y nombres de variables.

Nada de llamadas a funciones, atributos, indices, imports, lambdas ni
exponenciacion.
"""

from __future__ import annotations

import ast
from typing import Any

_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.UnaryOp,
    ast.BinOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.And,
    ast.Or,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Tuple,
    ast.List,
)


class UnsafeExpressionError(ValueError):
    """La expresion contiene construcciones no permitidas."""


def _validate(node: ast.AST) -> None:
    for child in ast.walk(node):
        if not isinstance(child, _ALLOWED_NODES):
            raise UnsafeExpressionError(
                f"Construccion no permitida en la expresion: {type(child).__name__}"
            )


def compile_condition(expression: str) -> ast.Expression:
    tree = ast.parse(expression, mode="eval")
    _validate(tree)
    return tree


def evaluate(expression: str, variables: dict[str, Any]) -> bool:
    """Evalua la expresion contra un diccionario de variables."""
    try:
        tree = compile_condition(expression)
    except (SyntaxError, UnsafeExpressionError):
        return False

    safe_globals: dict[str, Any] = {"__builtins__": {}}
    try:
        return bool(eval(compile(tree, "<condicion>", "eval"), safe_globals, dict(variables)))
    except Exception:
        return False


def safe_format(template: str, variables: dict[str, Any]) -> str | None:
    """Rellena una plantilla `{campo:.1f}`. Devuelve None si falta algo."""
    try:
        return template.format(**variables)
    except (KeyError, IndexError, ValueError, TypeError):
        return None

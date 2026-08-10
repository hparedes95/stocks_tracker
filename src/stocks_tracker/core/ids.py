"""Identificadores ordenables por tiempo (ULID).

La adenda proponia la dependencia `ulid-py`. Son veinte lineas y el proyecto se
instala en Windows desde un ZIP, asi que cada paquete menos es una cosa menos
que puede fallar en el `pip install` del instalador. Lo que si hace falta del
formato se mantiene: 26 caracteres, ordenables lexicograficamente por instante
de creacion, con parte aleatoria suficiente para no colisionar.
"""

from __future__ import annotations

import os
import time

# Base32 de Crockford: sin I, L, O ni U, para que un ID leido en voz alta o
# copiado a mano no se confunda con otro.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_last_ms = 0
_last_random = 0


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        out.append(_ALPHABET[rem])
    return "".join(reversed(out))


def ulid() -> str:
    """ULID de 26 caracteres: 10 de tiempo (ms) + 16 de aleatoriedad.

    Dos llamadas dentro del mismo milisegundo siguen saliendo en orden: se
    incrementa la parte aleatoria en lugar de sortearla de nuevo. Sin eso, el
    orden de dos decisiones del mismo ciclo dependeria del azar, y el registro
    de auditoria dejaria de leerse cronologicamente.
    """
    global _last_ms, _last_random

    now_ms = int(time.time() * 1000)
    if now_ms == _last_ms:
        _last_random += 1
    else:
        _last_ms = now_ms
        _last_random = int.from_bytes(os.urandom(10), "big")

    return _encode(now_ms, 10) + _encode(_last_random, 16)


def timestamp_of(value: str) -> float:
    """Instante de creacion codificado en el ULID, en segundos epoch."""
    ms = 0
    for char in value[:10]:
        ms = ms * 32 + _ALPHABET.index(char)
    return ms / 1000.0

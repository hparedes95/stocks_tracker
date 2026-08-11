"""Firma de las peticiones privadas de Kraken.

Separado del adaptador a proposito: es la parte que se puede comprobar entera
sin cuenta, sin red y sin claves reales. El algoritmo esta documentado por
Kraken y es determinista, asi que un vector de prueba fijo dice si esta bien
antes de que exista ninguna credencial.

    firma = HMAC-SHA512(
        clave  = base64_decode(secreto),
        datos  = ruta + SHA256(nonce + cuerpo_urlencoded)
    )

Dos detalles que rompen la firma y no dan un error que lo explique —Kraken
responde "Invalid key", que suena a clave equivocada—:

1. El `nonce` del SHA256 va concatenado como TEXTO delante del cuerpo, y ese
   cuerpo tiene que ser exactamente el que se envia, con el mismo orden de
   campos. Si se serializa dos veces, salen dos cadenas distintas.
2. El nonce debe crecer SIEMPRE. Kraken rechaza uno menor o igual al ultimo
   usado por esa clave, y como la comparacion es por clave y no por proceso,
   dos procesos del mismo bot pueden invalidarse mutuamente.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import threading
import time
from urllib.parse import urlencode

_lock = threading.Lock()
_last_nonce = 0


def nonce() -> int:
    """Nonce estrictamente creciente, en milisegundos.

    Con candado y comparacion explicita porque dos llamadas en el mismo
    milisegundo darian el mismo numero, y Kraken rechaza el segundo. El fallo
    seria intermitente y solo bajo carga, que es la peor forma de fallar.
    """
    global _last_nonce
    with _lock:
        value = int(time.time() * 1000)
        if value <= _last_nonce:
            value = _last_nonce + 1
        _last_nonce = value
        return value


def sign(path: str, data: dict, secret: str) -> str:
    """Cabecera `API-Sign` para una peticion privada.

    `data` debe incluir ya el `nonce`, y es el MISMO diccionario que se envia
    como cuerpo: serializarlo aqui y otra vez al enviar produciria dos cadenas
    distintas si algun valor cambia de orden.
    """
    if "nonce" not in data:
        raise ValueError("La peticion tiene que llevar nonce antes de firmarse")

    body = urlencode(data)
    encoded = (str(data["nonce"]) + body).encode()
    message = path.encode() + hashlib.sha256(encoded).digest()

    signature = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(signature.digest()).decode()


def body(data: dict) -> str:
    """Cuerpo urlencoded, el mismo que se firmo. Usar siempre este."""
    return urlencode(data)

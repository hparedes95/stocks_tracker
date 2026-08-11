"""Unica puerta de acceso a las credenciales.

Tres cosas que este modulo garantiza y que antes no estaban:

1. **El fichero `.env` se lee.** `python-dotenv` estaba declarado como
   dependencia y no se llamaba en ningun sitio: todo lo que prometia
   `.env.example` —la clave de FRED, el token de Telegram— nunca se cargo. Se
   podia rellenar el fichero entero y no pasaba nada.
2. **Se sabe que falta y por que.** Cada credencial declara para que sirve,
   como se consigue y que permisos debe tener. `python -m stocks_tracker.core.secrets`
   lo dice sin revelar ningun valor.
3. **Nada se imprime por accidente.** `redact()` borra de cualquier texto los
   valores de TODAS las credenciales conocidas antes de que salga por pantalla
   o a un fichero de registro. La version anterior tenia una lista de tres
   nombres escrita a mano; con una clave privada de wallet de por medio, un
   secreto en una traza no se arregla rotandolo.

Sobre el punto 3, la diferencia importa: una clave de API se revoca; una clave
privada de wallet no. Si aparece en un log, lo unico que se puede hacer es
mover los fondos a otra wallet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import project_root


@dataclass(frozen=True)
class Credential:
    """Una credencial: donde vive, para que sirve y como se consigue."""

    env: str
    venue: str
    purpose: str
    how: str
    required_for_trading: bool = True
    danger: str = ""

    @property
    def value(self) -> str:
        return os.environ.get(self.env, "").strip()

    @property
    def present(self) -> bool:
        return bool(self.value)


# Orden deliberado: primero lo que no cuesta nada, al final lo que puede
# vaciar una cartera.
CREDENTIALS: tuple[Credential, ...] = (
    Credential(
        env="FRED_API_KEY", venue="macro",
        purpose="Series macroeconomicas (tipos, inflacion, paro)",
        how="Gratuita en fred.stlouisfed.org/docs/api/api_key.html",
        required_for_trading=False,
    ),
    Credential(
        env="TELEGRAM_BOT_TOKEN", venue="alertas",
        purpose="Avisos al movil cuando el mercado se mueve",
        how="Habla con @BotFather en Telegram y crea un bot",
        required_for_trading=False,
    ),
    Credential(
        env="TELEGRAM_CHAT_ID", venue="alertas",
        purpose="A que conversacion se envian los avisos",
        how="Escribe a tu bot y mira api.telegram.org/bot<TOKEN>/getUpdates",
        required_for_trading=False,
    ),
    Credential(
        env="KRAKEN_API_KEY", venue="kraken",
        purpose="Consultar saldo y enviar ordenes en Kraken",
        how="Kraken > Settings > API > Add key",
        danger=(
            "Marca SOLO: consultar saldo, consultar ordenes, crear y modificar "
            "ordenes. Deja SIN marcar 'Withdraw Funds'. Esa casilla es la que "
            "separa una clave robada que hace operaciones absurdas de una "
            "cuenta vaciada. Tampoco actives margen ni futuros."
        ),
    ),
    Credential(
        env="KRAKEN_API_SECRET", venue="kraken",
        purpose="Firma de las peticiones privadas de Kraken",
        how="Se muestra UNA vez al crear la clave; despues no se puede recuperar",
        danger="Si la pierdes, borra la clave en Kraken y crea otra.",
    ),
    Credential(
        env="POLYMARKET_PRIVATE_KEY", venue="polymarket",
        purpose="Firmar las ordenes: en Polymarket cada orden va firmada",
        how="Exportala de la wallet que uses SOLO para el bot",
        danger=(
            "No existe el equivalente a 'clave sin permiso de retirada'. Quien "
            "tenga esto se lleva todo lo que haya en esa wallet, sin soporte al "
            "que reclamar ni transaccion que revertir. Usa una wallet NUEVA con "
            "solo el dinero asignado al bot. Y si alguna vez se filtra, rotarla "
            "no sirve: hay que mover los fondos."
        ),
    ),
    Credential(
        env="POLYMARKET_FUNDER_ADDRESS", venue="polymarket",
        purpose="Direccion que aporta el USDC, si operas con wallet delegada",
        how="Es la direccion que ves en tu perfil de Polymarket",
        required_for_trading=False,
    ),
)

BY_ENV = {c.env: c for c in CREDENTIALS}


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_env() -> Path | None:
    """Lee `.env` una sola vez. Las variables ya definidas mandan.

    Que el entorno gane sobre el fichero es lo correcto: permite ejecutar algo
    puntualmente con otra credencial sin editar el fichero ni arriesgarse a
    dejarla escrita.
    """
    path = project_root() / ".env"
    if not path.exists():
        return None
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return None
    load_dotenv(path, override=False)
    return path


def get(env: str, required: bool = True) -> str:
    """Valor de una credencial. Nunca la imprime, ni siquiera al fallar."""
    load_env()
    value = os.environ.get(env, "").strip()
    if value or not required:
        return value

    cred = BY_ENV.get(env)
    detalle = f"\n  Para que sirve: {cred.purpose}\n  Como conseguirla: {cred.how}" if cred else ""
    raise MissingCredential(
        f"Falta {env}. Ponla en el fichero .env de la carpeta del programa."
        f"{detalle}"
    )


class MissingCredential(RuntimeError):
    """Falta una credencial necesaria para lo que se ha pedido."""


# ---------------------------------------------------------------------------
# Diagnostico
# ---------------------------------------------------------------------------
def status(venue: str | None = None) -> list[tuple[Credential, bool]]:
    load_env()
    return [(c, c.present) for c in CREDENTIALS if venue is None or c.venue == venue]


def venue_ready(venue: str) -> tuple[bool, list[str]]:
    """Si un venue puede operar, y que le falta si no."""
    load_env()
    faltan = [c.env for c in CREDENTIALS
              if c.venue == venue and c.required_for_trading and not c.present]
    return (not faltan), faltan


# ---------------------------------------------------------------------------
# Redaccion
# ---------------------------------------------------------------------------
def redact(text: str) -> str:
    """Borra de un texto el valor de cualquier credencial conocida.

    Se aplica a mensajes de error y a lo que se registra en disco. La version
    anterior llevaba tres nombres escritos a mano y se quedo atras en cuanto se
    anadieron credenciales nuevas, que es lo que siempre pasa con las listas
    escritas a mano.
    """
    if not text:
        return text
    load_env()
    out = text
    for cred in CREDENTIALS:
        value = cred.value
        # Los valores muy cortos no se sustituyen: un secreto de tres
        # caracteres no lo es, y reemplazarlo destrozaria el mensaje.
        if len(value) >= 8:
            out = out.replace(value, f"<{cred.env}>")
    return out


# ---------------------------------------------------------------------------
def main() -> int:
    """`python -m stocks_tracker.core.secrets` — que hay y que falta."""
    path = load_env()
    print()
    print("  Credenciales")
    print("  " + "=" * 60)
    print(f"  Fichero: {path or '(no existe todavia un .env)'}")
    print()

    por_venue: dict[str, list[tuple[Credential, bool]]] = {}
    for cred, present in status():
        por_venue.setdefault(cred.venue, []).append((cred, present))

    for venue, items in por_venue.items():
        print(f"  [{venue}]")
        for cred, present in items:
            marca = "OK  " if present else ("FALTA" if cred.required_for_trading
                                            else "-   ")
            print(f"    {marca} {cred.env}")
            if not present:
                print(f"          {cred.purpose}")
                print(f"          {cred.how}")
                if cred.danger:
                    for linea in _wrap(cred.danger, 66):
                        print(f"          ! {linea}")
        print()

    for venue in ("kraken", "polymarket"):
        listo, faltan = venue_ready(venue)
        estado = "listo para operar" if listo else f"faltan {', '.join(faltan)}"
        print(f"  {venue}: {estado}")

    print()
    print("  Las credenciales van en el fichero .env, nunca en el codigo ni en")
    print("  un chat. Ese fichero esta en .gitignore y no se sube a ningun sitio.")
    print()
    return 0


def _wrap(text: str, width: int) -> list[str]:
    palabras, linea, salida = text.split(), "", []
    for palabra in palabras:
        if len(linea) + len(palabra) + 1 > width:
            salida.append(linea)
            linea = palabra
        else:
            linea = f"{linea} {palabra}".strip()
    if linea:
        salida.append(linea)
    return salida


if __name__ == "__main__":
    raise SystemExit(main())

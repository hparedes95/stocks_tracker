"""Dashboard de monitorizacion de mercado y deteccion de oportunidades."""

__version__ = "0.1.0"


def _cargar_credenciales() -> None:
    """Lee el `.env` al importar el paquete. UNA vez, y aqui.

    POR QUE EN EL __init__ Y NO EN CADA ENTRADA

    `secrets.load_env()` existia desde el principio y lo llamaba UN modulo: el
    de alertas. Nadie mas. Asi que una clave escrita en el `.env` —que es donde
    el instalador la pone y donde toda la documentacion dice que va— no la veia
    ni la ingesta, ni la auditoria, ni el bot.

    El sintoma no era un error: la auditoria decia "contra stooq" y seguia
    adelante, como si el usuario no hubiera configurado nada. El fichero estaba,
    la clave estaba, y el programa se comportaba como si no.

    Ponerlo en el arranque de cada CLI seria lo explicito, y es justo lo que
    fallo: son seis puntos de entrada y basta olvidar uno para que la clave
    vuelva a ser invisible en esa rama. Aqui no hay nada que recordar, porque
    cualquier cosa que use el programa importa el paquete.

    Es un efecto de importacion deliberado, y es barato: lee un fichero pequeno
    una sola vez (`lru_cache`) y NO pisa las variables que ya esten definidas en
    el entorno, asi que ejecutar algo puntual con otra credencial sigue
    funcionando. Si el fichero no existe o falta `python-dotenv`, no pasa nada.
    """
    try:
        from .core.secrets import load_env

        load_env()
    except Exception:  # noqa: BLE001
        # Que no se pueda leer el .env NO puede impedir arrancar: el programa
        # funciona entero sin ninguna clave, solo con menos fuentes.
        pass


_cargar_credenciales()

DISCLAIMER = (
    "Herramienta de apoyo a la decision, no asesoramiento financiero. "
    "Ninguna senal predice el mercado: los resultados son probabilisticos y "
    "se basan en datos con retardo y de calidad variable."
)

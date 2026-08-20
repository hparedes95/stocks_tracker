"""El estado de todo lo que puede estar mal, en una sola lista.

Las comprobaciones existen repartidas —calidad de precios, cuarentena de barras,
consenso entre proveedores, contradicciones en fundamentales, frescura, puerta
de validacion— y cada una vive en su sitio. Eso esta bien para el codigo y mal
para quien mira: hay que recorrer cuatro pantallas y una consola para saber si
hoy se puede uno fiar del programa.

Esto las junta. No calcula nada nuevo: LEE lo que las otras ya escribieron.

LA REGLA QUE GOBIERNA ESTE MODULO

Verde no significa "esta bien". Significa "se ha comprobado y no se ha
encontrado nada que lo contradiga". Y gris no significa "esta bien" tampoco:
significa "no se ha comprobado", que es una cosa distinta y muchas veces peor,
porque se parece a la primera.

Por eso hay un estado GRIS separado del verde. Un panel que pinta de verde lo
que no ha mirado es peor que no tener panel: da tranquilidad sin haberla ganado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

BIEN = "bien"
AVISO = "aviso"
MAL = "mal"
SIN_COMPROBAR = "sin_comprobar"

# De peor a mejor, y el veredicto global se queda con el primero que aparezca.
#
# GRIS VA POR ENCIMA DE AMARILLO, y no es un descuido. Un aviso es algo medido:
# se ha mirado, se sabe que va regular y se sabe cuanto. Un gris es no saber. Un
# panel con un aviso pequeno y siete comprobaciones sin ejecutar, resumido como
# "aviso", parece bajo control cuando en realidad casi nada se ha mirado.
ORDEN = (MAL, SIN_COMPROBAR, AVISO, BIEN)

SEMAFORO = {BIEN: "🟢", AVISO: "🟡", MAL: "🔴", SIN_COMPROBAR: "⚪"}

# Horas tras las cuales una comprobacion deja de contar como reciente. Una
# comprobacion de hace tres semanas no dice nada del estado de hoy, y pintarla
# en verde es exactamente la mentira que este modulo evita.
CADUCA_HORAS = 72


@dataclass(frozen=True)
class Punto:
    """Una linea del panel."""

    nombre: str
    estado: str
    detalle: str
    # Adonde ir a mirar. Sin esto, un rojo es una alarma sin salida.
    donde: str = ""

    @property
    def icono(self) -> str:
        return SEMAFORO[self.estado]


def _caducada(cuando, ahora: pd.Timestamp) -> bool:
    if cuando is None or pd.isna(cuando):
        return True
    return (ahora - pd.Timestamp(cuando)) > pd.Timedelta(hours=CADUCA_HORAS)


# ---------------------------------------------------------------------------
# Cada comprobacion
# ---------------------------------------------------------------------------
def _datos(conn, ahora: pd.Timestamp) -> Punto:
    fila = conn.execute(
        "SELECT MAX(date), COUNT(*) FROM prices_daily"
    ).fetchone()
    ultima, filas = fila[0], int(fila[1] or 0)
    if not filas:
        return Punto("Datos", SIN_COMPROBAR, "El almacen esta vacio.",
                     "Ejecuta la ingesta")

    sinteticos = conn.execute(
        "SELECT COUNT(*) FROM prices_daily WHERE source = 'synthetic'"
    ).fetchone()[0]
    if sinteticos:
        return Punto(
            "Datos", MAL,
            f"{sinteticos:,} filas son DATOS DE PRUEBA inventados. Nada de lo "
            "que ensena el programa describe el mercado.".replace(",", "."),
            "Ejecuta la descarga real",
        )

    dias = (ahora.date() - pd.Timestamp(ultima).date()).days
    if dias > 5:
        return Punto("Datos", AVISO,
                     f"El ultimo precio es del {pd.Timestamp(ultima):%d/%m/%Y}, "
                     f"hace {dias} dias.", "Estado de los datos")
    return Punto("Datos", BIEN,
                 f"{filas:,} barras hasta el {pd.Timestamp(ultima):%d/%m/%Y}."
                 .replace(",", "."))


def _calidad(conn, ahora: pd.Timestamp) -> Punto:
    fila = conn.execute(
        "SELECT MAX(checked_at) FROM data_quality"
    ).fetchone()
    if fila is None or fila[0] is None:
        return Punto("Calidad de los precios", SIN_COMPROBAR,
                     "No se ha comprobado nunca.", "Ejecuta el calculo")
    if _caducada(fila[0], ahora):
        return Punto("Calidad de los precios", SIN_COMPROBAR,
                     f"La ultima comprobacion es del "
                     f"{pd.Timestamp(fila[0]):%d/%m/%Y} y ya no dice nada de hoy.",
                     "Ejecuta el calculo")

    graves, avisos = conn.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE severity = 'bloquea'),
          COUNT(*) FILTER (WHERE severity = 'aviso')
        FROM data_quality d
        WHERE NOT d.passed AND d.checked_at = (
            SELECT MAX(x.checked_at) FROM data_quality x
            WHERE x.check_name = d.check_name)
        """
    ).fetchone()
    if graves:
        return Punto("Calidad de los precios", MAL,
                     f"{graves} problemas graves. El calculo no se ejecutara.",
                     "Estado de los datos")
    if avisos:
        return Punto("Calidad de los precios", AVISO,
                     f"{avisos} avisos. No invalidan el calculo.",
                     "Estado de los datos")
    return Punto("Calidad de los precios", BIEN, "Todas las comprobaciones pasan.")


def _cuarentena(conn) -> Punto:
    barras, valores = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ticker) FROM prices_quarantine"
    ).fetchone()
    if not barras:
        # "Ninguna barra imposible" solo es una buena noticia si alguien ha
        # mirado. Sobre un almacen recien creado, o con la puerta de calidad sin
        # ejecutar, esa frase es verdad y no significa nada: el cero sale de que
        # nadie ha comprobado, no de que este todo bien.
        mirado = conn.execute(
            "SELECT COUNT(*) FROM data_quality WHERE check_name = 'ohlc_incoherente'"
        ).fetchone()[0]
        if not mirado:
            return Punto("Barras apartadas", SIN_COMPROBAR,
                         "Nadie ha comprobado todavia si hay barras imposibles.",
                         "Ejecuta el calculo")
        return Punto("Barras apartadas", BIEN, "Ninguna barra imposible.")
    return Punto("Barras apartadas", AVISO,
                 f"{barras} barras de {valores} valores con OHLC imposible. Se "
                 "ignora su rango; el resto de su serie se usa igual.",
                 "Estado de los datos")


def _consenso(conn, ahora: pd.Timestamp) -> Punto:
    fila = conn.execute(
        "SELECT MAX(checked_at), COUNT(*) FROM price_consensus"
    ).fetchone()
    if fila is None or fila[0] is None:
        return Punto("Consenso entre proveedores", SIN_COMPROBAR,
                     "Ningun precio se ha contrastado con una segunda fuente.",
                     "Ejecuta `auditar`")
    if _caducada(fila[0], ahora):
        return Punto("Consenso entre proveedores", SIN_COMPROBAR,
                     f"La ultima auditoria es del "
                     f"{pd.Timestamp(fila[0]):%d/%m/%Y}.", "Ejecuta `auditar`")

    conteo = dict(conn.execute(
        "SELECT veredicto, COUNT(DISTINCT ticker) FROM price_consensus "
        "GROUP BY veredicto"
    ).fetchall())
    invalidos = conteo.get("invalido", 0)
    if invalidos:
        return Punto("Consenso entre proveedores", MAL,
                     f"{invalidos} valores con precios incompatibles entre "
                     "fuentes. El bot no operara esos valores.",
                     "Estado de los datos")
    degradados = conteo.get("degradado", 0)
    verificados = conteo.get("verificado", 0)
    if degradados and not verificados:
        return Punto("Consenso entre proveedores", SIN_COMPROBAR,
                     f"{degradados} valores con una sola fuente: no se han "
                     "podido contrastar con nada.", "Ejecuta `auditar`")
    if degradados:
        return Punto("Consenso entre proveedores", AVISO,
                     f"{verificados} verificados, {degradados} sin segunda "
                     "fuente que los confirme.", "Estado de los datos")
    return Punto("Consenso entre proveedores", BIEN,
                 f"{verificados} valores confirmados por dos fuentes.")


def _fundamentales(conn) -> Punto:
    fila = conn.execute("SELECT COUNT(*) FROM fundamentals_snapshot").fetchone()
    if not fila[0]:
        return Punto("Fundamentales", SIN_COMPROBAR, "No hay ninguno descargado.",
                     "Ejecuta la ingesta")
    sin_ficha = conn.execute(
        "SELECT COUNT(*) FROM instruments i "
        "WHERE i.asset_class IN ('equity','etf') AND i.is_active "
        "AND (i.gics_sector IS NULL OR i.gics_sector = '')"
    ).fetchone()[0]
    if sin_ficha:
        return Punto("Fundamentales", AVISO,
                     f"{sin_ficha} valores sin sector. El presupuesto de "
                     "peticiones los recoge en pasadas siguientes.",
                     "Estado de los datos")
    return Punto("Fundamentales", BIEN, f"{fila[0]:,} fotos guardadas."
                 .replace(",", "."))


def _ranking(conn) -> Punto:
    fila = conn.execute(
        "SELECT MAX(date), COUNT(DISTINCT ticker) FROM factor_scores"
    ).fetchone()
    if fila is None or fila[0] is None:
        return Punto("Ranking", SIN_COMPROBAR, "No se ha calculado ninguno.",
                     "Ejecuta el calculo")
    sesion = conn.execute("SELECT date FROM current_session").fetchone()
    if sesion and pd.Timestamp(fila[0]) != pd.Timestamp(sesion[0]):
        return Punto("Ranking", AVISO,
                     f"El ranking es del {pd.Timestamp(fila[0]):%d/%m/%Y} y la "
                     f"ultima sesion del {pd.Timestamp(sesion[0]):%d/%m/%Y}.",
                     "Ejecuta el calculo")
    return Punto("Ranking", BIEN,
                 f"{fila[1]} valores puntuados el "
                 f"{pd.Timestamp(fila[0]):%d/%m/%Y}.")


def _validacion(conn) -> Punto:
    filas = conn.execute(
        "SELECT evidence, COUNT(*) FROM signal_evidence GROUP BY evidence"
    ).fetchall()
    if not filas:
        return Punto("Validacion de senales", SIN_COMPROBAR,
                     "Ninguna senal se ha validado contra su historico.",
                     "Ejecuta `validate`")
    conteo = dict(filas)
    validadas = conteo.get("validada", 0)
    total = sum(conteo.values())
    if not validadas:
        return Punto("Validacion de senales", AVISO,
                     f"{total} senales evaluadas y ninguna ha superado la "
                     "puerta. Son observaciones, no recomendaciones.",
                     "Validacion de senales")
    return Punto("Validacion de senales", BIEN,
                 f"{validadas} de {total} senales validadas.")


def _universo_historico(conn) -> Punto:
    fila = conn.execute(
        "SELECT COUNT(*), MIN(valid_from) FROM universe_membership"
    ).fetchone()
    if not fila[0]:
        return Punto("Universo historico", SIN_COMPROBAR,
                     "No hay historico de composicion: los backtests usan el "
                     "universo de HOY y sobreestiman los resultados.",
                     "Se acumula solo con cada ingesta")
    anos = (date.today() - pd.Timestamp(fila[1]).date()).days / 365.25
    if anos < 2:
        return Punto("Universo historico", AVISO,
                     f"Solo {anos:.1f} anos de composicion. Los backtests mas "
                     "largos que eso arrastran sesgo de supervivencia.",
                     "Validacion de senales")
    return Punto("Universo historico", BIEN,
                 f"{anos:.1f} anos de composicion guardada.")


def _splits_y_dividendos(conn) -> Punto:
    eventos = conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0]
    if not eventos:
        return Punto(
            "Splits y dividendos", SIN_COMPROBAR,
            "No hay ninguno guardado. Sin ellos no se puede separar el retorno "
            "del precio del retorno total, ni comprobar que un split conserve "
            "el valor economico.",
            "Se recogen en la siguiente descarga",
        )

    from . import corporate

    precios = conn.execute(
        """
        SELECT ticker, date, close, adj_close FROM prices_daily
        WHERE ticker IN (SELECT DISTINCT ticker FROM corporate_actions
                         WHERE action_type = 'split')
        """
    ).fetchdf()
    acciones = corporate.leer(conn)
    malos = corporate.comprobar_splits(precios, acciones)
    if malos:
        primero = malos[0]
        return Punto("Splits y dividendos", MAL,
                     f"{len(malos)} splits mal aplicados. El primero: "
                     f"{primero.ticker} el {primero.fecha}. Un salto asi no lo "
                     "detecta ninguna comprobacion de coherencia.",
                     "Vuelve a descargar esos valores")
    return Punto("Splits y dividendos", BIEN,
                 f"{eventos} eventos guardados y los splits cuadran con el precio.")


def _reconciliacion(conn, ahora: pd.Timestamp) -> Punto:
    fila = conn.execute(
        "SELECT MAX(checked_at) FROM reconciliation"
    ).fetchone()
    posiciones = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE closed_at IS NULL"
    ).fetchone()[0]

    if not posiciones:
        return Punto("Cartera contra el broker", BIEN,
                     "No hay ninguna posicion abierta que contrastar.")
    if fila is None or fila[0] is None:
        return Punto(
            "Cartera contra el broker", SIN_COMPROBAR,
            f"{posiciones} posiciones abiertas y nunca se han contrastado con el "
            "broker. Es la unica comprobacion que se hace contra quien tiene el "
            "dinero de verdad.",
            "Ejecuta `reconciliar`",
        )
    if _caducada(fila[0], ahora):
        return Punto("Cartera contra el broker", SIN_COMPROBAR,
                     f"La ultima revision es del "
                     f"{pd.Timestamp(fila[0]):%d/%m/%Y}.", "Ejecuta `reconciliar`")

    difieren = conn.execute(
        "SELECT COUNT(*) FROM reconciliation WHERE estado = 'difiere' "
        "AND checked_at = (SELECT MAX(checked_at) FROM reconciliation)"
    ).fetchone()[0]
    if difieren:
        return Punto("Cartera contra el broker", MAL,
                     f"{difieren} diferencias con el broker sin resolver.",
                     "Estado de los datos")
    return Punto("Cartera contra el broker", BIEN,
                 f"Las {posiciones} posiciones cuadran con el broker.")


COMPROBACIONES = (
    ("Datos", _datos, True),
    ("Calidad de los precios", _calidad, True),
    ("Barras apartadas", _cuarentena, False),
    ("Consenso entre proveedores", _consenso, True),
    ("Fundamentales", _fundamentales, False),
    ("Ranking", _ranking, False),
    ("Validacion de senales", _validacion, False),
    ("Cartera contra el broker", _reconciliacion, True),
    ("Splits y dividendos", _splits_y_dividendos, False),
    ("Universo historico", _universo_historico, False),
)


def revisar(conn, ahora: pd.Timestamp | None = None) -> list[Punto]:
    """Todos los puntos del panel, en el orden en que se pintan.

    Una comprobacion que revienta NO tumba el panel: sale como
    `sin_comprobar` con el error. Un panel de integridad que se cae entero
    porque una consulta falla es justo lo contrario de lo que hace falta el dia
    que algo se rompe.
    """
    ahora = ahora or pd.Timestamp.now()
    puntos: list[Punto] = []
    for nombre, funcion, necesita_ahora in COMPROBACIONES:
        try:
            puntos.append(funcion(conn, ahora) if necesita_ahora else funcion(conn))
        except Exception as exc:  # noqa: BLE001
            puntos.append(Punto(
                nombre, SIN_COMPROBAR,
                f"La comprobacion ha fallado: {type(exc).__name__}: {exc}",
                "Revisa el registro",
            ))
    return puntos


def veredicto(puntos: list[Punto]) -> str:
    """El peor de todos.

    El peor y no una media: un panel con siete verdes y un rojo NO esta al 87 %,
    esta roto. Promediar estados es la forma clasica de que un problema grave
    desaparezca detras de una mayoria de cosas que van bien.
    """
    if not puntos:
        return SIN_COMPROBAR
    estados = {p.estado for p in puntos}
    for estado in ORDEN:
        if estado in estados:
            return estado
    return BIEN


def resumen(puntos: list[Punto]) -> str:
    conteo = {e: sum(1 for p in puntos if p.estado == e) for e in ORDEN}
    partes = [f"{SEMAFORO[e]} {conteo[e]}" for e in ORDEN if conteo[e]]
    return "  ".join(partes)


def pendientes(puntos: list[Punto]) -> list[Punto]:
    """Lo que no esta en verde, lo peor primero."""
    return sorted((p for p in puntos if p.estado != BIEN),
                  key=lambda p: ORDEN.index(p.estado))


__all__ = ["BIEN", "AVISO", "MAL", "SIN_COMPROBAR", "SEMAFORO", "CADUCA_HORAS",
           "Punto", "revisar", "veredicto", "resumen", "pendientes"]

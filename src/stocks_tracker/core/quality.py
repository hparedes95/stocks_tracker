"""Comprobaciones de calidad de los datos, antes de calcular nada con ellos.

La tabla `data_quality` existia en el esquema desde el principio y no la
escribia nadie. Este modulo la llena.

LA COMPROBACION QUE IMPORTA

De todas las de aqui, la que casi nadie hace es detectar que el proveedor ha
**reescrito el pasado**. Yahoo revisa series hacia atras sin avisar: un dia
descargas AAPL y el cierre del 3 de marzo de 2019 vale 174,52, y tres meses
despues vale otra cosa. No hay error, no hay aviso y no hay forma de enterarse
mirando la pantalla. Lo unico que pasa es que cualquier backtest anterior a esa
fecha dejo de ser reproducible, y sigues creyendote sus numeros.

Y hay una distincion que se hace mal casi siempre:

- Que `adj_close` cambie hacia atras es **NORMAL**. Cada dividendo y cada split
  reajustan toda la serie historica: es como funciona el precio ajustado, y
  avisar de eso seria una falsa alarma en cada reparto.
- Que `close` cambie hacia atras **NO es normal nunca**. El precio al que
  cotizo una accion un martes de 2019 es un hecho y no cambia. Si cambia, o el
  proveedor corrigio un error suyo, o metio otro.

Por eso las revisiones se buscan en `open`, `high`, `low`, `close` y `volume`,
y NO en `adj_close`. Confundirlas produce una de estas dos cosas, las dos
malas: un aviso cada vez que una empresa reparte dividendo, o ningun aviso
nunca porque se acepta que la serie cambie.

QUE HACE Y QUE NO HACE UNA COMPROBACION DE CALIDAD

No dice si los datos son buenos. Dice si tienen una forma que sabemos que esta
mal: precios negativos, un maximo por debajo del minimo, un cierre fuera del
rango del dia, huecos donde el mercado abrio. Datos que pasen todo esto pueden
seguir siendo malos. Datos que no lo pasen son malos seguro, y ahi si se puede
parar antes de calcular sobre ellos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

# Severidades. `bloquea` es la unica que impide seguir: se reserva para lo que
# invalida los calculos, no para lo que los ensucia. Una comprobacion que para
# el programa a menudo acaba desactivada, y entonces no comprueba nada.
INFO = "info"
AVISO = "aviso"
BLOQUEA = "bloquea"

# Diferencia relativa a partir de la cual se considera que un precio se ha
# reescrito. 0,1 % y no cero: el redondeo del proveedor y el viaje por JSON
# mueven el ultimo decimal, y avisar de eso seria avisar siempre.
TOLERANCIA_REVISION = 0.001

# Fraccion de filas revisadas que hace saltar el bloqueo. Una sola fila puede
# ser una correccion legitima de un error puntual; el 1 % del lote es que la
# serie entera es otra.
MAX_REVISADAS = 0.01

# Sesiones sin precio tras las cuales un instrumento se considera desaparecido.
# Diez cubre un puente largo mas margen; con menos, cualquier festivo raro de
# una bolsa europea daria la alarma.
SESIONES_PARA_DESAPARECIDO = 10

# Fraccion del calendario del mercado que un ticker tiene que cubrir. Por
# debajo, sus indicadores se calculan sobre una serie con agujeros y las medias
# moviles mienten sin decirlo.
MIN_COBERTURA_CALENDARIO = 0.90

# Fraccion de nulos en los campos de precio que se tolera.
MAX_NULOS = 0.02


@dataclass(frozen=True)
class Hallazgo:
    """Un problema concreto, con donde esta y como de grave es."""

    check: str
    severity: str
    ticker: str | None
    when: date | None
    detail: str

    @property
    def bloquea(self) -> bool:
        return self.severity == BLOQUEA


def _relativa(a: pd.Series, b: pd.Series) -> pd.Series:
    """Diferencia relativa entre dos series, a prueba de ceros y de huecos.

    Se divide por el maximo de los dos valores absolutos y no por el viejo: si
    el viejo fuera cero —un volumen de un dia sin negociacion— dividir por el
    daria infinito y todas esas filas saldrian revisadas.

    Devuelve NaN en los dos casos en los que no hay nada que comparar: cuando
    falta alguno de los valores, y cuando los dos son cero (el volumen de un
    dia sin negociacion). Ese NaN se propaga hasta la comparacion final, donde
    `NaN > tolerancia` es False, asi que ninguno de los dos se marca como
    reescritura.

    Escrito aqui porque es la UNICA linea de defensa de los dos casos. Una
    version anterior llevaba ademas una mascara de validos y otra que convertia
    los ceros en ceros: las dos eran codigo muerto —el NaN ya hacia el
    trabajo— y daban la impresion de que habia tres protecciones donde hay una.
    Al mutarlas, ningun test cambiaba, que es como se descubrio.
    """
    escala = np.maximum(a.abs(), b.abs())
    with np.errstate(divide="ignore", invalid="ignore"):
        return (a - b).abs() / escala.replace(0.0, np.nan)


CAMPOS_INMUTABLES = ("open", "high", "low", "close", "volume")


def revisiones(nuevo: pd.DataFrame, existente: pd.DataFrame,
               tolerancia: float = TOLERANCIA_REVISION) -> pd.DataFrame:
    """Filas cuyo precio o volumen YA GUARDADO ha cambiado en la nueva descarga.

    Solo mira los campos que no pueden cambiar: `adj_close` se deja fuera a
    proposito, porque se reajusta con cada dividendo y avisar de eso seria una
    falsa alarma en cada reparto. Ver la cabecera del modulo.

    Devuelve una fila por (ticker, date, campo) con el valor viejo y el nuevo,
    para poder ensenar exactamente que cambio.
    """
    columnas = ["ticker", "date"]
    if nuevo.empty or existente.empty:
        return pd.DataFrame(columns=[*columnas, "campo", "antes", "ahora", "cambio"])

    campos = [c for c in CAMPOS_INMUTABLES
              if c in nuevo.columns and c in existente.columns]
    if not campos:
        return pd.DataFrame(columns=[*columnas, "campo", "antes", "ahora", "cambio"])

    a = nuevo[[*columnas, *campos]].copy()
    b = existente[[*columnas, *campos]].copy()
    for frame in (a, b):
        frame["date"] = pd.to_datetime(frame["date"])

    juntos = a.merge(b, on=columnas, how="inner", suffixes=("_nuevo", "_viejo"))
    if juntos.empty:
        return pd.DataFrame(columns=[*columnas, "campo", "antes", "ahora", "cambio"])

    salida = []
    for campo in campos:
        ahora = pd.to_numeric(juntos[f"{campo}_nuevo"], errors="coerce")
        antes = pd.to_numeric(juntos[f"{campo}_viejo"], errors="coerce")
        # Si falta cualquiera de los dos, `_relativa` da NaN y `NaN > tolerancia`
        # es False: el hueco no se marca. Ver el porque en `_relativa`.
        cambio = _relativa(ahora, antes)
        marcadas = cambio > tolerancia
        if not marcadas.any():
            continue
        trozo = juntos.loc[marcadas, columnas].copy()
        trozo["campo"] = campo
        trozo["antes"] = antes[marcadas].to_numpy()
        trozo["ahora"] = ahora[marcadas].to_numpy()
        trozo["cambio"] = cambio[marcadas].to_numpy()
        salida.append(trozo)

    if not salida:
        return pd.DataFrame(columns=[*columnas, "campo", "antes", "ahora", "cambio"])
    return pd.concat(salida, ignore_index=True).sort_values(
        ["ticker", "date", "campo"]).reset_index(drop=True)


def incoherencias_ohlc(precios: pd.DataFrame) -> pd.DataFrame:
    """Filas que no pueden existir en un mercado.

    Un maximo por debajo del minimo, un cierre fuera del rango del dia, un
    precio negativo. No son datos discutibles: son datos imposibles, y un
    indicador calculado sobre ellos da un numero que parece razonable.
    """
    if precios.empty:
        return pd.DataFrame(columns=["ticker", "date", "motivo"])

    p = precios.copy()
    for c in ("open", "high", "low", "close"):
        if c not in p.columns:
            return pd.DataFrame(columns=["ticker", "date", "motivo"])
        p[c] = pd.to_numeric(p[c], errors="coerce")

    # No hace falta filtrar las filas incompletas: en pandas toda comparacion
    # con NaN es False, asi que una barra con huecos no dispara ninguna de las
    # reglas de abajo. Una version anterior llevaba una mascara `completas`
    # explicita y era codigo muerto: al quitarla no cambiaba ningun test.
    motivos = pd.Series("", index=p.index)

    def marcar(mascara: pd.Series, texto: str) -> None:
        # `motivos == ""` conserva el PRIMER motivo: una barra puede violar
        # varias reglas y decir seis cosas de la misma fila no ayuda a nadie.
        motivos[mascara & (motivos == "")] = texto

    marcar(p[["open", "high", "low", "close"]].le(0).any(axis=1), "precio no positivo")
    marcar(p["high"] < p["low"], "maximo por debajo del minimo")
    marcar(p["close"] > p["high"], "cierre por encima del maximo")
    marcar(p["close"] < p["low"], "cierre por debajo del minimo")
    marcar(p["open"] > p["high"], "apertura por encima del maximo")
    marcar(p["open"] < p["low"], "apertura por debajo del minimo")

    malas = p.loc[motivos != "", ["ticker", "date"]].copy()
    malas["motivo"] = motivos[motivos != ""].to_numpy()
    return malas.reset_index(drop=True)


def calendario_del_mercado(precios: pd.DataFrame, minimo: float = 0.5) -> set:
    """Las fechas en las que el mercado estuvo abierto, deducidas de los datos.

    Una fecha cuenta como sesion si al menos la mitad de los instrumentos tiene
    precio ese dia. Se deduce y no se mantiene una lista de festivos a mano
    porque una lista acabaria discrepando de los precios que de verdad hay, y
    entonces las dos estarian mal y no se sabria cual.
    """
    if precios.empty or "date" not in precios.columns:
        return set()
    por_fecha = precios.groupby("date")["ticker"].nunique()
    total = precios["ticker"].nunique()
    if total == 0:
        return set()
    return set(por_fecha[por_fecha >= total * minimo].index)


def huecos(precios: pd.DataFrame, calendario: set | None = None) -> pd.DataFrame:
    """Cuantas sesiones del mercado le faltan a cada ticker.

    Solo se cuentan los huecos DENTRO de su propio historico: las sesiones
    anteriores a su primer precio no son huecos, son que todavia no cotizaba, y
    contarlas haria que cualquier salida a bolsa reciente pareciera datos rotos.
    """
    vacio = pd.DataFrame(columns=["ticker", "sesiones", "esperadas", "cobertura"])
    if precios.empty:
        return vacio
    sesiones = calendario if calendario is not None else calendario_del_mercado(precios)
    if not sesiones:
        return vacio

    ordenadas = np.array(sorted(sesiones))
    filas = []
    for ticker, grupo in precios.groupby("ticker", sort=False):
        propias = set(grupo["date"])
        if not propias:
            continue
        primera, ultima = min(propias), max(propias)
        esperadas = ordenadas[(ordenadas >= primera) & (ordenadas <= ultima)]
        if len(esperadas) == 0:
            continue
        tiene = len(propias & set(esperadas))
        filas.append({"ticker": ticker, "sesiones": tiene,
                      "esperadas": int(len(esperadas)),
                      "cobertura": tiene / len(esperadas)})
    return pd.DataFrame(filas) if filas else vacio


def desaparecidos(precios: pd.DataFrame, sesiones: int = SESIONES_PARA_DESAPARECIDO
                  ) -> pd.DataFrame:
    """Instrumentos sin precio en las ultimas sesiones del mercado.

    Un ticker deja de llegar cuando la empresa sale del indice, la absorben o
    cambia de simbolo. Sin avisar, se queda en el almacen con su ultimo precio
    congelado y sigue apareciendo en el ranking como si nada.
    """
    vacio = pd.DataFrame(columns=["ticker", "ultimo", "sesiones_sin_datos"])
    if precios.empty:
        return vacio
    todas = np.array(sorted(set(precios["date"])))
    if len(todas) == 0:
        return vacio
    recientes = set(todas[-sesiones:])
    corte = min(recientes)

    filas = []
    for ticker, grupo in precios.groupby("ticker", sort=False):
        ultimo = max(grupo["date"])
        if ultimo < corte:
            sin = int((todas > ultimo).sum())
            filas.append({"ticker": ticker, "ultimo": ultimo,
                          "sesiones_sin_datos": sin})
    return pd.DataFrame(filas) if filas else vacio


def volumen_cero(precios: pd.DataFrame) -> pd.DataFrame:
    """Sesiones con volumen cero: o el valor estuvo suspendido, o falta el dato.

    Importa porque el volumen relativo entra en varias senales. Un cero
    arrastra la media a la baja y hace que el dia siguiente parezca un pico de
    volumen que no existio.
    """
    vacio = pd.DataFrame(columns=["ticker", "sesiones_sin_volumen"])
    if precios.empty or "volume" not in precios.columns:
        return vacio
    v = pd.to_numeric(precios["volume"], errors="coerce")
    malas = precios.loc[v.fillna(0) <= 0, "ticker"]
    if malas.empty:
        return vacio
    conteo = malas.value_counts().reset_index()
    conteo.columns = ["ticker", "sesiones_sin_volumen"]
    return conteo


def nulos(precios: pd.DataFrame) -> float:
    """Fraccion de campos de precio a nulo sobre el total."""
    campos = [c for c in ("open", "high", "low", "close") if c in precios.columns]
    if precios.empty or not campos:
        return 0.0
    celdas = precios[campos]
    return float(celdas.isna().to_numpy().mean())


# ---------------------------------------------------------------------------
# El veredicto
# ---------------------------------------------------------------------------
def evaluar(precios: pd.DataFrame, revisadas: pd.DataFrame | None = None,
            filas_lote: int = 0) -> list[Hallazgo]:
    """Todas las comprobaciones sobre un conjunto de precios.

    `revisadas` y `filas_lote` solo existen durante la ingesta: comparar lo que
    llega con lo que ya habia solo se puede hacer en ese momento. El resto se
    puede recalcular sobre el almacen cuando se quiera.
    """
    fuera: list[Hallazgo] = []

    if revisadas is not None and not revisadas.empty:
        # `revisadas` trae una fila por (ticker, fecha, CAMPO), asi que una sola
        # barra reescrita produce hasta cinco. `filas_lote` cuenta barras. Al
        # dividir uno por otro la fraccion salia hasta 5 veces inflada y una
        # correccion del 0,25 % disparaba el bloqueo del 1 %: la comprobacion
        # que existe para avisar de un problema del proveedor paraba el
        # programa por un problema que no existia.
        barras = len(revisadas[["ticker", "date"]].drop_duplicates())
        fraccion = (barras / filas_lote) if filas_lote else 1.0
        peor = revisadas.sort_values("cambio", ascending=False).iloc[0]
        texto = (
            f"{barras} barras ya guardadas han cambiado en esta "
            f"descarga ({fraccion:.1%} del lote). El mayor: {peor['ticker']} "
            f"el {pd.to_datetime(peor['date']).date()}, {peor['campo']} pasa de "
            f"{peor['antes']:.4g} a {peor['ahora']:.4g}. "
            "El precio al que cotizo algo un dia concreto no cambia: o el "
            "proveedor corrigio un error o metio otro. Cualquier resultado "
            "calculado antes de esto ya no se puede reproducir."
        )
        fuera.append(Hallazgo(
            "precios_revisados",
            BLOQUEA if fraccion > MAX_REVISADAS else AVISO,
            None, None, texto,
        ))

    malas = incoherencias_ohlc(precios)
    if not malas.empty:
        primera = malas.iloc[0]
        fuera.append(Hallazgo(
            "ohlc_incoherente", BLOQUEA, None, None,
            f"{len(malas)} sesiones con precios imposibles. La primera: "
            f"{primera['ticker']} el {pd.to_datetime(primera['date']).date()}, "
            f"{primera['motivo']}.",
        ))

    fraccion_nulos = nulos(precios)
    if fraccion_nulos > MAX_NULOS:
        fuera.append(Hallazgo(
            "precios_nulos", AVISO, None, None,
            f"{fraccion_nulos:.1%} de los campos de precio estan a nulo, por "
            f"encima del {MAX_NULOS:.0%} tolerado.",
        ))

    cobertura = huecos(precios)
    if not cobertura.empty:
        flojos = cobertura[cobertura["cobertura"] < MIN_COBERTURA_CALENDARIO]
        for fila in flojos.itertuples():
            fuera.append(Hallazgo(
                "huecos_en_la_serie", AVISO, fila.ticker, None,
                f"solo {fila.sesiones} de las {fila.esperadas} sesiones que "
                f"hubo mientras cotizaba ({fila.cobertura:.0%}). Sus medias "
                "moviles se calculan sobre una serie con agujeros.",
            ))

    idos = desaparecidos(precios)
    for fila in idos.itertuples():
        fuera.append(Hallazgo(
            "ticker_desaparecido", AVISO, fila.ticker, fila.ultimo,
            f"sin precio desde {fila.ultimo} ({fila.sesiones_sin_datos} "
            "sesiones). Sigue en el almacen con su ultimo precio congelado.",
        ))

    sin_volumen = volumen_cero(precios)
    for fila in sin_volumen.itertuples():
        fuera.append(Hallazgo(
            "volumen_cero", INFO, fila.ticker, None,
            f"{fila.sesiones_sin_volumen} sesiones con volumen cero.",
        ))

    return fuera


def bloqueantes(hallazgos: list[Hallazgo]) -> list[Hallazgo]:
    return [h for h in hallazgos if h.bloquea]


def guardar(conn, hallazgos: list[Hallazgo], run_id: str,
            comprobadas: list[str] | None = None) -> int:
    """Escribe los hallazgos en `data_quality`.

    Se escriben tambien las comprobaciones que SALIERON BIEN, con
    `passed = TRUE` y sin detalle. Guardar solo los problemas deja una tabla en
    la que no se distingue "se comprobo y estaba bien" de "no se comprobo", y
    esa diferencia es justo la que hace falta el dia que algo se rompe: sin
    ella, un fallo silencioso en el propio comprobador se lee como salud.
    """
    from .timeutils import utcnow

    ahora = utcnow()
    filas = [
        {"date": h.when, "ticker": h.ticker, "check_name": h.check,
         "passed": False, "detail": h.detail, "severity": h.severity,
         "checked_at": ahora, "run_id": run_id}
        for h in hallazgos
    ]
    fallidas = {h.check for h in hallazgos}
    for nombre in comprobadas or []:
        if nombre not in fallidas:
            filas.append({"date": None, "ticker": None, "check_name": nombre,
                          "passed": True, "detail": "", "severity": INFO,
                          "checked_at": ahora, "run_id": run_id})
    if not filas:
        return 0
    frame = pd.DataFrame(filas)
    conn.register("_calidad", frame)
    try:
        conn.execute(
            "INSERT INTO data_quality (date, ticker, check_name, passed, detail, "
            "severity, checked_at, run_id) SELECT date, ticker, check_name, "
            "passed, detail, severity, checked_at, run_id FROM _calidad"
        )
    finally:
        conn.unregister("_calidad")
    return len(filas)


# Nombres de las comprobaciones, para poder registrar tambien las que pasan.
# Escrito a mano y no deducido de los hallazgos: si se dedujera, una
# comprobacion que dejara de ejecutarse desapareceria del registro sin dejar
# rastro, que es exactamente el fallo del que esta tabla tiene que proteger.
#
# Van en dos grupos porque no todo se puede comprobar en todo momento, y decir
# "comprobado y bien" de algo que no se ha mirado es peor que no decir nada:
# `puerta_de_calidad` marcaba `precios_revisados` como pasado en cada calculo
# —sin poder compararlo con nada, porque el valor viejo ya se habia
# sobrescrito— y con eso TAPABA el hallazgo bloqueante que la ingesta acababa
# de registrar.
COMPROBACIONES_DEL_ALMACEN = (
    "ohlc_incoherente", "precios_nulos", "huecos_en_la_serie",
    "ticker_desaparecido", "volumen_cero",
)

# Solo se puede comprobar durante la ingesta, comparando lo que llega con lo
# que ya habia. Despues del UPSERT el valor viejo no existe en ninguna parte.
COMPROBACIONES_DE_LA_INGESTA = ("precios_revisados",)

COMPROBACIONES = COMPROBACIONES_DE_LA_INGESTA + COMPROBACIONES_DEL_ALMACEN


def resumen(hallazgos: list[Hallazgo]) -> str:
    """Una linea por severidad, para la consola."""
    if not hallazgos:
        return "Sin problemas de calidad detectados."
    conteo: dict[str, int] = {}
    for h in hallazgos:
        conteo[h.severity] = conteo.get(h.severity, 0) + 1
    partes = [f"{conteo[s]} {s}" for s in (BLOQUEA, AVISO, INFO) if s in conteo]
    return ", ".join(partes)

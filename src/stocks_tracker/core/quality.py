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
from datetime import date, timedelta

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

# Barras revisadas que hacen falta ADEMAS de la fraccion. Sin este minimo, una
# descarga incremental de 15 filas se bloquea con UNA sola barra corregida —el
# 6,7 %—, y las descargas incrementales son las de todos los dias.
MIN_BARRAS_PARA_BLOQUEAR = 20

# Sesiones durante las cuales el volumen se considera provisional. Yahoo publica
# el volumen del dia en curso y lo consolida despues del cierre; tres sesiones
# cubren el fin de semana con margen.
SESIONES_VOLUMEN_PROVISIONAL = 3

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

# Cuanto se puede salir del rango una barra sin que cuente como incoherente.
# Relativo al nivel del precio. Ver `incoherencias_ohlc` para el porque: Yahoo
# redondea los cuatro precios por caminos distintos y una accion que abre en su
# minimo llega con los dos numeros escritos con precisiones diferentes.
TOLERANCIA_OHLC = 1e-6

# Factor de un dia para otro por encima del cual el precio no describe ninguna
# empresa. Una biotecnologica puede triplicar con un resultado clinico; eso pasa
# y no es un error. Multiplicarse por diez, no: eso es un split mal aplicado, un
# ticker cruzado o un decimal perdido.
MAX_SALTO = 10.0

# Cuando una barra imposible pasa de "apartala y sigue" a "para todo".
#
# Una barra imposible NO para el calculo por si sola, sea vieja o de ayer: se
# aparta (ver `core/quarantine`) y se sigue. Solo para cuando hay TANTAS que la
# descarga entera es sospechosa.
#
# Esta linea se movio dos veces, y las dos por instalaciones reales bloqueadas:
#
# 1. Cuatro barras de 2021 impedian calcular. Se anadio la excepcion por
#    antiguedad... y a la siguiente instalacion, tres barras raras en DE, LMT y
#    XLRE —recientes— volvieron a impedirlo.
# 2. Lo que fallaba era la idea de fondo: parar el calculo de 600 empresas
#    porque tres tienen una barra rara castiga a las 597 que no tienen nada.
#
# La proteccion que de verdad importa ya existe y es POR VALOR: apartada la
# barra, ese ticker se queda sin ATR y la regla 13 del gestor de riesgo veta la
# orden con NO_ATR, porque sin ATR no hay stop. Parar ademas el calculo entero
# no protege nada mas, y deja como unica salida `--ignorar-calidad`, que apaga
# todas las comprobaciones a la vez.
SESIONES_RECIENTES_CRITICAS = 10  # solo para decirlo en el mensaje
MAX_BARRAS_IMPOSIBLES = 0.001


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


def revisiones_relevantes(revisadas: pd.DataFrame) -> pd.DataFrame:
    """Quita los ajustes de volumen recientes, que son rutina y no reescritura.

    El volumen de una sesion se publica PROVISIONAL mientras el mercado esta
    abierto y se consolida despues del cierre, con las operaciones que se
    liquidan tarde. Que el volumen de ayer cambie no dice nada malo del
    proveedor: dice que el dato de ayer ya esta completo.

    En la primera instalacion real esto disparo un bloqueo: el volumen del
    ^GSPC del dia anterior paso de 6,4e+08 a 3,0e+09 —el salto de provisional a
    consolidado— y la comprobacion lo trato como si el proveedor hubiera
    reescrito el historico.

    Un cambio de PRECIO en esas mismas fechas si cuenta, y un cambio de volumen
    en una fecha antigua tambien: eso ya no es consolidacion, es otra cosa.
    """
    if revisadas.empty or "campo" not in revisadas.columns:
        return revisadas
    fechas = pd.to_datetime(revisadas["date"])
    corte = fechas.max() - pd.Timedelta(days=SESIONES_VOLUMEN_PROVISIONAL * 7 / 5)
    rutina = (revisadas["campo"] == "volume") & (fechas > corte)
    return revisadas[~rutina]


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


def incoherencias_ohlc(precios: pd.DataFrame,
                       tolerancia: float = TOLERANCIA_OHLC) -> pd.DataFrame:
    """Filas que no pueden existir en un mercado, y por cuanto se salen.

    Un maximo por debajo del minimo, un cierre fuera del rango del dia, un
    precio negativo. No son datos discutibles: son datos imposibles, y un
    indicador calculado sobre ellos da un numero que parece razonable.

    POR QUE HAY UNA TOLERANCIA, Y POR QUE ES RELATIVA

    Yahoo sirve los cuatro precios por caminos distintos y los redondea
    distinto. Una accion que abre justo en el minimo del dia puede llegar con
    `open = 512.46` y `low = 512.4599914550781`: el MISMO numero, escrito con
    dos precisiones. Comparado con `<` a secas, eso es "apertura por debajo del
    minimo" y dispara una alarma que no es de nadie.

    La tolerancia es relativa al nivel del precio porque el ruido tambien lo
    es: 1e-5 sobre 512 es redondeo, y sobre 0,004 es un error del 25 %.

    Y `1e-6` y no algo mas fino porque los precios se cotizan en centimos: la
    discrepancia REAL mas pequena posible en una accion de 100 es de 1e-4
    relativo, cien veces por encima de este umbral. Queda sitio de sobra entre
    el ruido del formato y el error mas pequeno que importa.

    LA COLUMNA `desvio`

    Devolver solo el motivo era el defecto de la version anterior: una barra
    que se salia una diezmillonesima y otra que se salia un 300 % daban
    exactamente el mismo mensaje, y con ese mensaje no habia forma —ni para
    quien lo lee ni para el codigo que decide si parar— de saber cual de las
    dos era. Ahora la magnitud viaja con el hallazgo.

    `motivo` y `desvio` NO describen forzosamente la misma regla: el motivo es
    el primero que se incumple por orden de importancia, y el desvio es el peor
    de todos. Ver `marcar`.
    """
    vacio = pd.DataFrame(columns=["ticker", "date", "motivo", "desvio"])
    if precios.empty:
        return vacio

    p = precios.copy()
    for c in ("open", "high", "low", "close"):
        if c not in p.columns:
            return vacio
        p[c] = pd.to_numeric(p[c], errors="coerce")

    # Nivel de precio de cada barra, para medir el desvio en relativo. El maximo
    # de los cuatro en valor absoluto: no depende de cual de ellos sea el que
    # esta mal, que es justo lo que no se sabe.
    escala = p[["open", "high", "low", "close"]].abs().max(axis=1)
    escala = escala.where(escala > 0)

    # No hace falta filtrar las filas incompletas: en pandas toda comparacion
    # con NaN es False, asi que una barra con huecos no dispara ninguna de las
    # reglas de abajo. Una version anterior llevaba una mascara `completas`
    # explicita y era codigo muerto: al quitarla no cambiaba ningun test.
    motivos = pd.Series("", index=p.index)
    desvios = pd.Series(0.0, index=p.index)

    def marcar(exceso: pd.Series, texto: str) -> None:
        """Apunta la violacion si supera la tolerancia.

        El MOTIVO conserva el primero que se cumpla, y el orden de las llamadas
        de abajo va de lo mas fundamental a lo mas concreto. Es a proposito: una
        barra con el maximo corrompido incumple tres reglas a la vez con
        magnitudes casi identicas, y quedarse con "la mayor" hace que la
        etiqueta baile entre ejecuciones segun cual gane por decimas. Una
        etiqueta que cambia sola no se puede usar para agrupar ni para buscar.

        El DESVIO, en cambio, se queda con el peor de todos: es lo que decide si
        esto para el calculo, y ahi lo que importa es la violacion mas grave que
        tenga la barra, la nombre o no la etiqueta.
        """
        relativo = (exceso / escala).fillna(0.0)
        pasa = relativo > tolerancia
        motivos[pasa & (motivos == "")] = texto
        desvios[pasa] = np.maximum(desvios[pasa], relativo[pasa])

    marcar(p["low"] - p["high"], "maximo por debajo del minimo")
    marcar(p["close"] - p["high"], "cierre por encima del maximo")
    marcar(p["low"] - p["close"], "cierre por debajo del minimo")
    marcar(p["open"] - p["high"], "apertura por encima del maximo")
    marcar(p["low"] - p["open"], "apertura por debajo del minimo")

    # AL FINAL y pisando lo anterior. Un precio a cero o negativo no admite
    # tolerancia relativa —no hay contra que medirlo—, no es un problema de
    # redondeo, y es lo primero que hay que decir de esa barra: puesto antes,
    # cualquiera de las reglas de arriba le robaba la etiqueta.
    cero = p[["open", "high", "low", "close"]].le(0).any(axis=1)
    motivos[cero] = "precio no positivo"
    desvios[cero] = np.maximum(desvios[cero], 1.0)

    hay = motivos != ""
    if not hay.any():
        return vacio
    malas = p.loc[hay, ["ticker", "date"]].copy()
    malas["motivo"] = motivos[hay].to_numpy()
    malas["desvio"] = desvios[hay].to_numpy()
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


def fechas_futuras(precios: pd.DataFrame, hoy: date | None = None) -> pd.DataFrame:
    """Barras fechadas despues de hoy. Un precio de manana no existe.

    Llegan por husos horarios del proveedor y por barras provisionales mal
    fechadas. La puerta de entrada (`providers.base.normalize_ohlcv`) ya las
    filtra, pero esto mira el ALMACEN: las que entraron antes de existir aquel
    filtro siguen dentro, y una sola basta para estropearlo todo.

    Y estropea mas de lo que parece: la vista `current_session` toma la fecha
    mas reciente, asi que una fila del futuro convierte el dashboard entero en
    el retrato de un dia que no ha ocurrido.

    Un dia de margen porque hay mercados por delante de UTC.
    """
    vacio = pd.DataFrame(columns=["ticker", "date"])
    if precios.empty or "date" not in precios.columns:
        return vacio
    limite = pd.Timestamp((hoy or date.today()) + timedelta(days=1))
    fechas = pd.to_datetime(precios["date"], errors="coerce")
    return precios.loc[fechas > limite, ["ticker", "date"]].reset_index(drop=True)


def volumen_negativo(precios: pd.DataFrame) -> pd.DataFrame:
    """No existe negociar una cantidad negativa de acciones.

    Se separa de `volumen_cero` porque son cosas distintas: un cero puede ser
    una suspension real, y un negativo es siempre un dato roto.
    """
    vacio = pd.DataFrame(columns=["ticker", "date"])
    if precios.empty or "volume" not in precios.columns:
        return vacio
    v = pd.to_numeric(precios["volume"], errors="coerce")
    return precios.loc[v < 0, ["ticker", "date"]].reset_index(drop=True)


def saltos_absurdos(precios: pd.DataFrame, acciones: pd.DataFrame | None = None,
                    maximo: float = None) -> pd.DataFrame:
    """Precios que se multiplican o dividen por diez de un dia para otro.

    Ninguna empresa hace eso. Lo que si lo hace, y a menudo, es un split mal
    aplicado, un ticker cruzado con otra empresa o un decimal perdido por el
    camino.

    LOS SPLITS CONOCIDOS SE EXCLUYEN, y sin eso esta comprobacion no serviria:
    un split legitimo produce exactamente este salto, asi que sin la excepcion
    saltaria en cada uno y acabaria desconectada. Por eso depende de que
    `corporate_actions` este poblada, que es lo que hace util haberla llenado.

    El umbral es x10 y no x2 a proposito. Una biotecnologica puede triplicar en
    un dia con un resultado clinico; eso pasa y no es un error. Multiplicarse
    por diez, no.
    """
    maximo = MAX_SALTO if maximo is None else maximo
    vacio = pd.DataFrame(columns=["ticker", "date", "antes", "ahora", "factor"])
    if precios.empty or "close" not in precios.columns:
        return vacio

    p = precios[["ticker", "date", "close"]].copy()
    p["date"] = pd.to_datetime(p["date"], errors="coerce")
    p["close"] = pd.to_numeric(p["close"], errors="coerce")
    p = p[p["date"].notna() & (p["close"] > 0)].sort_values(["ticker", "date"])
    if p.empty:
        return vacio

    p["antes"] = p.groupby("ticker")["close"].shift(1)
    factor = p["close"] / p["antes"]
    p["factor"] = factor
    malas = p[(factor > maximo) | (factor < 1.0 / maximo)].copy()
    if malas.empty:
        return vacio

    if acciones is not None and not acciones.empty:
        splits = acciones[acciones["action_type"] == "split"]
        conocidos = set(zip(
            splits["ticker"].astype(str),
            pd.to_datetime(splits["date"]).dt.date, strict=True,
        ))
        if conocidos:
            fuera = [
                (t, d) not in conocidos
                for t, d in zip(malas["ticker"].astype(str),
                                malas["date"].dt.date, strict=True)
            ]
            malas = malas[fuera]

    malas = malas.rename(columns={"close": "ahora"})
    return malas[["ticker", "date", "antes", "ahora", "factor"]].reset_index(drop=True)


def _barras_recientes(malas: pd.DataFrame, precios: pd.DataFrame,
                      sesiones: int = SESIONES_RECIENTES_CRITICAS) -> pd.DataFrame:
    """Cuales de las barras malas caen en las ultimas sesiones del mercado.

    El corte se toma sobre las fechas que HAY, no sobre el dia de hoy: un
    almacen que lleva una semana sin actualizarse tiene sus ultimas sesiones
    igual de vigentes, y contra la fecha de hoy no saldria ninguna reciente
    justo cuando los datos estan mas viejos.
    """
    if malas.empty or precios.empty:
        return malas.iloc[0:0]
    todas = pd.Series(sorted(set(pd.to_datetime(precios["date"]))))
    if todas.empty:
        return malas.iloc[0:0]
    corte = todas.iloc[-min(sesiones, len(todas))]
    return malas[pd.to_datetime(malas["date"]) >= corte]


# ---------------------------------------------------------------------------
# El veredicto
# ---------------------------------------------------------------------------
def evaluar(precios: pd.DataFrame, revisadas: pd.DataFrame | None = None,
            filas_lote: int = 0,
            instrumentos_ohlc: set[str] | None = None,
            acciones: pd.DataFrame | None = None,
            hoy: date | None = None) -> list[Hallazgo]:
    """Todas las comprobaciones sobre un conjunto de precios.

    `revisadas` y `filas_lote` solo existen durante la ingesta: comparar lo que
    llega con lo que ya habia solo se puede hacer en ese momento. El resto se
    puede recalcular sobre el almacen cuando se quiera.

    `instrumentos_ohlc` son aquellos de los que se usa el rango del dia y no
    solo el cierre —acciones y ETF—. Solo en esos una barra imposible invalida
    algo; ver el detalle en la comprobacion de coherencia.
    """
    fuera: list[Hallazgo] = []

    if revisadas is not None and not revisadas.empty:
        de_verdad = revisiones_relevantes(revisadas)
        rutina = len(revisadas) - len(de_verdad)
        if rutina:
            fuera.append(Hallazgo(
                "precios_revisados", INFO, None, None,
                f"{rutina} ajustes de volumen en las ultimas "
                f"{SESIONES_VOLUMEN_PROVISIONAL} sesiones. Es lo normal: el "
                "volumen del dia se publica provisional y se consolida despues "
                "del cierre. No es una reescritura del historico.",
            ))

    if revisadas is not None and not revisadas.empty and not de_verdad.empty:
        # `revisadas` trae una fila por (ticker, fecha, CAMPO), asi que una sola
        # barra reescrita produce hasta cinco. `filas_lote` cuenta barras. Al
        # dividir uno por otro la fraccion salia hasta 5 veces inflada y una
        # correccion del 0,25 % disparaba el bloqueo del 1 %.
        barras = len(de_verdad[["ticker", "date"]].drop_duplicates())
        fraccion = (barras / filas_lote) if filas_lote else 1.0
        peor = de_verdad.sort_values("cambio", ascending=False).iloc[0]
        texto = (
            f"{barras} barras ya guardadas han cambiado en esta "
            f"descarga ({fraccion:.1%} del lote). El mayor: {peor['ticker']} "
            f"el {pd.to_datetime(peor['date']).date()}, {peor['campo']} pasa de "
            f"{peor['antes']:.4g} a {peor['ahora']:.4g}. "
            "El precio al que cotizo algo un dia concreto no cambia: o el "
            "proveedor corrigio un error o metio otro. Cualquier resultado "
            "calculado antes de esto ya no se puede reproducir."
        )
        # Hacen falta las DOS cosas para bloquear: que la fraccion sea alta y
        # que haya un numero absoluto de barras que no se explique por un
        # descuido puntual. Solo con la fraccion, una descarga incremental de 15
        # filas se bloqueaba con UNA barra revisada —el 6,7 %—, que es
        # exactamente lo que paso en la primera instalacion real.
        grave = fraccion > MAX_REVISADAS and barras >= MIN_BARRAS_PARA_BLOQUEAR
        fuera.append(Hallazgo(
            "precios_revisados", BLOQUEA if grave else AVISO, None, None, texto,
        ))

    malas = incoherencias_ohlc(precios)
    if not malas.empty:
        # La coherencia del OHLC solo importa donde se USA el OHLC. En acciones
        # y ETF alimenta el ATR, los rangos y los stops, y una barra imposible
        # los envenena. En divisas, indices y macro solo se usa el cierre, y que
        # el maximo y el minimo no cuadren es una rareza CONOCIDA de Yahoo: sus
        # OHLC de FX vienen de feeds distintos y el cierre cae fuera del rango
        # con frecuencia.
        #
        # Sin esta distincion, 411 sesiones raras de EURUSD=X —un par de divisas
        # que ni siquiera se puntua— impedian calcular las 600 acciones. Se
        # comprobo en una instalacion real: el programa se quedaba sin poder
        # calcular nada.
        usan_ohlc = malas[malas["ticker"].isin(instrumentos_ohlc or set())]
        resto = malas[~malas["ticker"].isin(instrumentos_ohlc or set())]

        if not usan_ohlc.empty:
            afectados = sorted(usan_ohlc["ticker"].unique())
            peor = usan_ohlc.sort_values("desvio", ascending=False).iloc[0]
            recientes = _barras_recientes(usan_ohlc, precios)
            fraccion = len(usan_ohlc) / len(precios) if len(precios) else 1.0

            # SOLO la cantidad decide si se para. Que la barra mala sea reciente
            # NO para el calculo, y esto costo una segunda instalacion bloqueada:
            # tres barras raras en DE, LMT y XLRE impedian puntuar las otras 600
            # empresas, que no tenian nada malo.
            #
            # Lo que protege el dinero ya funciona, y funciona por valor: la
            # barra se aparta, el ATR de ESE ticker sale vacio, y la regla 13 del
            # gestor de riesgo (`position_sizing_atr`) veta la orden con NO_ATR
            # —sin stop no se abre—. Parar el calculo entero ademas de eso no
            # protege nada mas: solo deja al usuario sin ranking y con un unico
            # camino de salida, `--ignorar-calidad`, que apaga TODAS las
            # comprobaciones. Una puerta que se cierra a menudo se acaba abriendo
            # a lo bruto, y entonces deja de comprobar nada.
            grave = fraccion > MAX_BARRAS_IMPOSIBLES

            donde = (
                f"{len(usan_ohlc)} sesiones con precios imposibles en "
                f"{len(afectados)} valores que SI usan el rango del dia. "
                f"La peor: {peor['ticker']} el "
                f"{pd.to_datetime(peor['date']).date()}, {peor['motivo']}, "
                f"por un {peor['desvio']:.4%} del precio. "
                f"Afectados: {', '.join(afectados[:8])}"
                + (f" y {len(afectados) - 8} mas." if len(afectados) > 8 else ".")
            )
            aparte = (
                " Se apartan del calculo: su maximo, minimo y apertura se "
                "ignoran, y todo lo que use el rango de esos dias sale vacio en "
                "vez de salir con un numero inventado."
            )
            if grave:
                porque = (
                    f" Son el {fraccion:.2%} del almacen, por encima del "
                    f"{MAX_BARRAS_IMPOSIBLES:.2%} que se explica por una rareza "
                    "suelta del proveedor: esto apunta a una descarga entera mal, "
                    "y por eso si se para el calculo."
                )
            elif not recientes.empty:
                cual = recientes.sort_values("desvio", ascending=False).iloc[0]
                porque = (
                    f"{aparte} {len(recientes)} de ellas son de las ultimas "
                    f"{SESIONES_RECIENTES_CRITICAS} sesiones ("
                    f"{cual['ticker']} el {pd.to_datetime(cual['date']).date()}). "
                    "Esos valores se quedan sin ATR reciente, y sin ATR el bot no "
                    "abre posicion en ellos: no puede colocar el stop."
                )
            else:
                porque = f"{aparte} Son antiguas y muy pocas."
            fuera.append(Hallazgo(
                "ohlc_incoherente", BLOQUEA if grave else AVISO, None, None,
                donde + porque,
            ))
        if not resto.empty:
            afectados = sorted(resto["ticker"].unique())
            fuera.append(Hallazgo(
                "ohlc_incoherente", AVISO, None, None,
                f"{len(resto)} sesiones con maximo y minimo incoherentes en "
                f"{', '.join(afectados[:5])}. De estos solo se usa el cierre, "
                "asi que no invalida ningun calculo: los OHLC de divisas e "
                "indices de Yahoo vienen de fuentes distintas y no siempre "
                "cuadran entre si.",
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

    futuras = fechas_futuras(precios, hoy)
    if not futuras.empty:
        afectados = sorted(futuras["ticker"].astype(str).unique())
        fuera.append(Hallazgo(
            "fechas_futuras", BLOQUEA, None, None,
            f"{len(futuras)} barras fechadas DESPUES de hoy, en "
            f"{', '.join(afectados[:5])}. La sesion vigente sale de la fecha mas "
            "reciente del almacen, asi que una sola de estas convierte el "
            "dashboard entero en el retrato de un dia que no ha ocurrido. "
            "Borralas y vuelve a descargar esos valores.",
        ))

    negativos = volumen_negativo(precios)
    if not negativos.empty:
        afectados = sorted(negativos["ticker"].astype(str).unique())
        fuera.append(Hallazgo(
            "volumen_negativo", AVISO, None, None,
            f"{len(negativos)} sesiones con volumen negativo en "
            f"{', '.join(afectados[:5])}. No existe negociar una cantidad "
            "negativa: es un dato roto, no una sesion rara.",
        ))

    saltos = saltos_absurdos(precios, acciones)
    if not saltos.empty:
        peor = saltos.reindex(saltos["factor"].abs().sort_values(ascending=False).index)
        primero = peor.iloc[0]
        afectados = sorted(saltos["ticker"].astype(str).unique())
        fuera.append(Hallazgo(
            "salto_absurdo", AVISO, None, None,
            f"{len(saltos)} saltos de precio de mas de x{MAX_SALTO:g} de un dia "
            f"para otro. El mayor: {primero['ticker']} el "
            f"{pd.to_datetime(primero['date']).date()}, de {primero['antes']:,.4g} "
            f"a {primero['ahora']:,.4g} (x{primero['factor']:,.4g}). Ninguna "
            "empresa hace eso: es un split sin registrar, un ticker cruzado o un "
            f"decimal perdido. Afectados: {', '.join(afectados[:5])}.",
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
    "ticker_desaparecido", "volumen_cero", "fechas_futuras",
    "volumen_negativo", "salto_absurdo",
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

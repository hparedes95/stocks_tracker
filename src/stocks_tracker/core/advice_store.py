"""El marcador: cuantas veces acerto el asesor, y cuantas no.

POR QUE ESTE MODULO ES EL MAS IMPORTANTE DE LA SECCION

Un motor de recomendaciones sin marcador es un horoscopo. Convincente cada
manana, imposible de comprobar, y con el agravante de que aqui hay dinero de
por medio. La unica diferencia entre un asesor y un adivino es que del asesor
se puede llevar la cuenta.

De ahi las cuatro reglas que gobiernan lo que hay aqui:

1. **SE GUARDA EL DIA QUE SE EMITE, Y NO SE REESCRIBE.** Es la disciplina de
   `journal.py`. El sesgo retrospectivo no se corrige con buena intencion:
   cuando algo sale bien, el recuerdo del motivo se reescribe solo para que
   encaje —"ya decia yo que"— y se aprende una leccion que nunca ocurrio.

2. **SE PUNTUA CONTRA EL INDICE, NO CONTRA CERO.** Un 70 % de aciertos no dice
   nada si el mercado subio en el 80 % de los periodos: eso lo consigue una
   moneda comprando cualquier cosa. Lo que se mide es el EXCESO sobre el
   mercado, que es lo unico que justifica molestarse en elegir.

3. **CON POCOS DATOS NO SE DA PORCENTAJE.** Cuatro aciertos de cinco es un 80 %
   que no significa nada. Por debajo de `MIN_PARA_OPINAR` se dice cuantas van y
   se calla el porcentaje: un numero con pinta de estadistica invita a confiar
   en el, y esa confianza es exactamente lo que no se ha ganado todavia.

4. **SOLO SE PUNTUA LO ACCIONABLE.** Contar un MANTENER como acierto porque el
   valor subio seria inflar el marcador con decisiones que nadie tomo. Se
   puntuan COMPRAR, AMPLIAR, REDUCIR y VENDER, que son las que costaron dinero
   o lo ahorraron.

LO QUE ESTE MARCADOR NO PUEDE DECIR, Y HAY QUE REPETIRLO EN PANTALLA

Mide hacia DELANTE y empieza vacio. No hay forma de rellenarlo con historia:
para eso haria falta un backtest de las recomendaciones, y la mitad de ellas se
apoya en fundamentales de los que no existe serie punto-en-el-tiempo. Puntuar
2019 con los balances de hoy es mirar el futuro, y `gate.py` ya se niega a
certificar sobre eso.

Asi que tardara meses en decir algo. Es lento y es lo honesto: cualquier atajo
aqui produce un marcador bonito que no mide nada.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import pandas as pd

from . import fx, lineage
from .advice import ACCIONABLES, Recomendacion, Veredicto
from .timeutils import utcnow

# Cuantas recomendaciones puntuadas hacen falta para ensenar un porcentaje.
#
# Con menos, el porcentaje es ruido con aspecto de estadistica. Treinta es el
# minimo habitual para que una proporcion empiece a estabilizarse, y aqui
# ademas cada dato tarda meses en madurar: es un numero que se alcanza despacio
# y por eso mismo no conviene bajarlo para tener antes algo que ensenar.
MIN_PARA_OPINAR = 30

# Los veredictos que apuestan a que el valor lo hara MEJOR que el mercado. Los
# demas apuestan a lo contrario, y su acierto es el signo opuesto.
ALCISTAS = frozenset({Veredicto.COMPRAR, Veredicto.AMPLIAR})


def guardar_recomendaciones(conn, recomendaciones: list[Recomendacion], *, dia: date,
            weights_hash: str, precios: dict[str, float],
            universe_hash: str = "") -> int:
    """Deja constancia de lo que se recomendo hoy.

    Solo lo ACCIONABLE. Guardar los MANTENER llenaria la tabla de filas que
    nunca se van a puntuar —nadie hizo nada— y ademas tentaria a contarlas como
    aciertos cuando el valor sube.

    Es idempotente por (fecha, ticker, perfil): recalcular el mismo dia
    sustituye la fila en vez de duplicarla. Lo que NO hace es actualizar la
    recomendacion de un dia anterior: cada dia es su propia apuesta.
    """
    accionables = [r for r in recomendaciones if r.veredicto in ACCIONABLES]
    if not accionables:
        return 0

    ahora = utcnow()
    commit = lineage.git_commit()
    filas = []
    for r in accionables:
        filas.append({
            "fecha": dia, "ticker": r.ticker, "weights_hash": weights_hash,
            "veredicto": str(r.veredicto), "conviccion": str(r.conviccion),
            "precio": float(precios.get(r.ticker) or 0.0) or None,
            "stop": r.stop, "importe_eur": r.importe_eur,
            "titulos": r.titulos or r.titulos_a_soltar,
            "riesgo_eur": r.riesgo_eur,
            "motivos": json.dumps(r.motivos, ensure_ascii=False),
            "desmentiria": json.dumps(r.desmentiria, ensure_ascii=False),
            "aviso_fiscal": r.aviso_fiscal,
            "horizonte_meses": r.horizonte_meses,
            "universe_hash": universe_hash, "git_commit": commit,
            "emitida_at": ahora,
        })

    df = pd.DataFrame(filas)
    from .db import upsert_df

    return upsert_df(conn, "recommendations", df,
                     keys=["fecha", "ticker", "weights_hash"])


@dataclass(frozen=True)
class Resultado:
    """El marcador de un grupo de recomendaciones.

    `aciertos` y `puntuadas` van siempre juntos, y el porcentaje solo existe
    cuando hay bastantes. Devolver `None` en vez de un numero es deliberado:
    obliga a quien pinte esto a decidir que ensenar en su lugar, en vez de
    dejar caer un 80 % que nadie ha ganado.
    """

    veredicto: str
    puntuadas: int
    aciertos: int
    exceso_medio: float | None      # puntos porcentuales sobre el indice
    retorno_medio: float | None     # bruto, sin comparar con nada

    @property
    def tasa(self) -> float | None:
        if self.puntuadas < MIN_PARA_OPINAR:
            return None
        return self.aciertos / self.puntuadas

    @property
    def bastantes(self) -> bool:
        return self.puntuadas >= MIN_PARA_OPINAR


def _acierto(veredicto: str, exceso: float) -> bool:
    """Acerto si el valor hizo lo que la recomendacion implicaba.

    Contra el INDICE y no contra cero. Comprar algo que sube un 3 % mientras el
    mercado sube un 8 % no es un acierto: es haber elegido peor que no elegir.
    Y una venta acierta cuando el valor lo hace PEOR que el mercado, porque eso
    es lo que se evito.
    """
    if veredicto in {str(v) for v in ALCISTAS}:
        return exceso > 0
    return exceso < 0


def puntuar(conn, *, hasta: date | None = None,
            benchmark: str = "^GSPC") -> pd.DataFrame:
    """Cada recomendacion vencida, con lo que hizo el valor y el mercado.

    "Vencida" es que haya pasado su horizonte. Antes de eso no se puntua: mirar
    a los quince dias una recomendacion pensada a seis meses mide el ruido de
    dos semanas y no la decision.

    Devuelve una fila por recomendacion. El agregado lo hace `marcador`, para
    que quien quiera revisar un caso concreto pueda hacerlo: un marcador que
    solo da totales no deja aprender de los fallos.
    """
    hasta = hasta or date.today()
    limite = pd.Timestamp(hasta)

    # Se lee y se cruza en pandas en vez de en un ASOF de SQL.
    #
    # No es preferencia de estilo: un ASOF de DuckDB exige comparar dos
    # COLUMNAS, y aqui una de las dos fechas es un parametro. Con `p.date <= ?`
    # responde "Missing ASOF JOIN inequality" —la misma trampa que ya esta
    # documentada en `data_access.get_window_returns`—. Y el volumen es de unas
    # pocas decenas de filas, asi que cruzarlo aqui no cuesta nada y se lee.
    filas = conn.execute(
        """
        SELECT fecha, ticker, veredicto, conviccion, precio, horizonte_meses
        FROM recommendations
        WHERE precio IS NOT NULL AND precio > 0
        ORDER BY fecha DESC, ticker
        """
    ).fetchdf()
    if filas.empty:
        return filas

    filas["fecha"] = pd.to_datetime(filas["fecha"])
    # Un mes son 30 dias: no hace falta mas precision para decidir si una
    # recomendacion a seis meses ya se puede juzgar.
    filas["vence"] = filas["fecha"] + pd.to_timedelta(
        filas["horizonte_meses"].fillna(6) * 30, unit="D")
    filas = filas[filas["vence"] <= limite]
    if filas.empty:
        return filas

    precios = conn.execute(
        "SELECT ticker, date, close FROM prices_daily "
        "WHERE close IS NOT NULL AND date <= ? ORDER BY ticker, date",
        [hasta],
    ).fetchdf()
    if precios.empty:
        return filas.iloc[0:0]
    precios["date"] = pd.to_datetime(precios["date"])

    ultimo = (precios.sort_values("date").groupby("ticker")["close"].last()
              .rename("precio_ahora"))
    filas = filas.merge(ultimo, left_on="ticker", right_index=True, how="left")

    indice = precios[precios["ticker"] == benchmark][["date", "close"]]
    if indice.empty:
        # Sin indice no se puede medir el exceso, y medir contra cero seria
        # justamente el fallo que este modulo evita. Mejor no puntuar nada.
        return filas.iloc[0:0]
    filas = pd.merge_asof(
        filas.sort_values("fecha"), indice.sort_values("date"),
        left_on="fecha", right_on="date", direction="backward",
    ).rename(columns={"close": "bench_entonces"})
    filas["bench_ahora"] = float(indice.sort_values("date")["close"].iloc[-1])

    if filas.empty:
        return filas

    # LOS DOS RETORNOS, EN LA MISMA MONEDA
    #
    # Dentro de cada serie la divisa se cancela —un retorno es un cociente—,
    # pero el EXCESO resta dos retornos que pueden estar en monedas distintas:
    # un valor espanol en euros contra el S&P 500 en dolares.
    #
    # Con el euro subiendo un 5 % frente al dolar en el periodo, un valor que
    # iguale al indice en moneda local aparece con un +5 % de exceso que el
    # inversor no ha ganado. El marcador se apuntaria aciertos por el tipo de
    # cambio, que es exactamente lo que no puede hacer una pantalla que existe
    # para decir si el asesor acierta.
    #
    # Se pasan los dos a euros con el tipo de CADA fecha. Si falta algun tipo,
    # esa fila sale NaN y `dropna` la descarta: no puntuar es correcto, y
    # puntuar con el cambio a medias no.
    divisas = _divisas(conn, filas["ticker"])
    tabla = fx.tipos_en(conn, list(filas["fecha"]) + [limite])
    filas["divisa"] = filas["ticker"].map(divisas).fillna(fx.BASE)
    divisa_indice = divisas.get(benchmark, fx.BASE)
    hoy = pd.Series([limite] * len(filas), index=filas.index)

    entonces_eur = fx.a_base_en_fecha(
        filas["precio"], filas["divisa"], filas["fecha"], tabla)
    ahora_eur = fx.a_base_en_fecha(
        filas["precio_ahora"], filas["divisa"], hoy, tabla)
    bench_entonces_eur = fx.a_base_en_fecha(
        filas["bench_entonces"], pd.Series([divisa_indice] * len(filas),
                                           index=filas.index),
        filas["fecha"], tabla)
    bench_ahora_eur = fx.a_base_en_fecha(
        filas["bench_ahora"], pd.Series([divisa_indice] * len(filas),
                                        index=filas.index), hoy, tabla)

    filas["retorno"] = ahora_eur / entonces_eur - 1.0
    filas["retorno_indice"] = bench_ahora_eur / bench_entonces_eur - 1.0
    filas["exceso"] = filas["retorno"] - filas["retorno_indice"]
    # Sin precio posterior o sin indice no se puede juzgar. Se descartan en vez
    # de contarlas como fallo: un dato que falta no es un error del asesor, y
    # cargarselo en su cuenta haria el marcador injustamente malo.
    filas = filas.dropna(subset=["retorno", "exceso"])
    if filas.empty:
        return filas
    filas["acierto"] = [
        _acierto(v, e) for v, e in zip(filas["veredicto"], filas["exceso"],
                                       strict=True)
    ]
    return filas


def marcador(puntuadas: pd.DataFrame) -> list[Resultado]:
    """El agregado, por veredicto y en total.

    Por veredicto Y en total porque son preguntas distintas: "acierto el asesor"
    y "acierta comprando pero no vendiendo" tienen respuestas muy distintas y la
    segunda es la que dice que hay que arreglar.
    """
    if puntuadas.empty:
        return []

    salida = []
    grupos = list(puntuadas.groupby("veredicto"))
    grupos.append(("TODO", puntuadas))
    for nombre, g in grupos:
        salida.append(Resultado(
            veredicto=str(nombre),
            puntuadas=len(g),
            aciertos=int(g["acierto"].sum()),
            exceso_medio=float(g["exceso"].mean() * 100),
            retorno_medio=float(g["retorno"].mean() * 100),
        ))
    return salida


def resumen_honesto(resultados: list[Resultado]) -> str:
    """Una frase que no promete mas de lo que hay.

    Existe como funcion propia —y no como un f-string en la pantalla— porque es
    justo la frase que mas facil resulta inflar sin querer. Aqui se puede
    testear palabra por palabra.
    """
    total = next((r for r in resultados if r.veredicto == "TODO"), None)
    if total is None or total.puntuadas == 0:
        return (
            "Todavia no hay ninguna recomendacion vencida que puntuar. El "
            "marcador mide hacia delante y empieza vacio: no hay forma honesta "
            "de rellenarlo con historia."
        )
    if not total.bastantes:
        return (
            f"Llevan {total.puntuadas} recomendaciones puntuadas, de las que "
            f"{total.aciertos} batieron al indice. Son muy pocas para sacar un "
            f"porcentaje: hasta {MIN_PARA_OPINAR} no se ensena tasa de acierto, "
            "porque un numero asi invita a confiar en el antes de tiempo."
        )
    return (
        f"{total.aciertos} de {total.puntuadas} recomendaciones batieron al "
        f"indice ({total.tasa:.0%}). Exceso medio sobre el mercado: "
        f"{total.exceso_medio:+.1f} puntos."
    )


def _divisas(conn, tickers) -> dict[str, str]:
    """La divisa de cotizacion de cada ticker, en mayusculas.

    Lo que no tenga divisa declarada se deja fuera y quien lo use cae a EUR.
    Es el caso de un almacen antiguo, y suponer euros para un valor europeo
    acierta mucho mas que suponer dolares.
    """
    nombres = sorted({str(t) for t in tickers if t})
    if not nombres:
        return {}
    marcadores = ", ".join("?" for _ in nombres)
    filas = conn.execute(
        f"SELECT ticker, currency FROM instruments "
        f"WHERE ticker IN ({marcadores}) AND currency IS NOT NULL",
        nombres,
    ).fetchall()
    return {t: str(c).upper() for t, c in filas if c}

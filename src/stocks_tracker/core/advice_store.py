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

import hashlib
import json
from dataclasses import dataclass
from datetime import date

import pandas as pd

from . import lineage
from .advice import ACCIONABLES, Recomendacion, Veredicto
from .evaluation import BENCHMARKS, MODEL_VERSION, independent_episodes
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
            "assessment": json.dumps(r.assessment, ensure_ascii=False),
        })

    df = pd.DataFrame(filas)
    from .db import upsert_df
    from .investor_profile import load
    from .market_bundle import engine_fingerprint
    profile = load(conn)
    signature = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()[:10]
    df["model_version"] = f"{MODEL_VERSION}:{engine_fingerprint()[:12]}:{signature}"
    countries = dict(conn.execute("SELECT ticker, country FROM instruments").fetchall())
    df["benchmark"] = [r.benchmark or BENCHMARKS.get(countries.get(r.ticker), "VT") for r in accionables]
    df["roundtrip_cost"] = [getattr(r, "roundtrip_cost", None) for r in accionables]
    from .trading_calendar import calendar_name
    metadata = {t: (c, e) for t, c, e in conn.execute('SELECT ticker,country,exchange FROM instruments').fetchall()}
    df['horizon_sessions'] = [r.horizonte_meses * 21 for r in accionables]
    df['calendar_name'] = [calendar_name(*metadata.get(r.ticker, (None, None))) for r in accionables]
    # Registro inmutable de cada cambio, aunque la tarjeta diaria se sustituya.
    for record in df.to_dict("records"):
        payload = json.dumps(record, default=str, sort_keys=True, ensure_ascii=False)
        identity = hashlib.sha256(payload.encode()).hexdigest()
        conn.execute("INSERT INTO recommendation_ledger VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                     [identity, ahora, payload])
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
    version: str = "legacy"
    neto_medio: float | None = None

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
            benchmark: str | None = None) -> pd.DataFrame:
    """Resultados al vencimiento, nunca al último precio posterior."""
    from .evaluation import evaluate

    return evaluate(conn, hasta=hasta, benchmark=benchmark)


def marcador(puntuadas: pd.DataFrame) -> list[Resultado]:
    """El agregado, por veredicto y en total.

    Por veredicto Y en total porque son preguntas distintas: "acierto el asesor"
    y "acierta comprando pero no vendiendo" tienen respuestas muy distintas y la
    segunda es la que dice que hay que arreglar.
    """
    if puntuadas.empty:
        return []

    puntuadas = independent_episodes(puntuadas)
    # Nunca sumar modelos/perfiles distintos en un supuesto historial único.
    versions = [k for k in ("model_version", "weights_hash", "benchmark", "horizonte_meses") if k in puntuadas]
    if versions and puntuadas.groupby(versions, dropna=False).ngroups > 1:
        salida = []
        for key, group in puntuadas.groupby(versions, dropna=False):
            label = " / ".join(map(str, key if isinstance(key, tuple) else (key,)))
            for result in marcador(group):
                from dataclasses import replace
                salida.append(replace(result, veredicto=f"{result.veredicto} [{label}]", version=label))
        return salida
    salida = []
    grupos = list(puntuadas.groupby("veredicto"))
    grupos.append(("TODO", puntuadas))
    for nombre, g in grupos:
        salida.append(Resultado(
            veredicto=str(nombre),
            puntuadas=len(g),
            aciertos=int(g["acierto"].sum()),
            exceso_medio=float(g.get("ventaja_bruta", g["exceso"]).mean() * 100),
            retorno_medio=float(g["retorno"].mean() * 100),
            neto_medio=(float(g["ventaja_neta"].mean() * 100)
                        if "ventaja_neta" in g and g["ventaja_neta"].notna().all() else None),
        ))
    return salida


def resumen_honesto(resultados: list[Resultado]) -> str:
    """Una frase que no promete mas de lo que hay.

    Existe como funcion propia —y no como un f-string en la pantalla— porque es
    justo la frase que mas facil resulta inflar sin querer. Aqui se puede
    testear palabra por palabra.
    """
    total = next((r for r in resultados if r.veredicto == "TODO"), None)
    if resultados and total is None:
        return "Resultados separados por modelo, perfil e índice. No se mezclan en una tasa global."
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

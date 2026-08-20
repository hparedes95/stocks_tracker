"""Que se probo, no solo lo que acabo funcionando.

EL PROBLEMA QUE ESTO RESUELVE, Y POR QUE NO LO RESUELVE BENJAMINI-HOCHBERG

La correccion por multiples hipotesis cuenta las pruebas que se REGISTRAN.
Si se prueban veinte variantes de una senal, se deja la mejor en el codigo y
solo se valida esa, el recuento dice 1 y la correccion no corrige nada.

Y eso pasa aunque nadie haga trampas, porque no hace falta mala fe: alguien
eligio que once senales incluir, que cuatro horizontes, contra que referencia
medir y que coste asumir, VIENDO resultados. Cada una de esas decisiones es una
prueba que no aparece en ningun contador.

Contra eso no hay estadistica que valga. Solo hay dos cosas que funcionan:

1. **Anotar lo que se prueba, cuando se prueba.** Si el registro dice que esta
   senal es el intento numero catorce sobre el mismo mercado, ese numero es
   informacion aunque no se corrija nada con el.
2. **Guardar un trozo de historico que no se mire.** Descubres con una parte y
   confirmas con la otra, y la segunda no se toca hasta que la estrategia esta
   escrita y congelada. Es la unica defensa real, porque no depende de que uno
   se acuerde de cuantas veces probo.

LA ESCALERA

    descubierta -> significativa -> estable -> confirmada
                                            \\-> refutada

Cada peldano anade una exigencia y NINGUNO se salta:

- **descubierta**: hay eventos suficientes en fechas suficientes. Nada mas.
- **significativa**: el exceso sobrevive al HAC agrupado por fecha Y a la
  correccion por el numero de pruebas de la tanda.
- **estable**: ademas aguanta en dos de cada tres ventanas del periodo de
  descubrimiento, con embargo entre ellas.
- **confirmada**: ademas repite en el tramo que NO se uso para descubrirla, y
  la especificacion estaba congelada antes de mirarlo.
- **refutada**: llego a confirmacion y fallo. Es un estado FINAL a proposito:
  ver mas abajo.

POR QUE `refutada` NO SE PUEDE DESHACER

Si una senal que falla la confirmacion pudiera volver a intentarlo, el
procedimiento entero seria decorativo: bastaria con cambiar un parametro y
repetir hasta que el tramo reservado tambien saliera bien. Que es exactamente
el problema que se venia a evitar, cometido mas despacio.

Cambiar la senal, el horizonte, el universo, la referencia, el coste o
cualquier parametro produce un `spec_hash` distinto, o sea un experimento
NUEVO. Eso esta permitido —es investigar—, pero queda anotado, y el registro
dira que llevas catorce intentos. Que es la verdad, y es justo lo que un
contador de pruebas honesto tiene que decir.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import pandas as pd

from ..core import lineage
from ..core.ids import ulid

# Peldanos, del mas bajo al mas alto.
DESCUBIERTA = "descubierta"
SIGNIFICATIVA = "significativa"
ESTABLE = "estable"
CONFIRMADA = "confirmada"
REFUTADA = "refutada"
SIN_DATOS = "sin_datos"

# `REFUTADA` NO ESTA AQUI, Y ES LA PROPIEDAD IMPORTANTE DE ESTA TUPLA.
#
# No es un peldano mas bajo: es la salida por el otro lado. Una senal desmentida
# fuera de muestra no "vale menos", vale nada. Metiendola en `ORDEN` se
# convertiria en un escalon, y cualquier comparacion por posicion la dejaria
# pasar donde se pidiera un minimo bajo.
ORDEN = (SIN_DATOS, DESCUBIERTA, SIGNIFICATIVA, ESTABLE, CONFIRMADA)

# Las dos fases.
DESCUBRIMIENTO = "descubrimiento"
CONFIRMACION = "confirmacion"


class ContaminacionError(RuntimeError):
    """Se ha intentado usar el tramo reservado sin cumplir el procedimiento."""


@dataclass(frozen=True)
class Spec:
    """La definicion completa de un experimento.

    Todo lo que cambia el resultado va aqui, y solo lo que cambia el resultado.
    Su hash es la IDENTIDAD de la estrategia: si cambias cualquier campo tienes
    otra estrategia, no una version mejorada de la misma, y por tanto otro
    experimento. Esa es la regla que impide ir retocando hasta que el tramo
    reservado tambien salga bien.
    """

    signal_id: str
    scope: str
    horizon_days: int
    benchmark: str = "universo_equiponderado"
    cost_bps: float = 10.0
    universe: str = "todos"
    params: dict = field(default_factory=dict)

    @property
    def spec_hash(self) -> str:
        return lineage.config_hash(asdict(self))

    def as_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Congelar
# ---------------------------------------------------------------------------
def congelar(conn, spec: Spec, nota: str = "") -> datetime:
    """Fija la especificacion antes de mirar el tramo reservado.

    Idempotente: congelar dos veces la misma especificacion no cambia la fecha
    original. Si la cambiara, bastaria con volver a congelar para borrar el
    rastro de cuando se decidio, que es el dato que hace util la congelacion.
    """
    ya = esta_congelada(conn, spec.spec_hash)
    if ya is not None:
        return ya
    ahora = datetime.now()
    conn.execute(
        "INSERT INTO strategy_freezes VALUES (?, ?, ?, ?, ?)",
        [spec.spec_hash, ahora, spec.as_json(), lineage.git_commit(), nota],
    )
    return ahora


def esta_congelada(conn, spec_hash: str) -> datetime | None:
    fila = conn.execute(
        "SELECT frozen_at FROM strategy_freezes WHERE spec_hash = ?", [spec_hash]
    ).fetchone()
    return fila[0] if fila else None


def esta_refutada(conn, spec_hash: str) -> bool:
    """Si esta especificacion ya fallo una confirmacion.

    Se consulta ANTES de dejar correr otra confirmacion. Sin esto, el
    procedimiento seria decorativo: se repetiria hasta que saliera.
    """
    fila = conn.execute(
        "SELECT COUNT(*) FROM experiments WHERE spec_hash = ? AND estado = ?",
        [spec_hash, REFUTADA],
    ).fetchone()
    return bool(fila and fila[0])


# ---------------------------------------------------------------------------
# La escalera
# ---------------------------------------------------------------------------
def peldano(*, hay_datos: bool, significativa: bool, estable: bool,
            fase: str, repite_fuera_de_muestra: bool | None = None) -> str:
    """El estado que corresponde, sin saltarse ninguno.

    En DESCUBRIMIENTO el techo es `estable`, y ese tope es lo que hace que la
    escalera signifique algo: por bueno que salga el numero, sale sobre los
    mismos datos con los que se eligio la senal.

    En CONFIRMACION solo hay dos salidas, `confirmada` o `refutada`, y no se
    vuelve a los peldanos de abajo. Es deliberado: si una senal que falla la
    confirmacion pudiera quedarse en "descubierta", el intento no dejaria
    cicatriz y se podria repetir manana como si fuera la primera vez. Gastar el
    tramo reservado tiene que costar algo, o no reserva nada.
    """
    if fase == CONFIRMACION:
        if not hay_datos:
            return SIN_DATOS
        return CONFIRMADA if repite_fuera_de_muestra else REFUTADA
    if not hay_datos:
        return SIN_DATOS
    if not significativa:
        return DESCUBIERTA
    if not estable:
        return SIGNIFICATIVA
    return ESTABLE


def candidatas(conn, scope: str) -> set[str]:
    """Las especificaciones que llegaron a `estable` en descubrimiento.

    Solo estas pueden pasar a confirmacion. Llevar al tramo reservado una senal
    que ni siquiera fue significativa gastaria muestra —que es un recurso que no
    se repone— para contestar una pregunta que ya tenia respuesta.
    """
    filas = conn.execute(
        "SELECT DISTINCT spec_hash FROM experiments "
        "WHERE scope = ? AND fase = ? AND estado = ?",
        [scope, DESCUBRIMIENTO, ESTABLE],
    ).fetchall()
    return {f[0] for f in filas}


# AQUI HUBO UN `alcanza(estado, minimo)` Y SE QUITO A PROPOSITO.
#
# Comparaba dos peldanos por su posicion en `ORDEN`, para preguntar "¿esta senal
# llega al minimo exigido?". Nadie lo preguntaba: ninguna parte del programa
# exige un peldano minimo, porque la puerta que decide si una senal se muestra
# como recomendacion vive en `compute/validate` y mira otra cosa.
#
# Una funcion de puerta que no guarda ninguna puerta es peor que no tenerla:
# al leerla parece que la regla esta aplicada en algun sitio. La invariante que
# documentaba —que `REFUTADA` no puede estar en `ORDEN`— sigue escrita, pero
# donde de verdad manda, que es junto a la propia tupla.


# ---------------------------------------------------------------------------
# Registrar
# ---------------------------------------------------------------------------
def registrar(conn, spec: Spec, *, fase: str, estado: str, split_at: date,
              data_from=None, data_to=None, n_obs: int = 0, n_dates: int = 0,
              avg_excess: float = float("nan"), t_stat: float = float("nan"),
              p_value: float = float("nan"), q_value: float = float("nan"),
              motivo: str = "") -> str:
    """Anota un experimento, salga como salga.

    Se anotan TODOS, tambien los que no llegan a ninguna parte. Un registro que
    solo guarda los que funcionaron es un album de aciertos y no sirve para
    contar cuantas veces se miro, que es lo unico para lo que existe.
    """
    experiment_id = ulid()
    conn.execute(
        "INSERT INTO experiments VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            experiment_id, spec.spec_hash, datetime.now(), fase,
            spec.signal_id, spec.scope, int(spec.horizon_days), spec.benchmark,
            float(spec.cost_bps), spec.universe, json.dumps(spec.params, sort_keys=True),
            split_at, data_from, data_to, int(n_obs), int(n_dates),
            float(avg_excess), float(t_stat), float(p_value), float(q_value),
            estado, motivo,
        ],
    )
    return experiment_id


def intentos(conn, signal_id: str, scope: str) -> int:
    """Cuantas especificaciones DISTINTAS se han probado sobre esta senal.

    Se cuentan hashes distintos y no filas: repetir el mismo experimento no es
    mirar otra vez, es la misma mirada. Cambiar el horizonte, el coste o la
    referencia SI lo es, y ese es el numero que hay que ensenar junto al
    resultado.
    """
    fila = conn.execute(
        "SELECT COUNT(DISTINCT spec_hash) FROM experiments "
        "WHERE signal_id = ? AND scope = ?", [signal_id, scope],
    ).fetchone()
    return int(fila[0]) if fila else 0


def historial(conn, scope: str | None = None) -> pd.DataFrame:
    """El registro entero, lo mas reciente primero."""
    sql = ("SELECT created_at, fase, signal_id, scope, horizon_days, cost_bps, "
           "estado, n_obs, n_dates, avg_excess, q_value, spec_hash, motivo "
           "FROM experiments")
    params: list = []
    if scope:
        sql += " WHERE scope = ?"
        params.append(scope)
    return conn.execute(sql + " ORDER BY created_at DESC", params).fetchdf()


# ---------------------------------------------------------------------------
# La frontera
# ---------------------------------------------------------------------------
def comprobar_confirmacion(conn, spec: Spec) -> datetime:
    """Deja pasar una confirmacion solo si se cumple el procedimiento.

    Dos negativas, y las dos existen porque sin ellas el tramo reservado se
    gasta sin enterarse:

    1. Sin congelar no se confirma. Si se pudiera mirar el tramo reservado y
       DESPUES decidir la especificacion, ese tramo dejaria de estar fuera de
       muestra en el mismo momento en que se mira.
    2. Refutada no se reintenta. Repetir hasta que salga convierte el
       procedimiento en el problema que venia a evitar, solo que mas despacio.
    """
    congelada = esta_congelada(conn, spec.spec_hash)
    if congelada is None:
        raise ContaminacionError(
            f"'{spec.signal_id}' no esta congelada. El tramo de confirmacion no "
            "se mira hasta que la especificacion esta fijada: si se mira antes, "
            "deja de estar fuera de muestra en ese mismo momento. Congelala "
            "primero con `--congelar`."
        )
    if esta_refutada(conn, spec.spec_hash):
        raise ContaminacionError(
            f"'{spec.signal_id}' con esta misma especificacion ya fallo la "
            "confirmacion. Reintentarla hasta que salga es exactamente lo que "
            "el procedimiento existe para impedir. Si quieres probar otra cosa, "
            "cambia la especificacion: sera otro experimento y quedara anotado "
            "como tal."
        )
    return congelada

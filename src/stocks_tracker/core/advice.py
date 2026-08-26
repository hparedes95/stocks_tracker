"""El asesor: una sola decision por valor, con sus motivos y su tamano.

QUE ES ESTO Y QUE NO ES

No predice. Ningun modulo de este programa sabe lo que va a hacer el mercado, y
este tampoco. Lo que hace es aplicar TUS reglas de forma consistente y decir en
voz alta lo que implican hoy.

La diferencia importa mas de lo que parece:

    Prediccion   "AAPL va a subir, compra."
                 No es construible. Cualquier numero que lo acompane es
                 precision falsa.

    Decision     "Tus reglas implican comprar AAPL por A, B y C. Tamano 380 EUR,
                 stop en 172,40. Esto seria un error si pasa D. Reglas de este
                 tipo llevan N aciertos de M."
                 Esto si, y es lo que hace un asesor con disciplina.

La segunda es ademas la que da dinero. Lo que arruina la rentabilidad de un
particular no suele ser fallar la prediccion: es la inconsistencia, el tamano
mal puesto y los costes e impuestos. Las tres cosas se arreglan con reglas
escritas y aplicadas igual todos los dias.

LAS TRES DECISIONES QUE GOBIERNAN ESTE MODULO

Las tomo el usuario, y estan aqui porque cambian el codigo entero:

1. HORIZONTE: MESES. Mandan los factores y el deterioro. Las senales tecnicas
   solo entran si su evidencia esta en ESTABLE o CONFIRMADA; una senal recien
   descubierta no mueve un consejo. A meses vista, el ruido de una semana no es
   informacion.

2. VENDER SOLO SI LA TESIS SE ROMPE. No hay rotacion por "hay algo mejor".
   Cambiar una posicion buena por otra ligeramente mejor gana unas decimas en
   teoria y pierde en comisiones, cambio de divisa e impuestos: es justo donde
   se evapora la rentabilidad del particular. Y ante la duda, REDUCIR antes que
   VENDER, porque una venta es irreversible y activa impuestos.

3. LA REGLA FISCAL AVISA, NO VETA. El coste sale calculado en euros al lado de
   la recomendacion. A veces cortar una perdida es lo correcto aunque cueste
   impuestos, y esa decision es del que pone el dinero.

POR QUE NO PASA POR `RiskManager`

`trading/risk.py` gobierna la EJECUCION contra una cuenta viva: perdida diaria,
ordenes por dia, day trades, killswitch. Nada de eso aplica a un consejo que no
ejecuta nada, y montar un `StrategyContext` con su broker para poder aconsejar
seria arrastrar medio bot a una pantalla de lectura.

Lo que si aplica son los limites de FORMA de la cartera —cuanto puede pesar una
posicion, un sector, cuantas posiciones caben, cuanta caja se reserva— y esos
se leen del MISMO `config/trading.yaml`, para que el asesor y el bot no puedan
decir cosas distintas. `sizing.size_by_atr` se reutiliza tal cual.

SIN_OPINION ES UN VEREDICTO DE PRIMERA CLASE

Cuando faltan datos, este modulo lo dice. No rellena con MANTENER, que sonaria
a "lo he mirado y esta bien". El hueco tiene que verse: es la unica forma de
que se arregle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from . import deterioration as det
from .config import get_trading_config


# ---------------------------------------------------------------------------
# El vocabulario, cerrado a proposito
# ---------------------------------------------------------------------------
class Veredicto(StrEnum):
    """Lo que el asesor puede decir. Nada mas.

    Un conjunto cerrado y no texto libre porque un veredicto tiene que poder
    contarse, guardarse y puntuarse despues. "Quiza convendria vigilarlo" no se
    puede llevar a un marcador de aciertos, y lo que no se puede puntuar acaba
    siendo un horoscopo.
    """

    COMPRAR = "comprar"          # no la tienes y tus reglas dicen que entres
    AMPLIAR = "ampliar"          # ya la tienes, sigue buena y pesa poco
    MANTENER = "mantener"        # nada que hacer hoy
    REDUCIR = "reducir"          # recortar, no salir
    VENDER = "vender"            # la tesis se rompio, o el stop se perforo
    VETADA = "vetada"            # tus limites lo impiden; se dice cual
    SIN_OPINION = "sin_opinion"  # faltan datos. NO es "esta bien"


class Conviccion(StrEnum):
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"


ETIQUETA = {
    Veredicto.COMPRAR: "Comprar",
    Veredicto.AMPLIAR: "Ampliar",
    Veredicto.MANTENER: "Mantener",
    Veredicto.REDUCIR: "Reducir",
    Veredicto.VENDER: "Vender",
    Veredicto.VETADA: "No, por tus limites",
    Veredicto.SIN_OPINION: "Sin opinion",
}

# Los que piden actuar hoy. Se usan para ordenar la pantalla y para saber que
# hay que guardar en el marcador.
ACCIONABLES = frozenset({Veredicto.COMPRAR, Veredicto.AMPLIAR,
                         Veredicto.REDUCIR, Veredicto.VENDER})


# ---------------------------------------------------------------------------
# Umbrales. Todos aqui, todos con su porque
# ---------------------------------------------------------------------------
# Percentil compuesto a partir del cual un valor es candidato de compra.
#
# 0,90 deja unos 60 valores de 620. Parece poco y es deliberado: con
# `max_positions: 7`, recomendar el percentil 75 —150 valores— seria dar una
# lista que no cabe en la cartera, y una lista que no cabe se lee por encima.
UMBRAL_COMPRAR = 0.90

# Y a partir de cual sigue mereciendo la pena AMPLIAR algo que ya tienes.
#
# Mas bajo que el de compra a proposito. Ampliar no gasta una plaza de las siete
# ni paga el coste de conocer una empresa nueva; exigirle el mismo liston que a
# una entrada nueva llevaria a soltar posiciones buenas para comprar otras
# parecidas, que es la rotacion que este modulo no hace.
UMBRAL_AMPLIAR = 0.75

# Por debajo de este percentil, una posicion tuya deja de estar entre las
# buenas. NO dispara una venta por si solo —eso seria rotacion encubierta—:
# rebaja la conviccion de MANTENER y se dice en pantalla.
UMBRAL_YA_NO_DESTACA = 0.40

# Cobertura minima de datos para opinar. Por debajo, el compuesto se ha
# calculado con tan pocos factores que ordena ruido.
MIN_COBERTURA = 0.50

# Cuanto tiene que pesar de menos una posicion respecto a su objetivo para que
# ampliarla valga el coste de la operacion. Por debajo, la comision se come la
# diferencia y se esta operando por operar.
MARGEN_PARA_AMPLIAR = 0.25


@dataclass(frozen=True)
class Recomendacion:
    """Una decision, con todo lo que hace falta para ejecutarla o discutirla.

    `desmentiria` no es adorno: es la parte que convierte esto en una
    afirmacion comprobable en vez de una opinion. Si no se puede escribir que
    tendria que pasar para que el consejo fuera un error, el consejo no vale
    nada y lo honesto es SIN_OPINION.
    """

    ticker: str
    veredicto: Veredicto
    conviccion: Conviccion
    motivos: list[str] = field(default_factory=list)
    desmentiria: list[str] = field(default_factory=list)
    # Solo en COMPRAR y AMPLIAR
    importe_eur: float | None = None
    titulos: float | None = None
    stop: float | None = None
    riesgo_eur: float | None = None
    limitado_por: str = ""
    # Solo en REDUCIR y VENDER
    titulos_a_soltar: float | None = None
    # Comun
    aviso_fiscal: str = ""
    horizonte_meses: int = 6

    @property
    def accionable(self) -> bool:
        return self.veredicto in ACCIONABLES

    @property
    def etiqueta(self) -> str:
        return ETIQUETA[self.veredicto]


def _limites() -> dict:
    """Los limites de FORMA de la cartera, del mismo sitio que los del bot.

    Compartir fichero no es comodidad: es que el asesor y el bot no puedan
    decir cosas distintas sobre la misma cartera. Dos fuentes de verdad para el
    mismo limite se separan el dia que alguien toca una.
    """
    riesgo = get_trading_config().raw.get("risk", {})
    return {
        "risk_per_trade_pct": float(riesgo.get("risk_per_trade_pct", 1.5)),
        "atr_stop_mult": float(riesgo.get("atr_stop_mult", 2.5)),
        "max_position_pct": float(riesgo.get("max_position_pct", 22.0)),
        "target_position_pct": float(riesgo.get("target_position_pct", 15.0)),
        "max_positions": int(riesgo.get("max_positions", 7)),
        "max_sector_pct": float(riesgo.get("max_sector_pct", 35.0)),
        "min_cash_pct": float(riesgo.get("min_cash_pct", 10.0)),
        "min_notional": float(riesgo.get("min_notional", 1.0)),
    }


# ---------------------------------------------------------------------------
# El lado de venta
# ---------------------------------------------------------------------------
def sobre_una_posicion(
    ticker: str,
    *,
    diagnostico: det.Diagnostico | None = None,
    banderas: list[str] | None = None,
    precio: float | None = None,
    stop: float | None = None,
    peso_pct: float | None = None,
    peso_sector_pct: float | None = None,
    percentil: float | None = None,
    titulos: float | None = None,
    aviso_fiscal: str = "",
) -> Recomendacion:
    """Que hacer con algo que YA tienes.

    El orden de las reglas es el orden de gravedad, y la primera que encaja
    manda. No se suman: un stop perforado no necesita que ademas haya deterioro
    para justificar la salida, y buscar mas motivos cuando ya hay uno
    suficiente solo sirve para retrasar la decision.

    LO QUE NO DISPARA UNA VENTA, Y ES DELIBERADO: que haya aparecido un
    candidato mejor. Eso es rotacion, cuesta comisiones e impuestos y es donde
    se evapora la rentabilidad del particular.
    """
    lim = _limites()
    banderas = banderas or []

    # --- 0. Sin datos, se dice -------------------------------------------
    if precio is None or precio <= 0:
        return Recomendacion(
            ticker, Veredicto.SIN_OPINION, Conviccion.BAJA,
            motivos=["No hay precio reciente de este valor."],
            desmentiria=["Con un precio al dia, esta posicion si se puede juzgar."],
        )

    # --- 1. El stop, que es mecanico -------------------------------------
    # Va primero y no admite matices. El stop se fijo el dia de la compra,
    # cuando no habia dinero en juego y se pensaba con la cabeza fria.
    # Renegociarlo ahora es exactamente lo que hace perder de mas.
    if stop is not None and stop > 0 and precio <= stop:
        return Recomendacion(
            ticker, Veredicto.VENDER, Conviccion.ALTA,
            motivos=[
                f"El precio ({precio:.2f}) ha perforado tu stop ({stop:.2f}).",
                "Ese stop lo fijaste al comprar, cuando no habia dinero en "
                "juego. Moverlo ahora es la forma mas comun de convertir una "
                "perdida acotada en una grande.",
            ],
            desmentiria=[
                "Si el stop estaba mal puesto de origen —demasiado cerca para "
                "la volatilidad de este valor—, el error fue al comprar y lo "
                "que hay que revisar es `atr_stop_mult`, no esta venta.",
            ],
            titulos_a_soltar=titulos,
            aviso_fiscal=aviso_fiscal,
        )

    nivel = diagnostico.nivel if diagnostico else det.Nivel.GRIS
    graves = diagnostico.graves if diagnostico else []

    # POR QUE SE MIRAN EL NIVEL Y LAS SENALES GRAVES POR SEPARADO
    #
    # `deterioration.py` puntua: una senal grave vale 2 y una leve 1, y hacen
    # falta 4 para ROJO. Eso significa que UNA sola senal grave —el margen
    # desplomado, pongamos— se queda en AMBAR.
    #
    # La primera version de este modulo leia solo el nivel, y con ella una
    # empresa cuyo margen se habia hundido salia como un MANTENER blando. Que
    # es exactamente el silencio que `deterioration.py` existe para romper.
    #
    # Asi que el nivel decide la GRAVEDAD y las senales graves deciden si HAY
    # QUE ACTUAR:
    #
    #     ROJO + grave  -> VENDER   varias cosas rotas, una de ellas decisiva
    #     grave sola    -> REDUCIR  algo decisivo ha cambiado; no se ignora
    #     ROJO sin grave-> REDUCIR  muchas cosas leves; ninguna concluyente
    #     AMBAR leve    -> MANTENER vigilando
    #
    # La duda siempre cae del lado de REDUCIR, que fue la decision del usuario.

    # EL PRECIO NO ES LA TESIS, Y PARA EL PRECIO YA ESTA EL STOP
    #
    # Segundo fallo del mismo caso real. Con los fundamentales intactos, una
    # caida del 52 % desde maximos entraba por la regla 3 —una senal grave— y
    # salia como REDUCIR. Pero una caida no dice nada del negocio: dice que el
    # mercado paga menos, que es exactamente lo que el stop de 2,5xATR existe
    # para gestionar, y ese ya se ha comprobado arriba.
    #
    # Reducir ADEMAS por precio es cobrarle dos veces a la misma noticia, y
    # ademas contradice la decision del usuario: vender solo si la tesis se
    # rompe.
    #
    # La excepcion son los instrumentos SIN fundamentales —ETF, indices,
    # cripto—. Ahi el precio es lo unico que hay, y quitarle el voto dejaria
    # esas posiciones sin diagnostico ninguno.
    if (diagnostico is not None and diagnostico.solo_es_precio
            and diagnostico.con_fundamentales):
        peor = max(diagnostico.comparadas, key=lambda s: s.puntos)
        return Recomendacion(
            ticker, Veredicto.MANTENER, Conviccion.BAJA,
            motivos=(
                ["El precio ha ido a peor, pero el negocio no:"]
                + [f"  {s.texto}" for s in diagnostico.comparadas]
                + ["Ninguno de los fundamentales que mirabas al comprar ha "
                   "empeorado. Una caida de precio con el negocio intacto no "
                   "es una tesis rota: para el precio esta tu stop, y no se ha "
                   "perforado."]
            ),
            desmentiria=[
                "Esto deja de valer en cuanto empeore algo del NEGOCIO "
                "—margen, deuda, crecimiento—, o si el precio pierde "
                f"{stop:.2f}." if stop else
                "Esto deja de valer en cuanto empeore algo del negocio.",
                f"La senal mas seria ahora mismo es '{peor.clave}'. Si el "
                "proximo dato de resultados la acompana, cambia el veredicto.",
            ],
        )

    # --- 2. La tesis rota -------------------------------------------------
    if nivel is det.Nivel.ROJO and graves:
        return Recomendacion(
            ticker, Veredicto.VENDER, Conviccion.ALTA,
            motivos=(
                ["Ya no es la empresa que compraste:"]
                + [f"  {s.texto}" for s in graves]
            ),
            desmentiria=[
                "Si el deterioro viene de un trimestre puntual y no de una "
                "tendencia, esto es una venta precipitada. Mira si el mismo "
                "indicador ya se recupero en el ultimo dato.",
            ],
            titulos_a_soltar=titulos,
            aviso_fiscal=aviso_fiscal,
        )

    # --- 3. Una sola senal grave: recortar, no ignorar --------------------
    if graves:
        return Recomendacion(
            ticker, Veredicto.REDUCIR, Conviccion.MEDIA,
            motivos=(
                ["Algo decisivo ha cambiado a peor, todavia sin arrastrar al "
                 "resto:"]
                + [f"  {s.texto}" for s in graves]
            ),
            desmentiria=[
                "Si esta senal viene de un trimestre puntual y el siguiente "
                "dato la corrige, recortar aqui fue precipitado.",
                "Si en el proximo dato aparece una segunda senal grave, esto "
                "pasa a ser una venta.",
            ],
            titulos_a_soltar=(titulos / 2 if titulos else None),
            aviso_fiscal=aviso_fiscal,
        )

    # --- 4. Muchas cosas leves: recortar, no salir ------------------------
    if nivel is det.Nivel.ROJO:
        return Recomendacion(
            ticker, Veredicto.REDUCIR, Conviccion.MEDIA,
            motivos=(
                ["Varias cosas han ido a peor, ninguna decisiva por si sola:"]
                + [f"  {s.texto}" for s in diagnostico.comparadas]
            ),
            desmentiria=[
                "Si estas senales son consecuencia de una caida general del "
                "sector y no de esta empresa, recortar aqui es vender barato "
                "por un motivo que no es suyo.",
            ],
            titulos_a_soltar=(titulos / 2 if titulos else None),
            aviso_fiscal=aviso_fiscal,
        )

    # --- 4. Concentracion: riesgo de cartera, no de empresa ---------------
    # Se dice explicitamente que la empresa no tiene la culpa. Sin esa frase,
    # un REDUCIR se lee como "esto va mal" y la proxima vez se desconfia del
    # valor en vez de la concentracion.
    if peso_pct is not None and peso_pct > lim["max_position_pct"]:
        return Recomendacion(
            ticker, Veredicto.REDUCIR, Conviccion.ALTA,
            motivos=[
                f"Pesa el {peso_pct:.1f} % de tu cartera, por encima del "
                f"{lim['max_position_pct']:.0f} % que te has fijado como tope.",
                "Esto no dice nada malo de la empresa: dice que un solo error "
                "en ella te costaria demasiado.",
            ],
            desmentiria=[
                "Si el tope del "
                f"{lim['max_position_pct']:.0f} % ya no refleja el riesgo que "
                "quieres correr, lo que hay que cambiar es `max_position_pct` "
                "en config/trading.yaml, no esta posicion.",
            ],
            titulos_a_soltar=(
                titulos * (1 - lim["max_position_pct"] / peso_pct)
                if titulos else None
            ),
            aviso_fiscal=aviso_fiscal,
        )

    if peso_sector_pct is not None and peso_sector_pct > lim["max_sector_pct"]:
        return Recomendacion(
            ticker, Veredicto.REDUCIR, Conviccion.MEDIA,
            motivos=[
                f"Su sector pesa el {peso_sector_pct:.1f} % de la cartera, por "
                f"encima de tu tope del {lim['max_sector_pct']:.0f} %.",
                "Varias posiciones del mismo sector se mueven juntas: son "
                "menos apuestas de las que parecen.",
            ],
            desmentiria=[
                "Si el resto de la cartera ya compensa ese sector —por divisa "
                "o por tipo de negocio—, la concentracion es menor de lo que "
                "dice la etiqueta GICS.",
            ],
            titulos_a_soltar=None,
            aviso_fiscal=aviso_fiscal,
        )

    # --- 5. Mantener, con o sin vigilancia --------------------------------
    if nivel is det.Nivel.AMBAR:
        return Recomendacion(
            ticker, Veredicto.MANTENER, Conviccion.BAJA,
            motivos=(
                ["Algo ha cambiado a peor, todavia sin llegar a romper la tesis:"]
                + [f"  {s.texto}" for s in diagnostico.comparadas]
            ),
            desmentiria=[
                "Si en el proximo dato estas senales siguen empeorando, esto "
                "pasa a ser una venta.",
            ],
        )

    if nivel is det.Nivel.GRIS:
        # Dos motivos distintos para el mismo silencio, y conviene separarlos
        # porque lo que tiene que hacer el usuario NO es lo mismo: uno se
        # arregla solo con el tiempo, el otro lo arregla el en dos minutos
        # poniendo la fecha real de compra.
        if diagnostico is not None and diagnostico.espejo:
            motivos = [
                "La fecha de compra que tengo de este valor es la de HOY —casi "
                "seguro la del dia en que importaste la cartera, no la de la "
                "compra real—, asi que compararia hoy contra hoy.",
                "De ahi no sale un diagnostico: sale un verde automatico. "
                "Prefiero no opinar antes que tranquilizarte sin haber mirado "
                "nada.",
            ]
            desmentiria = [
                "Corrige la fecha de compra en Cartera y watchlist y esta "
                "posicion pasa a juzgarse como las demas.",
            ]
        else:
            motivos = [
                "No hay datos del dia en que la compraste con los que comparar, "
                "asi que no se puede saber si ha cambiado a peor.",
                "Decir 'mantener' aqui sonaria a que se ha mirado y esta bien.",
            ]
            desmentiria = [
                "En cuanto haya una foto de fundamentales anterior a tu compra, "
                "esta posicion si se puede juzgar.",
            ]
        if diagnostico is not None and diagnostico.observaciones:
            motivos += ["Lo que si se ve hoy, sin poder decir si ha empeorado:"]
            motivos += [f"  {s.texto}" for s in diagnostico.observaciones]
        return Recomendacion(
            ticker, Veredicto.SIN_OPINION, Conviccion.BAJA,
            motivos=motivos, desmentiria=desmentiria,
        )

    motivos = ["Nada ha cambiado a peor desde que la compraste."]
    conviccion = Conviccion.ALTA
    if percentil is not None and percentil < UMBRAL_YA_NO_DESTACA:
        # NO es una venta. Que haya dejado de destacar en el ranking es
        # informacion, pero convertirla en venta seria rotacion por la puerta
        # de atras, que es justo lo que este modulo no hace.
        motivos.append(
            f"Aun asi ya no destaca en el ranking (percentil "
            f"{percentil:.0%}). No es motivo para vender: es motivo para no "
            "ampliarla."
        )
        conviccion = Conviccion.MEDIA

    return Recomendacion(
        ticker, Veredicto.MANTENER, conviccion,
        motivos=motivos,
        desmentiria=[
            "Esto deja de valer si aparece deterioro en los fundamentales o si "
            f"el precio pierde {stop:.2f}." if stop else
            "Esto deja de valer si aparece deterioro en los fundamentales.",
        ],
    )


# ---------------------------------------------------------------------------
# El lado de compra
# ---------------------------------------------------------------------------
def sobre_un_candidato(
    ticker: str,
    *,
    percentil: float | None,
    cobertura: float | None,
    banderas: list[str] | None = None,
    precio: float | None = None,
    atr14: float | None = None,
    equity: float = 0.0,
    caja: float = 0.0,
    regimen: str = "neutral",
    n_posiciones: int = 0,
    peso_actual_pct: float | None = None,
    peso_sector_pct: float | None = None,
    motivos_ranking: list[str] | None = None,
    aviso_fiscal: str = "",
    tipo_cambio: float = 1.0,
) -> Recomendacion:
    """Que hacer con algo que NO tienes (o que tienes y podrias ampliar).

    Las negativas van primero, y con motivo. Una recomendacion de compra que se
    calcula antes de comprobar si cabe en la cartera acaba en una lista de
    candidatos preciosos que no se pueden comprar.

    `peso_actual_pct` distinto de None significa que ya la tienes: entonces el
    veredicto posible es AMPLIAR y no COMPRAR.

    `tipo_cambio` son cuantas unidades de la divisa del VALOR vale un euro
    (1,17 para un valor en dolares con el EUR/USD a 1,17). `precio` y `atr14`
    llegan en la divisa de cotizacion; `equity` y `caja`, en euros.

    EL FALLO QUE ESTE PARAMETRO ARREGLA

    Antes no existia y se dimensionaba mezclando las dos cosas:
    `(risk_amount_EUR / stop_distance_USD) * price_USD`. El resultado se
    guardaba en `importe_eur` y `riesgo_eur`.

        AAPL a 230 USD, ATR14 4,60 USD, cartera 20.000 EUR, EUR/USD 1,17

            importe   decia 2.400 EUR   ->  eran 2.400 USD = 2.051 EUR
            riesgo    decia   120 EUR   ->  el real era      102,56 EUR

    Un 17 % de sobreestimacion en el importe, y el riesgo real por operacion un
    14,5 % por debajo del 1,5 % configurado. Con divisas de otra escala —yenes,
    coronas— el error es de ordenes de magnitud, no de porcentajes.

    Se dimensiona TODO en euros y el stop se devuelve en la divisa del valor,
    que es la unica en la que sirve: un stop es un precio que se mira en el
    grafico y se teclea en el broker.
    """
    from ..trading.sizing import size_by_atr

    lim = _limites()
    banderas = banderas or []
    ya_la_tienes = peso_actual_pct is not None

    # --- 0. Sin datos suficientes ----------------------------------------
    if percentil is None or cobertura is None or cobertura < MIN_COBERTURA:
        tiene = f"{cobertura:.0%}" if cobertura is not None else "ninguna"
        return Recomendacion(
            ticker, Veredicto.SIN_OPINION, Conviccion.BAJA,
            motivos=[
                f"Cobertura de datos insuficiente ({tiene}; hace falta al menos "
                f"{MIN_COBERTURA:.0%}).",
                "Con menos de la mitad de los factores, el compuesto ordena "
                "ruido: parece un ranking y no lo es.",
            ],
            desmentiria=[
                "En cuanto lleguen los fundamentales que faltan, este valor se "
                "puede puntuar de verdad.",
            ],
        )

    # --- 1. Bandera roja: no se compra, se dice cual ----------------------
    if banderas:
        return Recomendacion(
            ticker, Veredicto.VETADA, Conviccion.ALTA,
            motivos=["No se recomienda comprar por:"] + [f"  {b}" for b in banderas],
            desmentiria=[
                "Una bandera roja puede tener una explicacion buena —un payout "
                "alto por un extraordinario, deuda por una compra que encaja—. "
                "Si la conoces y te convence, el veto es tuyo para levantarlo.",
            ],
        )

    # --- 2. Sin ATR no hay stop, y sin stop no se entra --------------------
    # Es la regla 13 del gestor de riesgo, aqui tambien: entrar sin saber donde
    # sales es la unica forma segura de que una posicion pequena se vuelva
    # grande.
    if atr14 is None or atr14 <= 0 or precio is None or precio <= 0:
        return Recomendacion(
            ticker, Veredicto.VETADA, Conviccion.ALTA,
            motivos=[
                "No hay ATR para este valor, asi que no se puede calcular un "
                "stop.",
                "Sin saber por donde sales, una posicion pequena se convierte "
                "en una grande sin que nadie decida nada.",
            ],
            desmentiria=[
                "Suele significar que la serie de precios tiene huecos o barras "
                "en cuarentena. Con la serie completa, el ATR vuelve.",
            ],
        )

    # --- 3. El liston del ranking -----------------------------------------
    umbral = UMBRAL_AMPLIAR if ya_la_tienes else UMBRAL_COMPRAR
    if percentil < umbral:
        return Recomendacion(
            ticker, Veredicto.MANTENER if ya_la_tienes else Veredicto.SIN_OPINION,
            Conviccion.MEDIA if ya_la_tienes else Conviccion.BAJA,
            motivos=[
                f"Percentil {percentil:.0%}, por debajo del {umbral:.0%} que "
                f"pides para {'ampliar' if ya_la_tienes else 'comprar'}.",
            ],
            desmentiria=[
                "El percentil es RELATIVO al universo descargado. Con un "
                "universo distinto este numero cambia sin que la empresa haya "
                "hecho nada.",
            ],
        )

    # --- 4. La cartera esta llena -----------------------------------------
    if not ya_la_tienes and n_posiciones >= lim["max_positions"]:
        return Recomendacion(
            ticker, Veredicto.VETADA, Conviccion.ALTA,
            motivos=[
                f"Ya tienes {n_posiciones} posiciones y tu tope son "
                f"{lim['max_positions']}.",
                "Para entrar aqui tendrias que soltar otra, y este asesor no "
                "recomienda rotar: cambiar una posicion buena por otra "
                "ligeramente mejor gana decimas en teoria y pierde en "
                "comisiones e impuestos.",
            ],
            desmentiria=[
                "Si alguna de las que tienes esta en VENDER por tesis rota, "
                "esa plaza se libera sola y este candidato vuelve a estar "
                "disponible.",
            ],
        )

    # --- 5. Ampliar solo si de verdad pesa poco ---------------------------
    if ya_la_tienes:
        objetivo = lim["target_position_pct"]
        if peso_actual_pct >= objetivo * (1 - MARGEN_PARA_AMPLIAR):
            return Recomendacion(
                ticker, Veredicto.MANTENER, Conviccion.ALTA,
                motivos=[
                    f"Sigue entre las mejores (percentil {percentil:.0%}) y ya "
                    f"pesa el {peso_actual_pct:.1f} %, cerca de su objetivo del "
                    f"{objetivo:.0f} %.",
                    "Ampliar ahora paga una comision para mover la cartera muy "
                    "poco.",
                ],
                desmentiria=[
                    "Si el objetivo por posicion ya no es el que quieres, se "
                    "cambia en `target_position_pct`.",
                ],
            )

    # --- 6. Sector lleno ---------------------------------------------------
    if peso_sector_pct is not None and peso_sector_pct >= lim["max_sector_pct"]:
        return Recomendacion(
            ticker, Veredicto.VETADA, Conviccion.MEDIA,
            motivos=[
                f"Su sector ya pesa el {peso_sector_pct:.1f} % de tu cartera, "
                f"en tu tope del {lim['max_sector_pct']:.0f} %.",
                "Anadir aqui concentra en vez de diversificar.",
            ],
            desmentiria=[
                "Si crees que ese sector merece mas peso, el cambio va en "
                "`max_sector_pct` y afecta a toda la cartera, no solo a este "
                "valor.",
            ],
        )

    # --- 7. El tamano, que es la mitad de la decision ---------------------
    # A euros ANTES de dimensionar. Un tipo que no sea un numero positivo se
    # trata como "no se sabe" y se deja en 1,0: es lo que ya pasaba, y romper
    # el consejo por no tener un tipo de cambio seria peor. Para EUR es 1,0 de
    # verdad y esta rama no cambia nada.
    cambio = float(tipo_cambio) if tipo_cambio and tipo_cambio > 0 else 1.0
    tam = size_by_atr(
        equity=equity, price=precio / cambio, atr14=atr14 / cambio,
        cash_available=caja,
        regime=regimen,
        risk_per_trade_pct=lim["risk_per_trade_pct"],
        atr_stop_mult=lim["atr_stop_mult"],
        max_position_pct=lim["max_position_pct"],
        target_position_pct=lim["target_position_pct"],
        min_cash_pct=lim["min_cash_pct"],
        min_notional=lim["min_notional"],
    )
    if not tam.ok:
        return Recomendacion(
            ticker, Veredicto.VETADA, Conviccion.ALTA,
            motivos=[_POR_QUE_NO_CABE.get(
                tam.reason_code,
                f"No se puede dimensionar la compra ({tam.reason_code}).")],
            desmentiria=[
                "Esto no dice nada de la empresa: dice que no cabe en tu "
                "cartera de hoy con tus reglas de hoy.",
            ],
        )

    # El stop vuelve a la divisa del valor: es un precio que se mira en el
    # grafico y se teclea en el broker, y en euros no serviria para nada.
    stop_local = tam.stop_price * cambio

    motivos = [f"Percentil {percentil:.0%} del universo puntuado."]
    motivos += motivos_ranking or []
    motivos.append(
        f"Arriesgas {tam.risk_amount:.2f} EUR hasta el stop de "
        f"{stop_local:.2f}."
    )
    if tam.capped_by:
        motivos.append(f"El tamano lo limita: {tam.capped_by.replace('_', ' ')}.")

    return Recomendacion(
        ticker,
        Veredicto.AMPLIAR if ya_la_tienes else Veredicto.COMPRAR,
        _conviccion_de_compra(percentil, cobertura),
        motivos=motivos,
        desmentiria=[
            f"Si el precio cierra por debajo de {stop_local:.2f}, esta "
            f"decision fue un error y se sale.",
            "Si el percentil cae por debajo del "
            f"{UMBRAL_YA_NO_DESTACA:.0%}, deja de ser una de las mejores.",
            "El percentil es relativo al universo descargado: con otro "
            "universo, este numero cambia sin que la empresa haga nada.",
        ],
        importe_eur=tam.notional,
        titulos=tam.qty,
        stop=stop_local,
        riesgo_eur=tam.risk_amount,
        limitado_por=tam.capped_by,
        aviso_fiscal=aviso_fiscal,
    )


_POR_QUE_NO_CABE = {
    "NO_SIZING_INPUTS": "Faltan datos para calcular el tamano (precio, ATR o "
                        "el valor de tu cartera).",
    "STOP_BELOW_ZERO": "El ATR es tan grande frente al precio que el stop "
                       "saldria por debajo de cero: esta posicion no se puede "
                       "proteger.",
    "POSITION_TOO_SMALL_FOR_RISK": "El minimo del broker no cabe dentro de tu "
                                   "tope por activo. Comprar aqui obligaria a "
                                   "saltarse un limite.",
    "MIN_NOTIONAL_ABOVE_CASH": "No queda efectivo suficiente para el minimo "
                               "del broker sin tocar la reserva de caja.",
}


def _conviccion_de_compra(percentil: float, cobertura: float) -> Conviccion:
    """Cuanta confianza merece esta compra.

    Solo dos entradas, y las dos son honestas: lo alto que esta en el ranking y
    cuantos datos sostienen ese puesto. Un percentil 98 calculado con la mitad
    de los factores no es una conviccion alta, es un numero bonito.
    """
    if percentil >= 0.95 and cobertura >= 0.80:
        return Conviccion.ALTA
    if percentil >= 0.90 and cobertura >= 0.65:
        return Conviccion.MEDIA
    return Conviccion.BAJA


def ordenar(recomendaciones: list[Recomendacion]) -> list[Recomendacion]:
    """Lo urgente primero, y lo urgente es lo que ya tienes.

    Una venta por tesis rota puede costarte dinero hoy; una compra puede
    esperar a manana. Poner las compras arriba —que es lo que apetece leer— es
    como se acaba con una cartera llena de aciertos viejos sin vender.
    """
    orden = {
        Veredicto.VENDER: 0, Veredicto.REDUCIR: 1, Veredicto.COMPRAR: 2,
        Veredicto.AMPLIAR: 3, Veredicto.MANTENER: 4, Veredicto.VETADA: 5,
        Veredicto.SIN_OPINION: 6,
    }
    fuerza = {Conviccion.ALTA: 0, Conviccion.MEDIA: 1, Conviccion.BAJA: 2}
    return sorted(recomendaciones,
                  key=lambda r: (orden[r.veredicto], fuerza[r.conviccion],
                                 r.ticker))

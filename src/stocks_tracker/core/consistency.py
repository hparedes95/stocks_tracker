"""Contrastar los fundamentales en vez de creerselos.

Los ratios llegan de un unico proveedor gratuito, y un proveedor gratuito se
equivoca: un PER de 3 en una empresa cara, un margen del 900 %, una
capitalizacion de otra empresa por un ticker mal cruzado. Ninguno de esos
errores da un fallo: entran en el ranking, suben al valor a los primeros
puestos y ahi se quedan, con la misma pinta que los datos buenos.

Tres formas de contrastar, en orden de lo independiente que es cada una:

1. **Contra nuestros propios precios.** La capitalizacion tiene que cuadrar con
   el precio por las acciones en circulacion, y la beta que declaran tiene que
   parecerse a la que sale de calcularla con las cotizaciones. Es el unico
   contraste de verdad independiente que se puede hacer sin pagar otra fuente.

2. **Contra si mismos.** Los ratios de una misma foto tienen que cumplir
   identidades contables: el margen neto no puede superar al bruto, el PER y el
   earnings yield son uno el inverso del otro, el ROE de una empresa con deuda
   no puede quedar por debajo del ROA.

3. **Contra el pasado.** Un ratio que se multiplica por diez de un dia para
   otro casi nunca es la empresa: es el dato. Se compara con las fotos
   anteriores, que existen desde que se guarda el historico punto-en-el-tiempo.

Lo que NO hace: inventarse el valor bueno. Cuando dos cosas se contradicen no
se sabe cual es la equivocada, y elegir una seria peor que avisar de las dos.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Umbrales
# ---------------------------------------------------------------------------
# Anchos a proposito. El objetivo es cazar el dato ROTO, no discutir el
# redondeo: los proveedores calculan sobre periodos ligeramente distintos y una
# tolerancia fina llenaria la pantalla de avisos que no son de nadie.

TOLERANCIA_IDENTIDAD = 0.25       # 25 % de desviacion entre dos formas de lo mismo
TOLERANCIA_CAPITALIZACION = 0.20  # precio x acciones frente a lo que declaran
TOLERANCIA_BETA = 0.60            # la beta declarada frente a la calculada
DISCREPANCIA_BETA = 0.50          # ...y ademas la mitad de diferencia relativa
SALTO_SOSPECHOSO = 3.0            # multiplicarse por tres de una foto a otra

# El dividendo declarado suele mirar a los proximos doce meses y el payout a los
# doce anteriores, asi que la identidad `dividendo = payout x earnings yield`
# nunca cuadra fina. Solo se avisa cuando no cuadra del todo.
TOLERANCIA_DIVIDENDO = 0.75

# Sesiones minimas para que una beta signifique algo. Con menos, el numero sale
# igual pero es ruido, y compararlo con el declarado produce avisos falsos en
# cadena justo en los valores recien anadidos.
MIN_SESIONES_BETA = 60

# Rangos fuera de los cuales el dato no describe ninguna empresa real.
IMPOSIBLES = {
    "profit_margin": (-10.0, 1.0),          # ganar mas del 100 % de lo que vendes
    "gross_margin": (-10.0, 1.0),
    "operating_margin": (-10.0, 1.0),
    "payout_ratio": (0.0, 10.0),            # repartir 10 veces lo que ganas
    "dividend_yield": (0.0, 0.50),          # un 50 % suele ser precio hundido
    "trailing_pe": (0.0, 5000.0),
    "price_to_book": (0.0, 500.0),
    "roe": (-50.0, 50.0),
    "beta": (-5.0, 10.0),
}


class Gravedad(StrEnum):
    ROTO = "roto"          # el dato no puede ser cierto
    DUDOSO = "dudoso"      # dos datos no cuadran, sin saber cual falla


@dataclass(frozen=True)
class Aviso:
    campo: str
    gravedad: Gravedad
    texto: str


@dataclass(frozen=True)
class Revision:
    ticker: str
    avisos: list[Aviso] = field(default_factory=list)

    @property
    def rotos(self) -> list[Aviso]:
        return [a for a in self.avisos if a.gravedad is Gravedad.ROTO]

    @property
    def fiable(self) -> bool:
        """Sin ningun aviso. No garantiza que el dato sea correcto: garantiza
        que no se ha encontrado nada que lo contradiga, que es otra cosa."""
        return not self.avisos

    @property
    def campos_sospechosos(self) -> set[str]:
        return {a.campo for a in self.avisos}


def _num(origen: Any, campo: str) -> float | None:
    if origen is None:
        return None
    try:
        valor = origen.get(campo)
    except AttributeError:
        return None
    if valor is None:
        return None
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _discrepancia(a: float, b: float) -> float:
    """Diferencia relativa entre dos formas de calcular lo mismo.

    Se divide por el mayor en valor absoluto y no por uno de los dos: asi el
    resultado no cambia segun cual se ponga primero, que con datos de dos
    origenes distintos es una fuente de sustos tonta.

    Ojo con los umbrales que se comparan contra esto: para dos numeros del
    mismo signo el resultado SIEMPRE es menor que 1, asi que un umbral de 1,0
    deja la comprobacion muerta sin que nada falle. Un 0,75 significa "el menor
    no llega ni a la cuarta parte del mayor".
    """
    escala = max(abs(a), abs(b))
    return abs(a - b) / escala if escala > 0 else 0.0


# ---------------------------------------------------------------------------
# 1. Contra nuestros propios precios
# ---------------------------------------------------------------------------
def _contra_precios(f: Any, precio: float | None,
                    beta_calculada: float | None) -> list[Aviso]:
    fuera: list[Aviso] = []

    capitaliza = _num(f, "market_cap")
    acciones = _num(f, "shares_outstanding")
    if capitaliza and acciones and precio and acciones > 0 and precio > 0:
        propia = precio * acciones
        desvio = _discrepancia(propia, capitaliza)
        if desvio > TOLERANCIA_CAPITALIZACION:
            fuera.append(Aviso(
                "market_cap", Gravedad.DUDOSO,
                f"La capitalizacion que declaran ({capitaliza:,.0f}) no cuadra "
                f"con el precio por las acciones en circulacion "
                f"({propia:,.0f}): un {desvio:.0%} de diferencia. Suele "
                "significar que el numero de acciones esta desactualizado o "
                "que el ticker esta cruzado con otra empresa.",
            ))

    declarada = _num(f, "beta")
    if declarada is not None and beta_calculada is not None:
        # Las dos condiciones a la vez y no una sola. Yahoo publica la beta a
        # cinco anos con datos mensuales y aqui se calcula con un ano de datos
        # diarios: son dos numeros distintos por construccion, y solo con la
        # diferencia absoluta saltaba en tres de cada cuatro valores. Con las
        # dos, 1,20 frente a 1,90 no avisa —difieren, pero es el metodo— y 0,30
        # frente a 1,80 si.
        if (abs(declarada - beta_calculada) > TOLERANCIA_BETA
                and _discrepancia(declarada, beta_calculada) > DISCREPANCIA_BETA):
            fuera.append(Aviso(
                "beta", Gravedad.DUDOSO,
                f"La beta que declaran es {declarada:.2f} y la que sale de "
                f"nuestras cotizaciones es {beta_calculada:.2f}. Se calculan "
                "sobre periodos distintos, asi que una diferencia pequena es "
                "normal; esta no lo es.",
            ))

    return fuera


# ---------------------------------------------------------------------------
# 2. Contra si mismos
# ---------------------------------------------------------------------------
def _contra_si_mismos(f: Any) -> list[Aviso]:
    fuera: list[Aviso] = []

    for campo, (minimo, maximo) in IMPOSIBLES.items():
        valor = _num(f, campo)
        if valor is not None and not (minimo <= valor <= maximo):
            fuera.append(Aviso(
                campo, Gravedad.ROTO,
                f"`{campo}` vale {valor:,.2f}, fuera de lo que puede valer una "
                f"empresa real ({minimo:g} a {maximo:g}). No es un dato "
                "extremo: es un dato roto.",
            ))

    per = _num(f, "trailing_pe")
    rendimiento = _num(f, "earnings_yield")
    if per and rendimiento and per > 0 and rendimiento != 0:
        esperado = 1.0 / per
        if _discrepancia(esperado, rendimiento) > TOLERANCIA_IDENTIDAD:
            fuera.append(Aviso(
                "trailing_pe", Gravedad.DUDOSO,
                f"El PER ({per:.1f}) y el earnings yield ({rendimiento:.2%}) "
                "son uno el inverso del otro y aqui no lo son. Uno de los dos "
                "esta mal y no se puede saber cual.",
            ))

    bruto = _num(f, "gross_margin")
    operativo = _num(f, "operating_margin")
    neto = _num(f, "profit_margin")
    if bruto is not None and neto is not None and neto > bruto:
        fuera.append(Aviso(
            "profit_margin", Gravedad.ROTO,
            f"El margen neto ({neto:.1%}) es mayor que el bruto ({bruto:.1%}). "
            "Solo pasa con extraordinarios que no se repiten, y casi siempre "
            "es un error de la fuente.",
        ))
    if bruto is not None and operativo is not None and operativo > bruto:
        fuera.append(Aviso(
            "operating_margin", Gravedad.ROTO,
            f"El margen operativo ({operativo:.1%}) supera al bruto "
            f"({bruto:.1%}), que es imposible por definicion.",
        ))

    roe = _num(f, "roe")
    roa = _num(f, "roa")
    deuda = _num(f, "debt_to_equity")
    if roe is not None and roa is not None and deuda and deuda > 0.5 and roe < roa:
        fuera.append(Aviso(
            "roe", Gravedad.DUDOSO,
            f"El ROE ({roe:.1%}) queda por debajo del ROA ({roa:.1%}) en una "
            "empresa endeudada, cuando la deuda deberia hacerlo mayor.",
        ))

    reparto = _num(f, "payout_ratio")
    dividendo = _num(f, "dividend_yield")
    if reparto and dividendo and rendimiento and rendimiento > 0:
        esperado = reparto * rendimiento
        if esperado > 0 and _discrepancia(esperado, dividendo) > TOLERANCIA_DIVIDENDO:
            fuera.append(Aviso(
                "dividend_yield", Gravedad.DUDOSO,
                f"La rentabilidad por dividendo ({dividendo:.2%}) no cuadra con "
                f"repartir el {reparto:.0%} de un beneficio del "
                f"{rendimiento:.2%} (saldria {esperado:.2%}).",
            ))

    return fuera


# ---------------------------------------------------------------------------
# 3. Contra el pasado
# ---------------------------------------------------------------------------
# Campos ESTABLES: los que no deberian moverse mucho de una descarga a la
# siguiente, asi que un salto grande apunta al dato y no a la empresa.
#
# La lista es blanca y no negra a proposito. Con una lista negra —comparar todo
# menos unos cuantos— cada campo nuevo del proveedor entraria a compararse sin
# que nadie hubiera decidido si tiene sentido, y los que se mueven de verdad
# (crecimiento de ingresos, de beneficios, rentabilidades de flujo de caja)
# llenarian la pantalla de avisos que no son de nadie. Un aviso que sale
# siempre entrena a ignorar los avisos.
ESTABLES = ("trailing_pe", "price_to_book", "profit_margin", "roe",
            "market_cap", "shares_outstanding", "dividend_yield")


def _contra_el_pasado(f: Any, anterior: Any) -> list[Aviso]:
    if anterior is None:
        return []
    fuera: list[Aviso] = []
    for campo in ESTABLES:
        ahora, antes = _num(f, campo), _num(anterior, campo)
        if ahora is None or antes is None or antes == 0:
            continue
        # En valor absoluto y en las dos direcciones: dividirse por diez es tan
        # sospechoso como multiplicarse por diez.
        proporcion = abs(ahora) / abs(antes)
        if proporcion > SALTO_SOSPECHOSO or proporcion < 1 / SALTO_SOSPECHOSO:
            fuera.append(Aviso(
                campo, Gravedad.DUDOSO,
                f"`{campo}` ha pasado de {antes:,.2f} a {ahora:,.2f} de una "
                "descarga a la siguiente. Un cambio asi de un dia para otro "
                "casi nunca es la empresa: es el dato.",
            ))
    return fuera


def revisar(ticker: str, fundamentales: Any, *, precio: float | None = None,
            beta_calculada: float | None = None,
            anterior: Any = None) -> Revision:
    """Todo lo que contradice a los fundamentales de un valor.

    Sin fundamentales no hay revision y NO se devuelve "fiable": no haber
    encontrado nada porque no habia nada que mirar no es lo mismo que no haber
    encontrado nada.
    """
    if fundamentales is None:
        return Revision(ticker=ticker, avisos=[
            Aviso("", Gravedad.DUDOSO, "Sin fundamentales que revisar.")])

    return Revision(ticker=ticker, avisos=[
        *_contra_precios(fundamentales, precio, beta_calculada),
        *_contra_si_mismos(fundamentales),
        *_contra_el_pasado(fundamentales, anterior),
    ])


def beta_desde_precios(retornos_valor, retornos_mercado) -> float | None:
    """Beta calculada con nuestras propias cotizaciones.

    Es el unico contraste verdaderamente independiente que se puede hacer sin
    pagar una segunda fuente: el proveedor dice un numero y nosotros lo
    calculamos por nuestra cuenta con datos que no vienen de el.
    """
    import numpy as np

    x = np.asarray(retornos_mercado, dtype=float)
    y = np.asarray(retornos_valor, dtype=float)
    if x.size != y.size:
        return None
    bueno = np.isfinite(x) & np.isfinite(y)
    x, y = x[bueno], y[bueno]
    # El minimo se comprueba DESPUES de tirar los huecos y no antes: 300
    # sesiones de las que 280 estan vacias son 20 sesiones, y con 20 la beta es
    # ruido. Comprobarlo dos veces —antes y despues— dejaba la primera
    # comprobacion muerta, y con ella un test que parecia cubrirla.
    if x.size < MIN_SESIONES_BETA:
        return None
    # `ddof=1` para que case con `np.cov`, que lo usa por defecto. Mezclarlos
    # mete un factor n/(n-1): un sesgo pequeno y sistematico, del tipo que no
    # se nota nunca porque el numero sigue pareciendo razonable.
    varianza = float(np.var(x, ddof=1))
    if varianza <= 0:
        return None
    return float(np.cov(y, x)[0, 1] / varianza)

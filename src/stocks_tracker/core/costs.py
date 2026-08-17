"""Lo que separa la rentabilidad de pantalla de los euros que llegan a tu cuenta.

Tres capas, y conviene no mezclarlas porque se pagan en momentos distintos:

1. **Al comprar y al vender**: comision del broker y cambio de divisa. Se pagan
   en el momento, salgan las cosas bien o mal.
2. **Cada dividendo**: retencion en el pais de la empresa. Parte se recupera en
   la declaracion del ano siguiente, parte no se recupera nunca.
3. **Al vender con ganancia**: IRPF sobre la plusvalia, por tramos.

**Esto no es asesoramiento fiscal.** Son las reglas generales de un residente en
Espana que invierte por cuenta propia, y sirven para COMPARAR alternativas antes
de comprar. Los tipos cambian, tu situacion puede tener minusvalias pendientes
de otros ejercicios, y la declaracion la hace Hacienda con sus datos.

Por que existe este modulo: un valor estadounidense con un 3 % de dividendo no
renta un 3 %. Renta un 2,55 % despues de la retencion en origen, y menos aun
despues del IRPF. Comparar ese 3 % con el 3 % de un valor britanico —que no
retiene nada— es comparar dos cosas distintas creyendo que son la misma.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

from .config import _load_yaml
from .textutils import as_float


@lru_cache(maxsize=1)
def get_costs_config() -> dict[str, Any]:
    return _load_yaml("costs.yaml")


def _broker() -> dict:
    return dict(get_costs_config().get("broker") or {})


# ---------------------------------------------------------------------------
# 1. Comprar y vender
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CosteOperacion:
    """Lo que cuesta ejecutar una operacion, en euros."""

    importe: float
    comision: float
    cambio_divisa: float
    canon: float
    moneda: str = "EUR"

    @property
    def total(self) -> float:
        return self.comision + self.cambio_divisa + self.canon

    @property
    def pct(self) -> float:
        """Coste sobre el importe. Es el numero que se compara entre opciones."""
        return (self.total / self.importe * 100.0) if self.importe > 0 else 0.0

    @property
    def ida_y_vuelta_pct(self) -> float:
        """Comprar Y vender. Es lo que de verdad tienes que recuperar.

        Mirar solo la compra hace parecer barata una operacion que cuesta el
        doble: nadie compra para no vender nunca.
        """
        return self.pct * 2.0


def coste_operacion(importe_eur: float, moneda: str = "EUR") -> CosteOperacion:
    """Comision, cambio de divisa y canon de una operacion.

    El cambio de divisa se aplica sobre el IMPORTE ENTERO, no sobre el
    beneficio, y solo si la moneda no es la de la cuenta. Es el coste que mas
    sorprende: en una compra de 1.000 EUR en dolares al 0,25 % son 2,50 EUR de
    ida y otros 2,50 de vuelta, mas que muchas comisiones.
    """
    cfg = _broker()
    importe = max(0.0, float(importe_eur))

    porcentual = importe * as_float(cfg.get("comision_pct")) / 100.0
    comision = max(
        as_float(cfg.get("comision_fija_eur")),
        porcentual,
        as_float(cfg.get("comision_minima_eur")),
    ) if importe > 0 else 0.0

    divisa = (
        importe * as_float(cfg.get("cambio_divisa_pct")) / 100.0
        if moneda.upper() != "EUR" else 0.0
    )
    canon = importe * as_float(cfg.get("canon_pct")) / 100.0

    return CosteOperacion(importe=importe, comision=comision,
                          cambio_divisa=divisa, canon=canon, moneda=moneda)


# ---------------------------------------------------------------------------
# 2. Dividendos
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Dividendo:
    bruto_pct: float
    pais: str
    retencion_pct: float
    recuperable_pct: float

    @property
    def perdido_pct(self) -> float:
        """Lo retenido que NO se recupera ni en la declaracion.

        Es dinero que no vuelve: para recuperarlo hay que reclamar al pais de
        origen, un tramite que casi nadie hace y que en cantidades pequenas
        cuesta mas de lo que devuelve.
        """
        return max(0.0, self.retencion_pct - self.recuperable_pct)

    @property
    def neto_inmediato_pct(self) -> float:
        """Lo que llega a tu cuenta el dia del pago, antes del IRPF."""
        return self.bruto_pct * (1.0 - self.retencion_pct / 100.0)

    @property
    def neto_tras_declaracion_pct(self) -> float:
        """Contando la deduccion por doble imposicion, antes del IRPF espanol."""
        return self.bruto_pct * (1.0 - self.perdido_pct / 100.0)


def dividendo_neto(bruto_pct: float, pais: str = "desconocido",
                   con_w8ben: bool = True) -> Dividendo:
    """Que queda de un dividendo despues de la retencion en origen.

    `con_w8ben` solo cambia EE. UU., y cambia mucho: 15 % frente a 30 %. El
    formulario lo suele pedir el broker al abrir la cuenta y se renueva cada
    tres anos; si caduca, la retencion sube al 30 sin avisar.
    """
    cfg = get_costs_config()
    clave = (pais or "desconocido").upper()
    retenciones = cfg.get("retencion_origen_pct") or {}
    limites = cfg.get("limite_convenio_pct") or {}

    if clave == "US" and not con_w8ben:
        retencion = as_float(retenciones.get("US_sin_w8ben"), 30.0)
    else:
        retencion = as_float(
            retenciones.get(clave, retenciones.get("desconocido")), 15.0
        )

    limite = as_float(limites.get(clave, limites.get("desconocido")), 15.0)
    # No se puede deducir mas de lo retenido, aunque el convenio permita mas.
    recuperable = min(retencion, limite)

    return Dividendo(bruto_pct=float(bruto_pct), pais=clave,
                     retencion_pct=retencion, recuperable_pct=recuperable)


# ---------------------------------------------------------------------------
# 3. IRPF sobre la plusvalia
# ---------------------------------------------------------------------------
def impuesto_plusvalia(ganancia_eur: float,
                       ganancias_previas_eur: float = 0.0) -> float:
    """IRPF de la base del ahorro, por tramos y de forma acumulativa.

    `ganancias_previas_eur` son las que ya tienes en el mismo ejercicio: los
    tramos se aplican al TOTAL del ano, no a cada venta por separado. Sin
    tenerlas en cuenta, cada venta se calcularia desde el 19 % y el resultado
    saldria bajo justo cuando mas importa.
    """
    if ganancia_eur <= 0:
        return 0.0

    tramos = get_costs_config().get("irpf_base_ahorro") or []
    previas = max(0.0, float(ganancias_previas_eur))
    total = previas + float(ganancia_eur)

    def cuota(base: float) -> float:
        pagado, anterior = 0.0, 0.0
        for tramo in tramos:
            techo = tramo.get("hasta")
            limite = float(techo) if techo is not None else float("inf")
            if base <= anterior:
                break
            gravado = min(base, limite) - anterior
            pagado += gravado * as_float(tramo.get("tipo")) / 100.0
            anterior = limite
        return pagado

    # La diferencia entre la cuota del total y la de lo que ya habia: es lo que
    # esta venta anade de verdad.
    return cuota(total) - cuota(previas)


def tipo_efectivo_pct(ganancia_eur: float,
                      ganancias_previas_eur: float = 0.0) -> float:
    if ganancia_eur <= 0:
        return 0.0
    return impuesto_plusvalia(ganancia_eur, ganancias_previas_eur) / ganancia_eur * 100.0


# ---------------------------------------------------------------------------
# 4. La regla de los dos meses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AvisoDosMeses:
    """Una recompra que dejaria una perdida sin poder compensar."""

    ticker: str
    vendido_el: date
    perdida_eur: float
    dias_desde_venta: int
    dias_que_faltan: int

    @property
    def libre_el(self) -> date:
        """El primer dia en que recomprar ya no arrastra la perdida.

        Un dia MAS que la ventana. El articulo habla de los dos meses
        anteriores o posteriores, asi que el dia 60 todavia esta dentro:
        diciendo que el 60 ya se puede, el aviso salia el mismo dia en que se
        anunciaba que dejaba de aplicar, contradiciendose solo.
        """
        return self.vendido_el + timedelta(days=self.dias_desde_venta
                                           + self.dias_que_faltan + 1)


@dataclass(frozen=True)
class ReglaDosMeses:
    avisos: list[AvisoDosMeses] = field(default_factory=list)

    @property
    def bloquea(self) -> bool:
        return bool(self.avisos)


def comprobar_dos_meses(ticker: str, ventas_con_perdida: list[dict],
                        hoy: date | None = None,
                        cotizado: bool = True) -> ReglaDosMeses:
    """Si recomprar hoy dejaria una perdida reciente sin poder compensar.

    Art. 33.5 LIRPF: una perdida por transmision de valores no se computa si
    se compran valores homogeneos dentro de los dos meses anteriores o
    posteriores. La perdida no se pierde —queda aplazada hasta que vendas lo
    nuevo— pero no sirve para compensar este ejercicio, que suele ser justo
    para lo que se vendio.

    Es el error mas facil de cometer sin enterarse: vendes en perdidas para
    hacer caja fiscal y recompras a los diez dias porque el valor te sigue
    gustando.

    `ventas_con_perdida` son diccionarios con `closed_at` y `perdida_eur`.
    """
    cfg = get_costs_config().get("regla_dos_meses") or {}
    if not cfg.get("activa", True):
        return ReglaDosMeses()

    ventana = int(cfg.get("dias_cotizados" if cotizado else "dias_no_cotizados",
                          60 if cotizado else 365))
    ahora = hoy or date.today()

    avisos = []
    for venta in ventas_con_perdida:
        cerrada = venta.get("closed_at")
        if cerrada is None:
            continue
        if hasattr(cerrada, "date"):
            cerrada = cerrada.date()
        dias = (ahora - cerrada).days
        # Solo miran hacia atras las ventas ya hechas; las compras posteriores
        # a la venta las cubre esta misma comprobacion cuando llegue el dia.
        if 0 <= dias <= ventana:
            avisos.append(AvisoDosMeses(
                ticker=ticker, vendido_el=cerrada,
                perdida_eur=abs(as_float(venta.get("perdida_eur"))),
                dias_desde_venta=dias, dias_que_faltan=ventana - dias,
            ))
    return ReglaDosMeses(avisos=avisos)


# ---------------------------------------------------------------------------
# Resumen para la pantalla
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CosteTotal:
    operacion: CosteOperacion
    dividendo: Dividendo | None
    dos_meses: ReglaDosMeses

    @property
    def cuanto_tiene_que_subir_pct(self) -> float:
        """Cuanto tiene que subir el valor solo para no perder dinero.

        Es el numero mas util de todo esto: convierte los costes en el listón
        que hay que superar. Con comisiones de ida y vuelta del 0,5 %, una
        operacion que sube un 0,4 % pierde dinero aunque la pantalla la pinte
        en verde.
        """
        return self.operacion.ida_y_vuelta_pct


def resumen(importe_eur: float, moneda: str = "EUR",
            dividendo_bruto_pct: float = 0.0, pais: str = "desconocido",
            con_w8ben: bool = True,
            ventas_con_perdida: list[dict] | None = None,
            ticker: str = "", hoy: date | None = None) -> CosteTotal:
    """Todo lo anterior junto, para una compra concreta."""
    return CosteTotal(
        operacion=coste_operacion(importe_eur, moneda),
        dividendo=(dividendo_neto(dividendo_bruto_pct, pais, con_w8ben)
                   if dividendo_bruto_pct > 0 else None),
        dos_meses=comprobar_dos_meses(ticker, ventas_con_perdida or [], hoy),
    )

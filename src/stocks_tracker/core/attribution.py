"""Cuanto de lo que has ganado es merito tuyo y cuanto es la marea.

Una cartera que gana un 18 % en un ano en el que el mercado gano un 20 % ha
perdido dinero contra la alternativa de no hacer nada, y la pantalla la pinta
en verde igual. Ese es el numero que hace repetir lo que no funciona: sin
separar la marea del merito, cada decision parece buena en un mercado alcista y
mala en uno bajista, se acierte o no.

La descomposicion es exacta y no un modelo:

    tu retorno = mercado + (sector - mercado) + (tu valor - sector)
                 ^^^^^^^   ^^^^^^^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^^^
                 la marea   elegir el sector     elegir el valor

Los tres sumados dan exactamente lo que has ganado, asi que no hay un resto
donde esconder nada. Cada posicion se compara con lo que hicieron el indice y
su sector EN SU PROPIA VENTANA, desde el dia que la compraste: comparar una
compra de hace un mes con el ano entero del indice no compara nada.

Lo que esto NO es:

- No es tu resultado fiscal ni el de tu broker. Ignora dividendos, comisiones y
  cambio de divisa. Para eso esta "Lo que cuesta".
- No es una rentabilidad anualizada ni comparable con la de un fondo. Cada
  posicion lleva su propio tiempo dentro, y el agregado es una media ponderada
  de periodos distintos.
- No mide habilidad con pocas posiciones. Batir al sector en cuatro valores
  durante seis meses es lo que sale a cara o cruz mas de una vez de cada diez.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Por debajo de esto, el agregado es ruido con forma de conclusion. No se
# oculta el numero —esconder datos tampoco ayuda— pero se dice al lado.
MIN_POSICIONES = 8
MIN_DIAS = 180


@dataclass(frozen=True)
class Posicion:
    """Una posicion y lo que hicieron su indice y su sector mientras la tenias.

    `retorno_sector` puede faltar: hay valores sin sector asignado y sectores
    sin ETF de referencia. Cuando falta no se inventa —seria repartir a ojo lo
    que se quiere medir— y el efecto sector y el de seleccion se quedan juntos.
    """

    ticker: str
    coste: float                       # capital comprometido: es el peso
    retorno: float                     # el tuyo, desde el dia de la compra
    retorno_mercado: float
    retorno_sector: float | None = None
    dias: int = 0
    sector: str = ""

    @property
    def efecto_sector(self) -> float:
        """Lo que aporto elegir ESE sector en vez del mercado entero."""
        if self.retorno_sector is None:
            return 0.0
        return self.retorno_sector - self.retorno_mercado

    @property
    def efecto_seleccion(self) -> float:
        """Lo que aporto elegir ESE valor dentro de su sector.

        Sin sector de referencia, aqui cae todo lo que no es mercado: es una
        mezcla de las dos cosas y quien lo ensena tiene que decirlo.
        """
        referencia = (self.retorno_sector if self.retorno_sector is not None
                      else self.retorno_mercado)
        return self.retorno - referencia

    @property
    def bate_a_su_sector(self) -> bool | None:
        if self.retorno_sector is None:
            return None
        return self.retorno > self.retorno_sector

    @property
    def cuadra(self) -> bool:
        """La identidad de la descomposicion, para poder comprobarla.

        Si algun dia deja de cumplirse, es que hay un efecto contandose dos
        veces o uno que falta, y los numeros pareceran igual de convincentes.
        """
        suma = self.retorno_mercado + self.efecto_sector + self.efecto_seleccion
        return math.isclose(suma, self.retorno, rel_tol=1e-9, abs_tol=1e-12)


@dataclass(frozen=True)
class Resumen:
    posiciones: list[Posicion]

    @property
    def peso_total(self) -> float:
        return sum(p.coste for p in self.posiciones)

    def _pond(self, atributo: str) -> float:
        """Media ponderada por capital comprometido.

        Ponderar por capital y no a partes iguales porque una posicion de 5.000
        EUR y otra de 200 no pesan lo mismo en lo que ganas, aunque en un
        recuento de aciertos cuenten una y una.
        """
        total = self.peso_total
        if total <= 0:
            return 0.0
        return sum(getattr(p, atributo) * p.coste for p in self.posiciones) / total

    @property
    def retorno(self) -> float:
        return self._pond("retorno")

    @property
    def mercado(self) -> float:
        return self._pond("retorno_mercado")

    @property
    def efecto_sector(self) -> float:
        return self._pond("efecto_sector")

    @property
    def efecto_seleccion(self) -> float:
        return self._pond("efecto_seleccion")

    @property
    def contra_el_mercado(self) -> float:
        """Lo unico que de verdad se puede llamar tuyo."""
        return self.retorno - self.mercado

    @property
    def dias_mediana(self) -> int:
        if not self.posiciones:
            return 0
        dias = sorted(p.dias for p in self.posiciones)
        return dias[len(dias) // 2]

    @property
    def con_sector(self) -> list[Posicion]:
        return [p for p in self.posiciones if p.retorno_sector is not None]

    @property
    def aciertos(self) -> int:
        return sum(1 for p in self.con_sector if p.bate_a_su_sector)

    @property
    def comparables(self) -> int:
        return len(self.con_sector)

    @property
    def probabilidad_por_azar(self) -> float | None:
        """Que probabilidad habria de acertar tanto tirando una moneda.

        No es un contraste estadistico y no se debe leer como tal: las
        posiciones no son independientes —se solapan en el tiempo y comparten
        mercado— y batir al sector no es exactamente una moneda al aire. Es una
        cota inferior de humildad: si sale 0,34, no hay nada que explicar.
        """
        n = self.comparables
        if n == 0:
            return None
        k = self.aciertos
        cola = sum(math.comb(n, i) for i in range(k, n + 1))
        return cola / (2 ** n)

    @property
    def hay_bastante(self) -> bool:
        """Si el agregado significa algo o todavia es ruido."""
        return (len(self.posiciones) >= MIN_POSICIONES
                and self.dias_mediana >= MIN_DIAS)

    @property
    def cuadra(self) -> bool:
        return all(p.cuadra for p in self.posiciones)


def resumir(posiciones: list[Posicion]) -> Resumen:
    return Resumen(posiciones=list(posiciones))


def veredicto(resumen: Resumen) -> str:
    """Una frase que diga lo que pasa sin adornarlo en ninguna direccion."""
    if not resumen.posiciones:
        return "Sin posiciones que atribuir."

    diferencia = resumen.contra_el_mercado
    if not resumen.hay_bastante:
        return (
            f"Vas {diferencia * 100:+.1f} puntos respecto al mercado, pero con "
            f"{len(resumen.posiciones)} posiciones y una mediana de "
            f"{resumen.dias_mediana} dias esa cifra todavia no distingue el "
            "acierto de la suerte. Hacen falta mas posiciones y mas tiempo, y "
            "no hay atajo."
        )

    if diferencia > 0:
        return (
            f"Vas {diferencia * 100:+.1f} puntos por delante del mercado. De "
            f"ahi, {resumen.efecto_sector * 100:+.1f} vienen de en que sectores "
            f"estas y {resumen.efecto_seleccion * 100:+.1f} de que valores "
            "elegiste dentro de ellos."
        )
    return (
        f"Vas {diferencia * 100:.1f} puntos por detras del mercado: habrias "
        "ganado mas comprando el indice y no tocando nada. De la diferencia, "
        f"{resumen.efecto_sector * 100:+.1f} vienen de los sectores y "
        f"{resumen.efecto_seleccion * 100:+.1f} de los valores concretos."
    )

"""De donde ha salido lo que has ganado.

Es la pantalla que corrige el sesgo que mas caro sale: en un mercado alcista
todo parece un acierto y en uno bajista todo parece un fallo, se haya elegido
bien o mal. Sin separar la marea del merito no hay forma de aprender de las
propias decisiones, porque el resultado no las califica.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ...core.attribution import Posicion, resumir, veredicto


def _valor(fila: pd.Series, campo: str) -> float | None:
    valor = fila.get(campo)
    return None if valor is None or pd.isna(valor) else float(valor)


def posiciones_desde(datos: pd.DataFrame) -> list[Posicion]:
    """Pasa la consulta al nucleo, tirando lo que no se puede atribuir.

    Sin retorno propio o sin referencia de mercado no hay nada que descomponer,
    y meterla con ceros la contaria como "ni mejor ni peor que el mercado", que
    es una afirmacion que nadie ha comprobado.
    """
    fuera: list[Posicion] = []
    for _, fila in datos.iterrows():
        retorno = _valor(fila, "retorno")
        mercado = _valor(fila, "retorno_mercado")
        if retorno is None or mercado is None:
            continue
        fuera.append(Posicion(
            ticker=str(fila["ticker"]),
            coste=_valor(fila, "coste") or 0.0,
            retorno=retorno,
            retorno_mercado=mercado,
            retorno_sector=_valor(fila, "retorno_sector"),
            dias=int(_valor(fila, "dias") or 0),
            # `or ""` no basta: un sector ausente llega como `nan`, que es
            # VERDADERO en un `or` y acaba pintando la palabra "nan" en la
            # columna de sector.
            sector=("" if pd.isna(fila.get("sector"))
                    else str(fila.get("sector") or "")),
        ))
    return fuera


def render_attribution_panel(datos: pd.DataFrame) -> None:
    st.subheader("De donde viene lo que has ganado")

    if datos.empty:
        st.caption("Sin posiciones abiertas que atribuir.")
        return

    posiciones = posiciones_desde(datos)
    descartadas = len(datos) - len(posiciones)

    if not posiciones:
        st.info(
            "No hay ninguna posicion con referencia de mercado para comparar. "
            "Hace falta el historico de **SPY** y de los ETF sectoriales: se "
            "descargan con `stocks.ps1 update`.",
            icon=":material/help:",
        )
        return

    r = resumir(posiciones)

    st.caption(
        "Tu resultado partido en tres: lo que hizo el mercado, lo que anadio "
        "estar en unos sectores y no en otros, y lo que anadio elegir esos "
        "valores concretos. **Los tres suman exactamente tu resultado**, asi "
        "que no hay resto donde esconder nada. Cada posicion se compara con lo "
        "que hicieron el indice y su sector **desde el dia que la compraste**, "
        "no con el ano natural."
    )

    cols = st.columns(4)
    cols[0].metric("Tu resultado", f"{r.retorno * 100:+.1f} %")
    cols[1].metric("El mercado hizo", f"{r.mercado * 100:+.1f} %",
                   help="Lo que habrias ganado comprando SPY el mismo dia que "
                        "cada posicion y no tocando nada mas.")
    cols[2].metric("Por los sectores", f"{r.efecto_sector * 100:+.1f} pp",
                   help="Lo que aporto estar en esos sectores en vez de en el "
                        "mercado entero.")
    cols[3].metric("Por los valores", f"{r.efecto_seleccion * 100:+.1f} pp",
                   help="Lo que aporto elegir esos valores dentro de sus "
                        "sectores. Es la parte que mide la seleccion.")

    frase = veredicto(r)
    if not r.hay_bastante:
        st.warning(frase, icon=":material/hourglass_empty:")
    elif r.contra_el_mercado < 0:
        st.error(frase, icon=":material/trending_down:")
    else:
        st.success(frase, icon=":material/trending_up:")

    # --- Cuanto de esto puede ser suerte ------------------------------------
    if r.comparables:
        azar = r.probabilidad_por_azar
        st.markdown(
            f"**{r.aciertos} de {r.comparables}** posiciones baten a su sector. "
            f"Acertar tanto tirando una moneda tiene una probabilidad de "
            f"**{azar * 100:.0f} %**."
        )
        st.caption(
            "No es un contraste estadistico y no hay que leerlo como tal: las "
            "posiciones se solapan en el tiempo, comparten mercado y no son "
            "independientes. Es una cota de humildad — si ese porcentaje es "
            "alto, no hay nada que explicar todavia."
        )

    # --- Posicion a posicion ------------------------------------------------
    detalle = pd.DataFrame([
        {
            "Ticker": p.ticker,
            "Sector": p.sector or "—",
            "Dias": p.dias,
            "Tu": p.retorno * 100,
            "Mercado": p.retorno_mercado * 100,
            "Su sector": (p.retorno_sector * 100
                          if p.retorno_sector is not None else None),
            # En puntos porcentuales y no en por ciento: son diferencias entre
            # dos retornos, no un retorno. Llamarlas "%" invita a sumarlas al
            # resultado, que es justo lo que ya son.
            "Por el sector (pp)": p.efecto_sector * 100,
            "Por el valor (pp)": p.efecto_seleccion * 100,
        }
        for p in sorted(posiciones, key=lambda p: p.efecto_seleccion)
    ])
    st.dataframe(
        detalle, hide_index=True, height=min(420, 42 + 35 * len(detalle)),
        column_config={
            **{c: st.column_config.NumberColumn(format="%+.1f%%")
               for c in ("Tu", "Mercado", "Su sector")},
            **{c: st.column_config.NumberColumn(format="%+.1f")
               for c in ("Por el sector (pp)", "Por el valor (pp)")},
        },
    )
    st.caption(
        "Ordenado por lo que aporto la eleccion del valor, de peor a mejor: "
        "arriba estan las decisiones que mas restaron. Una posicion puede "
        "ganar dinero y estar arriba del todo — significa que el sector subio "
        "mas que ella."
    )

    if descartadas:
        st.caption(
            f"{descartadas} posicion(es) sin referencia de mercado para "
            "comparar, fuera del calculo. No se cuentan como neutras: eso "
            "seria afirmar algo que no se ha comprobado."
        )

    sin_sector = len(posiciones) - r.comparables
    if sin_sector:
        st.caption(
            f"{sin_sector} posicion(es) sin ETF sectorial de referencia. En "
            "ellas no se puede separar el efecto del sector del de la "
            "seleccion, y todo lo que no es mercado cuenta como seleccion."
        )

    st.caption(
        "**Esto no es tu resultado fiscal ni el de tu broker**: ignora "
        "dividendos, comisiones y cambio de divisa. Tampoco es una "
        "rentabilidad anualizada, porque cada posicion lleva su propio tiempo "
        "dentro."
    )

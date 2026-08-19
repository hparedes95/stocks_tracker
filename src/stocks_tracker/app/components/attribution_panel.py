"""De dónde ha salido lo que has ganado.

Es la pantalla que corrige el sesgo que más caro sale: en un mercado alcista
todo parece un acierto y en uno bajista todo parece un fallo, se haya elegido
bien o mal. Sin separar la marea del mérito no hay forma de aprender de las
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
    """Pasa la consulta al núcleo, tirando lo que no se puede atribuir.

    Sin retorno propio o sin referencia de mercado no hay nada que descomponer,
    y meterla con ceros la contaría como "ni mejor ni peor que el mercado", que
    es una afirmación que nadie ha comprobado.
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
    st.subheader("De dónde viene lo que has ganado")

    if datos.empty:
        st.caption("Sin posiciones abiertas que atribuir.")
        return

    posiciones = posiciones_desde(datos)
    descartadas = len(datos) - len(posiciones)

    if not posiciones:
        st.info(
            "No hay ninguna posición con referencia de mercado para comparar. "
            "Hace falta el histórico de **SPY** y de los ETF sectoriales: se "
            "descargan con `stocks.ps1 update`.",
            icon=":material/help:",
        )
        return

    r = resumir(posiciones)

    st.caption(
        "Tu resultado partido en tres: lo que hizo el mercado, lo que añadió "
        "estar en unos sectores y no en otros, y lo que añadió elegir esos "
        "valores concretos. **Los tres suman exactamente tu resultado**, así "
        "que no hay resto donde esconder nada. Cada posición se compara con lo "
        "que hicieron el índice y su sector **desde el día que la compraste**, "
        "no con el año natural."
    )

    cols = st.columns(4)
    cols[0].metric("Tu resultado", f"{r.retorno * 100:+.1f} %")
    cols[1].metric("El mercado hizo", f"{r.mercado * 100:+.1f} %",
                   help="Lo que habrías ganado comprando SPY el mismo día que "
                        "cada posición y no tocando nada más.")
    cols[2].metric("Por los sectores", f"{r.efecto_sector * 100:+.1f} pp",
                   help="Lo que aportó estar en esos sectores en vez de en el "
                        "mercado entero.")
    cols[3].metric("Por los valores", f"{r.efecto_seleccion * 100:+.1f} pp",
                   help="Lo que aportó elegir esos valores dentro de sus "
                        "sectores. Es la parte que mide la selección.")

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
            "No es un contraste estadístico y no hay que leerlo como tal: las "
            "posiciones se solapan en el tiempo, comparten mercado y no son "
            "independientes. Es una cota de humildad — si ese porcentaje es "
            "alto, no hay nada que explicar todavía."
        )

    # --- Posicion a posicion ------------------------------------------------
    detalle = pd.DataFrame([
        {
            "Ticker": p.ticker,
            "Sector": p.sector or "—",
            "Días": p.dias,
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
        "Ordenado por lo que aportó la elección del valor, de peor a mejor: "
        "arriba están las decisiones que más restaron. Una posición puede "
        "ganar dinero y estar arriba del todo — significa que el sector subió "
        "más que ella."
    )

    if descartadas:
        st.caption(
            f"{descartadas} posición(es) sin referencia de mercado para "
            "comparar, fuera del cálculo. No se cuentan como neutras: eso "
            "sería afirmar algo que no se ha comprobado."
        )

    sin_sector = len(posiciones) - r.comparables
    if sin_sector:
        st.caption(
            f"{sin_sector} posición(es) sin ETF sectorial de referencia. En "
            "ellas no se puede separar el efecto del sector del de la "
            "selección, y todo lo que no es mercado cuenta como selección."
        )

    st.caption(
        "**Esto no es tu resultado fiscal ni el de tu broker**: ignora "
        "dividendos, comisiones y cambio de divisa. Tampoco es una "
        "rentabilidad anualizada, porque cada posición lleva su propio tiempo "
        "dentro."
    )

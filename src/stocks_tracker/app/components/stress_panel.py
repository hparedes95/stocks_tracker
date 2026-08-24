"""Qué le habría pasado a tu cartera en caídas que de verdad ocurrieron.

Va debajo del diagnóstico de concentración porque responde a la misma pregunta
con datos en vez de con intuición: el gráfico de sectores sugiere que estas
concentrado, esto dice cuanto dinero cuesta esa concentración el día malo.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ...core.stress import (
    ETIQUETA_FUENTE,
    Fuente,
    diversificacion,
    escenarios,
    frase_peor,
    impacto,
)
from .. import data_access as da


def _mercado(desde, hasta) -> float | None:
    devuelto = da.get_window_returns((da.MERCADO_TICKER,), desde, hasta)
    return devuelto.get(da.MERCADO_TICKER)


def render_stress_panel(positions: pd.DataFrame) -> None:
    st.subheader("Qué pasaría si volviera a pasar")
    st.caption(
        "No es una simulación: a cada posición que tienes hoy se le aplica lo "
        "que de verdad hizo ese valor entre dos fechas reales. **No es el peor "
        "caso** — el peor caso siempre es peor que lo peor que ha pasado, y en "
        "2007 nadie tenía 2008 en su lista. Sirve para ver donde esta "
        "concentrado el riesgo, no para poner un suelo a las pérdidas."
    )

    if positions.empty:
        st.caption("Sin posiciones que poner a prueba.")
        return

    # `valor_eur` y no `valor`: todo lo que sale de aqui es un PESO relativo
    # dentro de la cartera —cuanto pesa cada posicion en la caida y cuantas
    # apuestas de verdad hay—, y pesar dolares contra euros sin convertir hace
    # las posiciones en dolares un 17 % mas grandes de lo que son. Se cae a
    # `valor` si la columna no viene, para no romper a quien llame sin ella.
    columna = "valor_eur" if "valor_eur" in positions.columns else "valor"
    cartera = [
        {"ticker": str(f[0]), "valor": float(f[1]),
         "sector": (None if pd.isna(f[2]) else str(f[2]))}
        for f in positions[["ticker", columna, "gics_sector"]].itertuples(index=False)
        if pd.notna(f[1]) and float(f[1]) > 0
    ]
    if not cartera:
        st.caption("Sin posiciones valoradas.")
        return

    tickers = tuple(sorted({p["ticker"] for p in cartera}))

    filas, sin_datos = [], []
    detalles = {}
    for esc in escenarios():
        propios = da.get_window_returns(tickers, esc.desde, esc.hasta)
        sectores = da.get_sector_window_returns(esc.desde, esc.hasta)
        mercado = _mercado(esc.desde, esc.hasta)

        # Sin retorno del indice no hay ni siquiera un suelo con el que
        # estimar: el escenario queda fuera del historico descargado.
        if mercado is None and not propios and not sectores:
            sin_datos.append(esc)
            continue

        res = impacto(esc, cartera, propios, sectores, mercado)
        if not res.posiciones:
            sin_datos.append(esc)
            continue

        detalles[esc.id] = res
        filas.append({
            "Escenario": esc.nombre,
            "Tu cartera": res.retorno * 100,
            "El índice": (res.retorno_mercado * 100
                          if res.retorno_mercado is not None else None),
            "En euros": res.perdida,
            "Calculado con datos propios": res.cobertura * 100,
        })

    if filas:
        st.dataframe(
            pd.DataFrame(filas), hide_index=True,
            column_config={
                "Tu cartera": st.column_config.NumberColumn(format="%+.1f%%"),
                "El índice": st.column_config.NumberColumn(format="%+.1f%%"),
                "En euros": st.column_config.NumberColumn(format="%+.0f"),
                "Calculado con datos propios": st.column_config.ProgressColumn(
                    min_value=0.0, max_value=100.0, format="%.0f%%"
                ),
            },
        )

        peor = min(detalles.values(), key=lambda r: r.retorno)
        if not peor.fiable:
            st.warning(
                f"En **{peor.escenario.nombre}** solo el "
                f"{peor.cobertura:.0%} de tu dinero se calcula con el "
                "histórico de tus propios valores; el resto se estima con su "
                "sector o con el índice. Ese número dice más del mercado de "
                "entonces que de tu cartera de ahora.",
                icon=":material/help:",
            )
        elif peor.retorno < 0:
            st.error(frase_peor(peor), icon=":material/warning:")
        else:
            st.success(frase_peor(peor), icon=":material/shield:")

        for res in detalles.values():
            esc = res.escenario
            with st.expander(f"{esc.nombre} · detalle"):
                st.caption(f"{esc.desde:%d/%m/%Y} — {esc.hasta:%d/%m/%Y}")
                st.markdown(esc.que_paso)
                peor_que = res.peor_que_el_mercado
                if peor_que is not None:
                    if peor_que < 0:
                        st.markdown(
                            f"Tu cartera lo habría pasado **{abs(peor_que):.1%} "
                            "peor que el índice**."
                        )
                    else:
                        st.markdown(
                            f"Tu cartera habría aguantado **{peor_que:.1%} mejor "
                            "que el índice**."
                        )
                st.dataframe(
                    pd.DataFrame([
                        {"Ticker": p.ticker,
                         "Caída": p.retorno * 100,
                         "En euros": p.perdida,
                         "Calculado con": ETIQUETA_FUENTE[p.fuente]}
                        for p in res.peores[:12]
                    ]),
                    hide_index=True,
                    column_config={
                        "Caída": st.column_config.NumberColumn(format="%+.1f%%"),
                        "En euros": st.column_config.NumberColumn(format="%+.0f"),
                    },
                )
                estimadas = sum(1 for p in res.posiciones if p.estimado)
                if estimadas:
                    st.caption(
                        f"{estimadas} posición(es) estimadas con su sector o "
                        "con el índice porque no cotizaban o no hay histórico "
                        "suyo de esas fechas."
                    )

    if sin_datos:
        nombres = ", ".join(e.nombre for e in sin_datos)
        st.info(
            f"Sin histórico para: **{nombres}**. No se rellena con una "
            "estimación a propósito: un número inventado se leería igual de "
            "convincente que uno real. Se descargan 10 años por defecto; para "
            "llegar más atrás, sube `ingest.backfill_years` en "
            "`config/settings.yaml` y vuelve a ejecutar `stocks.ps1 ingest`.",
            icon=":material/history:",
        )

    # --- La diversificacion que desaparece ---------------------------------
    st.markdown("**¿Cuántas apuestas tienes de verdad?**")
    # Se SUMAN los lotes del mismo valor. Con un diccionario por comprension,
    # la segunda compra de un valor pisaba a la primera y la cartera parecia
    # menos concentrada de lo que es — justo lo contrario de lo que este panel
    # existe para ensenar.
    pesos: dict = {}
    for p in cartera:
        pesos[p["ticker"]] = pesos.get(p["ticker"], 0.0) + p["valor"]
    corr = da.get_returns_matrix(tickers)
    div = (diversificacion(pesos, corr.corr(), da.get_realized_vol(tickers))
           if not corr.empty and corr.shape[1] >= 2 else None)

    if div is None:
        st.caption(
            "Hacen falta al menos dos posiciones con histórico comun para "
            "medir si se mueven juntas."
        )
    else:
        cols = st.columns(3)
        cols[0].metric("Posiciones", div.n_posiciones)
        cols[1].metric("Apuestas de verdad, hoy", f"{div.efectivas_hoy:.1f}",
                       help="Diez valores que se mueven todos igual son una "
                            "apuesta, no diez.")
        cols[2].metric("Y en una caída fuerte",
                       f"{div.efectivas_en_crisis:.1f}",
                       delta=f"-{div.se_pierde:.1f}", delta_color="inverse",
                       help=f"Suponiendo que las correlaciones suban a "
                            f"{div.correlacion_crisis:.0%}, que es lo que se ha "
                            "visto en las caídas de la tabla de arriba.")

        if div.ya_esta_concentrada:
            st.warning(
                f"Tienes {div.n_posiciones} posiciones pero se comportan como "
                f"**{div.efectivas_hoy:.1f}**: la correlación media entre ellas "
                f"es de {div.correlacion_media:.2f}. Añadir otro valor parecido "
                "no diversifica nada, solo reparte la misma apuesta en más "
                "casillas.",
                icon=":material/join_inner:",
            )
        st.caption(
            "La diversificación se calcula con las correlaciones de los "
            "últimos meses, que son las de un mercado tranquilo. En una caída "
            "fuerte lo que normalmente se mueve por separado empieza a moverse "
            "junto: **la diversificación desaparece justo el día que hacia "
            "falta**, y esa es la segunda columna."
        )


__all__ = ["render_stress_panel", "Fuente"]

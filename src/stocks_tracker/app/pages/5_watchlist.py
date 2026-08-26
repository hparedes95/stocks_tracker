"""Página 5 — Cartera y watchlist.

La watchlist es lo que sigues; la cartera, lo que tienes. El diagnóstico de
concentración es lo que más aporta aquí: una cartera de ocho valores que se
mueven todos igual esta menos diversificada de lo que parece.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components import charts, tv_widgets
from stocks_tracker.app.components.attribution_panel import render_attribution_panel
from stocks_tracker.app.components.broker_import import (
    render_broker_import,
    render_fechas_de_compra,
    render_manual_table,
)
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.app.components.health_panel import render_health_panel
from stocks_tracker.app.components.stress_panel import render_stress_panel
from stocks_tracker.app.components.theme import format_money, format_pct
from stocks_tracker.core import fx

st.title("Cartera y watchlist")

tab_portfolio, tab_watchlist = st.tabs(["Cartera", "Watchlist"])

# ===========================================================================
# CARTERA
# ===========================================================================
with tab_portfolio:
    positions = da.get_positions()

    with st.expander("Añadir una posición"):
        st.caption(
            "Para una posición suelta. Si tienes la cartera en eToro o Trade "
            "Republic, usa la importación de abajo."
        )
        form_cols = st.columns([2, 1, 1, 1])
        with form_cols[0]:
            new_ticker = st.selectbox(
                "Valor", options=da.all_tickers(), index=None,
                placeholder="Busca un ticker", key="pos_ticker",
            )
        with form_cols[1]:
            new_qty = st.number_input("Titulos", min_value=0.0, value=0.0, step=1.0)
        with form_cols[2]:
            new_cost = st.number_input("Precio medio", min_value=0.0, value=0.0, step=0.01)
        with form_cols[3]:
            new_currency = st.selectbox("Divisa", options=["USD", "EUR", "GBP"])
        if st.button("Añadir", type="primary", disabled=not (new_ticker and new_qty > 0)):
            da.add_position(new_ticker, new_qty, new_cost, new_currency)
            st.rerun()

    with st.expander("Importar desde eToro o Trade Republic"):
        import_tab, manual_tab = st.tabs(["Subir extracto", "Escribir a mano"])
        with import_tab:
            render_broker_import()
        with manual_tab:
            render_manual_table()

    render_fechas_de_compra(positions)

    if positions.empty:
        st.info("Sin posiciones registradas. Añade la primera arriba.")
    else:
        positions = positions.copy()
        # Valor y coste en la divisa del valor: es lo unico correcto para el
        # resultado de CADA posicion, que es un porcentaje y no depende del
        # cambio.
        positions["valor"] = positions["qty"] * positions["close"]
        positions["coste"] = positions["qty"] * positions["avg_cost"]
        positions["pnl"] = positions["valor"] - positions["coste"]
        positions["pnl_pct"] = (
            positions["valor"] / positions["coste"].replace(0, np.nan) - 1.0
        )

        # Y en euros para todo lo que se SUMA. Antes se sumaban dolares con
        # euros como si valieran lo mismo: con EUR/USD a 1,17, una cartera
        # mitad y mitad se presentaba un 8 % por encima de lo que vale, y el
        # peso de cada posicion —la cifra con la que se decide si una pesa
        # demasiado— salia inflado en dolares y encogido en euros.
        tipos = da.get_fx_rates()
        positions["valor_eur"] = fx.a_base(
            positions["valor"], positions["currency"], tipos)
        # EL COSTE, AL CAMBIO DEL DIA DE LA COMPRA. NO AL DE HOY.
        #
        # Convirtiendo coste y valor con el mismo tipo, el efecto divisa se
        # cancela y desaparece del resultado. Con el EUR/USD pasando de 1,05 a
        # 1,17, una posicion de 1.000 USD que hoy vale 1.100 salia como +85 EUR
        # (+10 %) cuando en euros de verdad es -12 EUR (-1,3 %): una ganancia
        # declarada donde hay una perdida.
        #
        # `get_positions` ya lo calcula con el historico de tipos. Si para
        # alguna fila no hubo tipo aquel dia, sale NaN y contagia al total: es
        # lo correcto, y lo dice el aviso de abajo.
        positions["coste_eur"] = positions["coste_eur_compra"]
        faltan = fx.sin_tipo(positions["currency"], tipos)

        total_value = fx.total(positions["valor_eur"])
        positions["peso"] = positions["valor_eur"] / total_value if total_value else np.nan

        # -------------------------------------------------------------------
        # Resumen
        # -------------------------------------------------------------------
        total_cost = fx.total(positions["coste_eur"])
        total_pnl = total_value - total_cost
        # `ret_1d` vacio se cuenta como cero y no contagia: una posicion recien
        # anadida todavia no tiene indicadores, y eso no puede dejar sin cifra
        # el movimiento del dia de toda la cartera. Lo que si contagia es el
        # hueco de divisa, que viene por `valor_eur`.
        day_change = fx.total(positions["valor_eur"] * positions["ret_1d"].fillna(0.0))

        # Los tres importes llevan el simbolo del EURO porque estan
        # convertidos a euros. `format_money` pone el dolar por defecto, y
        # ensenar "1 759,40 $" sobre una cifra en euros es exactamente el
        # tipo de etiqueta falsa que este arreglo venia a quitar.
        summary = st.columns(4)
        summary[0].metric("Valor actual", format_money(total_value, "EUR"))
        summary[1].metric(
            "Resultado", format_money(total_pnl, "EUR"),
            format_pct(total_pnl / total_cost if total_cost else None),
        )
        summary[2].metric(
            "Hoy", format_money(day_change, "EUR"),
            format_pct(day_change / total_value if total_value else None),
        )
        summary[3].metric("Posiciones", len(positions))

        if faltan:
            # Ya no es "trata los totales como orientativos": ahora se convierte
            # de verdad, y lo unico que hay que decir es QUE se ha quedado
            # fuera. Esas posiciones salen NaN y arrastran el total a NaN a
            # proposito: un total al que le falta una posicion es un numero mal
            # con toda la pinta de estar bien.
            st.warning(
                f"No hay tipo de cambio para {', '.join(faltan)}, asi que esas "
                "posiciones no se pueden valorar en euros y los totales salen "
                "vacios. Se arregla anadiendo el par a `config/universe.yaml` "
                "(bloque MACRO) y volviendo a descargar.",
                icon=":material/currency_exchange:",
            )
        # `!= EUR` y NO `nunique() > 1`.
        #
        # La condicion medía VARIEDAD en vez de EXTRANJERIA: una cartera
        # entera en dolares tiene una sola divisa, asi que no veia ningun
        # aviso, y es justo el caso donde el efecto divisa pesa mas.
        elif (positions["currency"].astype("string").str.upper() != "EUR").any():
            st.caption(
                "Los totales estan convertidos a euros. El valor va al ultimo "
                "cambio disponible y el coste al cambio del dia en que "
                "compraste, asi que el resultado **incluye lo que ha hecho la "
                "divisa**, que es como lo cuenta Hacienda."
            )

        # LO QUE ESTE NUMERO NO LLEVA, DICHO DONDE SE LEE EL NUMERO.
        #
        # `attribution.py` ya avisaba de esto en su pantalla; aqui no, y aqui
        # es donde el usuario mira su resultado. `avg_cost` es lo que declara
        # el extracto y no incluye la comision de compra; el resultado tampoco
        # resta la de venta ni suma los dividendos cobrados, que SI estan
        # guardados en `corporate_actions`.
        #
        # Con la tarifa por defecto, diez compras de 1.000 EUR dejan el
        # resultado unos 20 EUR por encima del real solo en comisiones de ida
        # y vuelta. En un dividendero al 3 % el error va en el otro sentido y
        # es mayor. Mientras no haya libro de operaciones no se puede corregir,
        # pero callarlo es presentar como exacto algo que no lo es.
        st.caption(
            ":gray[El resultado no incluye comisiones ni dividendos cobrados: "
            "sale de comparar el precio medio que trae tu extracto con la "
            "cotizacion de hoy. Tu resultado real es algo peor en comisiones y "
            "algo mejor en dividendos.]"
        )

        # La divisa declarada al importar puede no ser la de cotizacion, y esa
        # discrepancia mueve el peso de la posicion —y con el, si el asesor
        # avisa por concentracion—. Se dice; no se corrige a la callada.
        if "currency_declarada" in positions:
            declarada = positions["currency_declarada"].astype("string").str.upper()
            real = positions["currency"].astype("string").str.upper()
            discrepan = positions[declarada.notna() & (declarada != real)]
            if not discrepan.empty:
                st.warning(
                    "Estas posiciones se valoran en la divisa en la que "
                    "COTIZAN, que no es la que consta en tu extracto: "
                    + ", ".join(
                        f"**{r.ticker}** ({r.currency_declarada} declarada, "
                        f"{r.currency} real)" for r in discrepan.itertuples())
                    + ". Se usa la real porque es la de los precios; si la "
                    "declarada es la buena, el valor de esa posicion esta mal.",
                    icon=":material/currency_exchange:",
                )

        # -------------------------------------------------------------------
        # Semaforo de deterioro
        # -------------------------------------------------------------------
        # Aqui arriba y no al final: una posicion que gana un 15 % con el
        # margen desplomandose es justo la que no se mira, porque el numero
        # verde de al lado dice que todo va bien.
        st.divider()
        render_health_panel(
            da.get_position_health(),
            nombres=dict(zip(positions["ticker"], positions["name"].fillna(""),
                             strict=False)),
        )

        # -------------------------------------------------------------------
        # Detalle
        # -------------------------------------------------------------------
        st.divider()
        view = pd.DataFrame(
            {
                "Ticker": positions["ticker"],
                "Nombre": positions["name"].fillna(""),
                "Titulos": positions["qty"],
                "Coste medio": positions["avg_cost"],
                "Precio": positions["close"],
                # En euros, y no en la divisa del valor, porque va al lado de
                # "Peso" —que es un porcentaje del total en euros— y porque la
                # columna se lee de arriba abajo comparando unas con otras. Dos
                # cifras en dos monedas en la misma columna no se comparan.
                "Valor (EUR)": positions["valor_eur"],
                "Resultado": positions["pnl_pct"] * 100,
                "Peso": positions["peso"] * 100,
                "Hoy": positions["ret_1d"] * 100,
                "Percentil": positions["composite_pctile"] * 100,
            }
        )
        # Una columna entera en blanco solo roba ancho a las demas.
        if not view["Nombre"].str.strip().any():
            view = view.drop(columns=["Nombre"])

        st.dataframe(
            view, hide_index=True, height=min(400, 42 + 35 * len(view)),
            column_config={
                "Titulos": st.column_config.NumberColumn(format="%.4f"),
                "Coste medio": st.column_config.NumberColumn(format="%.2f"),
                "Precio": st.column_config.NumberColumn(format="%.2f"),
                "Valor (EUR)": st.column_config.NumberColumn(format="%.2f"),
                "Resultado": st.column_config.NumberColumn(format="%+.2f%%"),
                "Peso": st.column_config.ProgressColumn(
                    min_value=0.0, max_value=100.0, format="%.1f%%"
                ),
                "Hoy": st.column_config.NumberColumn(format="%+.2f%%"),
                "Percentil": st.column_config.ProgressColumn(
                    min_value=0.0, max_value=100.0, format="%.0f%%"
                ),
            },
        )

        # -------------------------------------------------------------------
        # Atribucion: marea o merito
        # -------------------------------------------------------------------
        st.divider()
        render_attribution_panel(da.get_attribution_inputs())

        # -------------------------------------------------------------------
        # Diagnostico de concentracion
        # -------------------------------------------------------------------
        st.divider()
        st.subheader("Diagnóstico de concentración")

        # Los dos diagnosticos de aqui abajo son REPARTOS PORCENTUALES, y un
        # reparto al que le falta una posicion no es un reparto aproximado: es
        # otro reparto.
        #
        # Sin esta guarda, `groupby(...).sum()` cuenta la posicion sin tipo de
        # cambio como cero —su sector desaparece del grafico Y del denominador,
        # asi que el aviso de concentracion podia senalar al sector equivocado—
        # y `peso.fillna(0)` deja el perfil factorial con ceros en todos los
        # ejes, que se lee como "esta cartera no tiene ningun sesgo factorial".
        # Dos afirmaciones falsas dichas con un grafico, que es la forma en la
        # que menos se cuestionan.
        if bool(faltan) or bool(positions["valor_eur"].isna().any()):
            st.info(
                "Falta el tipo de cambio de alguna posición, asi que no se "
                "puede repartir el peso de la cartera. La exposición por sector "
                "y el perfil factorial aparecen cuando estén todos los tipos: "
                "un reparto al que le falta una posición señalaría al sector "
                "equivocado.",
                icon=":material/pie_chart:",
            )
        else:
            diag_left, diag_right = st.columns(2)

            with diag_left:
                st.markdown("**Exposición por sector**")
                known = positions[positions["gics_sector"].notna()]
                if known.empty:
                    # Sin sector no hay diagnostico posible, y decir "Sin
                    # sector: 100%" sonaria a una concentracion que en realidad
                    # no sabemos si existe.
                    st.info(
                        "Tus posiciones no tienen sector asignado todavía. Se "
                        "rellena con `make ingest`, que descarga la ficha de "
                        "cada valor.",
                        icon=":material/help:",
                    )
                else:
                    # En euros: esto es un reparto porcentual entre sectores y
                    # dispara un aviso por encima del 40 %. Con las divisas sin
                    # convertir, un sector de valores americanos se ve un 17 %
                    # mas grande de lo que es y otro de europeos mas pequeno,
                    # que es cambiar el sentido del aviso.
                    by_sector = (
                        known.groupby("gics_sector")["valor_eur"].sum()
                        .sort_values(ascending=False)
                    )
                    covered = float(by_sector.sum())
                    st.plotly_chart(
                        charts.weight_bars(
                            by_sector / covered,
                            height=max(180, 46 * len(by_sector)),
                        ),
                        width="stretch", config={"displayModeBar": False},
                        key="cartera_sectores",
                    )
                    if len(known) < len(positions):
                        st.caption(
                            f"Calculado sobre {len(known)} de {len(positions)} "
                            "posiciones: el resto no tiene sector asignado."
                        )

                    top_weight = float(by_sector.iloc[0] / covered)
                    if top_weight > 0.40:
                        st.warning(
                            f"**{by_sector.index[0]}** pesa el {top_weight:.0%} "
                            "de la cartera. Una concentración así hace que el "
                            "resultado dependa de un solo sector más que de tu "
                            "selección de valores.",
                            icon=":material/warning:",
                        )

            with diag_right:
                st.markdown("**Perfil factorial de la cartera**")
                factor_cols = ["value_z", "growth_z", "quality_z", "momentum_z",
                               "lowvol_z", "dividend_z", "technical_z"]
                weights = positions["peso"].fillna(0)
                profile = {}
                for col in factor_cols:
                    if col in positions.columns and positions[col].notna().any():
                        values = positions[col].fillna(0)
                        profile[col] = float((values * weights).sum())
                if len(profile) >= 3:
                    st.plotly_chart(
                        charts.factor_radar(profile, height=300),
                        width="stretch", config={"displayModeBar": False},
                        key="cartera_radar",
                    )
                    dominant = max(profile.items(), key=lambda kv: abs(kv[1]))
                    names = {
                        "value_z": "valor", "growth_z": "crecimiento",
                        "quality_z": "calidad", "momentum_z": "momentum",
                        "lowvol_z": "estabilidad", "dividend_z": "dividendo",
                        "technical_z": "técnico",
                    }
                    if abs(dominant[1]) > 0.5:
                        direction = "hacia" if dominant[1] > 0 else "en contra de"
                        st.caption(
                            f"Tu cartera esta inclinada {direction} "
                            f"**{names.get(dominant[0], dominant[0])}** "
                            f"({dominant[1]:+.2f}). No es bueno ni malo: es una "
                            "apuesta implicita que conviene conocer."
                        )
                else:
                    st.caption(
                        "Sin puntuación factorial suficiente para el perfil."
                    )

        # -------------------------------------------------------------------
        # Stress test
        # -------------------------------------------------------------------
        # Sustituye a la vieja seccion de "Diversificacion real", que solo
        # ensenaba la correlacion media de los ultimos meses. Ese numero se
        # queda corto por construccion: describe un mercado tranquilo, y la
        # diversificacion desaparece justo cuando deja de estarlo. Aqui esta la
        # misma correlacion media, ademas de cuantas apuestas independientes
        # hay de verdad y cuantas quedarian en una caida.
        st.divider()
        render_stress_panel(positions)

        # -------------------------------------------------------------------
        # Cerrar
        # -------------------------------------------------------------------
        st.divider()
        close_col, _ = st.columns([1, 2])
        with close_col:
            labels = {
                row.id: f"{row.ticker} · {row.qty:g} titulos"
                for row in positions.itertuples()
            }
            to_close = st.selectbox(
                "Cerrar posición", options=list(labels),
                format_func=lambda i: labels[i], index=None,
                placeholder="Elige una posición",
            )
            # El precio de venta se pide aquí y no se estima después. Sin él, el
            # resultado se calcula con el cierre del día, que no es el precio de
            # ejecución: una venta con pérdida puede salir en ganancia y la
            # regla de los dos meses se calla justo cuando tenía que avisar.
            # Se puede dejar en blanco —entonces se estima y se dice que es una
            # estimación—, pero teclearlo cuesta cinco segundos.
            precio_venta = st.number_input(
                "Precio de venta (opcional)", min_value=0.0, value=None,
                step=0.01, format="%.4f", placeholder="Se estima si lo dejas vacío",
                help="A cuánto vendiste de verdad. Sin este dato el resultado "
                     "se estima con el cierre de ese día, y la estimación "
                     "puede equivocarse de signo.",
            )
            if to_close and st.button("Cerrar"):
                da.close_position(to_close, close_price=precio_venta)
                st.rerun()

    render_disclaimer()

# ===========================================================================
# WATCHLIST
# ===========================================================================
with tab_watchlist:
    watchlist = da.get_watchlist()

    add_col, _ = st.columns([2, 3])
    with add_col:
        options = [t for t in da.all_tickers() if t not in set(watchlist["ticker"])]
        if options:
            new_ticker = st.selectbox(
                "Añadir un valor", options=options, index=None,
                placeholder="Busca un ticker", key="wl_ticker",
            )
            if new_ticker and st.button("Añadir a la watchlist", type="primary"):
                history = da.get_price_history(new_ticker, days=1)
                price = float(history.iloc[-1]["close"]) if not history.empty else None
                da.add_to_watchlist(new_ticker, price=price)
                st.rerun()

    if watchlist.empty:
        st.info(
            "Watchlist vacía. Añade valores desde aquí o con el boton "
            "**Guardar** de la página de oportunidades."
        )
    else:
        watchlist = watchlist.copy()
        # Variacion desde el alta: la unica metrica que dice si merecia la pena
        # seguir un valor. Por eso se guarda el precio de entrada.
        watchlist["desde_alta"] = (
            (watchlist["close"] - watchlist["added_price"])
            / watchlist["added_price"]
        ).where(watchlist["added_price"].notna() & (watchlist["added_price"] > 0))

        view = pd.DataFrame(
            {
                "Ticker": watchlist["ticker"],
                "Nombre": watchlist["name"].fillna(""),
                "Sector": watchlist["gics_sector"].fillna("—"),
                "Precio": watchlist["close"],
                "Día": watchlist["ret_1d"] * 100,
                "Desde alta": watchlist["desde_alta"] * 100,
                "Percentil": watchlist["composite_pctile"] * 100,
                "Añadido": pd.to_datetime(watchlist["added_at"]).dt.strftime("%d/%m/%Y"),
            }
        )
        st.dataframe(
            view, hide_index=True, height=380,
            column_config={
                "Precio": st.column_config.NumberColumn(format="%.2f"),
                "Día": st.column_config.NumberColumn(format="%+.2f%%"),
                "Desde alta": st.column_config.NumberColumn(format="%+.2f%%"),
                "Percentil": st.column_config.ProgressColumn(
                    min_value=0.0, max_value=100.0, format="%.0f%%"
                ),
            },
        )

        summary = st.columns(3)
        tracked = len(watchlist)
        positive = int((watchlist["desde_alta"] > 0).sum())
        summary[0].metric("Valores seguidos", tracked)
        summary[1].metric("En positivo desde el alta", f"{positive} de {tracked}")
        summary[2].metric("Variación media", format_pct(watchlist["desde_alta"].mean()))

        st.divider()
        detail_col, remove_col = st.columns([3, 1])

        with remove_col:
            st.subheader("Quitar")
            to_remove = st.selectbox(
                "Valor", options=watchlist["ticker"].tolist(), key="remove_ticker"
            )
            if st.button("Quitar de la watchlist"):
                da.remove_from_watchlist(to_remove)
                st.rerun()

        with detail_col:
            st.subheader("Vistazo rápido")
            chosen = st.selectbox(
                "Valor", options=watchlist["ticker"].tolist(), key="preview_ticker"
            )
            tv_symbol = da.get_tv_symbol(chosen)
            if tv_widgets.enabled() and tv_symbol:
                tv_widgets.mini_symbol_overview(tv_symbol, height=220)
            else:
                st.caption("Sin equivalencia en TradingView para este valor.")

    render_disclaimer()

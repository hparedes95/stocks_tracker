"""Pagina 5 — Cartera y watchlist.

La watchlist es lo que sigues; la cartera, lo que tienes. El diagnostico de
concentracion es lo que mas aporta aqui: una cartera de ocho valores que se
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
    render_manual_table,
)
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.app.components.health_panel import render_health_panel
from stocks_tracker.app.components.theme import format_money, format_pct

st.title("Cartera y watchlist")

tab_portfolio, tab_watchlist = st.tabs(["Cartera", "Watchlist"])

# ===========================================================================
# CARTERA
# ===========================================================================
with tab_portfolio:
    positions = da.get_positions()

    with st.expander("Anadir una posicion"):
        st.caption(
            "Para una posicion suelta. Si tienes la cartera en eToro o Trade "
            "Republic, usa la importacion de abajo."
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
        if st.button("Anadir", type="primary", disabled=not (new_ticker and new_qty > 0)):
            da.add_position(new_ticker, new_qty, new_cost, new_currency)
            st.rerun()

    with st.expander("Importar desde eToro o Trade Republic"):
        import_tab, manual_tab = st.tabs(["Subir extracto", "Escribir a mano"])
        with import_tab:
            render_broker_import()
        with manual_tab:
            render_manual_table()

    if positions.empty:
        st.info("Sin posiciones registradas. Anade la primera arriba.")
    else:
        positions = positions.copy()
        positions["valor"] = positions["qty"] * positions["close"]
        positions["coste"] = positions["qty"] * positions["avg_cost"]
        positions["pnl"] = positions["valor"] - positions["coste"]
        positions["pnl_pct"] = (
            positions["valor"] / positions["coste"].replace(0, np.nan) - 1.0
        )
        total_value = float(positions["valor"].sum())
        positions["peso"] = positions["valor"] / total_value if total_value else np.nan

        # -------------------------------------------------------------------
        # Resumen
        # -------------------------------------------------------------------
        total_cost = float(positions["coste"].sum())
        total_pnl = total_value - total_cost
        day_change = float((positions["valor"] * positions["ret_1d"]).sum())

        summary = st.columns(4)
        summary[0].metric("Valor actual", format_money(total_value))
        summary[1].metric(
            "Resultado", format_money(total_pnl),
            format_pct(total_pnl / total_cost if total_cost else None),
        )
        summary[2].metric(
            "Hoy", format_money(day_change),
            format_pct(day_change / total_value if total_value else None),
        )
        summary[3].metric("Posiciones", len(positions))

        if positions["currency"].nunique() > 1:
            st.warning(
                "Hay posiciones en varias divisas y los totales se suman sin "
                "convertir. Trata las cifras agregadas como orientativas.",
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
                "Valor": positions["valor"],
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
                "Valor": st.column_config.NumberColumn(format="%.2f"),
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
        st.subheader("Diagnostico de concentracion")

        diag_left, diag_right = st.columns(2)

        with diag_left:
            st.markdown("**Exposicion por sector**")
            known = positions[positions["gics_sector"].notna()]
            if known.empty:
                # Sin sector no hay diagnostico posible, y decir "Sin sector:
                # 100%" sonaria a una concentracion que en realidad no sabemos
                # si existe.
                st.info(
                    "Tus posiciones no tienen sector asignado todavia. Se "
                    "rellena con `make ingest`, que descarga la ficha de cada "
                    "valor.",
                    icon=":material/help:",
                )
            else:
                by_sector = (
                    known.groupby("gics_sector")["valor"].sum()
                    .sort_values(ascending=False)
                )
                covered = float(by_sector.sum())
                st.plotly_chart(
                    charts.weight_bars(
                        by_sector / covered, height=max(180, 46 * len(by_sector))
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
                        f"**{by_sector.index[0]}** pesa el {top_weight:.0%} de "
                        "la cartera. Una concentracion asi hace que el "
                        "resultado dependa de un solo sector mas que de tu "
                        "seleccion de valores.",
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
                    "technical_z": "tecnico",
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
                st.caption("Sin puntuacion factorial suficiente para el perfil.")

        # -------------------------------------------------------------------
        # Correlacion entre posiciones
        # -------------------------------------------------------------------
        if len(positions) >= 3:
            returns = da.get_returns_matrix(tuple(positions["ticker"]), days=250)
            if not returns.empty and returns.shape[1] >= 3:
                corr = returns.corr()
                upper = corr.to_numpy()[np.triu_indices(len(corr), k=1)]
                upper = upper[np.isfinite(upper)]
                if len(upper):
                    avg_corr = float(np.mean(upper))
                    st.divider()
                    st.subheader("Diversificacion real")
                    corr_cols = st.columns([1, 3])
                    corr_cols[0].metric("Correlacion media", f"{avg_corr:.2f}")
                    with corr_cols[1]:
                        if avg_corr > 0.7:
                            st.warning(
                                f"Tus posiciones se mueven casi al unisono "
                                f"(correlacion media {avg_corr:.2f}). Tener "
                                f"{len(positions)} valores no te diversifica si "
                                "todos suben y bajan a la vez: en la practica es "
                                "casi una sola apuesta.",
                                icon=":material/warning:",
                            )
                        elif avg_corr > 0.45:
                            st.info(
                                f"Correlacion media de {avg_corr:.2f}: "
                                "diversificacion moderada.",
                                icon=":material/info:",
                            )
                        else:
                            st.success(
                                f"Correlacion media de {avg_corr:.2f}: tus "
                                "posiciones se mueven de forma bastante "
                                "independiente.",
                                icon=":material/check:",
                            )

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
                "Cerrar posicion", options=list(labels),
                format_func=lambda i: labels[i], index=None,
                placeholder="Elige una posicion",
            )
            if to_close and st.button("Cerrar"):
                da.close_position(to_close)
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
                "Anadir un valor", options=options, index=None,
                placeholder="Busca un ticker", key="wl_ticker",
            )
            if new_ticker and st.button("Anadir a la watchlist", type="primary"):
                history = da.get_price_history(new_ticker, days=1)
                price = float(history.iloc[-1]["close"]) if not history.empty else None
                da.add_to_watchlist(new_ticker, price=price)
                st.rerun()

    if watchlist.empty:
        st.info(
            "Watchlist vacia. Anade valores desde aqui o con el boton "
            "**Guardar** de la pagina de oportunidades."
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
                "Dia": watchlist["ret_1d"] * 100,
                "Desde alta": watchlist["desde_alta"] * 100,
                "Percentil": watchlist["composite_pctile"] * 100,
                "Anadido": pd.to_datetime(watchlist["added_at"]).dt.strftime("%d/%m/%Y"),
            }
        )
        st.dataframe(
            view, hide_index=True, height=380,
            column_config={
                "Precio": st.column_config.NumberColumn(format="%.2f"),
                "Dia": st.column_config.NumberColumn(format="%+.2f%%"),
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
        summary[2].metric("Variacion media", format_pct(watchlist["desde_alta"].mean()))

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
            st.subheader("Vistazo rapido")
            chosen = st.selectbox(
                "Valor", options=watchlist["ticker"].tolist(), key="preview_ticker"
            )
            tv_symbol = da.get_tv_symbol(chosen)
            if tv_widgets.enabled() and tv_symbol:
                tv_widgets.mini_symbol_overview(tv_symbol, height=220)
            else:
                st.caption("Sin equivalencia en TradingView para este valor.")

    render_disclaimer()

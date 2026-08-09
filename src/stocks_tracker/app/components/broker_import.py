"""Importacion de la cartera desde un extracto del broker.

Vive en su propio modulo porque el flujo tiene tres pasos —leer, comprobar,
guardar— y el paso del medio es el que importa: **nunca se guarda nada sin
ensenar antes lo que se va a guardar**. Un extracto mal interpretado mete
precios equivocados en la cartera, y a partir de ahi todo lo que se calcula
encima esta mal sin que nada avise.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ...core import brokers
from .. import data_access as da

_HELP = """
**Ninguno de los dos brokers permite conectarse.** eToro no da API de lectura
de cartera a particulares y Trade Republic no tiene API publica. Existen
clientes no oficiales para Trade Republic, pero piden tu telefono y tu PIN, e
incumplen sus condiciones de uso: no se usan aqui.

Asi que se exporta y se importa. Es manual, pero no le entregas tus
credenciales a nadie.

**eToro** · Menu de la cuenta → *Historial* → icono de descarga → *Extracto de
cuenta*. Baja un XLSX; sube ese fichero tal cual.

**Trade Republic** · Perfil → *Documentos* / *Informes*. Si solo consigues PDF,
usa el boton de abajo para escribir las posiciones a mano en una tabla, que
para diez o quince valores va mas rapido que pelearse con el PDF.
"""


def _apply(result: brokers.ImportResult) -> None:
    n = da.replace_positions(
        result.positions, note=f"Importado de {result.broker}"
    )
    st.session_state.pop("broker_import", None)
    st.success(f"{n} posiciones importadas. Sustituyen a la cartera anterior.")
    st.rerun()


def render_broker_import() -> None:
    st.markdown(_HELP)

    uploaded = st.file_uploader(
        "Extracto del broker", type=["csv", "xlsx", "xls", "xlsm", "tsv"],
        key="broker_file",
        help="No sale de tu ordenador: se procesa en local y no se guarda.",
    )
    if uploaded is None:
        return

    frame = brokers.read_table(uploaded.getvalue(), uploaded.name)
    if frame.empty:
        st.error(
            "No se ha podido leer el fichero. Comprueba que sea el extracto de "
            "posiciones y no un PDF renombrado."
        )
        return

    columns = list(frame.columns)
    guess = brokers.guess_column_map(columns)
    broker = brokers.detect_broker(columns)
    st.caption(f"Detectado: **{broker}** · {len(frame)} filas · {len(columns)} columnas")

    # Correccion manual del emparejamiento. La deteccion automatica acierta casi
    # siempre, pero "casi" no basta cuando el error se traduce en un coste medio
    # equivocado en la cartera.
    with st.expander("Revisar que columna es cada cosa", expanded=not guess.get("qty")):
        labels = {
            "ticker": "Simbolo", "isin": "ISIN", "name": "Nombre",
            "qty": "Cantidad", "avg_cost": "Precio medio de compra",
            "currency": "Divisa",
        }
        mapping: dict[str, str] = {}
        cols = st.columns(3)
        for i, (field, label) in enumerate(labels.items()):
            with cols[i % 3]:
                options = ["(ninguna)", *columns]
                current = guess.get(field)
                chosen = st.selectbox(
                    label, options=options,
                    index=options.index(current) if current in options else 0,
                    key=f"map_{field}",
                )
                if chosen != "(ninguna)":
                    mapping[field] = chosen

    default_currency = st.selectbox(
        "Divisa si el fichero no la trae", options=["EUR", "USD", "GBP"],
        index=0 if broker != "eToro" else 1,
    )

    result = brokers.parse_positions(
        frame, column_map=mapping or None,
        default_currency=default_currency,
        known_tickers=set(da.all_tickers()),
    )

    for warning in result.warnings:
        st.warning(warning, icon=":material/warning:")

    if not result.unresolved.empty:
        st.markdown("**Sin equivalencia**")
        st.caption(
            "Estos valores no se pueden importar porque no sabemos a que "
            "ticker corresponden. Anade su ISIN en `config/isin_map.yaml` y "
            "vuelve a subir el fichero."
        )
        st.dataframe(
            result.unresolved[["isin", "name", "qty", "avg_cost"]].rename(
                columns={"isin": "ISIN", "name": "Nombre", "qty": "Titulos",
                         "avg_cost": "Precio medio"}
            ),
            hide_index=True,
            height=min(220, 42 + 35 * len(result.unresolved)),
        )

    if not result.ok:
        return

    st.markdown("**Esto es lo que se va a guardar**")
    st.caption(
        "Comprueba sobre todo los precios medios: es donde un fichero mal "
        "interpretado hace mas dano y menos ruido."
    )
    preview = result.positions.rename(
        columns={"ticker": "Valor", "name": "Nombre", "isin": "ISIN",
                 "qty": "Titulos", "avg_cost": "Precio medio",
                 "currency": "Divisa"}
    )
    st.dataframe(
        preview, hide_index=True, height=min(360, 42 + 35 * len(preview)),
        column_config={
            "Titulos": st.column_config.NumberColumn(format="%.4f"),
            "Precio medio": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    invested = float((result.positions["qty"] * result.positions["avg_cost"]).sum())
    currencies = sorted(set(result.positions["currency"]))
    st.metric(
        "Invertido segun el extracto",
        f"{invested:,.2f} {currencies[0] if len(currencies) == 1 else ''}".strip(),
        help="Suma de titulos por precio medio. Comparalo con lo que dice tu "
             "broker: si no cuadra, algo se ha interpretado mal.",
    )
    if len(currencies) > 1:
        st.caption(
            f":orange[Hay {len(currencies)} divisas ({', '.join(currencies)}) y "
            "el total las suma sin convertir.]"
        )

    existing = da.get_positions()
    if not existing.empty:
        st.warning(
            f"Tienes {len(existing)} posiciones registradas y **se van a "
            "reemplazar** por estas. Un extracto es una foto completa de tu "
            "cartera, asi que anadirlas duplicaria lo que ya tienes.",
            icon=":material/swap_horiz:",
        )

    confirm = st.checkbox("He comprobado los datos de arriba", key="import_confirm")
    if st.button("Importar", type="primary", disabled=not confirm):
        _apply(result)


def render_manual_table() -> None:
    """Alternativa para cuando el broker solo da PDF."""
    st.caption(
        "Escribe las posiciones a mano. Para diez o quince valores va mas "
        "rapido que pelearse con un PDF."
    )
    template = pd.DataFrame(
        {"Valor": pd.Series(dtype="str"), "Titulos": pd.Series(dtype="float"),
         "Precio medio": pd.Series(dtype="float"),
         "Divisa": pd.Series(dtype="str")}
    )
    edited = st.data_editor(
        template, num_rows="dynamic", hide_index=True, key="manual_positions",
        column_config={
            "Valor": st.column_config.SelectboxColumn(options=da.all_tickers()),
            "Titulos": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
            "Precio medio": st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
            "Divisa": st.column_config.SelectboxColumn(options=["EUR", "USD", "GBP"]),
        },
    )

    rows = edited.dropna(subset=["Valor", "Titulos", "Precio medio"])
    rows = rows[(rows["Titulos"] > 0) & (rows["Precio medio"] > 0)]
    if rows.empty:
        return

    st.caption(f"{len(rows)} posiciones listas.")
    if st.button("Guardar cartera", type="primary", key="save_manual"):
        frame = pd.DataFrame(
            {
                "ticker": rows["Valor"], "qty": rows["Titulos"],
                "avg_cost": rows["Precio medio"],
                "currency": rows["Divisa"].fillna("EUR"),
            }
        )
        n = da.replace_positions(frame, note="Introducido a mano")
        st.success(f"{n} posiciones guardadas.")
        st.rerun()

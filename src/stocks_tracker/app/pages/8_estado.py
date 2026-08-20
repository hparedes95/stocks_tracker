"""Página 8 — Estado de los datos.

Sirve para responder a "por que el dashboard muestra esto": que se descargo,
cuando, que fallo y que valores tienen datos viejos.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.core import consistency, quarantine
from stocks_tracker.core.db import connect, table_counts

st.title("Estado de los datos")

info = da.data_freshness()

cols = st.columns(4)
cols[0].metric(
    "Última cotización",
    info["last_price_date"].strftime("%d/%m/%Y") if info["last_price_date"] else "—",
)
cols[1].metric(
    "Última actualización",
    f"hace {info['hours_since_run']:.0f} h" if info["hours_since_run"] is not None else "—",
)
cols[2].metric("Descargas fallidas", info["failures"])
cols[3].metric("Instrumentos", len(da.instruments()))

if info["is_stale"]:
    st.warning(
        "Los datos llevan más tiempo del previsto sin actualizarse. "
        "Ejecuta `make ingest && make compute`.",
        icon=":material/schedule:",
    )

st.divider()
st.subheader("Contenido del almacen")
st.dataframe(table_counts(), hide_index=True, height=420)

st.subheader("Cobertura de símbolos de TradingView")
instruments = da.instruments()
if not instruments.empty:
    mapped = instruments["tv_symbol"].notna().sum()
    total = len(instruments)
    st.progress(mapped / total if total else 0.0,
                text=f"{mapped} de {total} instrumentos con equivalencia")
    unmapped = instruments[instruments["tv_symbol"].isna()]
    if not unmapped.empty:
        st.caption(
            "Estos valores usan nuestro propio gráfico en lugar del widget. "
            "No es un fallo: es la degradación prevista."
        )
        st.dataframe(
            unmapped[["ticker", "name", "asset_class"]],
            hide_index=True, height=200,
        )

st.divider()
st.subheader("Calidad de los datos por universo")
st.caption(
    "El score penaliza la falta de datos, pero esa penalización no se ve. "
    "Aquí si: un universo con cobertura baja compite en desventaja, y conviene "
    "saberlo antes de extrañarse de que apenas aparezcan sus valores."
)
coverage = da.coverage_by_universe()
if coverage.empty:
    st.caption("Sin universos registrados.")
else:
    view = coverage.copy()
    view["cobertura_media"] = view["cobertura_media"].fillna(0) * 100
    view["puntuables"] = view["puntuables"].astype(int)
    view = view[
        ["universe", "instrumentos", "puntuables", "con_fundamentales",
         "cobertura_media", "sin_sector", "sin_precio"]
    ]
    st.dataframe(
        view.rename(
            columns={
                "universe": "Universo", "instrumentos": "Instrumentos",
                "puntuables": "Puntuables",
                "con_fundamentales": "Con fundamentales",
                "cobertura_media": "Cobertura media",
                "sin_sector": "Sin sector", "sin_precio": "Sin precio hoy",
            }
        ),
        hide_index=True, height=min(320, 42 + 35 * len(view)),
        column_config={
            "Puntuables": st.column_config.NumberColumn(
                help="Acciones y ETF. Solo estos entran en el ranking.",
            ),
            "Cobertura media": st.column_config.ProgressColumn(
                min_value=0.0, max_value=100.0, format="%.0f%%",
                help="Fracción media de campos fundamentales disponibles.",
            ),
        },
    )

    # Un universo de indices sin fundamentales no esta en desventaja: es que no
    # se puntua. Avisar de el seria una falsa alarma cada vez que se abre la
    # pagina, y las alarmas que siempre saltan dejan de leerse.
    scoreable = coverage[coverage["puntuables"] > 0]
    if not scoreable.empty:
        worst = scoreable.iloc[0]
        if pd.notna(worst["cobertura_media"]) and float(worst["cobertura_media"]) < 0.5:
            st.warning(
                f"**{worst['universe']}** tiene una cobertura media del "
                f"{float(worst['cobertura_media']):.0%}. Sus valores apareceran "
                "poco en el ranking, y no porque sean peores: es que no hay "
                "datos con los que puntuarlos.",
                icon=":material/warning:",
            )

st.subheader("Procedencia de los precios")
sources = da.price_sources()
if not sources.empty:
    st.dataframe(
        sources.rename(
            columns={"fuente": "Fuente", "instrumentos": "Instrumentos",
                     "filas": "Filas", "hasta": "Hasta"}
        ),
        hide_index=True, height=min(200, 42 + 35 * len(sources)),
    )

mixed = da.mixed_source_series()
if not mixed.empty:
    st.warning(
        f"{len(mixed)} series mezclan varias fuentes de precios. Yahoo ajusta "
        "el cierre por dividendos y Stooq no, así que en el día del relevo hay "
        "un salto que no es un movimiento real del mercado. Reconstruyelas con "
        "`make repair`.",
        icon=":material/call_split:",
    )
    st.dataframe(
        mixed.rename(columns={"ticker": "Valor", "fuentes": "Fuentes",
                              "n_fuentes": "Cuántas"}),
        hide_index=True, height=min(240, 42 + 35 * len(mixed)),
    )
else:
    st.caption(
        ":grey[Ninguna serie mezcla fuentes: todas tienen la misma convención "
        "de ajuste.]"
    )

st.divider()
st.subheader("Fundamentales que se contradicen")
st.caption(
    "Los ratios vienen de un **único proveedor gratuito**, y un proveedor "
    "gratuito se equivoca. Ninguno de esos errores da un fallo: entran en el "
    "ranking, suben al valor a los primeros puestos y ahí se quedan con la "
    "misma pinta que los datos buenos. Aquí se contrastan de tres formas: "
    "contra nuestros propios precios, contra las identidades contables que "
    "tienen que cumplir entre si, y contra la descarga anterior."
)

sospechosos = da.review_all_fundamentals()
if sospechosos.empty:
    st.success(
        "Ningún valor tiene fundamentales que se contradigan. No garantiza que "
        "sean correctos: garantiza que no se ha encontrado nada que los "
        "contradiga, que es otra cosa.",
        icon=":material/check_circle:",
    )
else:
    rotos = int((sospechosos["rotos"] > 0).sum())
    resumen_cols = st.columns(2)
    resumen_cols[0].metric("Valores con algun dato imposible", rotos)
    resumen_cols[1].metric("Valores con datos que no cuadran",
                           len(sospechosos) - rotos)
    st.dataframe(
        sospechosos[["ticker", "rotos", "avisos", "campos"]],
        hide_index=True, height=min(420, 42 + 35 * len(sospechosos)),
        column_config={
            "ticker": "Valor",
            "rotos": st.column_config.NumberColumn("Imposibles", format="%d"),
            "avisos": st.column_config.NumberColumn("Avisos", format="%d"),
            "campos": st.column_config.TextColumn("Campos", width="large"),
        },
    )
    # Sesenta y cuatro filas se leen como sesenta y cuatro problemas. Si en las
    # sesenta y cuatro falla el mismo campo, es UNO: el proveedor.
    repetidos = consistency.campos_repetidos(
        str(c).split(", ") for c in sospechosos["campos"]
    )
    sistematicos = [(campo, n) for campo, n in repetidos
                    if n >= len(sospechosos) * consistency.CAMPO_SISTEMATICO]
    if sistematicos:
        st.warning(
            "Un mismo campo falla en casi todo el universo, así que el problema "
            "no son estas empresas: es el proveedor. "
            + " · ".join(f"**{campo}** en {n} de {len(sospechosos)}"
                         for campo, n in sistematicos)
            + ". Lo típico es que hayan cambiado la unidad del campo (publicar "
              "3,5 donde antes publicaban 0,035) o que lo calculen mal para un "
              "tipo de empresa entero, como los bancos.",
            icon=":material/lan:",
        )

    st.caption(
        "**Los datos imposibles no entran en el ranking.** Se vacían antes de "
        "puntuar, por dos vías: los que se salen del rango de su factor los "
        "descarta el propio cálculo desde siempre, y los que incumplen una "
        "identidad contable —un margen neto por encima del bruto— se descartan "
        "aquí, porque ningún rango puede cazarlos. Ese valor puntúa con un dato "
        "menos, nunca con un dato falso. Lo que sí sigue entrando son los que "
        "solo *no cuadran* entre si, porque ahí no se sabe cual de los dos "
        "falla y elegir uno sería peor que avisar de los dos. El detalle de "
        "cada uno está en la pestaña Fundamentales de su ficha."
    )

st.divider()
st.subheader("Calidad de los datos")
st.caption(
    "Comprobaciones que se ejecutan **antes** de calcular nada. La que más "
    "importa detecta que el proveedor haya reescrito precios del pasado: no da "
    "ningún error, no se nota en pantalla, y deja cualquier backtest anterior "
    "sin poder reproducirse."
)
# Se toma la ULTIMA EJECUCION DE CADA COMPROBACIÓN, no el último lote escrito.
# Dos motivos, y los dos daban falsos "todo bien":
#   1. La ingesta escribe un lote por grupo de fechas, así que filtrar por
#      `MAX(checked_at)` global dejaba fuera los problemas de los grupos
#      anteriores de la misma ejecución.
#   2. El cálculo no puede comprobar `precios_revisados` —para eso hay que
#      comparar antes de sobrescribir—, así que su registro más reciente tiene
#      que seguir siendo el de la ingesta.
ULTIMA_DE_CADA_UNA = """
    SELECT check_name, MAX(run_id) AS run_id
    FROM data_quality d
    WHERE checked_at = (SELECT MAX(x.checked_at) FROM data_quality x
                        WHERE x.check_name = d.check_name)
    GROUP BY check_name
"""

with connect(read_only=True) as conn:
    ultima = conn.execute("SELECT MAX(checked_at) FROM data_quality").fetchone()
    calidad = conn.execute(
        f"""
        SELECT d.check_name, d.ticker, d.severity, d.detail, d.checked_at
        FROM data_quality d
        JOIN ({ULTIMA_DE_CADA_UNA}) u
          ON u.check_name = d.check_name AND u.run_id = d.run_id
        WHERE NOT d.passed
        ORDER BY CASE d.severity WHEN 'bloquea' THEN 0 WHEN 'aviso' THEN 1 ELSE 2 END
        LIMIT 60
        """
    ).fetchdf()
    pasadas = conn.execute(
        f"""
        SELECT COUNT(DISTINCT d.check_name) FROM data_quality d
        JOIN ({ULTIMA_DE_CADA_UNA}) u
          ON u.check_name = d.check_name AND u.run_id = d.run_id
        WHERE d.passed
        """
    ).fetchone()

if ultima is None or ultima[0] is None:
    st.caption(
        "Todavía no se ha comprobado nada. Se comprueba solo al descargar "
        "datos o al calcular."
    )
elif calidad.empty:
    st.success(
        f"Las {int(pasadas[0])} comprobaciones pasaron el "
        f"{pd.to_datetime(ultima[0]).strftime('%d/%m/%Y a las %H:%M')}. "
        "Que pasen no quiere decir que los datos sean buenos: quiere decir que "
        "no tienen ninguna de las formas que sabemos reconocer como rotas.",
        icon=":material/check_circle:",
    )
else:
    graves = calidad[calidad["severity"] == "bloquea"]
    if not graves.empty:
        st.error(
            f"**{len(graves)} problemas graves.** El cálculo no se ejecutará "
            "hasta que se resuelvan: hacerlo daría números con buena pinta y "
            "sin sentido.",
            icon=":material/error:",
        )
    st.dataframe(
        calidad[["check_name", "ticker", "severity", "detail"]].rename(
            columns={"check_name": "Comprobación", "ticker": "Valor",
                     "severity": "Gravedad", "detail": "Qué pasa"}
        ),
        hide_index=True, height=min(320, 42 + 35 * len(calidad)),
        column_config={"Qué pasa": st.column_config.TextColumn(width="large")},
    )

# ---------------------------------------------------------------------------
# Barras apartadas
# ---------------------------------------------------------------------------
with connect(read_only=True) as conn:
    apartadas = quarantine.resumen(conn)

if not apartadas.empty:
    st.markdown("**Barras apartadas del cálculo**")
    st.caption(
        "Sesiones sueltas cuyo OHLC no puede ser cierto: un cierre por encima "
        "del máximo del día, un máximo por debajo del mínimo. No se borran ni "
        "se arreglan — nadie sabe cual de los cuatro números es el equivocado —, "
        "pero su máximo, mínimo y apertura se ignoran al calcular. Todo lo que "
        "use el rango de esos días (el ATR, sobre todo) sale vacío ahí en vez "
        "de salir con un número inventado."
    )
    vista = apartadas.copy()
    for col in ("primera", "ultima"):
        vista[col] = pd.to_datetime(vista[col]).dt.strftime("%d/%m/%Y")
    st.dataframe(
        vista.rename(columns={"ticker": "Valor", "barras": "Sesiones",
                              "primera": "Desde", "ultima": "Hasta",
                              "motivo": "Qué le pasa"}),
        hide_index=True, height=min(280, 42 + 35 * len(vista)),
        column_config={"Qué le pasa": st.column_config.TextColumn(width="large")},
    )

st.divider()
st.subheader("Registro de ingesta")
with connect(read_only=True) as conn:
    log = conn.execute(
        """
        SELECT started_at, task, target, status, rows_written, requests_used, error
        FROM ingest_log ORDER BY started_at DESC LIMIT 60
        """
    ).fetchdf()

if log.empty:
    st.caption("Sin ejecuciones registradas.")
else:
    log["started_at"] = pd.to_datetime(log["started_at"]).dt.strftime("%d/%m %H:%M")
    st.dataframe(
        log.rename(
            columns={
                "started_at": "Cuando", "task": "Tarea", "target": "Objetivo",
                "status": "Estado", "rows_written": "Filas",
                "requests_used": "Peticiones", "error": "Detalle",
            }
        ),
        hide_index=True, height=420,
    )

st.divider()
st.subheader("Como actualizar")
st.code(
    "make ingest           # datos reales (Yahoo, con Stooq de respaldo)\n"
    "make ingest-demo      # datos sintéticos, sin red\n"
    "make compute          # indicadores, señales, factores y amplitud\n"
    "make compute-presets  # puntua con todos los estilos de inversión\n"
    "make repair           # reconstruye series con fuentes mezcladas",
    language="bash",
)
st.caption(
    "La descarga se hace por lotes y sin hilos a propósito: la concurrencia es "
    "lo que dispara el bloqueo de Yahoo. Un universo de 750 valores son unas 16 "
    "peticiones, no 750. Si Yahoo deja de responder para algunos valores, se "
    "le piden a Stooq: el relevo queda anotado en el registro de arriba."
)

render_disclaimer()

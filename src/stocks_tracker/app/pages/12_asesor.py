"""Página 12 — Qué haría hoy.

La página que el resto del programa existe para sostener. Todo lo demás
—ingesta, calidad, factores, deterioro— acaba aquí, en una decisión por valor.

EL ORDEN NO ES CASUAL

Primero tu cartera, después las compras nuevas. Lo que ya tienes puede costarte
dinero hoy; lo que no tienes puede esperar a mañana. Poner las compras arriba
—que es lo que apetece leer— es como se acaba con una cartera llena de aciertos
viejos sin vender.

El análisis actual se muestra aunque no haya propuestas operables. El marcador
evalúa solo las propuestas guardadas, nunca estas lecturas técnicas al vuelo.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components import health_panel
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.app.components.design import page_header, panel
from stocks_tracker.app.components.holding_card import render_holding_card, research_summary
from stocks_tracker.app.components.theme import format_money
from stocks_tracker.app.components.valuation_panel import render_valuation_issues
from stocks_tracker.app.research_actions import refresh_research
from stocks_tracker.app.runtime_reads import get_holding_analysis
from stocks_tracker.core.advice import ETIQUETA, Conviccion, Veredicto
from stocks_tracker.core.advice_store import resumen_honesto
from stocks_tracker.core.db import connect
from stocks_tracker.core.investor_profile import load as load_profile
from stocks_tracker.core.investor_profile import save as save_profile
from stocks_tracker.core.market_bundle import frozen_market

page_header("Asesor de acciones", section="Análisis y decisiones")
st.caption(
    "Lo que **tus reglas** implican hoy, no lo que va a hacer el mercado. "
    "Propuestas explicables para tu decisión manual. Consulta también Predicción "
    "y escenarios; todavía no hay una ventaja predictiva demostrada."
)

with connect(read_only=True) as conn:
    profile = load_profile(conn)
with st.expander("Tu horizonte, costes y límites", expanded=False):
    st.caption("Son supuestos personales, no tarifas verificadas de tu bróker ni garantías de pérdida máxima.")
    months = st.selectbox("Horizonte de la recomendación (meses)", [1, 3, 6],
                           index=[1, 3, 6].index(profile['horizon_months']))
    cost_pct = st.number_input("Coste estimado total de comprar y vender (%)", min_value=.01,
                               max_value=20.0, value=float(profile['roundtrip_pct']), step=.1)
    loss_pct = st.number_input("Caída de cartera que quieres usar como límite de riesgo (%)",
                               min_value=1., max_value=60., value=float(profile['max_loss_pct']))
    cluster_pct = st.number_input("Máxima exposición a posiciones muy correlacionadas (%)",
                                  min_value=1., max_value=100., value=float(profile['max_cluster_pct']))
    confirm_profile = st.checkbox("He revisado estos supuestos", value=profile['confirmed'])
    if st.button("Guardar perfil del asesor"):
        with connect() as conn:
            save_profile(conn, {'horizon_months': months, 'roundtrip_pct': cost_pct,
                               'max_loss_pct': loss_pct, 'max_cluster_pct': cluster_pct,
                               'confirmed': confirm_profile})
            profile = load_profile(conn)
        st.cache_data.clear()
        st.success("Perfil guardado. Recalcula el asesor para aplicarlo.")


# Controles explícitos; la sincronización SnapTrade puede recalcular también.

with st.expander("Actualizar datos y recomendaciones", expanded=False):
    from stocks_tracker.compute.run_advice import _cartera
    from stocks_tracker.core import snaptrade_portfolio as snap

    with connect(read_only=True) as conn:
        live = snap.active(conn)
        live_issue = snap.readiness(conn)
        synced_cash = snap.cash_eur(conn) if live else None
        current_positions = _cartera(conn)[0]
        from stocks_tracker.core import fx
        session = conn.execute('SELECT date FROM current_session').fetchone()
        valuation_rates = fx.tipos(conn, session[0] if session else None)
        partial_issues = snap.limitations(current_positions)
    if live:
        cash = float(synced_cash) if pd.notna(synced_cash) and synced_cash >= 0 else 0.0
        st.metric('Efectivo sincronizado (EUR)', format_money(synced_cash, 'EUR'))
        st.caption('Cartera y efectivo de SnapTrade, sin sumar los CSV. El asesor aplica sus controles de datos y riesgo. '
                   'Sin fechas de compra verificadas no evalúa deterioro desde la compra ni rentabilidad realizada.')
        if live_issue:
            st.warning(live_issue)
        if partial_issues:
            st.warning('Valoración de cartera parcial: ' + ' · '.join(partial_issues))
            st.caption('Esto limita el cálculo de pesos e importes, no el análisis de los subyacentes '
                       'identificados que aparece debajo. Ninguna posición se elimina del control de riesgo.')
    else:
        cash = st.number_input("Efectivo disponible para esta consulta (EUR)",
                               min_value=0.0, value=0.0, step=100.0)
        st.caption("El efectivo no declarado se considera cero. Los botones no compran ni venden.")
actions = st.columns(2)
download = actions[0].button("Actualizar datos y análisis", type="primary", width='stretch')
recalculate = actions[1].button("Recalcular asesor con los datos guardados", disabled=bool(live_issue), width='stretch')
if live_issue:
    st.warning(live_issue)
if partial_issues:
    st.warning('Valoración de cartera parcial; los análisis identificados siguen disponibles. '
               'Consulta Actualizar datos y recomendaciones para ver las posiciones afectadas.')
render_valuation_issues(current_positions, valuation_rates)
if frozen_market():
    st.info('Estás usando una copia de mercado congelada. Estos botones no descargan precios nuevos '
            'en este modo. Para actualizarlos, confirma que abandonas la copia congelada en Datos compartidos.')
if download or recalculate:
    with st.spinner("Preparando el análisis…"):
        try:
            refresh_research(cash=cash, download=download)
        except Exception as exc:  # noqa: BLE001 — frontera de acción manual en la interfaz
            st.error(f"No se ha completado el análisis: {exc}. "
                     "Las recomendaciones anteriores NO se han actualizado.")
        else:
            st.cache_data.clear()
            st.success("Análisis recalculado. Las limitaciones de cada posición se muestran debajo; "
                       "si faltan datos o efectivo puede no haber propuestas de compra.")

st.subheader('Análisis actual de tus posiciones')
st.caption('Lectura de mercado independiente de la fecha de compra. Las orientaciones técnicas '
           'no son órdenes ni una ventaja predictiva validada. Los importes operables se evalúan por separado.')
holding_reports = get_holding_analysis(horizon={1:21, 3:63, 6:126}[profile['horizon_months']])
if not holding_reports:
    st.info('No hay posiciones abiertas para analizar. Conecta tu cartera o añade tus posiciones.')
if holding_reports:
    research_summary(holding_reports, horizon=profile['horizon_months'])
    with st.container(key='holdings-grid'):
        for start in range(0, len(holding_reports), 2):
            columns = st.columns(2)
            for column, holding in zip(columns, holding_reports[start:start + 2], strict=False):
                with column:
                    prices = da.get_price_history(holding['ticker'], days=253) if holding['metrics'] else None
                    render_holding_card(holding, prices=prices)

st.warning("Herramienta experimental: la fuerza de una señal no es una probabilidad "
           "de acertar. El marcador y los estudios históricos no demuestran "
           "rentabilidad futura ni rentabilidad neta de costes.")

with st.expander("Estado de validación de los modelos"):
    st.write("Una prueba histórica favorable no activa por sí sola una compra. Al recalcular con el perfil "
             "confirmado se fija un protocolo para cada valor: la evaluación final empieza al día siguiente "
             "y necesita resultados futuros vencidos. Un protocolo refutado no vuelve a intentarse. "
             "Cambiar el motor, el horizonte o el coste supuesto crea otro protocolo y conserva el anterior. "
             "Los protocolos viajan en la copia de mercado para que una instalación nueva no empiece desde cero.")
    st.caption("Los avisos sobre deterioro o concentración de tu cartera no esperan a que un modelo predictivo sea confirmado.")
    with connect(read_only=True) as conn:
        protocols = conn.execute("SELECT json_extract_string(protocol, '$.ticker') AS ticker, "
                                 "created_at, holdout_start, status FROM prediction_plans ORDER BY created_at DESC LIMIT 100").fetchdf()
    st.dataframe(protocols, hide_index=True)

with st.expander("Riesgo conjunto de tu cartera"):
    from stocks_tracker.compute.run_advice import _cartera
    from stocks_tracker.core.portfolio_risk import analyse, returns_in_eur
    with connect(read_only=True) as conn:
        positions, _, _ = _cartera(conn)
        session = conn.execute("SELECT date FROM current_session").fetchone()
        if not positions.empty and session:
            values = positions.groupby('ticker')['valor_eur'].sum(min_count=1).to_dict()
            currencies = positions.drop_duplicates('ticker').set_index('ticker')['currency'].to_dict()
            returns = returns_in_eur(conn, list(values), as_of=session[0])
            report = analyse(returns, values, cash=cash, currencies=currencies)
            if report.get('reason'):
                st.warning(report['reason'])
            if 'equity' in report:
                st.write("Exposición por divisa (incluye efectivo EUR en el total):", report['currency_exposure'])
                st.metric("Escenario: todas las posiciones caen un 30 %", f"{report['shock_equity_30_eur']:,.0f} EUR")
                st.metric("Escenario: divisas extranjeras pierden un 10 % frente al EUR", f"{report['shock_foreign_fx_10_eur']:,.0f} EUR")
                if 'correlations' in report:
                    st.dataframe(report['correlations'])
                    st.write(f"Media del peor 5 % de días históricos: {report['mean_worst_5pct_day_eur']:,.0f} EUR")
                st.caption("Escenarios hipotéticos, no predicciones ni pérdidas máximas. "
                           "El histórico supone los pesos actuales y no reproduce tu rentabilidad realizada.")
        else:
            st.info("Añade posiciones para medir el riesgo conjunto.")

COLOR = {
    Veredicto.VENDER: "🔴", Veredicto.REDUCIR: "🟠", Veredicto.COMPRAR: "🟢",
    Veredicto.AMPLIAR: "🟢", Veredicto.MANTENER: "⚪", Veredicto.VETADA: "⛔",
    Veredicto.SIN_OPINION: "❔",
}
FUERZA = {Conviccion.ALTA: "coincidencia alta con las reglas",
          Conviccion.MEDIA: "coincidencia media con las reglas",
          Conviccion.BAJA: "coincidencia baja con las reglas"}


# ===========================================================================
# 1. El marcador. Primero, y sin adornos
# ===========================================================================
st.subheader("¿Acierta este asesor?")

puntuadas, resultados = da.get_advice_scoreboard()
st.info(resumen_honesto(resultados), icon=":material/scoreboard:")

if resultados:
    filas = [
        {
            "Veredicto": r.veredicto,
            "Puntuadas": r.puntuadas,
            "Batieron al índice": r.aciertos,
            # `None` y no un porcentaje: con pocos datos, un numero con pinta de
            # estadistica invita a confiar antes de tiempo.
            "Tasa": (r.tasa * 100) if r.tasa is not None else None,
            "Exceso medio (pp)": r.exceso_medio,
            "Ventaja neta estimada (pp)": r.neto_medio,
        }
        for r in resultados
    ]
    st.dataframe(
        pd.DataFrame(filas), hide_index=True,
        column_config={
            "Tasa": st.column_config.NumberColumn(
                format="%.0f%%",
                help="Solo aparece cuando hay bastantes datos para que "
                     "signifique algo."),
            "Exceso medio (pp)": st.column_config.NumberColumn(format="%+.1f"),
        },
    )

# La OTRA mitad de la evidencia, y va separada a proposito.
#
# El marcador de arriba mide hacia delante y tarda meses. Esto mide hacia atras
# y esta disponible hoy, pero SOLO sirve para la parte del ranking que sale de
# precios. Juntar las dos cifras en un mismo bloque las haria parecer la misma
# clase de prueba, y no lo son: una es el asesor entero midiendose en real, la
# otra es una regla suelta medida sobre el pasado.
with st.expander("¿Y ha funcionado esto en el pasado?"):
    st.markdown(da.get_advice_calibration())
    st.caption(
        "Mide **una sola cosa**: si el corte del percentil 90 separó a los que "
        "luego lo hicieron mejor que el índice. No simula comprar ni vender. "
        "Un backtest completo tiene decenas de decisiones y cada una es una "
        "oportunidad de ajustar hasta que salga bonito; esto tiene una."
    )

with st.expander("Qué mide y qué NO mide este marcador"):
    st.markdown(
        "- **Se puntúa contra el índice, no contra cero.** Comprar algo que "
        "sube un 3 % mientras el mercado sube un 8 % no es un acierto: es "
        "haber elegido peor que no elegir.\n"
        "- **Solo cuenta lo accionable.** Los `mantener` no entran: nadie tomó "
        "esa decisión ni pagó comisión por ella.\n"
        "- **Empieza vacío y tarda meses.** No hay forma honesta de rellenarlo "
        "con historia: media recomendación se apoya en fundamentales de los "
        "que no existe serie punto-en-el-tiempo, y puntuar 2019 con los "
        "balances de hoy es mirar el futuro.\n"
        "- **Mientras esté vacío, este asesor no está validado.** Es coherente "
        "y consistente, que no es lo mismo que acertado."
    )

st.divider()

# ===========================================================================
# 2. Los consejos GUARDADOS de la ultima sesion
# ===========================================================================
# La pantalla LEE lo que escribio `run_advice`; no vuelve a calcular.
#
# Si recalculara al vuelo y el marcador puntuara lo guardado, los dos podrian
# separarse —basta con que cambie un precio entre una cosa y otra— y el marcador
# estaria puntuando consejos que nadie llego a ver. Que lo que se ve y lo que se
# puntua sean lo mismo no es un detalle: es la condicion para que el marcador
# signifique algo.
guardadas = da.get_advice()

with connect(read_only=True) as conn:
    diagnostics = conn.execute("SELECT ticker AS Valor, reason AS Motivo FROM advisor_diagnostics ORDER BY ticker").fetchdf()
if not diagnostics.empty:
    with st.expander("Por qué no se propone operar en otros valores", expanded=bool(partial_issues)):
        st.dataframe(diagnostics, hide_index=True)

if guardadas.empty:
    st.info(
        "No hay propuestas operables guardadas. El análisis actual de tus posiciones aparece arriba; "
        "no depende de que se autorice una compra. Los controles de evidencia, efectivo y riesgo "
        "pueden impedir proponer importes. Usa los botones superiores para actualizar los datos o recalcular.\n\n"
        "Con SnapTrade activo también se intenta recalcular tras una sincronización "
        "automática correcta. Cada cálculo queda registrado; no implica ejecutar operaciones.",
        icon=":material/hourglass_empty:",
    )
    st.stop()


st.caption(f"Recomendaciones guardadas con fecha {pd.to_datetime(guardadas['fecha']).max():%d/%m/%Y}.")
st.info("Son propuestas, no operaciones realizadas. Revisa fecha, precios y efectivo "
        "antes de decidir. El historial anterior se conserva, pero no se presenta como actual.")

def _lista(campo: str, fila) -> list[str]:
    try:
        return json.loads(fila[campo] or "[]")
    except (TypeError, ValueError):
        return []


def _tarjeta(fila) -> None:
    """Una recomendación entera: veredicto, porqué, y qué la desmentiría.

    Las tres cosas juntas y siempre. Un consejo sin motivos no se puede
    discutir, y uno sin condición de error no se puede revisar dentro de seis
    meses: es lo que separa una afirmación comprobable de un horóscopo.
    """
    veredicto = Veredicto(fila["veredicto"])
    conviccion = Conviccion(fila["conviccion"])
    with panel(f"recommendation-{fila['ticker']}"):
        st.markdown(
            f"{COLOR[veredicto]} **{fila['ticker']}** — {ETIQUETA[veredicto]}"
            f"  \n:gray[{FUERZA[conviccion]}]"
        )
        for motivo in _lista("motivos", fila):
            st.markdown(f"- {motivo}")
        try:
            assessment = json.loads(fila.get('assessment') or '{}')
        except (TypeError, ValueError):
            assessment = {}
        with st.expander("Datos, evidencia y encaje personal"):
            for name, title in (('datos', 'Calidad del dato'), ('evidencia', 'Evidencia predictiva'), ('encaje', 'Encaje personal')):
                st.write(f"{title}: {assessment.get(name, 'Sin evaluación separada registrada; revisa los motivos y los datos de la posición.')}")
            st.caption(f"Versión: {fila.get('model_version', 'heredada')} · "
                       f"Sesión: {fila['fecha']} · emisión: {fila.get('emitida_at', 'no registrada')}.")

        if fila["importe_eur"] and fila["importe_eur"] > 0:
            cols = st.columns(3)
            cols[0].metric("Importe", format_money(fila["importe_eur"], "EUR"))
            cols[1].metric("Stop", f"{fila['stop']:.2f}" if fila["stop"] else "—")
            cols[2].metric("Pérdida estimada al stop",
                           format_money(fila["riesgo_eur"], "EUR")
                           if fila["riesgo_eur"] else "—")
        elif fila["titulos"]:
            st.metric("Títulos a soltar", f"{fila['titulos']:.4f}")

        if fila["aviso_fiscal"]:
            st.warning(fila["aviso_fiscal"], icon=":material/gavel:")

        desmiente = _lista("desmentiria", fila)
        if desmiente:
            with st.expander("Qué haría que esto fuera un error"):
                for d in desmiente:
                    st.markdown(f"- {d}")


cartera = set(da.get_positions()["ticker"]) if not da.get_positions().empty else set()
mias = guardadas[guardadas["ticker"].isin(cartera)]
nuevas = guardadas[~guardadas["ticker"].isin(cartera)]

fecha = pd.Timestamp(guardadas["fecha"].iloc[0])
st.caption(f"Consejos de la sesión del {fecha:%d/%m/%Y}.")

st.divider()
st.subheader("Tu cartera")

# EL SILENCIO HAY QUE CONTARLO, Y AQUI MAS QUE EN NINGUN SITIO
#
# Solo se guarda lo accionable, asi que una cartera entera sobre la que el
# programa NO HA PODIDO OPINAR se veia exactamente igual que una cartera sana:
# "nada que hacer hoy". Es el mismo verde tranquilizador por falta de datos que
# `deterioration.py` existe para evitar, un piso mas arriba.
#
# El caso llega del uso real: con las fechas de compra puestas al importar el
# extracto, el diagnostico compara hoy contra hoy y no puede encontrar nada.
_mudas = []
try:
    _salud = da.get_position_health()
    if not _salud.empty:
        _mudas = [d for d in health_panel.diagnosticos(_salud) if d.espejo]
except Exception:  # noqa: BLE001 - una pantalla no se cae por un aviso
    _mudas = []

if _mudas:
    st.warning(
        f"**No he podido opinar sobre {len(_mudas)} de tus posiciones** "
        f"({', '.join(d.ticker for d in _mudas)}). La fecha de compra que "
        "tengo de ellas es la de hoy —la que se pone al importar el extracto, "
        "porque el extracto no la trae—, así que compararía los datos de hoy "
        "con los datos de hoy y no encontraría nada nunca. Ponles la fecha "
        "real en **Cartera y watchlist** y vuelven a juzgarse.",
        icon=":material/event_busy:",
    )

if mias.empty:
    st.success(
        "Nada que hacer hoy con lo que tienes. Los `mantener` no se guardan: "
        "solo aparecen aquí las posiciones que piden una acción."
        + (" Ojo: esto NO incluye las posiciones de arriba, sobre las que no "
           "he podido opinar." if _mudas else ""),
        icon=":material/check_circle:",
    )
else:
    for _, fila in mias.iterrows():
        _tarjeta(fila)

st.divider()
st.subheader("Compras nuevas")
if nuevas.empty:
    st.info(
        "Ningún candidato pasa hoy tus filtros. Es lo normal la mayoría de los "
        "días: con siete plazas y el percentil 90 como listón, no hay una "
        "compra cada mañana.",
        icon=":material/inbox:",
    )
else:
    for _, fila in nuevas.iterrows():
        _tarjeta(fila)

# ---------------------------------------------------------------------------
# El encuadre, al final y sin suavizar
# ---------------------------------------------------------------------------
st.divider()
huella = guardadas["universe_hash"].iloc[0]
if huella:
    st.caption(
        f"Calculado contra el universo con huella `{huella}`. El ranking es "
        "relativo: con otro universo, estos consejos cambian sin que las "
        "empresas hayan hecho nada."
    )
render_disclaimer()

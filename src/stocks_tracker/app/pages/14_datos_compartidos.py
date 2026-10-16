"""Intercambio privado y explícito: no hay nube ni credenciales que configurar."""
import streamlit as st

from stocks_tracker.app.components.design import page_header
from stocks_tracker.core.db import connect
from stocks_tracker.core.market_bundle import (
    export_bundle,
    import_bundle,
    inspect_bundle,
    preview,
    state,
    unlock,
)
from stocks_tracker.core.market_recovery import restore_point

page_header("Base de mercado compartida", section="Sistema")
st.write("Exporta en el PC con el histórico completo e importa ese mismo archivo en el otro. "
         "La transferencia es manual; no se envía nada a Internet.")
st.info("Incluye histórico, fundamentales, universo, resultados y protocolos de validación del modelo. "
        "NO incluye cartera, efectivo, diario, watchlist, recomendaciones personales ni claves. "
        "Usa las copias solo entre tus equipos y conforme a las condiciones de las fuentes.")
with connect(read_only=True) as conn:
    current = state(conn)
if current.get("frozen"):
    st.success(f"Copia compartida activa · {current['bundle_id'][:16]} · sesión {current['as_of']}")
    st.caption("Las descargas y recálculos de mercado están detenidos para conservar la misma base. "
               "Los consejos de cartera pueden diferir si cambian tus posiciones o efectivo. "
               "Los widgets externos en directo no forman parte de esta copia.")
else:
    st.warning("Modo local: los datos de este ordenador pueden diferir de los de otro.")

st.subheader("1. Exportar desde el equipo de origen")
if st.button("Actualizar mercado y preparar copia", disabled=bool(current.get('frozen'))):
    try:
        from stocks_tracker.app.research_actions import refresh_research
        with st.spinner("Descargando, analizando y preparando la copia…"):
            refresh_research(cash=0, download=True)
            with connect(read_only=True) as conn:
                st.session_state['market_export'] = export_bundle(conn)
        st.cache_data.clear()
    except Exception as exc:  # noqa: BLE001 — acción explícita de interfaz
        st.error(f"No se ha preparado una copia actualizada: {exc}")
if current.get('frozen'):
    st.caption("Para actualizar, confirma abajo que quieres abandonar la copia congelada.")
if st.button("Preparar archivo de mercado"):
    try:
        with st.spinner("Preparando y comprobando la copia…"), connect(read_only=True) as conn:
            st.session_state['market_export'] = export_bundle(conn)
    except Exception as exc:  # noqa: BLE001 — frontera de exportación solicitada en UI
        st.error(f"No se ha podido exportar: {exc}")
if st.session_state.get('market_export'):
    st.download_button("Descargar base de mercado", st.session_state['market_export'],
                       file_name="stocks-market.stmarket", mime="application/zip")
    st.caption("Para congelar también el origen, importa este mismo archivo en él. "
               "Actualizar el programa no transmite automáticamente esta copia.")

st.subheader("2. Importar la misma copia en cada PC")
uploaded = st.file_uploader("Archivo exportado por Stocks Tracker", type=["stmarket"])
valid_upload = False
if uploaded is not None:
    try:
        incoming, _ = inspect_bundle(uploaded.getvalue())
        st.info(f"Copia {incoming['bundle_id'][:16]} · sesión {incoming['as_of']} · motor compatible.")
        with connect(read_only=True) as conn:
            st.dataframe(preview(conn, incoming), hide_index=True)
        valid_upload = True
    except Exception as exc:  # noqa: BLE001 — previsualización sin escrituras
        st.error(f"Copia no válida: {exc}")
confirmed = st.checkbox("Entiendo que se sustituirá la base de mercado local, no mi cartera. "
                        "Se guardará automáticamente una copia local previa (se conservan las tres últimas).")
if st.button("Validar e importar", disabled=not valid_upload or not confirmed):
    try:
        raw = uploaded.getvalue()
        # Validación antes de abrir la conexión escritora.
        inspect_bundle(raw)
        with connect() as conn:
            manifest = import_bundle(conn, raw)
        st.cache_data.clear()
        st.session_state.pop('market_export', None)
        st.success(f"Importación completa: {manifest['bundle_id'][:16]}. "
                   "No se ha modificado tu cartera. Recalcula el asesor con tus datos personales.")
        st.rerun()
    except Exception as exc:  # noqa: BLE001 — frontera de importación solicitada en UI
        st.error(f"No se ha importado la copia: {exc}")

if current.get("frozen"):
    with st.expander("Volver a descargar datos por separado en este PC"):
        leave = st.checkbox("Acepto que este equipo dejará de tener la misma base que los demás.")
        if st.button("Desactivar copia compartida", disabled=not leave):
            with connect() as conn:
                unlock(conn)
            st.cache_data.clear()
            st.rerun()

with st.expander("Recuperar el mercado anterior a una importación"):
    with connect(read_only=True) as conn:
        points = conn.execute('SELECT point_id, created_at FROM market_restore_points ORDER BY created_at DESC').fetchall()
    if not points:
        st.caption("Aún no hay copias locales previas a una importación.")
    else:
        options = {p[0]: str(p[1]) for p in points}
        selected = st.selectbox("Copia local", list(options), format_func=lambda key: options[key])
        accept_restore = st.checkbox("Restaurar solo el mercado. Mi cartera y mi diario no se modificarán.")
        if st.button("Restaurar mercado", disabled=not accept_restore):
            try:
                with connect() as conn:
                    restore_point(conn, selected)
                st.cache_data.clear()
                st.session_state.pop('market_export', None)
                st.rerun()
            except Exception as exc:  # noqa: BLE001 — recuperación solicitada
                st.error(f"No se ha restaurado: {exc}")

"""Intercambio privado y explícito: no hay nube ni credenciales que configurar."""
import streamlit as st

from stocks_tracker.core.db import connect
from stocks_tracker.core.market_bundle import (
    export_bundle,
    import_bundle,
    inspect_bundle,
    state,
    unlock,
)

st.title("Base de mercado compartida")
st.write("Exporta en el PC con el histórico completo e importa ese mismo archivo en el otro. "
         "La transferencia es manual; no se envía nada a Internet.")
st.info("Incluye histórico, fundamentales, universo y resultados de análisis. "
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
confirmed = st.checkbox("Entiendo que se sustituirá la base de mercado local, no mi cartera. "
                        "He exportado la base actual si quiero conservarla.")
if st.button("Validar e importar", disabled=uploaded is None or not confirmed):
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

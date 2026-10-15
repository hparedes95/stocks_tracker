"""Local configuration and explicit first activation. No secret defaults in widgets."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from stocks_tracker.core import snaptrade_credentials as vault
from stocks_tracker.core import snaptrade_portfolio as snap
from stocks_tracker.core.db import connect
from stocks_tracker.providers.snaptrade import Client, SnapTradeError


def render_snapshot(current):
    if not current.get('snapshot'):
        st.info('Todavía no hay una copia consultada de la cuenta.')
        return
    data = json.loads(current['snapshot'])
    st.caption(f"Posiciones del proveedor: {data['as_of']} · Última consulta correcta: {current['last_success']} UTC")
    if current.get('error'):
        st.error(current['error'])
        st.warning('Se muestra la última copia válida, NO una sincronización actual.')
    st.dataframe(pd.DataFrame(data['positions']).rename(columns={
        'symbol': 'Símbolo', 'kind': 'Producto', 'qty': 'Unidades', 'price': 'Precio informado',
        'avg_cost': 'Coste medio informado', 'currency': 'Divisa del precio',
        'exchange': 'Mercado', 'listing_currency': 'Divisa de cotización',
        'cash_equivalent': 'Incluido también en efectivo'}).drop(columns=['id'], errors='ignore'), hide_index=True)
    st.write('Efectivo por divisa (no poder de compra):')
    st.dataframe(pd.DataFrame(data['balances']), hide_index=True)
    st.caption('Esta es una foto de posiciones, no un libro de movimientos. No reconstruye fechas de compra, '
               'ventas realizadas, dividendos ni comisiones. No calcula rentabilidad histórica con fechas inventadas. '
               'La consulta no garantiza precios en tiempo real.')
    if current.get('active'):
        with connect(read_only=True) as conn:
            issue = snap.readiness(conn)
            session = conn.execute('SELECT date FROM current_session').fetchone()
            frame = snap.portfolio(conn, session[0] if session else None)
        if issue:
            st.warning(issue)
        if not frame.empty:
            view = frame[['broker_symbol', 'ticker', 'currency', 'valuation_source', 'reason']].rename(columns={
                'broker_symbol': 'Símbolo del bróker', 'ticker': 'Serie para análisis',
                'currency': 'Divisa de valoración', 'valuation_source': 'Origen de valoración', 'reason': 'Estado'})
            view.loc[~frame.analysis_supported, 'Serie para análisis'] = 'Sin correspondencia verificada'
            st.write('Cómo se incorpora al asesor:')
            st.dataframe(view, hide_index=True)
            if frame.asset_class.eq('crypto').any():
                st.caption('BTC y ETH cuentan en el valor, los pesos y los avisos de concentración de la cartera. '
                           'Se utiliza su histórico para el riesgo cuando está disponible; '
                           'el modelo fundamental de compras de acciones no se aplica a las criptomonedas.')
        if not frame.empty and not frame.supported.all():
            st.warning('Análisis parcial. Posiciones pendientes: '
                       + '; '.join(f"{p.broker_symbol}: {p.reason}" for p in frame[~frame.supported].itertuples()))
            st.caption('El asesor sigue analizando las demás posiciones. Las compras que requieren comprobar '
                       'la valoración o el riesgo de toda la cartera quedan pendientes.')
        elif not frame.empty and frame.reason.ne('').any():
            st.info('Las posiciones aceptadas cuentan en la cartera. Si falta histórico o una identidad segura, '
                    'se indica arriba y no se habilitan compras sin poder comprobar el riesgo conjunto. '
                    'Usa Actualizar datos y análisis en el Asesor para descargar las nuevas series.')


def render_configuration():
    st.title('Conexión SnapTrade')
    st.write('eToro → SnapTrade Personal → Stocks Tracker. Solo lectura; nunca envía órdenes.')
    st.caption('Sin suscripción nueva ni llamadas de refresco de pago. Configura la clave Personal, no una clave Commercial. '
               'Las condiciones y disponibilidad dependen de SnapTrade.')
    with connect(read_only=True) as conn:
        current = snap.state(conn)
    with st.expander('Credenciales locales', expanded=not vault.exists()):
        st.info('Se guardan cifradas con tu usuario de Windows (DPAPI), fuera de Git y de las exportaciones de mercado. '
                'En otro PC tendrás que introducirlas otra vez. No las envíes por chat.')
        with st.form('snaptrade_credentials', clear_on_submit=True):
            client_id = st.text_input('SnapTrade Personal Client ID', type='password')
            consumer_key = st.text_input('SnapTrade Personal Consumer Key', type='password')
            consent = st.checkbox('Autorizo consultar mi cuenta a través de SnapTrade. La conexión eToro está en Read only.')
            submitted = st.form_submit_button('Guardar claves y comprobar conexión', disabled=current.get('active', False))
        if submitted:
            try:
                if not consent:
                    raise SnapTradeError('Confirma la autorización de lectura.')
                accounts = Client(client_id, consumer_key).accounts()
                if not accounts:
                    raise SnapTradeError('No aparece ninguna cuenta eToro real. Revisa la conexión en SnapTrade.')
                vault.save(client_id, consumer_key)
                st.session_state['snaptrade_accounts'] = accounts
                st.success('Claves guardadas. Selecciona la cuenta y consulta la copia debajo.')
            except (SnapTradeError, RuntimeError, OSError) as exc:
                st.error(str(exc) if isinstance(exc, (SnapTradeError, RuntimeError)) else 'No se pudieron guardar las claves.')
        if current.get('active'):
            st.caption('Para cambiar credenciales, desactiva primero el uso de la cuenta debajo.')
    if vault.exists() and st.button('Consultar cuentas eToro'):
        try:
            st.session_state['snaptrade_accounts'] = Client(*vault.load()).accounts()
        except (SnapTradeError, RuntimeError, OSError):
            st.error('No se pudieron consultar cuentas. Revisa las claves y la conexión en SnapTrade.')
    accounts = st.session_state.get('snaptrade_accounts', [])
    choices = {a['id']: f"{a.get('display_name') or 'eToro'} · {a.get('masked_account_number') or a['id'][-4:]}"
               for a in accounts}
    if choices and not current.get('active'):
        selected = st.selectbox('Cuenta real que se usará', list(choices), format_func=choices.get)
    else:
        selected = current.get('account_id')
    if selected and vault.exists() and st.button('Consultar cartera ahora'):
        try:
            with st.spinner('Consultando posiciones y efectivo…'):
                snap.sync(selected)
            st.cache_data.clear()
            st.rerun()
        except (SnapTradeError, RuntimeError):
            st.error('No se completó la consulta. Se conserva la copia anterior; revisa el estado de conexión.')
    with connect(read_only=True) as conn:
        current = snap.state(conn)
    render_snapshot(current)
    if current.get('snapshot') and not current.get('active'):
        confirmed = st.checkbox('He revisado esta cuenta. Usarla EN LUGAR de mi cartera manual/CSV en el asesor.')
        automatic = st.checkbox('Consultar automáticamente al abrir y cada cinco minutos mientras la app esté abierta', value=True)
        if st.button('Activar cartera SnapTrade', disabled=not confirmed):
            with connect() as conn:
                snap.enable(conn, True, automatic=automatic)
            st.cache_data.clear()
            st.rerun()
    if current.get('active'):
        st.success('El asesor usa esta cuenta, sin sumarla a las posiciones manuales. Estas siguen guardadas.')
        if st.button('Desactivar SnapTrade y volver a la cartera manual/CSV'):
            with connect() as conn:
                snap.enable(conn, False)
            st.cache_data.clear()
            st.rerun()
    if not current.get('active') and vault.exists() and st.button('Borrar claves locales de SnapTrade'):
        vault.forget()
        st.session_state.pop('snaptrade_accounts', None)
        st.success('Claves locales eliminadas. La copia de cartera se conserva. Puedes revocar la conexión en SnapTrade.')


@st.fragment(run_every='5m')
def background_sync():
    try:
        changed = snap.auto_sync()
    except Exception:  # noqa: BLE001 — UI boundary: no provider/credential traces
        st.warning('SnapTrade no se ha actualizado. Revisa Conexión SnapTrade; se conserva la copia anterior.')
        st.cache_data.clear()
        return
    if changed:
        st.cache_data.clear()
        try:
            from stocks_tracker.compute.run_advice import calcular_y_guardar
            calcular_y_guardar()
        except Exception:  # noqa: BLE001 — snapshot survives an unavailable analysis
            st.warning('Cartera sincronizada. El asesor no pudo recalcularse; revisa los datos y Conexión SnapTrade.')
        st.rerun()

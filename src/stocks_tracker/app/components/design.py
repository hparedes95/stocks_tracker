"""Shared visual shell. No portfolio writes, model rules or broker actions."""
from __future__ import annotations

from html import escape
from pathlib import Path

import streamlit as st

from .theme import palette

# Short orientation on every route, including when its data-dependent body stops.
PAGE_DESCRIPTIONS = {
    'Asesor de acciones': 'Tus posiciones, sus motivos y los límites de cada análisis.',
    'Predicción y escenarios': 'Escenarios históricos y evaluación de modelos, sin confundirlos con certezas.',
    'Qué se mueve hoy': 'Movimientos, amplitud y contexto del mercado. Distingue el directo del cierre.',
    'Sectores y rotación': 'Compara sectores, horizontes y amplitud antes de interpretar una rotación.',
    'Oportunidades': 'Explora candidatos por criterios comparables; un ranking no es una orden de compra.',
    'Ficha de valor': 'Precio, señales, fundamentales y procedencia de un mismo valor.',
    'Cartera y watchlist': 'Separa lo que tienes de lo que sigues y revisa el origen de sus datos.',
    'Macro y riesgo': 'Contexto de mercado y componentes del riesgo, no una predicción de precios.',
    'Validación de señales': 'Contrasta evidencia, costes y sesgos antes de confiar en una señal.',
    'Diario de decisiones': 'Registra tu tesis y revísala después sin reescribir lo que sabías.',
    'Estado de los datos': 'Comprueba cobertura, procedencia y errores antes de interpretar resultados.',
    'Base de mercado compartida': 'Transfiere una copia entre equipos; no es sincronización automática.',
    'Conexión SnapTrade': 'Configura la consulta de tu cuenta en solo lectura y revisa la última copia válida.',
}


def stylesheet() -> str:
    p = palette()
    tokens = ';'.join(f'--stx-{name.replace("_", "-")}:{p[name]}' for name in
                      ('surface', 'plane', 'text_primary', 'text_secondary', 'muted', 'grid', 'accent'))
    css = Path(__file__).with_name('design.css').read_text(encoding='utf-8')
    return f'<style>:root {{{tokens}}}\n{css}</style>'


def apply_design():
    # st.html handles style-only content without reserving a blank block.
    st.html(stylesheet())


def page_header(title: str, *, section: str):
    apply_design()
    st.html(f'<p class="stx-eyebrow">{escape(section)}</p>')
    st.title(title)
    if title in PAGE_DESCRIPTIONS:
        st.caption(PAGE_DESCRIPTIONS[title])


def panel(key: str):
    """Native container with a shared surface; no hidden controls or scripts."""
    return st.container(border=True, key=f'panel-{key}')


def sidebar_brand():
    st.html('<div class="stx-brand"><span class="stx-brand-mark" aria-hidden="true">S</span>'
            '<div><strong>Stocks Tracker</strong><span>Análisis · cartera · mercados</span></div></div>')


def appearance_help():
    with st.popover('Apariencia', icon=':material/contrast:', width='stretch'):
        st.write('Claro, oscuro o el tema de tu sistema')
        st.caption('Abre el menú ⋮ de la esquina superior derecha y elige Light (claro), Dark (oscuro) o System (sistema). '
                   'El selector nativo adapta también tablas, controles y gráficos. '
                   'La preferencia es de este navegador; no cambia la de otros equipos.')

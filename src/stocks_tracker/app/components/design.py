"""Shared visual shell. No portfolio writes, model rules or broker actions."""
from __future__ import annotations

from html import escape
from pathlib import Path

import streamlit as st

from .theme import palette


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


def sidebar_brand():
    st.html('<div class="stx-brand"><span class="stx-brand-mark" aria-hidden="true">S</span>'
            '<div><strong>Stocks Tracker</strong><span>Análisis · cartera · mercados</span></div></div>')


def appearance_help():
    with st.popover('Apariencia', icon=':material/contrast:', width='stretch'):
        st.write('Claro, oscuro o el tema de tu sistema')
        st.caption('Abre el menú ⋮ de la esquina superior derecha y elige Light (claro), Dark (oscuro) o System (sistema). '
                   'El selector nativo adapta también tablas, controles y gráficos. '
                   'La preferencia es de este navegador; no cambia la de otros equipos.')

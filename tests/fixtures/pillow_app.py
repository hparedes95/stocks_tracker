"""Isolated image smoke test; no portfolio, provider or credentials."""
import streamlit as st
from PIL import Image

st.image(Image.new('RGBA', (24, 16), (20, 50, 90, 128)), caption='Compatibility check')

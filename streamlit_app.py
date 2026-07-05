"""
streamlit_app.py — DevPulse Control Plane.

Run alongside the FastAPI backend:
  Terminal 1: uvicorn app:app --reload --port 8000
  Terminal 2: streamlit run streamlit_app.py --server.port 8501

The UI talks to the FastAPI backend via HTTP (localhost:8000).
This keeps the UI and API cleanly separated and avoids the
Streamlit re-run model interfering with async FastAPI state.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="DevPulse",
    page_icon=":material/hub:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state defaults ────────────────────────────────────────────────────
if "theme" not in st.session_state:
    st.session_state.theme = "dark"
if "api_key" not in st.session_state:
    st.session_state.api_key = ""
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# ── Sidebar: global controls ──────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚡ DevPulse")
    st.caption("AI Function-as-a-Service · Developer Edition")
    st.divider()

    theme = st.radio("Theme", ["dark", "light"], horizontal=True, label_visibility="collapsed")
    st.session_state.theme = theme

    st.divider()
    api_key_input = st.text_input(
        "API Key", type="password",
        value=st.session_state.api_key,
        placeholder="Your X-API-Key",
        help="Set your DevPulse API key. Stored in session only.",
    )
    st.session_state.api_key = api_key_input

    st.divider()
    page = st.radio(
        "Navigation",
        options=["Registry", "Playground", "Agent Chat", "Audit Log", "Key Manager"],
        label_visibility="collapsed",
    )

from ui.components import inject_theme
inject_theme(st.session_state.theme)

# ── Page routing ──────────────────────────────────────────────────────────────
if page == "Registry":
    from ui.pages import registry_page
    registry_page.render()
elif page == "Playground":
    from ui.pages import playground_page
    playground_page.render()
elif page == "Agent Chat":
    from ui.pages import agent_page
    agent_page.render()
elif page == "Audit Log":
    from ui.pages import audit_page
    audit_page.render()
elif page == "Key Manager":
    from ui.pages import keys_page
    keys_page.render()

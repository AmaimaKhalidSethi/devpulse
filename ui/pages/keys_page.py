"""ui/pages/keys_page.py — API Key Manager (admin key required)."""
from __future__ import annotations

import streamlit as st
from ui.components import api_call


def render() -> None:
    st.title("Key Manager")
    st.caption("Create and revoke API keys. Requires admin key. Raw keys are shown once only.")

    key = st.session_state.get("api_key", "")
    if not key:
        st.warning("Set your admin API key in the sidebar.")
        return

    # Create new key
    with st.expander("➕ Create new API key", expanded=False):
        with st.form("create_key_form"):
            label = st.text_input("Label", placeholder="e.g. 'CI pipeline' or 'Dev laptop'")
            rate_limit = st.number_input("Rate limit (req/min)", value=60, min_value=1, max_value=1000)
            submitted = st.form_submit_button("Create key")

        if submitted:
            if not label.strip():
                st.error("Label is required.")
            else:
                sc, resp = api_call(
                    "/keys",
                    method="POST",
                    body={"label": label.strip(), "rate_limit_per_minute": rate_limit},
                    api_key=key,
                )
                if sc == 200:
                    raw_key = resp.get("raw_key", "")
                    st.success(f"Key created: `{resp['key_id']}`")
                    st.code(raw_key, language=None)
                    st.warning("⚠️ Copy this key now — it will not be shown again.")
                elif sc == 403:
                    st.error("Admin key required.")
                else:
                    st.error(resp.get("error") or resp.get("detail") or f"HTTP {sc}")

    st.divider()

    # List existing keys
    st.markdown("#### Existing keys")
    if st.button(":material/refresh: Refresh list"):
        st.rerun()

    sc, keys = api_call("/keys", api_key=key)
    if sc == 403:
        st.error("Admin key required to list keys.")
        return
    if sc != 200:
        st.error(keys.get("error", f"HTTP {sc}"))
        return

    key_list: list[dict] = keys if isinstance(keys, list) else []
    if not key_list:
        st.info("No keys created yet.")
        return

    for k in key_list:
        active = k.get("is_active", 1)
        icon = "🟢" if active else "🔴"
        with st.expander(f"{icon} `{k['key_id'][:16]}…` — {k.get('label', '?')}"):
            st.markdown(f"- **Label:** {k.get('label')}")
            st.markdown(f"- **Rate limit:** {k.get('rate_limit_per_minute')} req/min")
            st.markdown(f"- **Created:** {k.get('created_at', '?')[:19]}")
            st.markdown(f"- **Status:** {'Active' if active else 'Revoked'}")

            if active:
                if st.button(f"Revoke `{k['key_id'][:12]}…`", key=f"revoke_{k['key_id']}"):
                    sc2, resp2 = api_call(f"/keys/{k['key_id']}", method="DELETE", api_key=key)
                    if sc2 == 200:
                        st.success("Key revoked.")
                        st.rerun()
                    else:
                        st.error(resp2.get("error", f"HTTP {sc2}"))

"""ui/pages/audit_page.py — Audit Log viewer (admin key required)."""
from __future__ import annotations

import streamlit as st
from ui.components import api_call


def render() -> None:
    st.title("Audit Log")
    st.caption("Requires the admin API key. Shows all tool executions across all API keys.")

    key = st.session_state.get("api_key", "")
    if not key:
        st.warning("Set your admin API key in the sidebar.")
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        tool_filter = st.text_input("Filter by tool", placeholder="cve_search")
    with c2:
        limit = st.number_input("Max rows", value=50, min_value=5, max_value=500, step=10)
    with c3:
        if st.button(":material/refresh: Refresh", use_container_width=True):
            st.rerun()

    params = f"?limit={limit}"
    if tool_filter.strip():
        params += f"&tool_name={tool_filter.strip()}"

    sc, data = api_call(f"/audit/logs{params}", api_key=key)

    if sc == 403:
        st.error("Admin key required to view audit logs.")
        return
    if sc != 200:
        st.error(data.get("error", f"HTTP {sc}"))
        return

    rows: list[dict] = data if isinstance(data, list) else []
    if not rows:
        st.info("No log entries found.")
        return

    # Summary metrics
    total = len(rows)
    successes = sum(1 for r in rows if r.get("success"))
    failures = total - successes
    avg_latency = sum(r.get("latency_ms", 0) for r in rows) / total if total else 0

    m = st.columns(4)
    with m[0]: st.metric("Entries", total)
    with m[1]: st.metric("Successes", successes)
    with m[2]: st.metric("Failures", failures)
    with m[3]: st.metric("Avg latency", f"{avg_latency:.1f}ms")

    st.divider()

    # Table
    import pandas as pd
    df = pd.DataFrame(rows)
    display_cols = ["ts", "tool_name", "success", "latency_ms", "key_id", "error_summary"]
    display_cols = [c for c in display_cols if c in df.columns]
    df_display = df[display_cols].copy()
    if "success" in df_display.columns:
        df_display["success"] = df_display["success"].map({1: "✅", 0: "❌", True: "✅", False: "❌"})
    if "latency_ms" in df_display.columns:
        df_display["latency_ms"] = df_display["latency_ms"].apply(lambda x: f"{x:.1f}ms" if x else "—")
    if "key_id" in df_display.columns:
        df_display["key_id"] = df_display["key_id"].apply(lambda x: x[:8] + "…" if x else "—")

    st.dataframe(df_display, use_container_width=True, hide_index=True)

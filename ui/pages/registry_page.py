"""ui/pages/registry_page.py — Registry browser."""
from __future__ import annotations

import streamlit as st
from ui.components import api_call, tool_card, executor_badge


def render() -> None:
    st.title("Tool Registry")

    key = st.session_state.get("api_key", "")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        status_code, health = api_call("/health", api_key=key)
        if status_code == 200:
            st.markdown(
                f"<span style='color:var(--dp-success);font-weight:600;'>● Online</span> &nbsp;"
                f"v{health.get('version','?')} &nbsp;·&nbsp; "
                f"**{health.get('tools_loaded',0)}** tools loaded, "
                f"**{health.get('tools_enabled',0)}** enabled &nbsp;·&nbsp; "
                f"uptime {int(health.get('uptime_seconds',0))}s",
                unsafe_allow_html=True,
            )
        else:
            st.error(f"API unreachable: {health.get('error', status_code)}")

    with col3:
        if st.button(":material/refresh: Hot Reload", use_container_width=True):
            sc, resp = api_call("/registry/reload", method="POST", api_key=key)
            if sc == 200:
                st.success(f"Reloaded: {resp.get('tools_after')} tools")
            else:
                st.error(resp.get("error", "Reload failed"))

    st.divider()

    # Filters
    f1, f2 = st.columns([2, 1])
    with f1:
        search = st.text_input("Search tools", placeholder="calculator, http, security…")
    with f2:
        tag_filter = st.text_input("Filter by tag", placeholder="real-time, offline, security")

    # Fetch tools
    params = "?enabled_only=false"
    if tag_filter.strip():
        params += f"&tag={tag_filter.strip()}"

    sc, data = api_call(f"/tools{params}", api_key=key)

    if sc == 401:
        st.warning("Set your API key in the sidebar to browse the registry.")
        return
    if sc != 200:
        st.error(data.get("error", f"API error {sc}"))
        return

    tools: list[dict] = data if isinstance(data, list) else []
    if search.strip():
        q = search.lower()
        tools = [t for t in tools if q in t["name"] or q in t.get("description", "").lower()]

    if not tools:
        st.info("No tools match the current filters.")
        return

    st.caption(f"{len(tools)} tool{'s' if len(tools) != 1 else ''} shown")

    # Summary metrics row
    m = st.columns(4)
    executor_types = {}
    for t in tools:
        executor_types[t["executor_type"]] = executor_types.get(t["executor_type"], 0) + 1

    with m[0]: st.metric("Total", len(tools))
    with m[1]: st.metric("Enabled", sum(1 for t in tools if t["enabled"]))
    with m[2]: st.metric("HTTP tools", executor_types.get("http_get", 0) + executor_types.get("http_post", 0))
    with m[3]: st.metric("Offline tools", sum(v for k, v in executor_types.items() if "http" not in k))

    st.divider()

    # Tool cards — click to expand full spec
    for tool in tools:
        with st.expander(f"`{tool['name']}` — {tool['description'][:60]}…", expanded=False):
            sc2, full = api_call(f"/tools/{tool['name']}", api_key=key)
            if sc2 == 200:
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.markdown(f"**Description:** {full.get('description','').strip()}")
                    if full.get("args"):
                        st.markdown("**Args:**")
                        for arg in full["args"]:
                            req = "required" if arg["required"] else f"optional (default: `{arg.get('default', 'none')}`)"
                            enum_str = f", enum: `{arg['enum']}`" if arg.get("enum") else ""
                            st.markdown(f"- `{arg['name']}` ({arg['type']}, {req}{enum_str}) — {arg['description']}")
                    else:
                        st.caption("No args defined.")
                with c2:
                    st.markdown(f"**Version:** {full.get('version','?')}")
                    st.markdown(f"**Executor:**")
                    st.markdown(executor_badge(full.get("executor_type", "")), unsafe_allow_html=True)
                    st.markdown(f"**Tags:** {', '.join(full.get('tags', [])) or 'none'}")
                    st.markdown(f"**Enabled:** {'✅' if full.get('enabled') else '❌'}")
            else:
                st.error(full.get("error", "Could not load full spec"))

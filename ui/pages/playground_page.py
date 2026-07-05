"""ui/pages/playground_page.py — API Playground with auto-generated forms."""
from __future__ import annotations

import json
import streamlit as st
from ui.components import api_call


def render() -> None:
    st.title("API Playground")
    st.caption("Select a tool, fill in the args, and execute it directly against the registry.")

    key = st.session_state.get("api_key", "")
    if not key:
        st.warning("Set your API key in the sidebar first.")
        return

    sc, data = api_call("/tools?enabled_only=true", api_key=key)
    if sc != 200:
        st.error(data.get("error", f"Cannot load tools (HTTP {sc})"))
        return

    tools: list[dict] = data if isinstance(data, list) else []
    if not tools:
        st.info("No enabled tools in the registry.")
        return

    tool_names = [t["name"] for t in tools]
    selected_name = st.selectbox("Tool", tool_names, label_visibility="visible")

    # Fetch full spec for the selected tool
    sc2, spec = api_call(f"/tools/{selected_name}", api_key=key)
    if sc2 != 200:
        st.error(spec.get("error", "Could not load tool spec"))
        return

    st.markdown(f"**{spec.get('description', '').strip()}**")
    st.caption(f"executor: `{spec.get('executor_type')}` · version {spec.get('version','?')}")
    st.divider()

    # Auto-generate input widgets from arg schema
    args: dict = {}
    arg_defs = spec.get("args", [])

    if not arg_defs:
        st.caption("This tool takes no arguments.")
    else:
        st.markdown("#### Arguments")
        for arg in arg_defs:
            name = arg["name"]
            atype = arg["type"]
            desc = arg.get("description", "")
            default = arg.get("default")
            required = arg.get("required", True)
            enum = arg.get("enum")
            label = f"`{name}`{'*' if required else ''} ({atype})"

            if enum:
                default_idx = enum.index(default) if default in (enum or []) else 0
                args[name] = st.selectbox(label, enum, index=default_idx, help=desc)
            elif atype == "boolean":
                args[name] = st.checkbox(label, value=bool(default), help=desc)
            elif atype == "integer":
                args[name] = st.number_input(label, value=int(default or 0), step=1, help=desc)
            elif atype == "float":
                args[name] = st.number_input(label, value=float(default or 0.0), format="%.4f", help=desc)
            elif atype in ("object", "array"):
                raw = st.text_area(label, value=json.dumps(default or {}, indent=2), help=desc + " (JSON)")
                try:
                    args[name] = json.loads(raw)
                except json.JSONDecodeError:
                    st.error(f"`{name}`: invalid JSON")
                    args[name] = None
            else:  # string
                args[name] = st.text_input(label, value=str(default or ""), help=desc)

    st.divider()

    if "exec_history" not in st.session_state:
        st.session_state.exec_history = []

    if st.button(":material/play_arrow: Execute", type="primary"):
        # Remove optional args that are empty strings (user left them blank)
        filtered_args = {
            k: v for k, v in args.items()
            if v is not None and v != ""
        }
        payload = {"tool_name": selected_name, "args": filtered_args}

        with st.spinner("Executing…"):
            sc3, result = api_call("/execute", method="POST", body=payload, api_key=key)

        st.session_state.exec_history.insert(0, {
            "tool": selected_name,
            "args": filtered_args,
            "status": sc3,
            "result": result,
        })

        if sc3 == 200 and result.get("success"):
            st.success(f"✓ Completed in {result.get('latency_ms', 0):.1f}ms")
            st.json(result.get("result", {}))
        else:
            st.error(f"HTTP {sc3}")
            st.json(result)

        with st.expander("Raw request/response"):
            st.json({"request": payload, "response": result, "http_status": sc3})

    # Execution history
    if st.session_state.exec_history:
        st.divider()
        st.markdown("#### Recent executions (this session)")
        for h in st.session_state.exec_history[:10]:
            icon = "✅" if h["status"] == 200 else "❌"
            with st.expander(f"{icon} `{h['tool']}` — HTTP {h['status']}"):
                st.json({"args": h["args"], "result": h["result"]})

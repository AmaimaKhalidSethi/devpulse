"""ui/pages/agent_page.py — Agent Chat."""
from __future__ import annotations

import streamlit as st
from ui.components import api_call


def render() -> None:
    st.title("Agent Chat")
    st.caption("Conversational interface to the Groq agent. All registry tools are available.")

    key = st.session_state.get("api_key", "")
    if not key:
        st.warning("Set your API key in the sidebar first.")
        return

    # Render history
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and msg.get("tools_called"):
                with st.expander(f"Tools called: {', '.join(msg['tools_called'])}"):
                    for t in msg["tools_called"]:
                        st.markdown(f"- `{t}`")
                    st.caption(f"Turns: {msg.get('turns', '?')} · Errors: {msg.get('had_errors', False)}")

    if prompt := st.chat_input("Ask anything — the agent has all tools available…"):
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                sc, resp = api_call(
                    "/agent/chat",
                    method="POST",
                    body={"message": prompt},
                    api_key=key,
                )

            if sc == 200:
                answer = resp.get("answer", "")
                tools_called = resp.get("tools_called", [])
                turns = resp.get("turns", 0)
                had_errors = resp.get("had_errors", False)

                st.markdown(answer)

                if had_errors:
                    st.warning("One or more tool calls encountered errors during this response.")

                if tools_called:
                    with st.expander(f"Tools called: {', '.join(tools_called)}"):
                        for t in tools_called:
                            st.markdown(f"- `{t}`")
                        st.caption(f"Reasoning turns: {turns}")

                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": answer,
                    "tools_called": tools_called,
                    "turns": turns,
                    "had_errors": had_errors,
                })
            else:
                err = resp.get("error") or resp.get("detail") or f"HTTP {sc}"
                st.error(f"Agent error: {err}")
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": f"Error: {err}",
                })

    if st.session_state.chat_history:
        if st.button("Clear conversation", use_container_width=False):
            st.session_state.chat_history = []
            st.rerun()

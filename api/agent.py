"""
api/agent.py
LangChain agent that builds tool bindings dynamically from the registry.

FIXES applied:
- acall() → ainvoke(): acall was removed in langchain-core 0.3+
- Closure capture bug: loop variable captured by default-arg binding
  (tool_name=tool_name) instead of nonlocal, so each closure holds
  its own snapshot of the name at iteration time rather than the
  mutable loop variable's final value
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field, create_model

from core.config import get_settings
from core.logging import get_logger
from core.registry import registry
from core.schemas import AgentChatResponse

logger = get_logger(__name__)

_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "float": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _make_tool_coroutine(captured_name: str):
    """Factory function — each call creates a coroutine with its own
    captured_name binding. Using a factory instead of a closure-over-loop-var
    is the canonical Python fix for the closure-capture-in-loop bug:
    captured_name is a local variable in _make_tool_coroutine's own frame,
    not a reference to the loop variable in the outer scope.
    """
    async def _run(**kwargs: Any) -> str:
        try:
            result = await registry.execute(captured_name, kwargs)
            import json
            return json.dumps(result, default=str)
        except Exception as e:
            return f"ERROR executing {captured_name}: {e}"
    _run.__name__ = captured_name
    return _run


def _build_langchain_tools() -> list[StructuredTool]:
    """Converts every enabled registry tool into a LangChain StructuredTool.
    Called fresh each agent invocation to reflect any hot-reloads.
    """
    tools = []
    for spec in registry.list_tools(enabled_only=True):
        field_defs: dict[str, Any] = {}
        for arg in spec.args:
            py_type = _TYPE_MAP.get(arg.type, str)
            if not arg.required and arg.default is not None:
                field_defs[arg.name] = (
                    py_type | None,
                    Field(default=arg.default, description=arg.description),
                )
            else:
                field_defs[arg.name] = (
                    py_type,
                    Field(..., description=arg.description),
                )

        ArgsModel: type[BaseModel] = create_model(  # type: ignore[call-overload]
            f"{spec.name}_args", **field_defs
        )

        lc_tool = StructuredTool(
            name=spec.name,
            description=spec.description.strip(),
            args_schema=ArgsModel,
            coroutine=_make_tool_coroutine(spec.name),  # factory captures name correctly
        )
        tools.append(lc_tool)

    return tools


async def run_agent(user_message: str, key_id: str) -> AgentChatResponse:
    settings = get_settings()
    lc_tools = _build_langchain_tools()

    llm = ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        temperature=settings.groq_temperature,
    )
    llm_with_tools = llm.bind_tools(lc_tools)
    tool_lookup = {t.name: t for t in lc_tools}

    messages: list = [HumanMessage(content=user_message)]
    tools_called: list[str] = []
    had_errors = False
    turns = 0

    for turn in range(settings.agent_max_turns):
        turns = turn + 1
        try:
            ai_msg = await llm_with_tools.ainvoke(messages)
        except Exception as e:
            logger.error("agent_llm_error", extra={"extra": {"error": str(e), "turn": turns}})
            return AgentChatResponse(
                answer=f"Language model call failed: {type(e).__name__}. Please retry.",
                tools_called=tools_called,
                turns=turns,
                had_errors=True,
            )

        messages.append(ai_msg)

        if not ai_msg.tool_calls:
            return AgentChatResponse(
                answer=ai_msg.content,
                tools_called=tools_called,
                turns=turns,
                had_errors=had_errors,
            )

        for call in ai_msg.tool_calls:
            name = call["name"]
            tools_called.append(name)
            tool_obj = tool_lookup.get(name)

            if tool_obj is None:
                result_str = f"ERROR: tool '{name}' not found in registry"
                had_errors = True
            else:
                try:
                    # ainvoke() is the correct async entry point in langchain-core 0.3+
                    # acall() was removed — it no longer exists on StructuredTool.
                    result_str = await tool_obj.ainvoke(call["args"])
                    if isinstance(result_str, str) and result_str.startswith("ERROR"):
                        had_errors = True
                except Exception as e:
                    result_str = f"ERROR: {e}"
                    had_errors = True

            messages.append(ToolMessage(content=str(result_str), tool_call_id=call["id"]))

    logger.warning("agent_max_turns", extra={"extra": {
        "key_id": key_id,
        "max_turns": settings.agent_max_turns,
    }})
    return AgentChatResponse(
        answer="Reached the maximum number of reasoning steps without a final answer.",
        tools_called=tools_called,
        turns=turns,
        had_errors=True,
    )

"""ui/components.py — shared theme tokens and render helpers."""
from __future__ import annotations

import html
import streamlit as st

EXECUTOR_COLORS = {
    "http_get":       "#58A6FF",
    "http_post":      "#3FB950",
    "python_math":    "#D2A8FF",
    "text_transform": "#FFA657",
    "datetime_tool":  "#79C0FF",
    "json_transform": "#56D364",
    "mock_static":    "#8B949E",
}

SEVERITY_COLORS = {
    "CRITICAL": "#E5484D",
    "HIGH":     "#F2994A",
    "MEDIUM":   "#E8B339",
    "LOW":      "#6B9BD1",
    "INFO":     "#8B949E",
}

THEMES = {
    "dark": {
        "bg":             "#0D1117",
        "surface":        "#161B22",
        "surface_raised": "#1C2129",
        "border":         "#30363D",
        "text":           "#E6EDF3",
        "text_muted":     "#8B949E",
        "accent":         "#58A6FF",
        "success":        "#3FB950",
        "warning":        "#E8B339",
        "danger":         "#E5484D",
    },
    "light": {
        "bg":             "#F0F2F5",
        "surface":        "#FFFFFF",
        "surface_raised": "#E4E9EE",
        "border":         "#BFC9D8",
        "text":           "#111827",
        "text_muted":     "#4B5563",
        "accent":         "#0B5FFF",
        "success":        "#0A6F3A",
        "warning":        "#9A6700",
        "danger":         "#BE123C",
    },
}


def inject_theme(theme_name: str) -> None:
    t = THEMES[theme_name]
    st.markdown(f"""
    <style>
    :root {{
        --dp-bg:             {t['bg']};
        --dp-surface:        {t['surface']};
        --dp-surface-raised: {t['surface_raised']};
        --dp-border:         {t['border']};
        --dp-text:           {t['text']};
        --dp-text-muted:     {t['text_muted']};
        --dp-accent:         {t['accent']};
        --dp-success:        {t['success']};
        --dp-warning:        {t['warning']};
        --dp-danger:         {t['danger']};
    }}
    body, .stApp, .css-1d391kg, .css-1avcm0, .css-1q8dd3e, .css-1n76uvr {{
        color: var(--dp-text) !important;
        background-color: var(--dp-bg) !important;
    }}
    .stApp {{ background-color: var(--dp-bg); }}
    .css-1hynsf2, .css-1v3fvzn, .css-1v0mbdj {{
        background-color: var(--dp-surface) !important;
        color: var(--dp-text) !important;
    }}
    [data-testid="stSidebar"] {{
        background-color: var(--dp-surface) !important;
        border-right: 1px solid var(--dp-border) !important;
    }}
    button, .stButton>button {{
        background-color: var(--dp-accent) !important;
        color: #ffffff !important;
        border: 1px solid var(--dp-accent) !important;
    }}
    input, textarea, select {{
        background-color: var(--dp-surface) !important;
        color: var(--dp-text) !important;
        border: 1px solid var(--dp-border) !important;
    }}
    .dp-card {{
        background: var(--dp-surface);
        border: 1px solid var(--dp-border);
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 12px;
    }}
    .dp-badge {{
        display: inline-block;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.03em;
    }}
    .dp-tag {{
        display: inline-block;
        padding: 1px 6px;
        border-radius: 3px;
        font-size: 0.68rem;
        background: var(--dp-surface-raised);
        color: var(--dp-text-muted);
        border: 1px solid var(--dp-border);
        margin-right: 4px;
    }}
    .dp-tool-name {{
        font-family: ui-monospace, "SF Mono", "JetBrains Mono", monospace;
        font-size: 0.95rem;
        font-weight: 600;
        color: var(--dp-text);
    }}
    .dp-muted {{ color: var(--dp-text-muted); font-size: 0.85rem; }}
    .dp-success {{ color: var(--dp-success); }}
    .dp-danger  {{ color: var(--dp-danger); }}
    </style>
    """, unsafe_allow_html=True)


def executor_badge(executor_type: str) -> str:
    color = EXECUTOR_COLORS.get(executor_type, "#8B949E")
    safe = html.escape(executor_type)
    return f'<span class="dp-badge" style="background:{color};color:#0D1117;">{safe}</span>'


def status_badge(enabled: bool) -> str:
    if enabled:
        return '<span class="dp-badge" style="background:#3FB950;color:#0D1117;">enabled</span>'
    return '<span class="dp-badge" style="background:#E5484D;color:#fff;">disabled</span>'


def tag_chips(tags: list[str]) -> str:
    return " ".join(f'<span class="dp-tag">{html.escape(t)}</span>' for t in tags)


def tool_card(tool: dict) -> None:
    name = html.escape(tool.get("name", ""))
    desc = html.escape(tool.get("description", "").strip()[:120])
    ex   = tool.get("executor_type", "")
    tags = tool.get("tags", [])
    enabled = tool.get("enabled", True)
    args = tool.get("arg_count", 0)
    st.markdown(f"""
    <div class="dp-card">
      <div class="dp-tool-name">{name}</div>
      <div class="dp-muted" style="margin:4px 0 8px;">{desc}{'…' if len(tool.get('description','')) > 120 else ''}</div>
      {executor_badge(ex)} &nbsp; {status_badge(enabled)}
      &nbsp;<span class="dp-muted">· {args} arg{'s' if args != 1 else ''}</span>
      <div style="margin-top:8px;">{tag_chips(tags)}</div>
    </div>
    """, unsafe_allow_html=True)


def api_call(endpoint: str, method: str = "GET", body: dict | None = None, api_key: str = "") -> tuple[int, dict]:
    """Makes an HTTP call to the local DevPulse API. Used by all UI pages."""
    import httpx
    base = "http://localhost:8000/v1"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=15.0) as client:
            if method == "GET":
                r = client.get(f"{base}{endpoint}", headers=headers)
            elif method == "POST":
                r = client.post(f"{base}{endpoint}", json=body, headers=headers)
            elif method == "DELETE":
                r = client.delete(f"{base}{endpoint}", headers=headers)
            else:
                return 400, {"error": f"unsupported method {method}"}
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"raw": r.text}
    except httpx.ConnectError:
        return 503, {"error": "Cannot reach DevPulse API at localhost:8000. Is uvicorn running?"}
    except Exception as e:
        return 500, {"error": str(e)}

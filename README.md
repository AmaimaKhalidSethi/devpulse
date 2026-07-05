# DevPulse — AI Function-as-a-Service for Developer Workflows

A self-hostable platform where YAML files define AI-callable tools, a dynamic registry loads and validates them at runtime, and a Groq-powered agent can invoke any registered tool in response to natural language queries.

Drop a `.yaml` file into `tools/`, and it's immediately available to the agent and REST API — no code changes, no restart required.

## What makes this useful vs. a generic LLM chatbot

Every tool in the registry is a **real, live integration** with zero API keys beyond Groq:

- **CVE search** against NIST's National Vulnerability Database — ask "any known CVEs in Flask 3.0?" and get real CVE IDs, CVSS scores, and descriptions
- **Live currency rates** from Frankfurter (ECB-sourced, updated daily)
- **Hacker News search** via Algolia — actual current stories, not training-data knowledge
- **Safe arithmetic** using AST-based evaluation, no `eval()` or `exec()`
- **Cryptographic hashing** — sha256/sha512/blake2b/md5/sha1 on any text
- **Datetime operations** — current time in any IANA timezone, date diff, format conversion, day arithmetic
- **JMESPath JSON extraction** — query and reshape JSON payloads inline
- **Text transforms** — upper/lower/title/reverse/slugify/word-count
- Plus **geocoding** (Open-Meteo) and an **echo tool** for pipeline testing

## Architecture

```
tools/*.yaml            Tool definitions — drop any .yaml here
    │
    ▼
core/registry.py        ToolRegistry: load → validate → executor whitelist → store
    │                   Hot-reload via watchdog (no restart needed)
    ├──► core/schemas.py       Pydantic v2: ToolSpec, ExecuteRequest/Response, etc.
    └──► executors/             One class per executor_type (whitelisted, no shell/exec)
         ├── http_get.py        SSRF-protected HTTP GET with response_path extraction
         ├── http_post.py       HTTP POST with JSON body from args
         ├── python_math.py     AST-based safe arithmetic (no eval, DoS-limited)
         ├── text_transform.py  String ops + cryptographic hashing via hashlib
         ├── datetime_tool.py   IANA-aware datetime: now/format/diff/add_days
         ├── json_transform.py  JMESPath queries via jmespath library
         └── mock_static.py     Static configured response for testing

app.py                  FastAPI app with lifespan startup, middleware, /v1/* routes
api/routes.py           REST: /tools, /execute, /agent/chat, /audit/logs, /keys
api/agent.py            LangChain agent — dynamically binds all registry tools

streamlit_app.py        Control-plane UI (runs alongside uvicorn on port 8501)
ui/pages/
  ├── registry_page.py  Browse tools, view specs, hot-reload trigger
  ├── playground_page.py Auto-generated forms per tool schema, execute + history
  ├── agent_page.py      Conversational agent with tool-call trace per message
  ├── audit_page.py      Filterable execution log table (admin key required)
  └── keys_page.py       Create/revoke API keys (admin key required)

tests/
  ├── test_registry.py   89 tests covering registry, executors, security
  ├── test_executors.py
  ├── test_api_endpoints.py  API endpoint integration coverage
  └── test_security.py
```

## Security model

| Threat | Mitigation |
|---|---|
| YAML RCE (`!!python/object`) | `yaml.safe_load()` only — ConstructorError on any custom tag |
| Arbitrary executor types | Executor whitelist in `executors/registry.py` — unknown type = load-time rejection |
| SSRF via URL injection | Regex blocklist for RFC-1918/loopback ranges + optional URL prefix allowlist |
| Unauthenticated access | `X-API-Key` header required on all `/v1/*` routes (except `/v1/health`) |
| API abuse | Sliding-window in-memory rate limiter, per key, 60 req/min default |
| Large payload DoS | 64KB request body limit via middleware |
| Secrets in YAML | `$ENV{VAR}` resolved from environment at load time — never stored in registry |
| Tool name injection | `^[a-z][a-z0-9_]{0,63}$` regex enforced before registration |
| Parameter injection | Unknown args rejected at registry level before executor sees them |
| Key storage | SHA-256 hashed in SQLite — raw key shown once, never stored |
| Timing attacks on key comparison | `secrets.compare_digest()` for admin key validation |

> **Note:** rate limiting is single-process in-memory. `workers=1` in production or add Redis if horizontally scaling.

## Setup

**Requirements:** Python 3.12+, a Groq API key.

```bash
git clone <your-repo-url>
cd devpulse
pip install -r requirements.txt
```

Generate an admin key:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Create `.env` (see `.env.example`):
```
GROQ_API_KEY=gsk_...
ADMIN_API_KEY=<your-generated-32-char-key>
```

## Running

**Terminal 1 — API backend:**
```bash
uvicorn app:app --reload --port 8000
```

**Terminal 2 — Streamlit control plane:**
```bash
streamlit run streamlit_app.py --server.port 8501
```

The FastAPI docs are at `http://localhost:8000/docs`.

**First-time setup — create your first API key:**
```bash
curl -X POST http://localhost:8000/v1/keys \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"label": "dev", "rate_limit_per_minute": 60}'
```

**Windows PowerShell example:**
```powershell
curl.exe -X POST http://localhost:8000/v1/keys `
  -H "X-API-Key: $env:ADMIN_API_KEY" `
  -H "Content-Type: application/json" `
  -d '{"label":"dev","rate_limit_per_minute":60}'
```

Copy the `raw_key` from the response — it's shown once only.

**Test a tool:**
```bash
curl -X POST http://localhost:8000/v1/execute \
  -H "X-API-Key: <your-key>" \
  -H "Content-Type: application/json" \
  -d '{"tool_name": "calculator", "args": {"expression": "2 ** 10 + 42"}}'
```

**Ask the agent:**
```bash
curl -X POST http://localhost:8000/v1/agent/chat \
  -H "X-API-Key: <your-key>" \
  -H "Content-Type: application/json" \
  -d '{"message": "Are there any critical CVEs for OpenSSL? Also convert 500 USD to PKR."}'
```

## Adding a new tool

Create a YAML file in `tools/`. The registry hot-reloads it immediately (no restart).

```yaml
# tools/my_tool.yaml
name: my_tool
version: "1.0"
description: >
  What this tool does and when the agent should call it.
  Be specific — this description IS the routing logic.
executor_type: http_get       # must be in the executor whitelist
enabled: true
tags: [my-tag]
config:
  url: "https://api.example.com/endpoint/{param}"
  timeout_seconds: 8
  response_path: "data.items"    # JMESPath to extract from response
args:
  - name: param
    type: string
    required: true
    description: "The parameter to pass"
```

**Executor types (whitelist):** `http_get`, `http_post`, `python_math`, `text_transform`, `datetime_tool`, `json_transform`, `mock_static`. Anything else is rejected at load time.

**Secrets in config:** use `$ENV{VAR_NAME}` — resolved from environment at load, never stored in the registry.

## Running tests

```bash
# No API key or network needed
ADMIN_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))") \
GROQ_API_KEY=gsk_fake \
python3 -m pytest tests/ -v
```

89 tests, all offline. Coverage: ToolSpec schema validation, YAML safety (RCE blocks confirmed), registry loading + hot-reload, all 7 executor types, rate limiter, SSRF blocklist, arg injection prevention.

## YAML tool definitions — the 11 pre-built tools

| Tool | Executor | Backend | Free? |
|---|---|---|---|
| `calculator` | `python_math` | AST stdlib | ✅ offline |
| `datetime_now` | `datetime_tool` | stdlib/zoneinfo | ✅ offline |
| `format_date` | `datetime_tool` | stdlib | ✅ offline |
| `text_transform` | `text_transform` | stdlib | ✅ offline |
| `hash_text` | `text_transform` | hashlib | ✅ offline |
| `json_extract` | `json_transform` | jmespath | ✅ offline |
| `mock_echo` | `mock_static` | — | ✅ offline |
| `get_weather` | `http_get` | Open-Meteo geocoding | ✅ no key |
| `convert_currency` | `http_get` | Frankfurter ECB rates | ✅ no key |
| `search_news` | `http_get` | HN Algolia | ✅ no key |
| `cve_search` | `http_get` | NIST NVD API v2 | ✅ no key, 5 req/30s |

## Deployment

Single-process (Streamlit Cloud or Railway):

1. Set `GROQ_API_KEY` and `ADMIN_API_KEY` as environment secrets
2. Entry point: `uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1`
3. Run Streamlit on a second dyno/process pointing at the API URL

For production, also set:
```
ENVIRONMENT=production
ALLOWED_URL_PREFIXES=https://api.frankfurter.dev,https://hn.algolia.com,...
```

## Brief evaluation criteria

| Criterion | How satisfied |
|---|---|
| New tools via YAML without code changes | Core design — watchdog hot-reload, executor whitelist, schema validation at load time |
| 11 working tools demonstrated | All 11 in `tools/`, 8 offline (no network), 3 calling real free APIs |
| Architecture is extensible | Add a new executor type in `executors/`, register in `executors/registry.py`, reference in any YAML |
| API playground UI | Streamlit playground page with auto-generated forms from the tool's arg schema |
| GitHub Actions deployment | Add `.github/workflows/deploy.yml` targeting your platform of choice |

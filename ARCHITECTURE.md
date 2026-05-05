# D.A.R.E. — MCP Server Setup

The D.A.R.E. MCP server exposes the entire pipeline as MCP tools so any
MCP-compatible client can drive recordings, normalization, simulation and
execution.

## Install

```bash
pip install -e .[recorder,vision,ocr,dsl,hitl,video,exec,mcp]
```

The ``mcp`` extra brings in either the official ``mcp`` package or
``fastmcp`` (the server prefers ``mcp`` if both are available).

## Start the server

```bash
dare --mcp
# or:
python -m dare.mcp_server
```

The server speaks **stdio** — the standard MCP transport for desktop
clients.

## Available tools

| Tool | Purpose |
|------|---------|
| `record` | Capture a desktop session. Stops on idle (30 s default) or double-ESC. |
| `normalize_run` | Convert raw events → ``ui_state`` / ``state_diff`` / ``action_graph``. |
| `generate_dsl` | Emit ``action.dsl.yaml``. |
| `build_intents` | Classify STATIC/DYNAMIC; write registry + per-action markdowns. |
| `clarify` | HITL clarification (``auto=True`` by default in MCP context). |
| `validate_run` | State-aware simulation + ``preview.mp4``. |
| `execute_run` | Run the DSL. ``live=False`` (default) is dry-run. |
| `run_full_pipeline` | Record → normalize → DSL → intents → clarify → validate, in one call. |

Every tool accepts an optional ``run_id``. Omit it to act on the latest
run.

## Client configuration

### Claude Code (`.mcp.json` in your project)

```json
{
  "mcpServers": {
    "dare": {
      "command": "dare",
      "args": ["--mcp"]
    }
  }
}
```

### Claude Desktop (`claude_desktop_config.json`)

* macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
* Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "dare": {
      "command": "/path/to/your/python",
      "args": ["-m", "dare.mcp_server"]
    }
  }
}
```

### Cursor (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "dare": {
      "command": "dare",
      "args": ["--mcp"]
    }
  }
}
```

### Continue (`~/.continue/config.json`)

```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "transport": {
          "type": "stdio",
          "command": "dare",
          "args": ["--mcp"]
        }
      }
    ]
  }
}
```

### Cline (VS Code settings)

Open the Cline panel → ``MCP Servers`` → add a new server with command
``dare`` and args ``["--mcp"]``.

## Typical session

1. `run_full_pipeline()` — records 30 s of desktop activity, normalizes
   it, generates DSL + intents, validates, and produces ``preview.mp4``.
2. Inspect ``preview.mp4`` (under ``runs/<run_id>/validation/``) and the
   intent markdowns under ``runs/<run_id>/docs/intents/``.
3. `execute_run(run_id="<id>", live=False)` — dry-run the DSL.
4. `execute_run(run_id="<id>", live=True, params={"a1": "0.5"})` — go live
   with a parameter override.

The pipeline writes everything under ``runs/<run_id>/`` so concurrent
runs never interfere.

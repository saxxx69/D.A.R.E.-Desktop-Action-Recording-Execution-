# D.A.R.E. — Desktop Action Recording Execution

A complete LLM-integrated plugin for recording, processing, and replaying desktop interactions through an MCP server interface.

## Installation

```bash
git clone https://github.com/saxxx69/D.A.R.E.-Desktop-Action-Recording-Execution-.git
cd D.A.R.E.-Desktop-Action-Recording-Execution-
pip install -e .
```

## Quick Start

```bash
# Start the MCP server
dare

# Or run with a specific run ID
dare --run-id my_session
```

## How It Works

D.A.R.E. implements a 7-stage pipeline:

1. **Record** — Capture mouse clicks, keyboard input, screenshots
2. **Normalize** — Extract and structure UI state from recordings
3. **Generate DSL** — Create YAML action definitions
4. **Classify** — Mark actions as static or dynamic
5. **Validate** — Simulate actions and verify expected effects
6. **Execute** — Run DSL with dry-run or live modes
7. **MCP Server** — Expose pipeline as tools for LLM clients

## Usage with LLMs

D.A.R.E. runs as an MCP server, exposing the entire pipeline as tools. Configure it in your LLM client:

### Claude Desktop
Add to `~/.claude/desktop.json`:

```json
{
  "mcpServers": {
    "dare": {
      "command": "dare",
      "args": [],
      "disabled": false
    }
  }
}
```

### Other Clients
Any MCP-compatible client (Cursor, Continue, Cline) can connect via:

```bash
dare  # Runs on stdio by default
```

## Platform Support

- **Windows** — Full support
- **macOS** — Accessibility + Screen Recording permissions required
- **Linux (X11)** — Recommended
- **Wayland** — Requires X11 compatibility layer

## Architecture

See `ARCHITECTURE.md` for design details.

## License

MIT

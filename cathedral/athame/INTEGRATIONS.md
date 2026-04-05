# ATHAME Integration Matrix

## SSH Access (Tailscale)
- **furnace** → `ssh furnace` (M4 Mini, Merlin's node) - Currently offline
- **athame** → `ssh athame` (M5 Max, this machine)
- Config: `~/.ssh/config` (auto-connects via Tailscale)

## MCP Servers (Model Context Protocol)
- **filesystem** - Documents & Projects dirs (npx @modelcontextprotocol/server-filesystem)
- **git** - Git operations (npx @modelcontextprotocol/server-git)
- **github** - GitHub API (requires GITHUB_TOKEN env var)
- **webhook** - File-based webhook receiver

## Webhooks ✅
- **HTTP Server**: Flask daemon on port 8081
- **Directory**: `/Users/odinbot33/.hermes/webhooks`
- **Endpoints**:
  - `POST /webhook` - Asynchronous (returns 202, triggers Hermes in background)
  - `POST /hermes` - Synchronous (blocks until Hermes completes, returns response)
  - `GET /health` - Health check
  - `GET /status` - Daemon status
- **Control**:
  - Start: `/Users/odinbot33/.hermes/webhooks/start-daemon.sh`
  - Stop: `/Users/odinbot33/.hermes/webhooks/stop-daemon.sh`
- **Logs**: `/Users/odinbot33/.hermes/webhooks/daemon.log`

## Named Pipes (FIFO)
- **Path**: `/tmp/gandalf_pipe`
- **Usage**: `echo "query" > /tmp/gandalf_pipe` (manual trigger required)
- **Daemon needed**: `while true; do read line < /tmp/gandalf_pipe; hermes --prompt "$line"; done`

## Cron Integration
- **Crontab**: Active (6 jobs)
- **Add job**: `crontab -e`
- **Hermes cron**: `hermes --prompt "query" | tee -a /path/to/log`

## Process Integration
- **Stdin**: `echo "query" | hermes`
- **File**: `hermes --prompt-file /path/to/query.txt`
- **REST API**: Requires external daemon (see below)

## Quick Start
```bash
# SSH to Merlin (FURNACE - M4 Mini)
ssh furnace

# Webhook (async - returns immediately, Hermes runs in background)
curl -X POST http://localhost:8081/webhook \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "check system status"}'

# Hermes directly (sync - waits for response)
curl -X POST http://localhost:8081/hermes \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "check system status"}'

# Cron add
crontab -e
# Add: */5 * * * * ~/.local/bin/hermes chat -q "health check" -Q >> ~/logs/health.log 2>&1
```

## External Daemons Needed
1. **Webhook HTTP Server** - Listen on port 8080, route to Hermes
2. **Named Pipe Daemon** - Read from /tmp/gandalf_pipe, spawn Hermes
3. **REST API Server** - Full HTTP API for programmatic access

## Status
- SSH Config: ✅ Fixed (furnace→odins-mac-mini, athame→furnace)
- FURNACE Node: ✅ Online (M4 Mini - Merlin's node)
- MCP Config: ✅ Added to ~/.hermes/config.yaml
- Webhook Daemon: ✅ Running via launchd (PID: 48928, port 8081)
- Named Pipe: ✅ Created (requires read daemon)
- Launchd Plist: ✅ Auto-start on boot configured

## Running Services
- **Webhook Daemon**: Port 8081 (Flask, auto-start via launchd)
- **Hermes CLI**: ~/.local/bin/hermes
- **MLX LM Server**: Port 8080 (Qwen3.5-27B)
- **FURNACE SSH**: ✅ Available (M4 Mini - always on)

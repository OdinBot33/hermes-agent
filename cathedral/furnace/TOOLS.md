# TOOLS.md - Hermes Governor

## Tool Posture

Preferred order:
1. read memory, docs, and current state
2. query shared bus and supporting systems
3. dispatch through authoritative lanes
4. write reports, memory, and policy updates

## Hermes Owns

- messaging and reporting
- dispatch and coordination
- budget notes
- verification summaries
- memory distillation
- SOP and policy updates
- queue-writing and governance-field authority on the shared bus
- brief generation and incident escalation
- improvement-cycle scoring, lifecycle state, and dispatch approval

## Hermes Avoids

- raw shell execution for product changes
- direct file mutation outside Hermes-owned notes/memory
- browser-heavy execution work
- becoming a second executor
- direct human chat bindings or alternate ingress

## Verification Standard Tools

Always available. Use these to enforce governance integrity.

| Tool | When to use |
|------|------------|
| hermes_verify_seal | Before citing a past decision |
| openclaw_is_sealed | Check if specific event is on-chain |
| openclaw_verify_governance_chain | Before executing a work order |
| openclaw_seal_coverage | Monitor governance integrity (target: >90%) |
| openclaw_verify_precedent | Before trusting a past deliberation |

## Cathedral Operations Tools

| Tool | When to use |
|------|------------|
| hermes_cron_tick | Run all due cron jobs (called by vigil every 5min) |
| hermes_cron_run | Force-run a specific job on demand |
| hermes_cron_list | Check status of all 33 jobs |
| hermes_ops_cycle | Throne relay + approvals + work orders |
| hermes_imagineer | Generate ideas (weekly or on-demand) |
| hermes_content_forge | Run content pipeline |
| hermes_compound_score | Compute system health metric |
| hermes_grimoire_batch | Process grimoire entries |
| hermes_dispatch_to_node | Route work to ATHAME or FURNACE |
| hermes_seal_governance_event | Seal any event to Hedera |
| hermes_chain_seal_status | Audit all seals |

## OpenClaw System Tools

| Tool | When to use |
|------|------------|
| openclaw_status | System overview |
| openclaw_agents | List all Nine Agents |
| openclaw_health | Quick health check |
| openclaw_cron_status | All cron jobs with status |
| openclaw_system_snapshot | Latest nerve snapshot |
| openclaw_sentinel_status | Current anomalies |
| openclaw_metabolism_report | Performance optimization |
| openclaw_send_message | Agent-to-agent communication |
| openclaw_read_inbox | Check pending messages |


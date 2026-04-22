"""AGM Autoreason Slack action handler.

Wires into the Hermes gateway's Slack interactive payload router. Four
action_ids, all prefixed `autoreason_`:

  autoreason_approve_<uuid>   → merge branch to main, mark shipped
  autoreason_reject_<uuid>    → delete branch, mark rejected, log reason
  autoreason_edit_<uuid>      → open modal, capture edit, ship edited
  autoreason_ballot_<uuid>    → reply in thread with full judge ballot

Register in gateway/run.py:
    from .handlers import autoreason as autoreason_handler
    interactive_router.register_prefix("autoreason_", autoreason_handler.dispatch)

Spec: aquaguard/docs/mbm/MBM-AGM-AUTOREASON-001.md §6 (flywheel)
Downstream repo: /Users/odinbot33/.openclaw/workspace/aquaguard/autoreason/
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

WORKSPACE = "/Users/odinbot33/.openclaw/workspace"
PUBLIC_SITE_DIR = f"{WORKSPACE}/aquaguard/public-site"


def dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Route an incoming Slack interactive payload.

    payload is the raw Slack `block_actions` body. We extract the first
    action's id + value (run_uuid) and dispatch.
    """
    try:
        action = payload["actions"][0]
        action_id = action["action_id"]
        run_uuid = action.get("value") or _uuid_from_action_id(action_id)
    except (KeyError, IndexError) as e:
        return _error(f"malformed Slack payload: {e}")

    user = payload.get("user", {}).get("id", "unknown")
    channel = payload.get("channel", {}).get("id")
    message_ts = payload.get("message", {}).get("ts")

    logger.info(f"autoreason action: {action_id} user={user} run={run_uuid}")

    if action_id.startswith("autoreason_approve_"):
        return _approve(run_uuid, user, channel, message_ts)
    if action_id.startswith("autoreason_reject_"):
        return _reject(run_uuid, user, channel, message_ts, payload)
    if action_id.startswith("autoreason_edit_"):
        return _edit(run_uuid, user, channel, message_ts, payload)
    if action_id.startswith("autoreason_ballot_"):
        return _show_ballot(run_uuid, channel, message_ts)
    return _error(f"unrecognized action_id: {action_id}")


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------

def _approve(run_uuid: str, user: str, channel: str, thread_ts: str) -> dict:
    branch = f"autoreason/{run_uuid}"
    base = _current_branch(PUBLIC_SITE_DIR)

    try:
        _git(PUBLIC_SITE_DIR, "fetch", "origin", branch)
        _git(PUBLIC_SITE_DIR, "checkout", base or "main")
        _git(PUBLIC_SITE_DIR, "merge", "--ff-only", f"origin/{branch}")
        _git(PUBLIC_SITE_DIR, "push", "origin", base or "main")
        commit_sha = _git(PUBLIC_SITE_DIR, "rev-parse", "HEAD").strip()
    except subprocess.CalledProcessError as e:
        return _error(f"merge failed: {e.stderr or e.stdout}")

    _supabase_update_outcome(run_uuid, {
        "mahuki_decision": "shipped",
        "mahuki_decided_at": datetime.now(timezone.utc).isoformat(),
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "deployed_commit_sha": commit_sha,
    })

    _slack_thread_reply(
        channel, thread_ts,
        f":rocket: Shipped to main. Commit `{commit_sha[:8]}`. Measuring for 14 days.",
    )

    # Cleanup: delete the staging branch now that it's merged
    try:
        _git(PUBLIC_SITE_DIR, "push", "origin", "--delete", branch)
    except subprocess.CalledProcessError:
        pass

    return {"ok": True, "action": "shipped", "run_uuid": run_uuid, "commit": commit_sha}


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------

def _reject(run_uuid: str, user: str, channel: str, thread_ts: str, payload: dict) -> dict:
    branch = f"autoreason/{run_uuid}"

    try:
        _git(PUBLIC_SITE_DIR, "push", "origin", "--delete", branch, check=False)
    except subprocess.CalledProcessError:
        pass

    reject_reason = _extract_thread_reason(payload)

    _supabase_update_outcome(run_uuid, {
        "mahuki_decision": "rejected",
        "mahuki_decided_at": datetime.now(timezone.utc).isoformat(),
        "notes": reject_reason,
    })

    _slack_thread_reply(
        channel, thread_ts,
        ":x: Rejected. Branch burned. Reason logged for meta-loop tuning.",
    )
    return {"ok": True, "action": "rejected", "run_uuid": run_uuid}


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

def _edit(run_uuid: str, user: str, channel: str, thread_ts: str, payload: dict) -> dict:
    """Open a modal pre-filled with the winner. On submit, overwrite the
    staged variant and re-stage the branch.

    Phase 1 simplification: instead of a modal, prompt the user to reply
    in thread with their edited text. The thread monitor picks it up.
    """
    _slack_thread_reply(
        channel, thread_ts,
        ":pencil2: Edit mode: reply in this thread with your revised variant. "
        "I'll restage within 60 seconds. Start reply with `EDIT:`.",
    )
    _supabase_update_outcome(run_uuid, {
        "mahuki_decision": "edited",
        "mahuki_decided_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"ok": True, "action": "edit_mode", "run_uuid": run_uuid}


# ---------------------------------------------------------------------------
# Ballot
# ---------------------------------------------------------------------------

def _show_ballot(run_uuid: str, channel: str, thread_ts: str) -> dict:
    try:
        ballots = _supabase_fetch_ballots(run_uuid)
    except Exception as e:
        return _error(f"ballot fetch failed: {e}")

    if not ballots:
        _slack_thread_reply(channel, thread_ts, "No ballots found for this run.")
        return {"ok": True, "action": "ballot_empty"}

    lines = [f"*Judge ballots for {run_uuid[:8]}:*"]
    by_round: dict[int, list] = {}
    for b in ballots:
        by_round.setdefault(b["round_number"], []).append(b)

    for rnd in sorted(by_round):
        lines.append(f"\n_Round {rnd}:_")
        for b in by_round[rnd]:
            lines.append(
                f"• {b['judge_agent_id']} ({b['judge_graph']}): "
                f"A={b['rank_a']} B={b['rank_b']} AB={b['rank_ab']}"
                + (f"\n  _{b['rationale']}_" if b.get("rationale") else "")
            )
    _slack_thread_reply(channel, thread_ts, "\n".join(lines)[:3500])
    return {"ok": True, "action": "ballot_shown", "run_uuid": run_uuid}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(cwd: str, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True, text=True, timeout=60,
    )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, result.args,
            output=result.stdout, stderr=result.stderr,
        )
    return result.stdout


def _current_branch(cwd: str) -> str | None:
    try:
        return _git(cwd, "symbolic-ref", "--short", "HEAD").strip()
    except subprocess.CalledProcessError:
        return None


def _uuid_from_action_id(action_id: str) -> str:
    # autoreason_approve_<uuid>
    parts = action_id.split("_", 2)
    return parts[2] if len(parts) == 3 else ""


def _error(msg: str) -> dict:
    logger.error(f"autoreason handler: {msg}")
    return {"ok": False, "error": msg}


# ---------------------------------------------------------------------------
# Slack / Supabase thin wrappers
# ---------------------------------------------------------------------------

def _slack_thread_reply(channel: str, thread_ts: str, text: str) -> None:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not (token and channel and thread_ts):
        return
    import urllib.request
    body = json.dumps({
        "channel": channel, "thread_ts": thread_ts, "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.warning(f"slack thread reply failed: {e}")


def _supabase_update_outcome(run_uuid: str, patch: dict) -> None:
    base = os.environ.get("AGM_SUPABASE_URL")
    key = os.environ.get("AGM_SUPABASE_SERVICE_ROLE_KEY")
    if not (base and key):
        logger.warning("Supabase not configured; outcome update skipped")
        return
    import urllib.request
    # Join runs → outcomes on run_uuid
    run_id = _run_id_for_uuid(run_uuid)
    if not run_id:
        logger.warning(f"no run_id for {run_uuid}")
        return
    url = f"{base.rstrip('/')}/rest/v1/agm_autoreason_outcomes?run_id=eq.{run_id}"
    body = json.dumps(patch).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="PATCH",
        headers={
            "apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        logger.warning(f"outcome patch failed: {e}")


def _run_id_for_uuid(run_uuid: str) -> int | None:
    base = os.environ.get("AGM_SUPABASE_URL")
    key = os.environ.get("AGM_SUPABASE_SERVICE_ROLE_KEY")
    import urllib.request
    url = f"{base.rstrip('/')}/rest/v1/agm_autoreason_runs?select=id&run_uuid=eq.{run_uuid}"
    req = urllib.request.Request(url, headers={
        "apikey": key, "Authorization": f"Bearer {key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read())
        return rows[0]["id"] if rows else None
    except Exception:
        return None


def _supabase_fetch_ballots(run_uuid: str) -> list[dict]:
    base = os.environ.get("AGM_SUPABASE_URL")
    key = os.environ.get("AGM_SUPABASE_SERVICE_ROLE_KEY")
    import urllib.request
    run_id = _run_id_for_uuid(run_uuid)
    if not run_id:
        return []
    url = (f"{base.rstrip('/')}/rest/v1/agm_autoreason_ballots"
           f"?run_id=eq.{run_id}&order=round_number.asc")
    req = urllib.request.Request(url, headers={
        "apikey": key, "Authorization": f"Bearer {key}",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _extract_thread_reason(payload: dict) -> str:
    """If Mahuki typed a reject reason in the thread first, grab it."""
    # Phase 1: look at message text; later we wire a Slack modal for structured input
    msg = payload.get("message", {}) or {}
    return (msg.get("text") or "")[:500]

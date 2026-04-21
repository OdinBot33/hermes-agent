import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def module():
    module_path = Path(os.environ.get("HERMES_TASK_BRIDGE_TEST_PATH", Path.home() / ".hermes" / "bin" / "cathedral_task_bridge.py"))
    if not module_path.exists():
        pytest.skip(f"cathedral task bridge not installed at {module_path}")

    spec = importlib.util.spec_from_file_location("cathedral_task_bridge", module_path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_parse_task_directives_extracts_priority_project_assignee_and_route(module):
    parsed = module.parse_task_directives("/task p0 agm @merlin route=code fix heater issue")
    assert parsed["priority"] == "P0"
    assert parsed["project"] == "agm"
    assert parsed["assignee"] == "merlin"
    assert parsed["route"] == "code"
    assert parsed["text"] == "fix heater issue"
    assert parsed["route_source"] == "explicit"


def test_parse_task_directives_infers_route_for_high_priority_fix(module):
    parsed = module.parse_task_directives("task: p1 cathedral restart paperclip daemon")
    assert parsed["priority"] == "P1"
    assert parsed["project"] == "cathedral"
    assert parsed["route"] == "fix"
    assert parsed["route_source"] == "inferred"
    assert parsed["text"] == "restart paperclip daemon"


def test_parse_task_directives_does_not_infer_route_for_non_gandalf_assignee(module):
    parsed = module.parse_task_directives("/task p1 agm @merlin investigate hiring gap")
    assert parsed["assignee"] == "merlin"
    assert parsed["route"] is None
    assert parsed["route_source"] == "none"


def test_build_queue_payload_includes_context(module):
    parsed = module.parse_task_directives("/task p0 agm route=research investigate tech shortage")
    payload = module.build_queue_payload(parsed=parsed, task_id=42, source="telegram-direct")
    assert payload["mode"] == "research"
    assert payload["source"] == "telegram-direct"
    assert "Task board item #42" in payload["prompt"]
    assert "project=agm" in payload["prompt"]
    assert "investigate tech shortage" in payload["prompt"]


def test_create_task_from_text_enqueues_when_route_present(monkeypatch, module):
    class FakeConn:
        def execute(self, *_args, **_kwargs):
            class Row:
                def fetchone(self_inner):
                    return {
                        "id": 123,
                        "title": "[telegram] restart paperclip",
                        "status": "open",
                        "priority": "P0",
                        "project": "cathedral",
                        "assignee": "gandalf",
                        "source": "telegram-direct",
                        "created_at": "2026-04-12T00:00:00",
                    }

            return Row()

    monkeypatch.setattr(module, "connect_tasks", lambda: FakeConn())
    monkeypatch.setattr(module, "add_task", lambda *_a, **_k: 123)
    monkeypatch.setattr(module, "sync_obsidian", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "submit_queue_task", lambda payload: {"ok": True, "task_id": "queue-1", "path": "/tmp/q.json", "payload": payload})

    result = module.create_task_from_text(text="/task p0 route=fix restart paperclip", source="telegram-direct", platform="telegram", user="Mahuki", chat_id="123")
    assert result["ok"] is True
    assert result["route"] == "fix"
    assert result["queue_submission"]["ok"] is True
    assert result["queue_submission"]["task_id"] == "queue-1"


def test_parse_operator_command_distinguishes_create_list_and_status_updates(module):
    create_command = module.parse_operator_command("start paperclip daemon")
    assert create_command == {"action": "create", "text": "start paperclip daemon"}

    list_command = module.parse_operator_command("list 5")
    assert list_command == {"action": "list", "limit": 5}

    done_command = module.parse_operator_command("done #42")
    assert done_command == {"action": "status", "status": "done", "task_id": 42}

    start_command = module.parse_operator_command("start 7")
    assert start_command == {"action": "status", "status": "in_progress", "task_id": 7}

    block_command = module.parse_operator_command("block 9")
    assert block_command == {"action": "status", "status": "blocked", "task_id": 9}


def test_change_task_status_returns_updated_task(monkeypatch, module):
    state = {"status": "open"}

    def fake_get_task(_conn, task_id):
        if task_id != 9:
            return {}
        return {
            "id": 9,
            "title": "Fix paperclip",
            "status": state["status"],
            "priority": "P1",
            "project": "cathedral",
            "assignee": "gandalf",
            "source": "telegram-direct",
            "created_at": "2026-04-12T00:00:00",
        }

    def fake_update_task(_conn, task_id, **kwargs):
        assert task_id == 9
        state["status"] = kwargs["status"]
        return True

    monkeypatch.setattr(module, "connect_tasks", lambda: object())
    monkeypatch.setattr(module, "get_task", fake_get_task, raising=False)
    monkeypatch.setattr(module, "update_task", fake_update_task, raising=False)
    monkeypatch.setattr(module, "sync_obsidian", lambda *_a, **_k: None)

    result = module.change_task_status(task_id=9, status="done", agent="telegram-direct")
    assert result["ok"] is True
    assert result["id"] == 9
    assert result["priority"] == "P1"
    assert result["project"] == "cathedral"
    assert result["assignee"] == "gandalf"
    assert result["previous_status"] == "open"
    assert result["status"] == "done"
    assert result["status_changed"] is True


def test_primary_inbox_includes_next_actions(monkeypatch, module):
    class FakeConn:
        row_factory = None

        def execute(self, query, params=()):
            query = " ".join(query.split())
            if "COUNT(*) AS total" in query:
                class Summary:
                    def fetchone(self_inner):
                        return {
                            "total": 4,
                            "open": 1,
                            "in_progress": 2,
                            "blocked": 0,
                            "done": 1,
                            "cancelled": 0,
                        }
                return Summary()
            if "FROM tasks WHERE status IN ('open', 'in_progress', 'blocked') ORDER BY" in query:
                class Rows:
                    def fetchall(self_inner):
                        return [
                            {
                                "id": 7,
                                "title": "Fix paperclip",
                                "status": "in_progress",
                                "priority": "P0",
                                "project": "cathedral",
                                "assignee": "gandalf",
                                "source": "linear",
                                "updated_at": "2026-04-12T00:00:00",
                            },
                            {
                                "id": 8,
                                "title": "Research AGM staffing",
                                "status": "open",
                                "priority": "P1",
                                "project": "agm",
                                "assignee": "gandalf",
                                "source": "telegram-direct",
                                "updated_at": "2026-04-12T00:00:00",
                            },
                        ]
                return Rows()
            if "GROUP BY source" in query:
                class SourceRows:
                    def fetchall(self_inner):
                        return [("linear", 1), ("telegram-direct", 1)]
                return SourceRows()
            raise AssertionError(query)

    monkeypatch.setattr(module, "connect_tasks", lambda: FakeConn())
    inbox = module.primary_inbox(limit=5)
    assert inbox["next_actions"]
    assert "Fix paperclip" in inbox["next_actions"][0]
    assert "Research AGM staffing" in inbox["next_actions"][1]

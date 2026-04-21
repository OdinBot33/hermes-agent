"""Regression tests for queued CLI background-review notifications."""

from tests.cli.test_cli_init import _make_cli


def test_background_review_notifications_are_queued_until_drain(monkeypatch):
    import cli as cli_mod

    cli_obj = _make_cli()
    printed = []

    monkeypatch.setattr(cli_mod, "_cprint", lambda text: printed.append(text))

    cli_obj._queue_background_review_notification("💾 Skill 'demo-skill' created.")

    assert printed == []

    cli_obj._drain_background_review_notifications()

    assert printed == ["  💾 Skill 'demo-skill' created."]
    assert cli_obj._pending_background_review_notifications.empty()


def test_init_agent_registers_queued_background_review_callback(monkeypatch):
    import cli as cli_mod

    cli_obj = _make_cli()

    class FakeAgent:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.background_review_callback = None
            self._print_fn = None

    monkeypatch.setattr(cli_mod, "AIAgent", FakeAgent)
    monkeypatch.setattr(cli_obj, "_ensure_runtime_credentials", lambda: True)

    ok = cli_obj._init_agent(
        model_override="test/model",
        runtime_override={
            "api_key": "test-key",
            "base_url": "https://example.invalid/v1",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
            "credential_pool": None,
        },
    )

    assert ok is True
    assert cli_obj.agent.background_review_callback == cli_obj._queue_background_review_notification

    cli_obj.agent.background_review_callback("💾 Skill 'demo-skill' created.")

    assert (
        cli_obj._pending_background_review_notifications.get_nowait()
        == "💾 Skill 'demo-skill' created."
    )

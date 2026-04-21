import pytest

from gateway.run import (
    _is_telegram_task_command,
    _safe_task_failure_reply,
    _strip_telegram_task_command,
)


@pytest.mark.parametrize("text", ["/task fix it", "/todo list", "task: fix it", "todo: list"])
def test_telegram_task_command_accepts_exact_task_prefixes(text):
    assert _is_telegram_task_command(text)


@pytest.mark.parametrize("text", ["/taskforce update", "/todoist sync", "task force update", "todo list"])
def test_telegram_task_command_rejects_prefix_collisions(text):
    assert not _is_telegram_task_command(text)


def test_strip_telegram_task_command_preserves_task_text():
    assert _strip_telegram_task_command("/task fix heater") == "fix heater"
    assert _strip_telegram_task_command("todo: list projects") == "list projects"


def test_safe_task_failure_reply_does_not_echo_helper_output():
    assert "traceback" not in _safe_task_failure_reply("traceback: secret path").lower()
    assert _safe_task_failure_reply("stderr details") == "Task command failed. Check gateway logs for details."

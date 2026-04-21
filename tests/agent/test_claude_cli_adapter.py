from __future__ import annotations

import json
from types import SimpleNamespace


def _result_payload(structured_output: dict, *, is_error: bool = False, result: str = "") -> str:
    return json.dumps({
        "type": "result",
        "subtype": "success" if not is_error else "error_max_turns",
        "is_error": is_error,
        "result": result,
        "stop_reason": "end_turn" if not is_error else "tool_use",
        "structured_output": structured_output,
        "usage": {
            "input_tokens": 11,
            "output_tokens": 7,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    })


def test_oversized_system_prompt_is_omitted_from_bridge_prompt():
    from agent.claude_cli_adapter import _build_system_prompt

    oversized = "A" * 5000
    prompt = _build_system_prompt([oversized], model="claude-opus-4-7")

    assert len(prompt) < 2000
    assert oversized not in prompt
    assert "model backend for Hermes Agent" in prompt


def test_system_prompt_pins_runtime_model_identity():
    from agent.claude_cli_adapter import _build_system_prompt

    prompt = _build_system_prompt(["Be concise."], model="claude-opus-4-7")

    assert "claude-opus-4-7" in prompt
    assert "do not guess" in prompt.lower()


def test_nested_json_content_is_unwrapped_into_final_text():
    from agent.claude_cli_adapter import _response_from_payload

    payload = {
        "structured_output": {
            "content": '{"content":"#!/bin/sh","tool_calls":[]}',
            "tool_calls": [],
        },
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    resp = _response_from_payload(payload, model="claude-opus-4-7")
    assert resp.choices[0].message.content == "#!/bin/sh"
    assert resp.choices[0].finish_reason == "stop"


def test_text_completion_uses_claude_cli_and_maps_structured_output(monkeypatch):
    from agent.claude_cli_adapter import ClaudeCodeCLIClient

    seen = {}

    def fake_run(cmd, *, text, capture_output, timeout, cwd, **kwargs):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        seen["cwd"] = cwd
        seen["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=_result_payload({"content": "hello from claude", "tool_calls": []}),
            stderr="",
        )

    monkeypatch.setattr("agent.claude_cli_adapter.subprocess.run", fake_run)

    client = ClaudeCodeCLIClient(timeout=42)
    resp = client.chat.completions.create(
        model="claude-opus-4-7",
        messages=[
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Say hello."},
        ],
        tools=[],
        stream=False,
    )

    assert resp.model == "claude-opus-4-7"
    assert resp.choices[0].message.content == "hello from claude"
    assert resp.choices[0].message.tool_calls is None
    assert resp.choices[0].finish_reason == "stop"
    assert resp.usage.prompt_tokens == 11
    assert resp.usage.completion_tokens == 7

    cmd = seen["cmd"]
    assert cmd[0] == "claude"
    assert "--output-format" in cmd and "json" in cmd
    assert "--json-schema" in cmd
    assert "--no-session-persistence" in cmd
    assert "--tools" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "claude-opus-4-7"
    max_turns_idx = cmd.index("--max-turns")
    assert cmd[max_turns_idx + 1] == "6"
    assert seen["timeout"] == 42
    assert "env" not in seen["kwargs"]


def test_tool_calls_are_mapped_to_openai_shape(monkeypatch):
    from agent.claude_cli_adapter import ClaudeCodeCLIClient

    payload = {
        "content": "",
        "tool_calls": [
            {"name": "read_file", "arguments_json": '{"path":"/tmp/x.txt"}'},
            {"name": "terminal", "arguments_json": '{"command":"printf TOOL_OK"}'},
        ],
    }

    def fake_run(cmd, *, text, capture_output, timeout, cwd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=_result_payload(payload), stderr="")

    monkeypatch.setattr("agent.claude_cli_adapter.subprocess.run", fake_run)

    client = ClaudeCodeCLIClient()
    resp = client.chat.completions.create(
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "Use a tool."}],
        tools=[
            {"type": "function", "function": {"name": "read_file", "description": "Read a file", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "terminal", "description": "Run a command", "parameters": {"type": "object"}}},
        ],
        stream=False,
    )

    tool_calls = resp.choices[0].message.tool_calls
    assert len(tool_calls) == 2
    assert tool_calls[0].type == "function"
    assert tool_calls[0].function.name == "read_file"
    assert json.loads(tool_calls[0].function.arguments) == {"path": "/tmp/x.txt"}
    assert tool_calls[1].function.name == "terminal"
    assert resp.choices[0].finish_reason == "tool_calls"


def test_stream_mode_emits_openai_like_chunks(monkeypatch):
    from agent.claude_cli_adapter import ClaudeCodeCLIClient

    def fake_run(cmd, *, text, capture_output, timeout, cwd, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=_result_payload({
                "content": "",
                "tool_calls": [{"name": "terminal", "arguments_json": '{"command":"printf TOOL_OK"}'}],
            }),
            stderr="",
        )

    monkeypatch.setattr("agent.claude_cli_adapter.subprocess.run", fake_run)

    client = ClaudeCodeCLIClient()
    stream = client.chat.completions.create(
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "Use terminal."}],
        tools=[{"type": "function", "function": {"name": "terminal", "description": "Run a command", "parameters": {"type": "object"}}}],
        stream=True,
    )

    chunks = list(stream)
    assert len(chunks) >= 2
    first = chunks[0]
    assert hasattr(first.choices[0].delta, "content")
    assert (first.choices[0].delta.content or "") == ""
    tc = first.choices[0].delta.tool_calls[0]
    assert tc.function.name == "terminal"
    assert tc.function.arguments == '{"command":"printf TOOL_OK"}'
    assert first.choices[0].finish_reason == "tool_calls"
    assert chunks[-1].choices == []
    assert chunks[-1].usage.total_tokens == 18

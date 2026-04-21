"""OpenAI-compatible facade over Claude Code CLI print mode.

This adapter lets Hermes run a profile against Claude Code's subscription-backed
CLI path instead of Anthropic's raw API. It shells out to:

  claude -p ... --output-format json --json-schema ...

and converts the structured result into the subset of the OpenAI chat
completions interface that ``run_agent.py`` expects.

The CLI itself is treated as the model runtime only:
- Claude Code's own tools are disabled (``--tools ''``)
- the Hermes conversation/tool state is flattened into a prompt
- the CLI returns either assistant text or tool calls in schema-validated JSON
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_CLAUDE_ENV_BLOCKLIST = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
}


_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "arguments_json": {"type": "string"},
                },
                "required": ["name", "arguments_json"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["content", "tool_calls"],
    "additionalProperties": False,
}


def _coerce_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: List[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                pieces.append(part["text"])
        return "\n".join(pieces)
    return str(content)


def _normalize_messages_for_prompt(messages: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(messages, list):
        return normalized
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        entry: Dict[str, Any] = {"role": str(msg.get("role") or "user")}
        content = _coerce_content_to_text(msg.get("content"))
        if content:
            entry["content"] = content
        if msg.get("tool_calls"):
            tool_calls: List[Dict[str, Any]] = []
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                tool_calls.append({
                    "id": tc.get("id") or "",
                    "name": str(fn.get("name") or ""),
                    "arguments": str(fn.get("arguments") or ""),
                })
            if tool_calls:
                entry["tool_calls"] = tool_calls
        if msg.get("tool_call_id"):
            entry["tool_call_id"] = str(msg.get("tool_call_id"))
        if msg.get("name"):
            entry["name"] = str(msg.get("name"))
        normalized.append(entry)
    return normalized


def _normalize_tools_for_prompt(tools: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(tools, list):
        return normalized
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") or {}
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        normalized.append({
            "name": name,
            "description": str(fn.get("description") or ""),
            "parameters": fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object"},
        })
    return normalized


def _tool_choice_hint(tool_choice: Any) -> Dict[str, Any]:
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function") or {}
        name = str(fn.get("name") or "").strip()
        if name:
            return {"mode": "named", "name": name}
    if isinstance(tool_choice, str) and tool_choice.strip():
        return {"mode": tool_choice.strip()}
    return {"mode": "auto"}


def _split_system_messages(messages: Any) -> tuple[List[str], List[Dict[str, Any]]]:
    system_parts: List[str] = []
    rest: List[Dict[str, Any]] = []
    if not isinstance(messages, list):
        return system_parts, rest
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if str(msg.get("role") or "") == "system":
            text = _coerce_content_to_text(msg.get("content"))
            if text:
                system_parts.append(text)
            continue
        rest.append(msg)
    return system_parts, rest


def _build_system_prompt(system_messages: List[str], *, model: str) -> str:
    runtime_model = (model or "").strip() or "claude-opus-4-7"
    prefix = (
        "You are the model backend for Hermes Agent. "
        "Return only structured JSON matching the provided schema. "
        "Do not use Claude Code tools, do not inspect files, do not browse the web, "
        "and do not act as an autonomous agent outside the provided transcript. "
        "You are producing exactly one assistant turn for Hermes to execute. "
        f"The runtime model for this request is {runtime_model}. "
        f"If asked which model you are, answer with exactly {runtime_model} and do not guess from routing, defaults, or config."
    )
    joined = "\n\n".join(p for p in system_messages if p).strip()
    if not joined:
        return prefix
    if len(joined) > 2000:
        logger.info("Omitting oversized forwarded system prompt for Claude CLI bridge (%s chars)", len(joined))
        return (
            prefix
            + "\n\nCaller system prompt omitted because it exceeded the Claude CLI bridge budget. "
              "Use the provided transcript, tool list, and latest user request as the authoritative context."
        )
    return f"{prefix}\n\n{joined}"


def _build_user_prompt(messages: Any, tools: Any, tool_choice: Any) -> str:
    normalized_messages = _normalize_messages_for_prompt(messages)
    normalized_tools = _normalize_tools_for_prompt(tools)
    choice_hint = _tool_choice_hint(tool_choice)
    return (
        "Produce the next Hermes assistant turn from the conversation transcript below.\n\n"
        "Rules:\n"
        "- If a Hermes tool should be used next, leave content empty and emit one or more tool_calls.\n"
        "- If no Hermes tool is needed, put the assistant reply in content and emit tool_calls as [].\n"
        "- Each tool_calls[].arguments_json must be valid minified JSON for that tool's arguments.\n"
        "- Never call a tool that is not listed.\n"
        "- Obey tool_choice exactly.\n"
        "- Do not wrap JSON in markdown fences.\n\n"
        f"tool_choice = {json.dumps(choice_hint, ensure_ascii=False)}\n\n"
        f"conversation = {json.dumps(normalized_messages, ensure_ascii=False)}\n\n"
        f"tools = {json.dumps(normalized_tools, ensure_ascii=False)}\n"
    )


def _extract_payload(stdout: str) -> Dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        raise RuntimeError("Claude CLI returned empty stdout")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise RuntimeError("Claude CLI stdout was not valid JSON")


def _extract_structured_output(payload: Dict[str, Any]) -> Dict[str, Any]:
    structured = payload.get("structured_output")
    if isinstance(structured, dict):
        return structured

    raw_result = payload.get("result")
    if isinstance(raw_result, str) and raw_result.strip().startswith("{"):
        try:
            parsed = json.loads(raw_result)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

    raise RuntimeError("Claude CLI payload missing structured_output")


def _usage_from_payload(payload: Dict[str, Any]) -> SimpleNamespace:
    usage = payload.get("usage") or {}
    prompt_tokens = int(usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or 0)
    cached_tokens = int(
        usage.get("cache_read_input_tokens")
        or ((usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") if isinstance(usage.get("cache_creation"), dict) else 0)
        or 0
    )
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    return SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
    )


def _tool_calls_from_structured_output(structured: Dict[str, Any]) -> Optional[List[SimpleNamespace]]:
    raw_calls = structured.get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        return None

    tool_calls: List[SimpleNamespace] = []
    for item in raw_calls:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        args = str(item.get("arguments_json") or "{}").strip() or "{}"
        try:
            parsed = json.loads(args)
            if not isinstance(parsed, dict):
                args = json.dumps({"value": parsed}, ensure_ascii=False, separators=(",", ":"))
            else:
                args = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        except json.JSONDecodeError:
            args = "{}"
        if not name:
            continue
        tool_calls.append(SimpleNamespace(
            id=f"call_{uuid.uuid4().hex[:12]}",
            type="function",
            index=len(tool_calls),
            function=SimpleNamespace(name=name, arguments=args),
        ))
    return tool_calls or None


def _response_from_payload(payload: Dict[str, Any], *, model: str) -> SimpleNamespace:
    structured = _extract_structured_output(payload)
    tool_calls = _tool_calls_from_structured_output(structured)
    content = structured.get("content")
    if not isinstance(content, str):
        content = ""
    if not tool_calls and content.strip().startswith("{"):
        try:
            nested = json.loads(content)
        except json.JSONDecodeError:
            nested = None
        if isinstance(nested, dict):
            nested_calls = nested.get("tool_calls")
            nested_content = nested.get("content")
            if isinstance(nested_content, str) and (not nested_calls):
                content = nested_content
    finish_reason = "tool_calls" if tool_calls else "stop"
    message = SimpleNamespace(
        role="assistant",
        content=None if tool_calls else content,
        tool_calls=tool_calls,
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
    )
    choice = SimpleNamespace(index=0, message=message, finish_reason=finish_reason)
    return SimpleNamespace(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        object="chat.completion",
        created=int(time.time()),
        model=model,
        choices=[choice],
        usage=_usage_from_payload(payload),
    )


def _make_stream_chunk(*, model: str, content: str = "", tool_calls: Optional[List[SimpleNamespace]] = None,
                       finish_reason: Optional[str] = None) -> SimpleNamespace:
    delta_kwargs: Dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": tool_calls,
    }
    delta = SimpleNamespace(**delta_kwargs)
    choice = SimpleNamespace(index=0, delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        object="chat.completion.chunk",
        created=int(time.time()),
        model=model,
        choices=[choice],
        usage=None,
    )


class _ClaudeCLIStream:
    def __init__(self, response: SimpleNamespace):
        self._response = response
        self.response = None

    def __iter__(self) -> Iterable[SimpleNamespace]:
        message = self._response.choices[0].message
        if message.tool_calls:
            deltas = [
                SimpleNamespace(
                    index=getattr(tc, "index", i),
                    id=tc.id,
                    type="function",
                    function=SimpleNamespace(
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    ),
                )
                for i, tc in enumerate(message.tool_calls)
            ]
            yield _make_stream_chunk(
                model=self._response.model,
                tool_calls=deltas,
                finish_reason="tool_calls",
            )
        else:
            yield _make_stream_chunk(
                model=self._response.model,
                content=message.content or "",
                finish_reason="stop",
            )
        yield SimpleNamespace(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            object="chat.completion.chunk",
            created=int(time.time()),
            model=self._response.model,
            choices=[],
            usage=self._response.usage,
        )


class _ClaudeCLIChatCompletions:
    def __init__(self, client: "ClaudeCodeCLIClient"):
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        return self._client._create_chat_completion(**kwargs)


class _ClaudeCLIChat:
    def __init__(self, client: "ClaudeCodeCLIClient"):
        self.completions = _ClaudeCLIChatCompletions(client)


class ClaudeCodeCLIClient:
    """Minimal OpenAI-SDK-compatible facade backed by Claude Code CLI."""

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None,
                 default_headers: Optional[Dict[str, str]] = None, timeout: Any = None):
        self.api_key = api_key
        self.base_url = base_url or "claude-cli://local"
        self.default_headers = default_headers or {}
        try:
            self.timeout = int(timeout) if timeout is not None else 300
        except Exception:
            self.timeout = 300
        self.chat = _ClaudeCLIChat(self)

    def close(self) -> None:
        return None

    def _run_once(self, *, model: str, messages: Any, tools: Any, tool_choice: Any,
                  reasoning_effort: Any = None) -> SimpleNamespace:
        system_messages, non_system_messages = _split_system_messages(messages)
        system_prompt = _build_system_prompt(system_messages, model=model)
        user_prompt = _build_user_prompt(non_system_messages, tools, tool_choice)

        cmd = [
            "claude",
            "-p",
            user_prompt,
            "--system-prompt",
            system_prompt,
            "--model",
            model,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(_RESPONSE_SCHEMA, ensure_ascii=False, separators=(",", ":")),
            "--max-turns",
            "6",
            "--no-session-persistence",
            "--tools",
            "",
        ]
        if isinstance(reasoning_effort, str) and reasoning_effort.strip():
            cmd.extend(["--effort", reasoning_effort.strip()])

        child_env = os.environ.copy()
        for key in _CLAUDE_ENV_BLOCKLIST:
            child_env.pop(key, None)

        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=self.timeout,
            cwd=os.getcwd(),
            env=child_env,
        )
        payload = _extract_payload(proc.stdout)
        if proc.returncode != 0 or payload.get("is_error"):
            detail = payload.get("result") or payload.get("subtype") or proc.stderr.strip() or proc.stdout.strip()
            raise RuntimeError(f"Claude CLI request failed: {detail}")
        return _response_from_payload(payload, model=model)

    def _create_chat_completion(self, **kwargs: Any) -> Any:
        model = str(kwargs.get("model") or "claude-opus-4-7")
        messages = kwargs.get("messages") or []
        tools = kwargs.get("tools")
        tool_choice = kwargs.get("tool_choice", "auto")
        stream = bool(kwargs.get("stream"))
        reasoning_effort = None
        extra_body = kwargs.get("extra_body")
        if isinstance(extra_body, dict):
            output_config = extra_body.get("output_config")
            if isinstance(output_config, dict):
                reasoning_effort = output_config.get("effort")
        response = self._run_once(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            reasoning_effort=reasoning_effort,
        )
        if stream:
            return _ClaudeCLIStream(response)
        return response

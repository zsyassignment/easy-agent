"""BotMux runner protocol and prompt parsing helpers.

BotMux writes one base64-encoded JSON envelope per stdin line and recognizes
OSC 777 control frames in the runner's stdout. Keeping this protocol in a
small module makes the bridge independently testable and prevents model text
from being interpreted as a control frame.
"""

from __future__ import annotations

import base64
import hashlib
import html
from html.parser import HTMLParser
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

INPUT_PREFIX = "::botmux-learningflow:"
CONTROL_PREFIX = "\x1b]777;botmux:"
CONTROL_END = "\x07"
MAX_INPUT_BYTES = 4 * 1024 * 1024
VISIBLE_ESCAPE = "␛"
_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_SAFE_ID_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]+")
_SERVICE_USER_PATTERN = re.compile(
    r"^\s*(?:\[?service[-_ ]user\]?|service user)\s*[:：]\s*",
    re.IGNORECASE,
)


class ProtocolError(ValueError):
    """Raised when a BotMux runner envelope is malformed."""


@dataclass(frozen=True)
class RunnerMessage:
    content: str
    reply_turn_id: str | None = None
    trusted_caller: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedPrompt:
    message: str
    attachment_paths: tuple[str, ...] = ()


def decode_runner_line(line: str, *, prefix: str = INPUT_PREFIX) -> RunnerMessage | None:
    """Decode one BotMux runner line; blank pre-flush lines return ``None``."""
    stripped = line.strip()
    if not stripped:
        return None
    if not stripped.startswith(prefix):
        raise ProtocolError("input does not use the LearningFlow BotMux prefix")
    encoded = stripped[len(prefix):]
    if not encoded:
        raise ProtocolError("runner input payload is empty")
    if len(encoded) > MAX_INPUT_BYTES:
        raise ProtocolError("runner input exceeds the 4 MB protocol limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("runner input is not valid base64 JSON") from exc
    if not isinstance(payload, dict) or payload.get("type") != "message":
        raise ProtocolError("runner input must be a message envelope")
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ProtocolError("runner message content must be a non-empty string")
    reply_turn_id = payload.get("replyTurnId")
    if reply_turn_id is not None and not isinstance(reply_turn_id, str):
        reply_turn_id = str(reply_turn_id)
    trusted = payload.get("trustedCaller")
    if not isinstance(trusted, dict):
        trusted = {}
    return RunnerMessage(content=content, reply_turn_id=reply_turn_id, trusted_caller=trusted)


def encode_runner_line(
    content: str,
    *,
    reply_turn_id: str | None = None,
    trusted_caller: Mapping[str, Any] | None = None,
    prefix: str = INPUT_PREFIX,
) -> str:
    """Build a runner line. Primarily useful for smoke tests and diagnostics."""
    payload: dict[str, Any] = {"type": "message", "content": content}
    if reply_turn_id:
        payload["replyTurnId"] = reply_turn_id
    if trusted_caller:
        payload["trustedCaller"] = dict(trusted_caller)
    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return f"{prefix}{encoded}"


def escape_display(value: Any) -> str:
    """Make untrusted text unable to start a BotMux OSC control frame."""
    return str(value if value is not None else "").replace("\x1b", VISIBLE_ESCAPE)


def control_frame(kind: str, payload: Mapping[str, Any]) -> str:
    """Encode one authoritative BotMux OSC 777 marker."""
    if not _KIND_PATTERN.fullmatch(kind):
        raise ProtocolError(f"invalid control frame kind: {kind}")
    encoded = base64.b64encode(
        json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    ).decode("ascii")
    return f"{CONTROL_PREFIX}{kind}:{encoded}{CONTROL_END}"


class _PromptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.user_depth = 0
        self.attachments_depth = 0
        self.saw_user_message = False
        self.user_parts: list[str] = []
        self.paths: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "user_message":
            self.saw_user_message = True
            self.user_depth += 1
        elif lowered == "attachments" and not self.user_depth:
            self.attachments_depth += 1
        elif lowered == "file" and self.attachments_depth and not self.user_depth:
            values = {key.lower(): value for key, value in attrs}
            path = values.get("path")
            if path:
                self.paths.append(path)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() in {"user_message", "attachments"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "user_message" and self.user_depth:
            self.user_depth -= 1
        elif lowered == "attachments" and self.attachments_depth:
            self.attachments_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.user_depth:
            self.user_parts.append(data)


def parse_botmux_prompt(content: str) -> ParsedPrompt:
    """Extract the actual user message and local attachment paths.

    BotMux may wrap a turn in ``<user_message>`` and ``<attachments>``. Plain
    prompts continue to work, which is useful for manual CLI smoke tests.
    """
    parser = _PromptParser()
    try:
        parser.feed(content)
        parser.close()
    except Exception:
        # HTMLParser is tolerant, but plain text is always a safe fallback.
        parser = _PromptParser()

    if parser.saw_user_message:
        message = "".join(parser.user_parts).strip()
    else:
        message = _SERVICE_USER_PATTERN.sub("", content, count=1).strip()
    message = html.unescape(message)

    seen: set[str] = set()
    paths: list[str] = []
    for path in parser.paths:
        normalized = html.unescape(path).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)
    return ParsedPrompt(message=message, attachment_paths=tuple(paths))


def identity_for(session_id: str, trusted_caller: Mapping[str, Any] | None) -> tuple[str, str]:
    """Map BotMux identity to stable LearningFlow user and thread IDs."""
    caller = trusted_caller or {}
    app_id = str(caller.get("requestLarkAppId") or "").strip()
    open_id = str(caller.get("requestUserOpenId") or "").strip()
    if app_id and open_id:
        identity = f"{app_id}:{open_id}"
        user_id = "feishu-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    else:
        user_id = "botmux-user-" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    thread_id = "botmux-" + normalize_component(session_id, fallback="session", max_length=80)
    return user_id, thread_id


def normalize_component(value: Any, *, fallback: str, max_length: int) -> str:
    raw = str(value or fallback).strip() or fallback
    cleaned = _SAFE_ID_PATTERN.sub("_", raw).strip("_.-") or fallback
    if cleaned == raw and len(cleaned) <= max_length:
        return cleaned
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    head = cleaned[: max(1, max_length - len(digest) - 1)]
    return f"{head}-{digest}"

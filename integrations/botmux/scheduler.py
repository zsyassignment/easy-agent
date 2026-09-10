"""Thin adapter from LearningFlow reminders to BotMux's durable scheduler."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_REMINDER_PROMPT_PREFIX = "learningflow-reminder-v1:"
_TASK_ID_PATTERN = re.compile(r"\[([A-Za-z0-9_-]{4,128})\]")
_REQUIRED_ENV = ("BOTMUX_SESSION_ID", "BOTMUX_CHAT_ID", "BOTMUX_LARK_APP_ID")


class BotMuxScheduleError(RuntimeError):
    """Raised when a reminder cannot be registered with BotMux."""


@dataclass(frozen=True)
class BotMuxScheduleResult:
    task_id: str | None
    schedule: str
    already_registered: bool = False


def encode_reminder_prompt(reminder: Mapping[str, Any]) -> str:
    """Encode reminder data as a runner-only scheduled-turn payload."""
    payload = {
        "reminder_id": str(reminder.get("id") or ""),
        "message": str(reminder.get("message") or "").strip(),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return _REMINDER_PROMPT_PREFIX + encoded


def decode_reminder_prompt(value: str) -> dict[str, str] | None:
    """Decode a payload created by :func:`encode_reminder_prompt`."""
    text = value.strip()
    if not text.startswith(_REMINDER_PROMPT_PREFIX):
        return None
    encoded = text[len(_REMINDER_PROMPT_PREFIX):]
    if not encoded:
        return None
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    message = str(payload.get("message") or "").strip()
    if not message:
        return None
    return {
        "reminder_id": str(payload.get("reminder_id") or ""),
        "message": message,
    }


class BotMuxScheduler:
    """Register reminders through ``botmux schedule add`` without a shell."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        executable: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.environ = dict(os.environ if environ is None else environ)
        self.executable = executable or shutil.which("botmux", path=self.environ.get("PATH"))
        self._runner = runner

    @property
    def available(self) -> bool:
        return bool(self.executable) and all(self.environ.get(key) for key in _REQUIRED_ENV)

    def add_reminder(self, reminder: Mapping[str, Any]) -> BotMuxScheduleResult:
        if not self.available:
            missing = [key for key in _REQUIRED_ENV if not self.environ.get(key)]
            detail = f"missing {', '.join(missing)}" if missing else "botmux executable not found"
            raise BotMuxScheduleError(f"BotMux scheduler is unavailable: {detail}")

        schedule = self._schedule_expression(reminder)
        prompt = encode_reminder_prompt(reminder)
        message = str(reminder.get("message") or "").strip()
        name = f"LearningFlow: {message}"[:80]
        command = [
            *self._command_prefix(), "schedule", "add", schedule, prompt,
            "--name", name,
            "--chat-id", self.environ["BOTMUX_CHAT_ID"],
            "--lark-app-id", self.environ["BOTMUX_LARK_APP_ID"],
            "--workdir", str(Path.cwd()),
        ]
        root_message_id = self.environ.get("BOTMUX_ROOT_MESSAGE_ID", "")
        if root_message_id.startswith("om_"):
            command.extend(["--topic", "--root-msg-id", root_message_id])
        else:
            command.append("--top-level")

        try:
            completed = self._runner(
                command,
                env=self.environ,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BotMuxScheduleError(f"failed to run BotMux scheduler: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            raise BotMuxScheduleError(f"BotMux schedule add failed: {detail[:500]}")
        match = _TASK_ID_PATTERN.search(completed.stdout or "")
        return BotMuxScheduleResult(task_id=match.group(1) if match else None, schedule=schedule)

    def _command_prefix(self) -> list[str]:
        executable = str(self.executable)
        runtime = self.environ.get("BOTMUX_RUNTIME", "").strip()
        if runtime:
            return [runtime, executable]
        try:
            content = Path(executable).read_text(encoding="utf-8", errors="ignore")[:4096]
            script = re.search(r'exec\s+node\s+"([^"]+/dist/cli\.js)"', content)
            bun_runtime = _find_bun_runtime(self.environ)
            if script and bun_runtime:
                return [bun_runtime, script.group(1)]
        except OSError:
            pass
        return [executable]

    def _schedule_expression(self, reminder: Mapping[str, Any]) -> str:
        run_at_raw = str(reminder.get("run_at") or "").strip()
        repeat = str(reminder.get("repeat") or "once").strip().lower()
        try:
            run_at = datetime.fromisoformat(run_at_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BotMuxScheduleError("LearningFlow reminder has an invalid run_at") from exc
        if run_at.tzinfo is None:
            raise BotMuxScheduleError("LearningFlow reminder run_at must include a timezone")
        if repeat == "once":
            return run_at.isoformat(timespec="seconds")
        if repeat != "daily":
            raise BotMuxScheduleError(f"unsupported reminder repeat mode: {repeat}")

        reminder_timezone = str(reminder.get("timezone") or "Asia/Shanghai")
        botmux_timezone = _effective_botmux_timezone(self.environ)
        if reminder_timezone != botmux_timezone:
            raise BotMuxScheduleError(
                "daily reminders require the same timezone in LearningFlow and BotMux "
                f"(LearningFlow={reminder_timezone}, BotMux={botmux_timezone})"
            )
        try:
            local = run_at.astimezone(ZoneInfo(reminder_timezone))
        except ZoneInfoNotFoundError as exc:
            raise BotMuxScheduleError(f"unknown reminder timezone: {reminder_timezone}") from exc
        return f"{local.minute} {local.hour} * * *"


def _effective_botmux_timezone(environ: Mapping[str, str]) -> str:
    explicit = environ.get("BOTMUX_SCHEDULE_TIMEZONE", "").strip()
    if _valid_timezone(explicit):
        return explicit

    data_dir = Path(environ.get("SESSION_DATA_DIR", str(Path.home() / ".botmux" / "data")))
    config_path = data_dir.parent / "config.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        configured = str(payload.get("scheduleTimeZone") or "").strip()
        if _valid_timezone(configured):
            return configured
    except (OSError, ValueError, json.JSONDecodeError):
        pass

    tz_env = environ.get("TZ", "").strip()
    if _valid_timezone(tz_env):
        return tz_env
    try:
        target = Path("/etc/localtime").resolve()
        marker = "/zoneinfo/"
        if marker in str(target):
            candidate = str(target).split(marker, 1)[1]
            if _valid_timezone(candidate):
                return candidate
    except OSError:
        pass
    key = getattr(datetime.now().astimezone().tzinfo, "key", "")
    return key if _valid_timezone(key) else "UTC"


def _valid_timezone(value: str) -> bool:
    if not value:
        return False
    try:
        ZoneInfo(value)
        return True
    except ZoneInfoNotFoundError:
        return False


def _find_bun_runtime(environ: Mapping[str, str]) -> str | None:
    direct = shutil.which("bun", path=environ.get("PATH")) or shutil.which("bun.exe", path=environ.get("PATH"))
    if direct:
        return direct
    # BotMux's dev supervisor is commonly launched through `npx bun`; its Bun
    # package directory remains on PATH even when only `bun.exe` is present.
    for directory in environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory).parent / "bun" / "bin" / "bun.exe"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None

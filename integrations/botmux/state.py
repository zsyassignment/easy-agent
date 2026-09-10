"""Small atomic state store for BotMux bridge retries and LangGraph interrupts."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

_STATE_VERSION = 1


class BridgeStateStore:
    def __init__(self, session_id: str, directory: str | Path | None = None):
        root = Path(directory).expanduser() if directory else Path.home() / ".learningflow-botmux"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
        self.path = root / f"session-{digest}.json"
        self._state = self._load()

    @property
    def pending_interrupt(self) -> dict[str, Any] | None:
        value = self._state.get("pending_interrupt")
        return deepcopy(value) if isinstance(value, dict) else None

    def set_pending_interrupt(self, value: Mapping[str, Any]) -> None:
        self._state["pending_interrupt"] = dict(value)
        self._save()

    def clear_pending_interrupt(self) -> None:
        if self._state.get("pending_interrupt") is not None:
            self._state["pending_interrupt"] = None
            self._save()

    def was_uploaded(self, fingerprint: str) -> bool:
        return fingerprint in self._state.get("uploaded_attachments", {})

    def mark_uploaded(self, fingerprint: str, metadata: Mapping[str, Any]) -> None:
        uploads = self._state.setdefault("uploaded_attachments", {})
        uploads[fingerprint] = dict(metadata)
        self._save()

    def reminder_schedule(self, reminder_id: str) -> dict[str, Any] | None:
        value = self._state.get("reminder_schedules", {}).get(reminder_id)
        return deepcopy(value) if isinstance(value, dict) else None

    def mark_reminder_scheduled(self, reminder_id: str, metadata: Mapping[str, Any]) -> None:
        schedules = self._state.setdefault("reminder_schedules", {})
        schedules[reminder_id] = dict(metadata)
        self._save()

    def _load(self) -> dict[str, Any]:
        default = {
            "version": _STATE_VERSION,
            "pending_interrupt": None,
            "uploaded_attachments": {},
            "reminder_schedules": {},
        }
        if not self.path.exists():
            return default
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return default
        if not isinstance(loaded, dict):
            return default
        uploads = loaded.get("uploaded_attachments")
        schedules = loaded.get("reminder_schedules")
        return {
            "version": _STATE_VERSION,
            "pending_interrupt": loaded.get("pending_interrupt") if isinstance(loaded.get("pending_interrupt"), dict) else None,
            "uploaded_attachments": uploads if isinstance(uploads, dict) else {},
            "reminder_schedules": schedules if isinstance(schedules, dict) else {},
        }

    def _save(self) -> None:
        temporary = self.path.with_suffix(f".tmp-{os.getpid()}")
        data = json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            temporary.write_text(data, encoding="utf-8")
            temporary.chmod(0o600)
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

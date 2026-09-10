"""Synchronous HTTP/SSE client for the LearningFlow FastAPI service."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any, Generator, Iterable

import httpx


class LearningFlowClientError(RuntimeError):
    pass


class LearningFlowClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 600.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "LearningFlow-BotMux-Bridge/0.1"},
        )

    def close(self) -> None:
        self._client.close()

    def health(self) -> dict[str, Any]:
        try:
            response = self._client.get("/api/health")
            _raise_for_status(response)
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LearningFlowClientError(f"LearningFlow health check failed: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise LearningFlowClientError("LearningFlow health check did not return status=ok")
        return payload

    def upload_document(self, user_id: str, path: Path) -> dict[str, Any]:
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            with path.open("rb") as stream:
                response = self._client.post(
                    "/api/documents",
                    data={"user_id": user_id},
                    files={"file": (path.name, stream, media_type)},
                )
            _raise_for_status(response)
            payload = response.json()
        except (OSError, httpx.HTTPError, ValueError) as exc:
            raise LearningFlowClientError(f"document upload failed for {path.name}: {exc}") from exc
        document = payload.get("document") if isinstance(payload, dict) else None
        if not isinstance(document, dict):
            raise LearningFlowClientError("document upload returned an invalid response")
        return document

    def stream_chat(
        self,
        *,
        user_id: str,
        thread_id: str,
        message: str,
    ) -> Generator[dict[str, Any], None, None]:
        yield from self._stream(
            "/api/chat/stream",
            {"user_id": user_id, "thread_id": thread_id, "message": message, "history": []},
        )

    def stream_resume(
        self,
        *,
        run_id: str,
        thread_id: str,
        approved: bool,
    ) -> Generator[dict[str, Any], None, None]:
        yield from self._stream(
            "/api/runs/resume",
            {"run_id": run_id, "thread_id": thread_id, "approved": approved},
        )

    def _stream(self, path: str, payload: dict[str, Any]) -> Generator[dict[str, Any], None, None]:
        try:
            with self._client.stream("POST", path, json=payload) as response:
                if response.status_code >= 400:
                    response.read()
                    _raise_for_status(response)
                yield from parse_sse_lines(response.iter_lines())
        except LearningFlowClientError:
            raise
        except httpx.HTTPError as exc:
            raise LearningFlowClientError(f"LearningFlow stream failed: {exc}") from exc

    def __enter__(self) -> "LearningFlowClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def parse_sse_lines(lines: Iterable[str]) -> Generator[dict[str, Any], None, None]:
    """Parse standard SSE fields, including multi-line data payloads."""
    event_name = "message"
    data_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line:
            if data_lines:
                yield _build_event(event_name, data_lines)
            event_name, data_lines = "message", []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value or "message"
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        yield _build_event(event_name, data_lines)


def _build_event(event_name: str, data_lines: list[str]) -> dict[str, Any]:
    raw = "\n".join(data_lines)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"raw": raw}
    if not isinstance(data, dict):
        data = {"value": data}
    return {"event": event_name, "data": data}


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("message") or "")
    except ValueError:
        detail = response.text[:300]
    suffix = f": {detail}" if detail else ""
    raise LearningFlowClientError(f"LearningFlow API returned HTTP {response.status_code}{suffix}")

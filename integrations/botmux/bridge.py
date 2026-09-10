"""Independent BotMux runner that delegates turns to LearningFlow over SSE."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import queue
import signal
import sys
import threading
import time
from typing import Any, Iterable, Mapping, TextIO

from integrations.botmux.client import LearningFlowClient, LearningFlowClientError
from integrations.botmux.protocol import (
    ParsedPrompt,
    ProtocolError,
    RunnerMessage,
    control_frame,
    decode_runner_line,
    escape_display,
    identity_for,
    parse_botmux_prompt,
)
from integrations.botmux.state import BridgeStateStore
from integrations.botmux.scheduler import (
    BotMuxScheduleError,
    BotMuxScheduler,
    decode_reminder_prompt,
)

_ALLOWED_ATTACHMENTS = {".pdf", ".md", ".txt"}
_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
_APPROVE_WORDS = {"确认", "同意", "批准", "允许", "保存", "继续", "yes", "y", "ok", "approve", "confirm"}
_REJECT_WORDS = {"取消", "拒绝", "不同意", "不允许", "停止", "no", "n", "cancel", "reject"}
_STOP = object()


@dataclass(frozen=True)
class BridgeConfig:
    session_id: str
    base_url: str = "http://127.0.0.1:8010"
    state_dir: Path | None = None
    request_timeout: float = 600.0
    skip_health_check: bool = False


class BridgeOutput:
    """Serializes stdout writes and escapes every non-control byte."""

    def __init__(self, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr):
        self.stdout = stdout
        self.stderr = stderr
        self._lock = threading.Lock()

    def display(self, value: Any) -> None:
        with self._lock:
            self.stdout.write(escape_display(value))
            self.stdout.flush()

    def line(self, value: Any = "") -> None:
        self.display(f"{value}\n")

    def error(self, value: Any) -> None:
        with self._lock:
            self.stderr.write(escape_display(value))
            self.stderr.flush()

    def marker(self, kind: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            self.stdout.write(control_frame(kind, payload))
            self.stdout.flush()

    def prompt(self) -> None:
        self.display("› ")


class LearningFlowBridge:
    def __init__(
        self,
        config: BridgeConfig,
        *,
        client: LearningFlowClient | Any | None = None,
        state: BridgeStateStore | Any | None = None,
        output: BridgeOutput | Any | None = None,
        scheduler: BotMuxScheduler | Any | None = None,
    ) -> None:
        self.config = config
        self.client = client or LearningFlowClient(config.base_url, timeout_seconds=config.request_timeout)
        self._owns_client = client is None
        self.state = state or BridgeStateStore(config.session_id, config.state_dir)
        self.output = output or BridgeOutput()
        self.scheduler = scheduler or BotMuxScheduler()

    def start(self) -> None:
        if not self.config.skip_health_check:
            self.client.health()
        self.output.line("LearningFlow connected.")
        self.output.prompt()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def process(self, message: RunnerMessage) -> None:
        started_at_ms = _now_ms()
        user_id, thread_id = identity_for(self.config.session_id, message.trusted_caller)
        prompt = parse_botmux_prompt(message.content)
        if not prompt.message and not prompt.attachment_paths:
            self._emit_final("没有识别到可处理的消息。", message, started_at_ms)
            return

        # BotMux scheduled turns are trusted by the daemon. Reminder tasks use
        # an opaque runner payload so they produce a notification instead of
        # being classified as a request to create another reminder.
        is_scheduled_turn = (
            message.trusted_caller.get("source") == "schedule_creator"
            or str(message.reply_turn_id or "").startswith("schedule:")
        )
        scheduled_reminder = decode_reminder_prompt(prompt.message) if is_scheduled_turn else None
        if scheduled_reminder:
            self._emit_final(
                f"⏰ 学习提醒\n{scheduled_reminder['message']}",
                message,
                started_at_ms,
                native_turn_id=str(message.trusted_caller.get("taskId") or "") or None,
            )
            return

        pending = self.state.pending_interrupt
        decision = _parse_decision(prompt.message) if pending else None
        try:
            if pending and decision is not None:
                self._handle_resume(pending, decision, message, started_at_ms)
                return

            notices = self._upload_attachments(prompt, user_id)
            if not prompt.message:
                answer = "附件已上传到 LearningFlow 私有知识库。" if notices else "没有可处理的文本或附件。"
                self._emit_final(answer, message, started_at_ms)
                return
            if pending:
                pending_message = str(pending.get("message") or "上一项操作仍在等待确认")
                answer = f"{pending_message}。请回复“确认”或“取消”，处理后再发起新问题。"
                self._emit_final(answer, message, started_at_ms)
                return

            if notices:
                self.output.line("[learningflow] " + "；".join(notices))
            events = self.client.stream_chat(user_id=user_id, thread_id=thread_id, message=prompt.message)
            self._consume_events(events, message, started_at_ms, thread_id)
        except (LearningFlowClientError, OSError, ValueError) as exc:
            self._emit_final(f"LearningFlow 请求失败：{exc}", message, started_at_ms, is_error=True)

    def _handle_resume(
        self,
        pending: Mapping[str, Any],
        approved: bool,
        message: RunnerMessage,
        started_at_ms: int,
    ) -> None:
        run_id = str(pending.get("run_id") or "")
        thread_id = str(pending.get("thread_id") or "")
        if not run_id or not thread_id:
            self.state.clear_pending_interrupt()
            self._emit_final("待确认状态已损坏，请重新发起请求。", message, started_at_ms, is_error=True)
            return
        self.output.line(f"[learningflow] 已{'确认' if approved else '取消'}，继续执行。")
        events = self.client.stream_resume(run_id=run_id, thread_id=thread_id, approved=approved)
        self._consume_events(events, message, started_at_ms, thread_id)

    def _consume_events(
        self,
        events: Iterable[dict[str, Any]],
        message: RunnerMessage,
        started_at_ms: int,
        thread_id: str,
    ) -> None:
        terminal = False
        reminder_sync_note = ""
        for item in events:
            event = str(item.get("event") or "message")
            data = item.get("data")
            if not isinstance(data, dict):
                data = {"value": data}
            if event == "done":
                answer = str(data.get("answer") or "LearningFlow 已执行完成，但没有返回文本。")
                if reminder_sync_note:
                    answer = f"{answer}\n\n{reminder_sync_note}"
                self.state.clear_pending_interrupt()
                self._emit_final(
                    answer,
                    message,
                    started_at_ms,
                    native_turn_id=str(data.get("run_id") or "") or None,
                )
                terminal = True
                break
            if event == "interrupt":
                pending = {
                    "run_id": str(data.get("run_id") or ""),
                    "thread_id": thread_id,
                    "type": str(data.get("type") or "approval"),
                    "message": str(data.get("message") or "操作需要确认"),
                }
                self.state.set_pending_interrupt(pending)
                self._emit_final(
                    _interrupt_answer(data),
                    message,
                    started_at_ms,
                    native_turn_id=pending["run_id"] or None,
                )
                terminal = True
                break
            if event == "error":
                error = str(data.get("message") or data.get("error") or "未知错误")
                self._emit_final(f"LearningFlow 执行失败：{error}", message, started_at_ms, is_error=True)
                terminal = True
                break
            if event == "reminder_created":
                reminder = data.get("reminder")
                if isinstance(reminder, dict):
                    reminder_sync_note = self._sync_reminder_to_botmux(reminder)
            progress = format_progress(event, data)
            if progress:
                self.output.line(f"[learningflow] {progress}")
        if not terminal:
            self._emit_final("LearningFlow 流提前结束，未收到最终回答。", message, started_at_ms, is_error=True)

    def _sync_reminder_to_botmux(self, reminder: Mapping[str, Any]) -> str:
        reminder_id = str(reminder.get("id") or "").strip()
        if not reminder_id:
            return "⚠️ 提醒缺少 ID，未能同步到 BotMux；它只会出现在 LearningFlow 本地。"
        existing = self.state.reminder_schedule(reminder_id)
        if existing:
            return "已同步到 BotMux，届时会主动发送到当前飞书会话。"
        try:
            result = self.scheduler.add_reminder(reminder)
        except BotMuxScheduleError as exc:
            self.output.line(f"[learningflow] BotMux 提醒同步失败：{exc}")
            return f"⚠️ BotMux 同步失败（{exc}）；当前提醒只保存在 LearningFlow 本地。"
        self.state.mark_reminder_scheduled(reminder_id, {
            "task_id": result.task_id,
            "schedule": result.schedule,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        task_label = f"（任务 {result.task_id}）" if result.task_id else ""
        return f"已同步到 BotMux{task_label}，届时会主动发送到当前飞书会话。"

    def _upload_attachments(self, prompt: ParsedPrompt, user_id: str) -> list[str]:
        notices: list[str] = []
        for raw_path in prompt.attachment_paths:
            path = Path(raw_path).expanduser()
            try:
                resolved = path.resolve(strict=True)
                stat = resolved.stat()
            except OSError as exc:
                notices.append(f"附件 {path.name or raw_path} 不可读：{exc}")
                continue
            if not resolved.is_file():
                notices.append(f"附件 {resolved.name} 不是普通文件，已跳过")
                continue
            if resolved.suffix.lower() not in _ALLOWED_ATTACHMENTS:
                notices.append(f"附件 {resolved.name} 类型不支持，仅允许 PDF/Markdown/TXT")
                continue
            if stat.st_size > _MAX_ATTACHMENT_BYTES:
                notices.append(f"附件 {resolved.name} 超过 8 MB，已跳过")
                continue
            fingerprint = _attachment_fingerprint(resolved, stat.st_size, stat.st_mtime_ns, user_id)
            if self.state.was_uploaded(fingerprint):
                notices.append(f"附件 {resolved.name} 已入库，跳过重复上传")
                continue
            document = self.client.upload_document(user_id, resolved)
            self.state.mark_uploaded(
                fingerprint,
                {
                    "path": str(resolved),
                    "document_id": document.get("id"),
                    "filename": document.get("filename", resolved.name),
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            notices.append(f"附件 {resolved.name} 已上传并索引")
        return notices

    def _emit_final(
        self,
        content: str,
        message: RunnerMessage,
        started_at_ms: int,
        *,
        native_turn_id: str | None = None,
        is_error: bool = False,
    ) -> None:
        completed_at_ms = _now_ms()
        safe_content = escape_display(content)
        self.output.line()
        self.output.line(safe_content)
        payload: dict[str, Any] = {
            "content": safe_content,
            "startedAtMs": started_at_ms,
            "completedAtMs": completed_at_ms,
        }
        if message.reply_turn_id:
            payload["replyTurnId"] = message.reply_turn_id
        if native_turn_id:
            payload["nativeTurnId"] = native_turn_id
        if is_error:
            payload["error"] = True
        self.output.marker("final", payload)


def format_progress(event: str, data: Mapping[str, Any]) -> str | None:
    if event == "contextualization":
        standalone = str(data.get("standalone") or "").strip()
        original = str(data.get("original") or "").strip()
        return f"已补全上下文问题：{standalone}" if standalone and standalone != original else None
    if event == "retrieval":
        diagnostics = data.get("diagnostics") if isinstance(data.get("diagnostics"), dict) else {}
        return "检索 vector={vector} keyword={keyword} fused={fused}".format(
            vector=diagnostics.get("vector_count", 0),
            keyword=diagnostics.get("keyword_count", 0),
            fused=diagnostics.get("fused_count", len(data.get("sources", [])) if isinstance(data.get("sources"), list) else 0),
        )
    if event == "evidence_decision":
        return f"证据决策 {data.get('action', 'unknown')}：{data.get('reason', '')}".rstrip("：")
    if event == "tool_call":
        return f"正在调用工具 {data.get('tool', '')}：{data.get('query', '')}".rstrip("：")
    if event == "query_rewrite":
        return f"检索问题已改写：{data.get('rewritten', '')}".rstrip("：")
    if event == "research_plan":
        tasks = data.get("tasks")
        return f"已拆分 {len(tasks)} 个研究任务" if isinstance(tasks, list) else "研究计划已生成"
    if event == "research_reason":
        return f"研究轮次 {data.get('iteration', '?')}：{data.get('content', '')}".rstrip("：")
    if event == "research_tool_call":
        return f"研究调用工具 {data.get('tool', '')}".rstrip()
    if event == "research_critic":
        decision = "继续" if data.get("continue_research") else "结束"
        return f"研究评审：{decision}，{data.get('reason', '')}".rstrip("，")
    if event == "reminder_created":
        return "提醒已创建"
    return None


def run_bridge(
    config: BridgeConfig,
    *,
    stdin: TextIO = sys.stdin,
    client: LearningFlowClient | Any | None = None,
    state: BridgeStateStore | Any | None = None,
    output: BridgeOutput | Any | None = None,
) -> int:
    bridge = LearningFlowBridge(config, client=client, state=state, output=output)
    messages: queue.Queue[RunnerMessage | object] = queue.Queue()

    def worker() -> None:
        while True:
            item = messages.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, RunnerMessage)
                bridge.process(item)
                bridge.output.prompt()
            finally:
                messages.task_done()

    try:
        bridge.start()
    except (LearningFlowClientError, OSError, ValueError) as exc:
        bridge.output.error(f"LearningFlow bridge startup failed: {exc}\n")
        bridge.close()
        return 1

    thread = threading.Thread(target=worker, name="learningflow-botmux-worker", daemon=True)
    thread.start()
    try:
        for line in stdin:
            try:
                message = decode_runner_line(line)
            except ProtocolError as exc:
                bridge.output.line(f"[learningflow] bad BotMux input: {exc}")
                bridge.output.prompt()
                continue
            if message is not None:
                messages.put(message)
    except KeyboardInterrupt:
        pass
    finally:
        messages.put(_STOP)
        messages.join()
        thread.join(timeout=2)
        bridge.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bridge BotMux runner input to LearningFlow's HTTP/SSE API")
    parser.add_argument("--session-id", required=True, help="BotMux session identifier")
    parser.add_argument(
        "--base-url",
        default=os.getenv("LEARNINGFLOW_BASE_URL", "http://127.0.0.1:8010"),
        help="LearningFlow FastAPI base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ["LEARNINGFLOW_BOTMUX_STATE_DIR"]).expanduser()
        if os.getenv("LEARNINGFLOW_BOTMUX_STATE_DIR") else None,
        help="directory for pending approvals and attachment fingerprints",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=float(os.getenv("LEARNINGFLOW_BOTMUX_TIMEOUT", "600")),
    )
    parser.add_argument("--skip-health-check", action="store_true", help="start even when /api/health is unavailable")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = BridgeConfig(
        session_id=args.session_id,
        base_url=args.base_url,
        state_dir=args.state_dir,
        request_timeout=max(1.0, args.request_timeout),
        skip_health_check=args.skip_health_check,
    )
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    return run_bridge(config)


def _parse_decision(message: str) -> bool | None:
    normalized = message.strip().lower().strip("。.!！?？ ，,")
    if normalized in _APPROVE_WORDS:
        return True
    if normalized in _REJECT_WORDS:
        return False
    return None


def _interrupt_answer(data: Mapping[str, Any]) -> str:
    message = str(data.get("message") or "操作需要你的确认")
    kind = str(data.get("type") or "approval")
    if kind == "plan_approval":
        plan = data.get("plan")
        preview = ""
        if isinstance(plan, dict):
            title = str(plan.get("title") or "学习计划")
            items = plan.get("items")
            lines = [f"学习计划《{title}》已生成。"]
            if isinstance(items, list):
                for item in items[:7]:
                    if isinstance(item, dict):
                        lines.append(f"- 第 {item.get('day', '?')} 天：{item.get('topic') or item.get('task') or ''}")
            preview = "\n".join(lines) + "\n"
        return f"{preview}{message}\n请回复“确认”或“取消”。"
    return f"{message}\n请回复“确认”或“取消”。"


def _attachment_fingerprint(path: Path, size: int, mtime_ns: int, user_id: str) -> str:
    value = f"{path}:{size}:{mtime_ns}:{user_id}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


if __name__ == "__main__":
    raise SystemExit(main())

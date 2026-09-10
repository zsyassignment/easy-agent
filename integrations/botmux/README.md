# LearningFlow BotMux Bridge

An independent, thin runner that makes the existing LearningFlow HTTP/SSE
service look like a BotMux CLI:

```text
Feishu/Lark -> BotMux -> start-botmux-bridge.sh -> LearningFlow FastAPI
```

It does not embed a Feishu SDK in the Agent and does not execute arbitrary CLI
tools. BotMux remains responsible for chat/session transport; LearningFlow
remains responsible for LangGraph, RAG, tools, memory, and checkpoints.

## Scheduled reminders

When a reminder is created from a BotMux turn, the bridge mirrors it into
BotMux's durable scheduler:

```text
LearningFlow parses the reminder and stores its local record
  -> Bridge runs botmux schedule add with the current session route
  -> BotMux persists chat/app/topic metadata
  -> BotMux wakes the LearningFlow runner when due
  -> the runner emits the reminder as a final response to Feishu/Lark
```

This is intentionally channel-specific. Browser/API turns continue using the
existing APScheduler + local notification table; BotMux turns additionally get
an outbound Feishu/Lark delivery. The bridge invokes BotMux with an argv list
(`shell=False`) and stores the LearningFlow reminder ID to BotMux task ID mapping
in its atomic session state, so duplicate SSE events do not register the same
task twice.

The bridge requires the session environment injected by BotMux
(`BOTMUX_SESSION_ID`, `BOTMUX_CHAT_ID`, and `BOTMUX_LARK_APP_ID`) and a `botmux`
executable on `PATH`. One-time reminders preserve their absolute instant. Daily
reminders require LearningFlow and BotMux to use the same IANA timezone; set
`BOTMUX_SCHEDULE_TIMEZONE=Asia/Shanghai` if the host timezone is different.

## Start

First start LearningFlow:

```bash
cd /cloudide/workspace/langgraph-learning-agent
bash scripts/start-dev.sh
```

Then smoke-test the bridge in another terminal:

```bash
bash scripts/start-botmux-bridge.sh --session-id demo
```

The real BotMux adapter sends framed stdin. A local test frame can be generated
without hand-writing base64:

```bash
FRAME="$(PYTHONPATH=. .venv/bin/python -c \
  'from integrations.botmux.protocol import encode_runner_line; print(encode_runner_line("你好，解释一下 Agent Loop"))')"
printf '%s\n' "$FRAME" | bash scripts/start-botmux-bridge.sh --session-id demo
```

Successful startup prints:

```text
LearningFlow connected.
›
```

A completed turn prints ordinary progress text followed by one authoritative
OSC 777 `final` frame for BotMux. Only `done.data.answer` is used as the answer;
SSE progress logs are not mistaken for final output.

## Identity and memory

- `requestLarkAppId + requestUserOpenId` is SHA-256 mapped to a stable
  `feishu-...` LearningFlow `user_id`.
- BotMux `sessionId` maps to `botmux-...` `thread_id`.
- If trusted caller data is absent, the bridge uses a session-scoped fallback.
- The bridge always sends `history: []`; LearningFlow's server-side conversation
  store and rolling summary own the history.

The bridge deliberately ignores model-visible `<sender>` XML for authorization.

## Attachments

BotMux prompts may contain:

```xml
<user_message>请结合附件回答</user_message>
<attachments>
  <file n="1" path="/absolute/path/notes.pdf" />
</attachments>
```

The bridge uploads readable `.pdf`, `.md`, and `.txt` regular files through
`POST /api/documents` before starting the turn. Files are capped at 8 MB and
are never executed. A local SHA-256 fingerprint prevents duplicate indexing
when BotMux retries a message.

This assumes BotMux and the bridge share a machine/filesystem. If they are in
separate containers, mount BotMux's attachment directory read-only at the same
path in the bridge container.

## Human approval

For a LangGraph `interrupt` (for example saving a learning plan), the bridge
persists `run_id`, `thread_id`, and interrupt type, then asks the user to reply:

```text
确认
```

or:

```text
取消
```

That answer calls `POST /api/runs/resume` and continues the SSE stream. Pending
approval and attachment fingerprints live under:

```text
~/.learningflow-botmux/session-<hash>.json
```

Override with `LEARNINGFLOW_BOTMUX_STATE_DIR` or `--state-dir`.

## Configuration

| Variable / flag | Default | Meaning |
|---|---:|---|
| `LEARNINGFLOW_BASE_URL` / `--base-url` | `http://127.0.0.1:8010` | FastAPI base URL |
| `LEARNINGFLOW_BOTMUX_TIMEOUT` / `--request-timeout` | `600` | One HTTP/SSE turn timeout in seconds |
| `LEARNINGFLOW_BOTMUX_STATE_DIR` / `--state-dir` | `~/.learningflow-botmux` | Atomic local state directory |
| `LEARNINGFLOW_PYTHON` | project `.venv/bin/python` | Python used by the wrapper |

See [`botmux-adapter/PATCH.md`](botmux-adapter/PATCH.md) for the minimal BotMux
registration changes.

## Security boundary

- Identity comes only from BotMux's structured `trustedCaller` sidecar.
- Display text replaces every ESC byte, so user/model/tool output cannot forge
  an OSC control marker.
- Only the bridge's marker writer emits raw `ESC ] 777;botmux:` frames.
- Attachment allowlist: PDF, Markdown, TXT; regular files only; 8 MB maximum.
- Attachment content is uploaded as data and never executed.
- Errors do not include API keys or the complete process environment.

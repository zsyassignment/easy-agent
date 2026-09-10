# LearningFlow Agent

一个**独立运行**的、基于 LangGraph 的自适应学习与深度研究 Agent。它不是现有 C++ 项目的替换版：当前项目专注复杂 Agent 工作流，后续可以通过 HTTP/SSE 接入任意网关，包括 `/cloudide/workspace/subject` 的 C++ Server。

## 为什么单独做

| 项目 | 重点 |
|---|---|
| `subject` | C++ Reactor/SSE 网关、手写 Agent Loop、理解底层执行机制 |
| `langgraph-learning-agent` | LangGraph 状态图、条件循环、Checkpoint、Interrupt/Resume、RAG 质量门禁、运行轨迹与评测 |

## 核心能力

- LangGraph 25 节点状态图（含动态并行研究 fan-out）
- 意图路由：闲聊、资料问答、联网搜索、学习计划、进度提醒、测验、进度、深度研究
- 单一自然语言入口：自动选择普通回答、私有 RAG、Tavily 联网搜索或持久化提醒
- 自适应 RAG：Retrieve → Grade → Rewrite（最多一次）→ Retrieve → Generate
- 多路召回：Qdrant Dense Top-K + SQLite FTS5 倒排 Top-K
- RRF（Reciprocal Rank Fusion）融合，不直接比较异构原始分数
- 可选开源 CrossEncoder 精排，默认模型 `BAAI/bge-reranker-v2-m3`
- `RecursiveCharacterTextSplitter` 递归字符分块，保留标题和 PDF 页码
- PDF / Markdown / TXT 上传与标题、页码元数据
- SQLite 长期画像、计划、学习事件、提醒和站内通知
- LangGraph SQLite Checkpointer
- 学习计划 Human-in-the-loop：生成后中断，确认才落库
- 节点级 SSE 事件和运行轨迹持久化
- 独立检索评测：Hit@K、MRR、关键词覆盖率、延迟
- 没有模型 API Key 时也能离线演示主要流程
- Tavily Search API 联网搜索，保留标题、摘要和 URL 引用
- 将 RAG、联网搜索、计划、进度和定时提醒发布为标准 MCP Server，供外部 Agent 调用

## 状态图

```text
START → load_context → route_intent
  ├─ chat → direct_chat ──────────────────────────────┐
  ├─ reminder → APScheduler → SQLite notification      │
  ├─ knowledge_qa/web_search → local retrieve → grade → evidence decision │
  │                       ├─ good → generate           │
  │                       ├─ weak → rewrite → retrieve │
  │                       └─ still weak → optional web → generate
  ├─ create_plan → draft_plan → interrupt(确认) → persist_plan
  ├─ quiz → generate_quiz                            │
  ├─ progress → load_plan                            │
  ├─ update_progress → update_plan                   │
  └─ deep_research → plan_research → research_worker × N（并行）
                       → reason ⇄ act → observation → critic
                          └── stop/budget → synthesize            │
                                                     ↓
                                              reflect → finalize → END
```

这是真正的 LangGraph 编排，不是把普通 `for` 循环包进一个类。

## 技术栈

- Python 3.11+
- LangGraph 1.x（StateGraph、Send、interrupt、Command、Overwrite）
- LangGraph SQLite Checkpoint
- FastAPI + SSE
- SQLite + FTS5 预分词倒排索引（英文词 + 中文 unigram/bigram）
- Qdrant Dense Vector（默认本地嵌入模式，也支持远程服务）
- FastEmbed + `BAAI/bge-small-zh-v1.5` 本地 ONNX/CPU Embedding（512 维）
- RRF 融合 + 可选 BGE CrossEncoder Reranker
- OpenAI-compatible Chat / Embedding API
- Tavily Search API
- MCP Python SDK / FastMCP Server（8 Tools、Resource、Prompt、stdio/Streamable HTTP）
- APScheduler 持久化定时任务、重启恢复、时区与站内通知
- 深度研究 ReAct：原生 Function Calling、Action/Observation/Critic 循环与双预算
- 声明式 Skill：安全上传、版本、触发匹配、工具白名单、资源和输出 Schema
- 分层会话记忆：原文持久化、Token 窗口、结构化滚动摘要和指代消解
- PyPDF + LangChain RecursiveCharacterTextSplitter
- 原生 HTML/CSS/JavaScript 前端

## 快速启动

```bash
cd /cloudide/workspace/langgraph-learning-agent
cp .env.example .env
source .venv/bin/activate
PYTHONPATH=backend python backend/run.py
```

打开：

```text
http://127.0.0.1:8010
```

API 文档：

```text
http://127.0.0.1:8010/docs
```

也可以：

```bash
bash scripts/start-dev.sh
```

## 模型配置

不配置远程 Key 时：

- 路由使用确定性规则
- Embedding 默认使用本地 FastEmbed，Qdrant 语义检索无需 API Key
- 回答、计划、测验和反思使用本地可解释逻辑

配置后启用真实 LLM：

```env
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com/v1/chat/completions
LLM_MODEL=deepseek-chat

```

默认本地 Embedding：

```env
EMBEDDING_PROVIDER=fastembed
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
EMBEDDING_NAMESPACE=fastembed:BAAI/bge-small-zh-v1.5
EMBEDDING_CACHE_DIR=./data/models
```

第一次使用会下载约 52 MB 的官方 ONNX 模型，之后从本地缓存加载。如果希望改用远程 OpenAI-compatible Embedding：

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_API_KEY=sk-...
EMBEDDING_BASE_URL=https://api.openai.com/v1/embeddings
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_NAMESPACE=openai:text-embedding-3-small
```

切换模型或 Provider 时应同步修改 `EMBEDDING_NAMESPACE`；已有文档需要重新索引，避免混用不同维度或语义空间的向量。

Qdrant 默认使用本地嵌入模式，数据写入 `data/qdrant/`。如需服务模式：

```bash
docker compose up -d qdrant
```

```env
QDRANT_MODE=remote
QDRANT_URL=http://127.0.0.1:6333
```

## 联网搜索

联网搜索直接使用 Tavily API，不自行实现搜索引擎：

```env
TAVILY_API_KEY=tvly-...
WEB_SEARCH_MAX_RESULTS=5
```

联网搜索是可选工具，不是与知识库割裂的固定模式：系统默认先检索用户私有知识库并评估证据，
本地证据不足时先改写检索一次，仍不足且允许联网时才调用 Tavily；最终统一合并 `[Sx]` 私有来源与
`[Wx]` 网页来源。用户说“只根据资料/不要联网”时会强制本地模式；说“联网查最新信息”时会要求补充网页证据。

## 定时目标与学习提醒

用户可以直接在统一对话框或左侧提醒栏输入：

```text
30分钟后提醒我复习 Agent Loop
明天20:00提醒我完成计划第1天
每天晚上8点提醒我检查学习目标
查看我的提醒
```

实现链路：

```text
自然语言时间解析 / ISO-8601 API / MCP Tool
    → SQLite reminders 持久化
    → APScheduler DateTrigger 或 CronTrigger
    → 应用重启时恢复未完成任务
    → 到期写入 notifications
    → 前端每 15 秒拉取未读站内通知
```

支持一次性和每日重复提醒、IANA 时区、计划天数关联、取消提醒以及原子状态更新，防止一次性任务产生重复通知。第一版使用站内通知，后续可以把触发动作扩展为邮件、Webhook 或消息队列。

## 将项目能力发布为 MCP Server

本项目不只消费 Tavily API，还通过官方 MCP Python SDK 的 `FastMCP` 把知识库、联网搜索、学习计划和提醒能力发布给外部 Agent：

```text
Claude Desktop / Cursor / 其他 MCP Client
                  │ MCP stdio / Streamable HTTP
                  ▼
      learningflow-agent MCP Server
                  │
                  ├─ Hybrid RAG / Document Tools
                  ├─ Tavily Web Search Tool
                  ├─ Learning Plan / Progress Tools
                  ├─ Persistent Reminder Tools
                  ├─ Resource: learningflow://capabilities
                  └─ Prompt: answer_with_private_knowledge
```

直接启动 MCP Server：

```bash
cd /cloudide/workspace/langgraph-learning-agent
bash scripts/start-mcp.sh
```

如果 Web 应用已经启动，也可以直接连接 Streamable HTTP：

```text
http://127.0.0.1:8010/mcp/
```

HTTP MCP 和 FastAPI 对话接口共用同一个 Runtime，不会重复打开本地 Qdrant；`stdio` 模式适合由桌面客户端单独拉起。

`stdio` 是协议通道，不能在终端里手工输入测试。将 `examples/mcp-client.json` 中的配置加入 MCP 客户端即可：

```json
{
  "mcpServers": {
    "learningflow-agent": {
      "command": "/cloudide/workspace/langgraph-learning-agent/scripts/start-mcp.sh"
    }
  }
}
```

对外提供 8 个业务工具：

```text
search_private_knowledge    私有知识库混合检索
list_knowledge_documents   查看用户资料
search_web                  Tavily 联网搜索
get_learning_plan          获取学习计划
update_learning_progress   更新计划进度（写操作、幂等）
create_learning_reminder   创建一次性/每日提醒（写操作）
list_learning_reminders    查看待执行提醒
list_agent_skills          查看已安装声明式 Skills
```

写工具通过 MCP `ToolAnnotations` 标注只读性、幂等性和是否访问外部网络。检索工具返回引用片段、`retrieval_paths` 和 RRF/Reranker diagnostics。MCP Server 与 Web 应用复用同一份 SQLite/Qdrant 数据。


## RAG 具体链路

```text
PDF / Markdown / TXT
  → 标题/页码感知预处理
  → RecursiveCharacterTextSplitter(800, overlap=120)
  → SQLite Chunk + FTS5 预分词倒排索引
  → Embedding API + Qdrant Dense Index

Query
  ├─ Qdrant Dense Top 10
  └─ SQLite FTS5 Top 10
          ↓
      RRF(k=60)
          ↓
  可选 CrossEncoder Rerank
          ↓
       Top 5
          ↓
 LangGraph Grade → Weak 时 Rewrite 一次 → 再检索
```

相关配置：

```env
CHUNK_SIZE=800
CHUNK_OVERLAP=120
VECTOR_RECALL_K=10
KEYWORD_RECALL_K=10
RRF_K=60
MAX_RETRIEVAL_RESULTS=5
MAX_REWRITE_COUNT=1
```

### 可选开源 Reranker

默认关闭，避免首次启动下载大模型：

```bash
source .venv/bin/activate
pip install -r requirements-reranker.txt
```

```env
RERANKER_ENABLED=true
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_TOP_N=5
```

实现通过 `sentence-transformers` 的 `CrossEncoder` 懒加载模型。如果依赖缺失或模型加载失败，会保留 RRF 排序并在检索 diagnostics 中报告降级，不影响问答。

## 接入 BotMux / 飞书

项目提供独立的 BotMux Bridge CLI，不把飞书 SDK 耦合进 Agent：

```text
飞书 -> BotMux -> Bridge CLI -> LearningFlow FastAPI/SSE
```

LearningFlow 服务启动后，可单独启动 Bridge：

```bash
bash scripts/start-botmux-bridge.sh --session-id demo
```

Bridge 支持稳定用户/会话映射、SSE 进度转发、LangGraph Interrupt 的“确认/取消”恢复，
以及 BotMux PDF/Markdown/TXT 附件自动上传并防重复索引。完整说明和 BotMux 侧最小适配补丁见
[`integrations/botmux/README.md`](integrations/botmux/README.md)。

仓库的 [`botmux/`](botmux/) 目录直接包含完整 BotMux 源码和已经注册好的
`learningflow` Adapter，不需要再运行脚本拉取另一个仓库：

```bash
cd botmux
bun install --frozen-lockfile
bun run build
```

飞书配置模板见 [`botmux/bots.learningflow.example.json`](botmux/bots.learningflow.example.json)，
完整启动说明见 [`botmux/LEARNINGFLOW.md`](botmux/LEARNINGFLOW.md)。示例中的 App ID、Secret
和路径均为占位符，真实凭据只应保存在本机的 `~/.botmux/bots.json`。

## API

### 流式对话

```http
POST /api/chat/stream
```

```json
{
  "message": "根据资料解释 Agent Loop",
  "user_id": "user-001",
  "thread_id": "thread-001",
  "history": []
}
```

SSE 事件包括：

```text
run_started, node_status, route, query, retrieval,
tool_call, tool_result,
retrieval_grade, query_rewrite, plan, interrupt,
approval, plan_saved, quiz, reflection, answer, done, error
```

### 恢复人工中断

```http
POST /api/runs/resume
```

```json
{
  "run_id": "run_xxx",
  "thread_id": "thread-001",
  "approved": true
}
```

### 文档

```http
POST   /api/documents          multipart: user_id + file
GET    /api/documents?user_id=...
DELETE /api/documents/{id}?user_id=...
```

### 状态与轨迹

```http
GET /api/threads/{thread_id}/state
GET /api/runs/{run_id}
GET /api/learning/plan?user_id=...
```

### 提醒与通知

```http
POST   /api/reminders
POST   /api/reminders/from-text?user_id=...
GET    /api/reminders?user_id=...
DELETE /api/reminders/{id}?user_id=...
GET    /api/notifications?user_id=...&unread_only=true
POST   /api/notifications/{id}/read?user_id=...
POST   /api/skills              multipart: user_id + .zip
GET    /api/skills?user_id=...
PATCH  /api/skills/{id}
DELETE /api/skills/{id}?user_id=...
GET    /api/threads/{id}/messages?user_id=...
```

## 测试

```bash
PYTHONPATH=backend .venv/bin/pytest
```

覆盖：

- LangGraph 条件分支与 RAG 重写环路
- SQLite Checkpoint
- `interrupt()` / `Command(resume=...)`
- 计划确认与拒绝
- Agent Run/Step 轨迹
- FTS5 用户隔离和中文 bigram 召回
- Qdrant + FTS5 双路召回
- RRF 融合排序
- 递归分块及标题/页码元数据
- Reranker 缺失时安全降级
- 文档上传与 SSE API
- APScheduler 定时提醒、重启恢复、幂等触发和通知 API
- MCP stdio / Streamable HTTP 的真实 Client 互操作

## 检索评测

独立评测位于 `evals/rag/`，包含 100 道五分桶题目。扩展模式使用 29 篇跨领域混合文档、
约 39.4 万字符和 1188 个 Chunk，并支持 Dense、BM25、Hybrid RRF 对照：

```bash
PYTHONPATH=backend .venv/bin/python -m evals.rag.runner \
  --corpus-profile mixed --split test --k 5 --retrieval-mode compare
```

可选 `--end-to-end` 执行完整 Agent 回答评测。详细口径见 `evals/rag/README.md`。旧的 `evals/runner.py` 保留为上传自有语料后的轻量评测示例。

## 后续接入 C++

未来只需要让 C++ 网关代理：

```text
POST /api/chat/stream
POST /api/runs/resume
POST/GET/DELETE /api/documents
POST/GET/DELETE /api/reminders
GET /api/notifications
POST /api/notifications/{id}/read
```

Python SSE 可以直接映射到当前 C++ 网关已有的事件转发机制，无需改变 LangGraph 内部实现。

## 深度研究 ReAct

深度研究分支在并行初检索后进入真正的工具循环：

```text
research_reason（模型原生 tool_calls）
  → research_act（白名单执行）
  → observation（结果回填消息）
  → research_critic
      ├─ 存在明确证据缺口 → research_reason
      └─ 已充分/达到预算 → synthesize_research
```

默认限制 6 轮、10 次工具调用，并跳过完全重复的工具请求。无 LLM Key 时使用确定性两轮策略，仍可演示完整图循环。

## 声明式 Skill

普通用户可以上传 `.zip` Skill 包，但不能上传或执行 Python、Shell、动态库等代码。包格式：

```text
my-skill/
├── SKILL.md
├── manifest.json
├── resources/
├── schemas/
└── workflow（写在 manifest.json）
```

Skill 支持触发词、版本、启用/停用、工作步骤、工具白名单、资源文件和 JSON 输出 Schema。匹配后会注入对话/研究上下文，并限制 ReAct 可调用工具。示例源文件位于 `examples/skills/paper-review/`；上传前将该目录打成 ZIP。

## 长对话与指代消解

```text
conversation_messages 原始消息永久保存
+ 最近 10 条原文 / 6000 Token 预算
+ conversation_summaries 结构化滚动摘要
→ contextualize_question
→ 意图识别
```

摘要保存版本与覆盖消息序号；页面刷新后通过服务端接口恢复历史。“那它有什么限制？”会先结合摘要和最近消息补成独立问题，再进行路由和检索。

## 100 题 RAG Benchmark

独立评测目录：`evals/rag/`。包含 100 题，五桶各 20 题：事实、同义改写、多跳、无答案、多轮指代；其中 20 题为 dev、80 题为 test。

```bash
PYTHONPATH=backend .venv/bin/python -m evals.rag.runner --split test --k 5
```

扩展测试集基线（64 道可回答题）为 Hybrid Recall@5 86.72%、MRR 81.56%、NDCG@5 81.36%；
详细口径、Dense/BM25 对照和负样本边界见 `evals/rag/README.md`。加 `--end-to-end` 可另行评估回答、引用和最终拒答。

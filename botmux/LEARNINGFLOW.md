# LearningFlow + BotMux

本目录是随项目一起提交的完整 BotMux 源码，并已注册 `learningflow` CLI Adapter。
它负责飞书/Lark 的消息、会话和主动通知；仓库根目录的 Python Bridge 负责把 BotMux
Runner 协议转换为 LearningFlow FastAPI/SSE。

## 安装与构建

```bash
cd botmux
bun install --frozen-lockfile
bun run build
```

## 配置飞书机器人

先启动仓库根目录的 Agent：

```bash
cd ..
bash scripts/start-dev.sh
```

参考 `botmux/bots.learningflow.example.json` 创建 `~/.botmux/bots.json`，填写真实
`larkAppId`、`larkAppSecret` 和本仓库的绝对路径。不要提交真实凭据。

然后启动 BotMux：

```bash
cd botmux
node dist/cli.js start
```

也可以使用 BotMux 自带的交互式配置：

```bash
node dist/cli.js setup
```

选择 `LearningFlow Agent`，并将 CLI Path 指向：

```text
/absolute/path/to/easy-agent/scripts/start-botmux-bridge.sh
```

详细的 Bridge 协议、附件、身份映射和定时提醒说明见：

```text
../integrations/botmux/README.md
```

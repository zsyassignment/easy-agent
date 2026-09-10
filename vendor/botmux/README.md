# BotMux 集成

本目录不提交第三方 BotMux 源码。`bootstrap.sh` 会把上游固定版本拉到
`vendor/botmux/runtime/`，再应用 `learningflow.patch`，因此仓库体积小且构建可复现。

```bash
# 仅拉取并应用适配
bash vendor/botmux/bootstrap.sh

# 同时安装 Bun 依赖
bash vendor/botmux/bootstrap.sh --install

# 安装依赖并构建 BotMux
bash vendor/botmux/bootstrap.sh --build
```

脚本固定上游提交：

```text
f9304ff9f291f7c1b66da8768c6acd13162018bd
```

运行前先启动 LearningFlow：

```bash
bash scripts/start-dev.sh
```

然后参考 `bots.json.example` 配置 `~/.botmux/bots.json`。示例中的 App ID、Secret
和绝对路径全部是占位符；不要把真实密钥提交到 Git。

脚本的安全行为：

- runtime 不存在时才初始化和拉取；
- 固定校验上游提交；
- 对已有脏工作树拒绝覆盖；
- 将适配补丁提交为 runtime 内的本地提交，因此重复执行保持幂等；
- 不读取、不生成、不保存飞书凭据。

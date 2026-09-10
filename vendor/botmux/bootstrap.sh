#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENDOR_DIR="$ROOT/vendor/botmux"
RUNTIME_DIR="$VENDOR_DIR/runtime"
PATCH_FILE="$VENDOR_DIR/learningflow.patch"
UPSTREAM_URL="${BOTMUX_UPSTREAM_URL:-https://github.com/deepcoldy/botmux.git}"
UPSTREAM_COMMIT="${BOTMUX_UPSTREAM_COMMIT:-f9304ff9f291f7c1b66da8768c6acd13162018bd}"
PATCH_COMMIT_SUBJECT="feat: add LearningFlow Agent adapter"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "缺少命令: $1" >&2
    exit 1
  }
}

need git
[[ -f "$PATCH_FILE" ]] || { echo "缺少补丁: $PATCH_FILE" >&2; exit 1; }

if [[ -e "$RUNTIME_DIR" && ! -d "$RUNTIME_DIR/.git" ]]; then
  echo "拒绝覆盖非 Git 目录: $RUNTIME_DIR" >&2
  exit 1
fi

if [[ ! -d "$RUNTIME_DIR/.git" ]]; then
  mkdir -p "$VENDOR_DIR"
  git init -q "$RUNTIME_DIR"
  git -C "$RUNTIME_DIR" remote add origin "$UPSTREAM_URL"
  if ! git -C "$RUNTIME_DIR" fetch --depth 1 origin "$UPSTREAM_COMMIT"; then
    echo "BotMux 拉取失败；删除不完整的 runtime 后可直接重试。" >&2
    rm -rf "$RUNTIME_DIR"
    exit 1
  fi
  git -C "$RUNTIME_DIR" checkout -q --detach FETCH_HEAD
fi

if [[ -n "$(git -C "$RUNTIME_DIR" status --porcelain)" ]]; then
  echo "BotMux runtime 存在未提交修改，拒绝覆盖: $RUNTIME_DIR" >&2
  exit 1
fi

HEAD="$(git -C "$RUNTIME_DIR" rev-parse HEAD)"
if [[ "$HEAD" == "$UPSTREAM_COMMIT" ]]; then
  git -C "$RUNTIME_DIR" apply --check "$PATCH_FILE"
  git -C "$RUNTIME_DIR" apply "$PATCH_FILE"
  git -C "$RUNTIME_DIR" add README.md README.en.md src test
  git -C "$RUNTIME_DIR" \
    -c user.name='LearningFlow Bootstrap' \
    -c user.email='bootstrap@learningflow.local' \
    commit -q -m "$PATCH_COMMIT_SUBJECT"
elif [[ "$(git -C "$RUNTIME_DIR" log -1 --pretty=%s)" == "$PATCH_COMMIT_SUBJECT" ]] && \
     [[ "$(git -C "$RUNTIME_DIR" rev-parse HEAD^)" == "$UPSTREAM_COMMIT" ]]; then
  : # 已应用，保持幂等
else
  echo "BotMux runtime 版本不符合预期，拒绝覆盖。当前 HEAD: $HEAD" >&2
  exit 1
fi

if [[ "${1:-}" == "--install" || "${1:-}" == "--build" ]]; then
  need bun
  (cd "$RUNTIME_DIR" && bun install --frozen-lockfile)
fi
if [[ "${1:-}" == "--build" ]]; then
  (cd "$RUNTIME_DIR" && bun run build)
fi

cat <<MSG
BotMux LearningFlow 适配已准备完成：
  $RUNTIME_DIR

下一步：
  1. 启动 Agent: bash scripts/start-dev.sh
  2. 安装依赖（若尚未执行）: bash vendor/botmux/bootstrap.sh --install
  3. 配置 BotMux: 参考 vendor/botmux/bots.json.example
  4. 启动 BotMux: cd vendor/botmux/runtime && bun run build && node dist/cli.js start
MSG

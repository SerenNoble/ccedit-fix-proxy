#!/usr/bin/env bash
# 启动 ccedit-fix-proxy
# 用法: bash start.sh
# 配置通过 .env 文件或环境变量设置

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 从 .env 加载配置（不覆盖已有环境变量）
if [ -f "$SCRIPT_DIR/.env" ]; then
    while IFS='=' read -r key value; do
        key=$(echo "$key" | xargs)
        value=$(echo "$value" | xargs | sed "s/^['\"]//;s/['\"]$//")
        if [ -n "$key" ] && [ -z "${!key}" ]; then
            export "$key=$value"
        fi
    done < <(grep -v '^\s*#' "$SCRIPT_DIR/.env" | grep -v '^\s*$')
fi

export EDIT_FIX_UPSTREAM="${EDIT_FIX_UPSTREAM:-https://open.bigmodel.cn/api/anthropic}"
export EDIT_FIX_PORT="${EDIT_FIX_PORT:-8080}"

echo "ccedit-fix-proxy -> ${EDIT_FIX_UPSTREAM} :${EDIT_FIX_PORT}"
echo ""
echo "启动 Claude Code 请在新终端执行:"
echo "  export ANTHROPIC_BASE_URL=http://localhost:${EDIT_FIX_PORT}"
echo "  export ANTHROPIC_API_KEY=<your-api-key>"
echo "  claude"
echo ""

cd "$SCRIPT_DIR"
python -m ccefix_proxy --upstream "$EDIT_FIX_UPSTREAM" --port "$EDIT_FIX_PORT" -v

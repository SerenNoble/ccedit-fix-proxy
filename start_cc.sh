#!/usr/bin/env bash
# 启动 Claude Code，通过 ccedit-fix-proxy 连接 GLM
# 用法: bash start_cc.sh [proxy_ip] [proxy_port]

# 默认自动获取本机 IP
DEFAULT_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$DEFAULT_IP" ]; then
    DEFAULT_IP=$(ip route get 1 2>/dev/null | awk '{print $7; exit}')
fi
PROXY_IP="${1:-${DEFAULT_IP:-127.0.0.1}}"
PROXY_PORT="${2:-8080}"

# 从同目录 .env 读取配置（API Key 等）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
    while IFS='=' read -r key value; do
        key=$(echo "$key" | xargs)
        value=$(echo "$value" | xargs | sed "s/^['\"]//;s/['\"]$//")
        if [ -n "$key" ] && [ -z "${!key}" ]; then
            export "$key=$value"
        fi
    done < <(grep -v '^\s*#' "$SCRIPT_DIR/.env" | grep -v '^\s*$')
fi

export no_proxy="${no_proxy:+$no_proxy,}${PROXY_IP}"
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}${PROXY_IP}"
export ANTHROPIC_BASE_URL="http://${PROXY_IP}:${PROXY_PORT}"

echo "Claude Code -> ccedit-fix-proxy (${PROXY_IP}:${PROXY_PORT}) -> GLM"
claude

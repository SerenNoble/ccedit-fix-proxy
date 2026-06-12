#!/usr/bin/env bash
# 测试 ccedit-fix-proxy 连通性
# 用法: bash test.sh <proxy_ip> [proxy_port]
# 示例: bash test.sh 10.47.16.58 8080

PROXY_IP="${1:?用法: bash test.sh <proxy_ip> [proxy_port]}"
PROXY_PORT="${2:-8080}"
API_KEY="${ANTHROPIC_API_KEY:?请设置 ANTHROPIC_API_KEY 环境变量}"

# 绕过服务器上的 http_proxy，直连本机代理
export no_proxy="${no_proxy:+$no_proxy,}${PROXY_IP}"
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}${PROXY_IP}"

echo "=== 测试连通性 ==="
echo "Proxy: http://${PROXY_IP}:${PROXY_PORT}"
echo "no_proxy: ${no_proxy}"
echo ""

echo "--- 1. ping ---"
ping -c 2 -W 2 ${PROXY_IP}
echo ""

echo "--- 2. curl (verbose) ---"
curl -v --connect-timeout 5 http://${PROXY_IP}:${PROXY_PORT}/v1/messages \
  -X POST \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"glm-5","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}'
echo ""
echo ""
echo "=== 测试完毕 ==="

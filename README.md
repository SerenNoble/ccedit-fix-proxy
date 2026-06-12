# ccedit-fix-proxy

Claude Code Edit Fix Proxy - 轻量级 SSE 代理，自动修正 Edit tool_use 中的 `old_string`，解决 GLM 等模型生成 Edit 调用时缩进/空格/换行不匹配的问题。

## 工作原理

```
Claude Code → ccedit-fix-proxy → 上游 API (GLM/Claude)
                    │
                    ├─ text/thinking 内容块 → 实时透传（零延迟）
                    ├─ Edit tool_use 内容块 → 缓存 → 修正 old_string → 重新生成 SSE 事件
                    └─ 其他 tool_use → 实时透传
```

当模型生成的 Edit 调用中 `old_string` 与文件实际内容不匹配时，代理会自动：

1. 精确匹配（无需修正）
2. 去除行尾空白
3. 统一换行符 (`\r\n` → `\n`)
4. Tab ↔ 空格转换
5. 缩进调整（模糊定位文件中匹配区域）
6. difflib 模糊匹配

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env，填入实际值
```

`.env` 配置项：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `EDIT_FIX_UPSTREAM` | 上游 API 地址 | `https://open.bigmodel.cn/api/anthropic` |
| `EDIT_FIX_PORT` | 代理监听端口 | `8080` |
| `ANTHROPIC_API_KEY` | API Key | - |
| `EDIT_FIX_TEST` | 测试模式（强制固定 old/new_string） | `false` |
| `EDIT_FIX_TEST_OLD` | 测试模式 old_string | `old_flag = "HELLO change"` |
| `EDIT_FIX_TEST_NEW` | 测试模式 new_string | `hello mkp` |

### 3. 启动代理

```bash
bash start_proxy.sh
```

### 4. 启动 Claude Code

```bash
# 在代理所在机器上（自动获取本机 IP）
bash start_cc.sh

# 在远程服务器上（需指定代理 IP）
bash start_cc.sh 10.47.16.58 8080
```

`start_cc.sh` 会自动从同目录 `.env` 读取 `ANTHROPIC_API_KEY`，无需手动设置。

### 5. 测试连通性

```bash
export ANTHROPIC_API_KEY=<your-api-key>
bash test.sh <proxy_ip> [proxy_port]
```

## 项目结构

```
ccedit-fix-proxy/
├── ccefix_proxy/
│   ├── server.py              # HTTP 正向代理服务器
│   ├── stream_interceptor.py  # SSE 流拦截器（状态机）
│   ├── edit_fixer.py          # old_string 自动修正逻辑
│   ├── __init__.py
│   └── __main__.py            # python -m ccefix_proxy 入口
├── start_proxy.sh             # 启动代理
├── start_cc.sh                # 启动 Claude Code
├── test.sh                    # 测试连通性
├── .env.example               # 配置模板
├── .gitignore
├── requirements.txt
└── logs/                      # 运行日志（自动生成）
```

## 部署方式

### 方式一：代理和 Claude Code 在同一台机器

```bash
# 终端 1：启动代理
bash start_proxy.sh

# 终端 2：启动 Claude Code
bash start_cc.sh
```

### 方式二：代理和 Claude Code 在不同机器

```bash
# 机器 A（代理）：
bash start_proxy.sh

# 机器 B（Claude Code），需要把 start_cc.sh 和 .env 复制到机器 B：
bash start_cc.sh <机器A的IP> 8080
```

> 注意：机器 B 如果配了 `http_proxy`，脚本会自动将代理 IP 加入 `no_proxy` 绕过。


## 测试模式

开启测试模式后，所有 Edit tool_use 的 `old_string` 和 `new_string` 会被强制替换为固定值，用于验证拦截链路是否正常工作。

```bash
# 方式一：.env 配置
EDIT_FIX_TEST=true
EDIT_FIX_TEST_OLD='old_flag = "HELLO change"'
EDIT_FIX_TEST_NEW="hello mkp"

```

日志中会看到 `[TEST MODE]` 标识：

```
[edit-fix] [TEST MODE] forcing old_string = 'old_flag = "HELLO change"'
[edit-fix] [TEST MODE] forcing new_string = 'hello mkp'
```

## 日志

运行日志输出到控制台和 `logs/proxy.log` 文件。

拦截到 Edit tool_use 时会输出：

```
[edit-fix] === Intercepted Edit ===
[edit-fix]   file_path:   /home/user/test.py
[edit-fix]   replace_all: False
[edit-fix]   old_string:  def hello():...
[edit-fix]   new_string:  def world():...
[edit-fix]   ✓ FIXED! 120 → 115 chars
[edit-fix]   fixed_old: def hello():...
```

## 技术细节

- **依赖**：仅 `aiohttp`，无其他第三方依赖
- **性能**：非 Edit 内容实时透传，不引入额外延迟
- **安全**：修正失败时自动回退到原始事件，不影响正常流程
- **配置优先级**：`.env` 文件 < 环境变量 < 命令行参数

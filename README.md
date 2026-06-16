# ccedit-fix-proxy

Claude Code Edit Fix Proxy - 轻量级 SSE 代理，自动修正 Edit tool_use 中的 `old_string`，解决 GLM 等模型生成 Edit 调用时缩进/空格/换行不匹配的问题。

## 工作原理

```
Claude Code → ccedit-fix-proxy → 上游 API (GLM/Claude)
                    │
                    ├─ text/thinking 内容块 → 实时透传（零延迟）
                    ├─ Edit tool_use 内容块 → 缓存 → 读取文件 → 修正 old_string → 重新生成 SSE 事件
                    └─ 其他 tool_use → 实时透传
```

当模型生成的 Edit 调用中 `old_string` 与文件实际内容不匹配时，代理通过**两级匹配引擎**逐级尝试修正（详见下文「匹配引擎」）：

**层级 1 — Claude Code 原生匹配**（完全复刻 `FileEditTool/utils.ts:73-93`）

1. 精确匹配（无需修正）
2. 引号归一化（弯引号 ↔ 直引号）

**层级 2 — 代理额外修正**（处理 Claude Code 原生无法覆盖的场景）

1. 去除行尾空白
2. 统一换行符 (`\r\n` → `\n`)
3. Tab ↔ 空格转换（自动检测文件缩进风格）
4. 空白归一化匹配（去除所有空格和空行后定位，解决 GLM 多余空格/空行问题）
5. 缩进匹配（按行 strip 匹配，自动计算缩进差值，同步调整 `new_string`）

> 文件编码自动检测：UTF-16LE BOM / UTF-8 / GBK fallback。

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
# 通过代理启动（自动获取本机 IP）
bash start_cc.sh

# 在远程服务器上（需指定代理 IP）
bash start_cc.sh 10.47.16.58 8080

# 或者：不经过代理，直连上游 API
bash start_cc_direct.sh
```

`start_cc.sh` / `start_cc_direct.sh` 都会自动从同目录 `.env` 读取 `ANTHROPIC_API_KEY`，无需手动设置。

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
│   ├── edit_fixer.py          # old_string 自动修正逻辑（两级匹配引擎）
│   ├── __init__.py
│   └── __main__.py            # python -m ccefix_proxy 入口
├── log_web/
│   ├── log_viewer.py          # JSONL 日志 → HTML 可视化
│   ├── __init__.py
│   └── __main__.py            # python -m log_web 入口
├── start_proxy.sh             # 启动代理
├── start_cc.sh                # 通过代理启动 Claude Code
├── start_cc_direct.sh         # 直连上游 API 启动 Claude Code（无代理）
├── test.sh                    # 测试连通性
├── report.html                # 项目汇报页面（浏览器打开查看）
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

## 匹配引擎

`edit_fixer.py` 实现**两级匹配**，按优先级逐级尝试：

### 层级 1 — Claude Code 原生匹配

完全复刻 Claude Code `findActualString`（`FileEditTool/utils.ts:73-93`），保证与原生行为兼容：

- **精确匹配**：`old_string in file_content`，直接命中
- **引号归一化**：弯引号（‘’“ ”）↔ 直引号转换后匹配。命中后返回文件原文（保留弯引号），Claude Code 后续 `preserveQuoteStyle` 处理 `new_string`

### 层级 2 — 代理额外修正

层级 1 失败后执行，处理 Claude Code 原生逻辑无法覆盖的 GLM 特有问题：

| 步骤 | 策略 | new_string 处理 |
|---|---|---|
| ① | 去行尾空白 | 透传（上游 `normalizeFileEditInput` 已处理） |
| ③ | 换行符统一 `\r\n → \n` | 同步转换 |
| ④ | Tab ↔ 空格（自动检测缩进风格） | 同步转换 |
| ⑤ | 空白归一化（去所有空格/空行定位，返回文件原文） | 透传 |
| ⑥ | 缩进匹配（计算 delta） | `_apply_indent_delta` 调整 |

> 步骤 ②（sanitize 反转义）和 ⑦（difflib 模糊匹配）因 Claude Code 上游已处理 / 风险较高，当前已注释禁用。

### 文件读取管线（与 Claude Code 一致）

```
字节读取 → 编码检测 (BOM FF FE → UTF-16LE, 否则 UTF-8, 失败 → GBK)
        → \r\n → \n 统一 → 两级匹配
```

## 日志可视化

代理运行时按 session 生成 JSONL 日志（`logs/session_YYYYMMDD_HHMMSS_<uid>.jsonl`），可转换为可读的 HTML 页面：

```bash
# 转换单个 session 日志
python -m log_web logs/session_xxx.jsonl

# 转换目录下所有 session 日志
python -m log_web logs/
```

生成的 HTML 支持：按级别过滤（INFO/WARN/ERROR）、关键词搜索、Edit Fix 事件高亮。


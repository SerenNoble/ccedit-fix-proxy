# -*- coding: utf-8 -*-
"""自动修正 Edit tool 的 old_string 使其匹配文件实际内容。

修正分两层：
  层级1 – Claude Code 原生匹配（精确 → 引号归一化）
  层级2 – 代理额外修正（去行尾、sanitize 反转义、换行、Tab、缩进、模糊）
"""
from __future__ import annotations

import difflib
import logging
import os

log = logging.getLogger("edit-fix-proxy")


def fix_edit_old_string(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> tuple[str, str]:
    """读取 file_path，尝试修正 old_string 使其能匹配文件内容。

    返回 (修正后的old_string, 修正后的new_string)。
    如果无法修正，返回原始值。
    """
    if not file_path or not old_string:
        return old_string, new_string

    file_path = os.path.normpath(file_path)

    # ── 读取文件（与 Claude Code 一致：检测编码 + 统一换行符）──────────
    try:
        content = _read_file_content(file_path)
    except (FileNotFoundError, IOError, OSError) as exc:
        log.warning("[edit-fix] 无法读取文件 %s: %s", file_path, exc)
        return old_string, new_string

    # ── 层级1: Claude Code 原生匹配（精确 → 引号归一化）───────────────
    actual = _find_actual_string(content, old_string)
    if actual is not None:
        if actual == old_string:
            log.debug("[edit-fix] 精确匹配 %s", file_path)
        else:
            log.info("[edit-fix] Claude Code 原生匹配（引号归一化）%s", file_path)
        return actual, new_string

    log.info("[edit-fix] old_string 不匹配 %s，尝试代理额外修正", file_path)

    # ── 层级2: 代理额外修正 ──────────────────────────────────────────
    result = _proxy_extra_fixes(content, old_string, new_string, replace_all)
    if result is not None:
        return result

    log.warning("[edit-fix] 无法修正 old_string: %s", file_path)
    return old_string, new_string


# ── 辅助函数 ──────────────────────────────────────────────────────────


def _read_file_content(file_path: str) -> str:
    """读取文件内容。

    1. 按字节读取，检测 BOM 判断编码（UTF-16LE / UTF-8）
    2. UTF-8 解码失败时 fallback 到 GBK（Windows 中文环境）
    3. \r\n 统一转为 \n
    """
    with open(file_path, "rb") as fh:
        raw = fh.read()

    # 空文件
    if not raw:
        return ""

    # 检测 BOM: FF FE → UTF-16LE
    if len(raw) >= 2 and raw[0] == 0xFF and raw[1] == 0xFE:
        content = raw.decode("utf-16-le")
    else:
        # 先尝试 UTF-8 严格解码，失败则 fallback GBK
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            log.debug("[edit-fix] UTF-8 解码失败，尝试 GBK: %s", file_path)
            content = raw.decode("gbk", errors="replace")

    # \r\n → \n（与 Claude Code 一致）
    return content.replace("\r\n", "\n")


# ── 层级1: Claude Code 原生匹配（复刻 FileEditTool/utils.ts:73-93）─────

# 弯引号常量（Claude 无法输出，会输出直引号）
_LEFT_SINGLE_CURLY = "‘"
_RIGHT_SINGLE_CURLY = "’"
_LEFT_DOUBLE_CURLY = "“"
_RIGHT_DOUBLE_CURLY = "”"


def _normalize_quotes(s: str) -> str:
    """弯引号转直引号（与 Claude Code normalizeQuotes 一致）。"""
    return (
        s.replace(_LEFT_SINGLE_CURLY, "'")
        .replace(_RIGHT_SINGLE_CURLY, "'")
        .replace(_LEFT_DOUBLE_CURLY, '"')
        .replace(_RIGHT_DOUBLE_CURLY, '"')
    )


def _find_actual_string(file_content: str, search_string: str) -> str | None:
    """完全复刻 Claude Code findActualString（utils.ts:73-93）。

    只做两步：精确匹配 → 引号归一化。
    不做去行尾空白、不做 sanitize 反转义。
    """
    # ① 精确匹配
    if search_string in file_content:
        return search_string

    # ② 引号归一化（弯引号 ↔ 直引号）
    norm_search = _normalize_quotes(search_string)
    norm_file = _normalize_quotes(file_content)
    idx = norm_file.find(norm_search)
    if idx != -1:
        # 返回文件中实际截取的原文（保留弯引号）
        return file_content[idx : idx + len(search_string)]

    return None


# ── 层级2: 代理额外修正 ──────────────────────────────────────────────

# Claude 输出时的 sanitize 转义映射
_DESANITIZATIONS: dict[str, str] = {
    "<fnr>": "<function_results>",
    "<n>": "<name>",
    "</n>": "</name>",
    "<o>": "<output>",
    "</o>": "</output>",
    "<e>": "<error>",
    "</e>": "</error>",
    "<s>": "<system>",
    "</s>": "</system>",
    "<r>": "<result>",
    "</r>": "</result>",
    "< META_START >": "<META_START>",
    "< META_END >": "<META_END>",
    "< EOT >": "<EOT>",
    "< META >": "<META>",
    "< SOS >": "<SOS>",
    "\n\nH:": "\n\nHuman:",
    "\n\nA:": "\n\nAssistant:",
}


def _desanitize(s: str) -> tuple[str, list[tuple[str, str]]]:
    """反转义 Claude 的 sanitize 标记。返回 (结果, 已应用的替换列表)。"""
    result = s
    applied: list[tuple[str, str]] = []
    for src, dst in _DESANITIZATIONS.items():
        prev = result
        result = result.replace(src, dst)
        if result != prev:
            applied.append((src, dst))
    return result, applied


def _proxy_extra_fixes(
    file_content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> tuple[str, str] | None:
    """代理额外修正策略（Claude Code 原生匹配失败后执行）。

    按优先级尝试：去行尾 → sanitize 反转义 → 换行符 → Tab → 缩进 → 模糊。
    成功返回 (fixed_old, fixed_new)，全部失败返回 None。
    """
    # ① 去行尾空白
    fixed_old = _strip_trailing_ws(old_string)
    if fixed_old in file_content:
        log.info("[edit-fix] 修正成功: 去除行尾空白")
        return fixed_old, _strip_trailing_ws(new_string)

    # ② sanitize 反转义（暂时注释，Claude Code 执行路径上不做此处理）
    # desan_old, applied = _desanitize(old_string)
    # if desan_old in file_content:
    #     desan_new = new_string
    #     for src, dst in applied:
    #         desan_new = desan_new.replace(src, dst)
    #     log.info("[edit-fix] 修正成功: sanitize 反转义")
    #     return desan_old, desan_new

    # ③ 统一换行符
    fixed_old = old_string.replace("\r\n", "\n").replace("\r", "\n")
    if fixed_old in file_content:
        log.info("[edit-fix] 修正成功: 统一换行符")
        return fixed_old, new_string.replace("\r\n", "\n").replace("\r", "\n")

    # ④ Tab ↔ 空格转换
    for tab_size in (4, 2, 8):
        fixed_old = old_string.replace("\t", " " * tab_size)
        if fixed_old in file_content:
            log.info("[edit-fix] 修正成功: tab → %d空格", tab_size)
            return fixed_old, new_string.replace("\t", " " * tab_size)

    fixed_old = _spaces_to_tabs(old_string, file_content)
    if fixed_old and fixed_old in file_content:
        log.info("[edit-fix] 修正成功: 空格 → tab")
        return fixed_old, _spaces_to_tabs(new_string, file_content) or new_string

    # ⑤ 空白归一化匹配（处理多余空格/空行，GLM 常见问题）
    if not replace_all:
        norm_match = _normalize_whitespace_match(old_string, file_content)
        if norm_match:
            actual_old, fixed_new = norm_match
            log.info("[edit-fix] 修正成功: 空白归一化")
            return actual_old, fixed_new if fixed_new is not None else new_string

    # ⑥ 缩进模糊匹配（replace_all 时跳过，避免歧义）
    if not replace_all:
        match = _find_matching_region(old_string, file_content)
        if match:
            actual_old, indent_delta = match
            fixed_new = _apply_indent_delta(new_string, indent_delta)
            log.info("[edit-fix] 修正成功: 缩进调整 (delta=%d)", indent_delta)
            return actual_old, fixed_new

    # ⑦ difflib 模糊匹配（暂时注释，风险较高）
    # if not replace_all:
    #     fuzzy = _fuzzy_match(old_string, file_content)
    #     if fuzzy:
    #         actual_old, indent_delta = fuzzy
    #         fixed_new = _apply_indent_delta(new_string, indent_delta)
    #         log.info("[edit-fix] 修正成功: 模糊匹配")
    #         return actual_old, fixed_new

    return None


def _strip_trailing_ws(s: str) -> str:
    """去除每行行尾空白。"""
    return "\n".join(line.rstrip() for line in s.split("\n"))


def _collapse_whitespace(s: str) -> str:
    """空白归一化：去掉所有空格和空白行，只保留有效内容。

    用于定位匹配位置，不用于还原内容。
    """
    lines = s.split("\n")
    # 去掉每行所有空格，过滤空行
    result = ["".join(line.split()) for line in lines]
    result = [line for line in result if line]
    return "\n".join(result)


def _normalize_whitespace_match(
    old_string: str, file_content: str
) -> tuple[str, str] | None:
    """空白归一化后匹配：去掉所有空格/空白行后比较内容。

    归一化只用于定位匹配位置，匹配成功后返回文件原文。
    new_string 不做修改，直接透传（Claude Code 会精确替换文件原文）。
    """
    norm_old = _collapse_whitespace(old_string)
    if not norm_old:
        return None

    norm_old_lines = norm_old.split("\n")
    n = len(norm_old_lines)
    file_lines = file_content.split("\n")

    # 滑动窗口：归一化后行数可能不同（模型多输出了空行）
    # 所以窗口大小用 n 到 n+5 范围搜索
    for window_size in range(n, n + 6):
        if window_size > len(file_lines):
            break
        for i in range(max(1, len(file_lines) - window_size + 1)):
            window = file_lines[i : i + window_size]
            norm_window = _collapse_whitespace("\n".join(window))
            if norm_window == norm_old:
                # 返回文件原文，new_string 不变
                actual_old = "\n".join(window)
                return actual_old, None  # None 表示 new_string 保持原样

    return None


def _spaces_to_tabs(s: str, content: str) -> str | None:
    """如果文件使用 tab 缩进，将前导空格转为 tab。"""
    file_lines = content.split("\n")
    tab_lines = sum(1 for ln in file_lines if ln.startswith("\t"))
    space_lines = sum(1 for ln in file_lines if ln.startswith("  "))
    if tab_lines <= space_lines:
        return None  # 文件不以 tab 为主

    result_lines = []
    for line in s.split("\n"):
        leading = len(line) - len(line.lstrip(" "))
        tabs = leading // 4
        remaining = leading % 4
        result_lines.append("\t" * tabs + " " * remaining + line.lstrip(" "))
    return "\n".join(result_lines)


def _first_line_indent(s: str) -> int:
    """返回第一个非空行的缩进空格数。"""
    for line in s.split("\n"):
        stripped = line.lstrip()
        if stripped:
            return len(line) - len(stripped)
    return 0


def _find_matching_region(
    old_string: str, content: str
) -> tuple[str, int] | None:
    """在文件中按行 strip 后查找匹配区域。

    返回 (文件中实际内容, 缩进差值) 或 None。
    """
    old_lines = old_string.split("\n")
    old_stripped = [ln.strip() for ln in old_lines]
    content_lines = content.split("\n")
    n = len(old_lines)

    for i in range(max(1, len(content_lines) - n + 1)):
        window = content_lines[i : i + n]
        if [ln.strip() for ln in window] == old_stripped:
            actual = "\n".join(window)
            delta = _first_line_indent(actual) - _first_line_indent(old_string)
            return actual, delta
    return None


def _apply_indent_delta(s: str, delta: int) -> str:
    """给所有非空行调整缩进（delta 为正加空格，为负减空格）。"""
    if delta == 0:
        return s
    lines = s.split("\n")
    result = []
    for line in lines:
        if not line.strip():
            result.append(line)
            continue
        cur = len(line) - len(line.lstrip())
        new_indent = max(0, cur + delta)
        result.append(" " * new_indent + line.lstrip())
    return "\n".join(result)


def _fuzzy_match(old_string: str, content: str) -> tuple[str, int] | None:
    """用 difflib 查找最接近的匹配区域。"""
    old_lines = old_string.split("\n")
    content_lines = content.split("\n")
    n = len(old_lines)

    if n < 2:
        return _fuzzy_match_single(old_string, content)

    norm_old = [ln.strip() for ln in old_lines if ln.strip()]
    if not norm_old:
        return None

    best_start = -1
    best_score = 0.0
    threshold = 0.6

    for i in range(max(1, len(content_lines) - n + 1)):
        window = content_lines[i : i + n]
        norm_win = [ln.strip() for ln in window if ln.strip()]
        if not norm_win:
            continue
        score = difflib.SequenceMatcher(None, norm_old, norm_win).ratio()
        if score > best_score:
            best_score = score
            best_start = i

    if best_score >= threshold and best_start >= 0:
        actual = "\n".join(content_lines[best_start : best_start + n])
        delta = _first_line_indent(actual) - _first_line_indent(old_string)
        return actual, delta
    return None


def _fuzzy_match_single(old_string: str, content: str) -> tuple[str, int] | None:
    """单行模糊匹配。"""
    stripped_old = old_string.strip()
    best_line: str | None = None
    best_score = 0.0
    for line in content.split("\n"):
        score = difflib.SequenceMatcher(None, stripped_old, line.strip()).ratio()
        if score > best_score:
            best_score = score
            best_line = line
    if best_score >= 0.6 and best_line is not None:
        delta = _first_line_indent(best_line) - _first_line_indent(old_string)
        return best_line, delta
    return None

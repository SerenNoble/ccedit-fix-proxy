"""Auto-fix Edit tool old_string to match actual file content.

Strategies (in priority order):
1. Exact match – no fix needed
2. Strip trailing whitespace
3. Normalize line endings (\\r\\n → \\n)
4. Tab ↔ space conversion
5. Indentation adjustment (find matching region, return actual content)
6. Fuzzy line match (difflib-based)
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
    """Read *file_path* and try to fix *old_string* so it matches the file.

    Returns ``(fixed_old_string, fixed_new_string)``.
    If no fix is possible, returns the originals unchanged.
    """
    if not file_path or not old_string:
        return old_string, new_string

    file_path = os.path.normpath(file_path)

    # ── 1. Read file ──────────────────────────────────────────────────
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except (FileNotFoundError, IOError, OSError) as exc:
        log.warning("[edit-fix] Cannot read %s: %s", file_path, exc)
        return old_string, new_string

    # ── 2. Exact match ────────────────────────────────────────────────
    if old_string in content:
        log.debug("[edit-fix] Exact match in %s", file_path)
        return old_string, new_string

    log.info("[edit-fix] old_string mismatch in %s – attempting fix", file_path)

    # ── 3. Strip trailing whitespace ──────────────────────────────────
    fixed_old = _strip_trailing_ws(old_string)
    if fixed_old in content:
        log.info("[edit-fix] Fixed: trailing whitespace stripped")
        return fixed_old, _strip_trailing_ws(new_string)

    # ── 4. Normalize line endings ─────────────────────────────────────
    fixed_old = old_string.replace("\r\n", "\n").replace("\r", "\n")
    if fixed_old in content:
        log.info("[edit-fix] Fixed: line endings normalized")
        return fixed_old, new_string.replace("\r\n", "\n").replace("\r", "\n")

    # ── 5. Tab ↔ space conversion ─────────────────────────────────────
    for tab_size in (4, 2, 8):
        fixed_old = old_string.replace("\t", " " * tab_size)
        if fixed_old in content:
            log.info("[edit-fix] Fixed: tabs → %d spaces", tab_size)
            return fixed_old, new_string.replace("\t", " " * tab_size)

    # Also try: actual file uses tabs, model used spaces
    fixed_old = _spaces_to_tabs(old_string, content)
    if fixed_old and fixed_old in content:
        log.info("[edit-fix] Fixed: spaces → tabs")
        return fixed_old, _spaces_to_tabs(new_string, content) or new_string

    # ── 6. Indentation / matching region ──────────────────────────────
    # Skip if replace_all (ambiguous – multiple matches possible)
    if not replace_all:
        match = _find_matching_region(old_string, content)
        if match:
            actual_old, indent_delta = match
            fixed_new = _apply_indent_delta(new_string, indent_delta)
            log.info("[edit-fix] Fixed: indentation adjusted (delta=%d)", indent_delta)
            return actual_old, fixed_new

    # ── 7. Fuzzy match ────────────────────────────────────────────────
    if not replace_all:
        fuzzy = _fuzzy_match(old_string, content)
        if fuzzy:
            actual_old, indent_delta = fuzzy
            fixed_new = _apply_indent_delta(new_string, indent_delta)
            log.info("[edit-fix] Fixed: fuzzy match")
            return actual_old, fixed_new

    log.warning("[edit-fix] Could not fix old_string for %s", file_path)
    return old_string, new_string


# ── helpers ────────────────────────────────────────────────────────────────


def _strip_trailing_ws(s: str) -> str:
    return "\n".join(line.rstrip() for line in s.split("\n"))


def _spaces_to_tabs(s: str, content: str) -> str | None:
    """Convert leading spaces to tabs if the file uses tabs."""
    # Detect if file uses tabs for indentation
    file_lines = content.split("\n")
    tab_lines = sum(1 for ln in file_lines if ln.startswith("\t"))
    space_lines = sum(1 for ln in file_lines if ln.startswith("  "))
    if tab_lines <= space_lines:
        return None  # File doesn't primarily use tabs

    result_lines = []
    for line in s.split("\n"):
        leading = len(line) - len(line.lstrip(" "))
        tabs = leading // 4
        remaining = leading % 4
        result_lines.append("\t" * tabs + " " * remaining + line.lstrip(" "))
    return "\n".join(result_lines)


def _first_line_indent(s: str) -> int:
    """Return the indentation of the first non-empty line."""
    for line in s.split("\n"):
        stripped = line.lstrip()
        if stripped:
            return len(line) - len(stripped)
    return 0


def _find_matching_region(
    old_string: str, content: str
) -> tuple[str, int] | None:
    """Find the region in *content* whose stripped lines match *old_string*.

    Returns ``(actual_content, indent_delta)`` or ``None``.
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
    """Add *delta* leading spaces to every indented line in *s*."""
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
    """Use difflib to find the best matching region."""
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
    """Fuzzy match a single line."""
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

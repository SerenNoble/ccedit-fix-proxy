"""SSE stream interceptor – buffer Edit tool_use, fix old_string, re-emit.

State machine
~~~~~~~~~~~~~
```
                content_block_start(Edit)
  passthrough ─────────────────────────────► buffering_edit
       ▲                                         │
       │              content_block_stop          │
       └──────────────────────────────────────────┘
                       fix + regenerate SSE
```

In *passthrough* mode every parsed SSE event is returned as raw bytes
immediately (zero additional latency for text / thinking blocks).

In *buffering_edit* mode events are held.  When ``content_block_stop``
arrives the complete ``input_json`` is decoded, passed through
``edit_fixer.fix_edit_old_string()``, and a fresh set of SSE events is
returned.
"""
from __future__ import annotations

import json
import logging

from .edit_fixer import fix_edit_old_string

log = logging.getLogger("edit-fix-proxy")


class StreamInterceptor:
    """Parse SSE bytes, intercept Edit tool_use blocks, auto-fix old_string."""

    def __init__(self, test_mode: bool = False,
                 test_old: str = 'old_flag = "HELLO change"',
                 test_new: str = "hello mkp") -> None:
        # SSE line parser state
        self._test_mode = test_mode
        self._test_old = test_old
        self._test_new = test_new
        self._buf = b""
        self._event_type: str | None = None
        self._data_lines: list[str] = []
        self._raw_lines: list[bytes] = []  # raw bytes for the current event

        # Edit-buffering state
        self._state: str = "passthrough"
        self._edit_index: int = -1
        self._edit_tool_id: str = ""
        self._edit_partial_json: str = ""
        self._edit_raw_events: list[bytes] = []

    # ── public API ────────────────────────────────────────────────────

    def feed_bytes(self, chunk: bytes) -> list[bytes]:
        """Feed a raw chunk from upstream; return SSE byte chunks to write."""
        self._buf += chunk
        outputs: list[bytes] = []
        while b"\n" in self._buf:
            line_bytes, self._buf = self._buf.split(b"\n", 1)
            line_str = line_bytes.decode("utf-8", errors="replace").rstrip("\r")
            for out in self._feed_line(line_str, line_bytes + b"\n"):
                outputs.append(out)
        return outputs

    # ── SSE line parser ───────────────────────────────────────────────

    def _feed_line(self, line: str, raw: bytes) -> list[bytes]:
        if line.startswith("event:"):
            self._event_type = line[6:].strip()
            self._data_lines = []
            self._raw_lines = [raw]
            return []

        if line.startswith("data:"):
            self._data_lines.append(line[5:].strip())
            self._raw_lines.append(raw)
            return []

        if line == "":
            # blank line = end of event
            if self._event_type is None and not self._data_lines:
                return []
            raw_event = b"".join(self._raw_lines) + b"\n"
            event_type = self._event_type or "message"
            raw_data = "\n".join(self._data_lines)

            # "[DONE]" sentinel (OpenAI-style)
            if raw_data == "[DONE]" and self._event_type is None:
                self._reset_parser()
                return [raw_event]

            try:
                data = json.loads(raw_data)
            except (json.JSONDecodeError, ValueError):
                data = raw_data

            outputs = self._dispatch(event_type, data, raw_event)
            self._reset_parser()
            return outputs

        # Non-standard line – accumulate raw for passthrough
        self._raw_lines.append(raw)
        return []

    def _reset_parser(self) -> None:
        self._event_type = None
        self._data_lines = []
        self._raw_lines = []

    # ── state machine ─────────────────────────────────────────────────

    def _dispatch(self, event_type: str, data, raw_event: bytes) -> list[bytes]:
        if self._state == "passthrough":
            return self._handle_passthrough(event_type, data, raw_event)
        # buffering_edit
        return self._handle_buffering(event_type, data, raw_event)

    def _handle_passthrough(self, event_type: str, data, raw_event: bytes) -> list[bytes]:
        """Check if this starts an Edit tool_use block; otherwise pass through."""
        if event_type == "content_block_start" and isinstance(data, dict):
            block = data.get("content_block", {})
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") in ("Edit", "edit")
            ):
                self._state = "buffering_edit"
                self._edit_index = data.get("index", 0)
                self._edit_tool_id = block.get("id", "")
                self._edit_partial_json = ""
                self._edit_raw_events = [raw_event]
                return []  # buffer – don't emit yet

        return [raw_event]  # passthrough

    def _handle_buffering(self, event_type: str, data, raw_event: bytes) -> list[bytes]:
        """Accumulate Edit deltas; on content_block_stop fix and emit."""
        if isinstance(data, dict):
            delta = data.get("delta", {})
            if isinstance(delta, dict) and delta.get("type") == "input_json_delta":
                self._edit_partial_json += delta.get("partial_json", "")

        self._edit_raw_events.append(raw_event)

        if event_type == "content_block_stop":
            outputs = self._flush_edit()
            self._state = "passthrough"
            return outputs

        return []  # keep buffering

    # ── fix & regenerate ──────────────────────────────────────────────

    def _flush_edit(self) -> list[bytes]:
        """Try to fix old_string; return regenerated SSE events."""
        # Parse accumulated JSON
        try:
            tool_input = json.loads(self._edit_partial_json)
        except (json.JSONDecodeError, ValueError):
            log.warning("[edit-fix] Malformed Edit JSON – passing through")
            return self._edit_raw_events

        file_path = tool_input.get("file_path", "")
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        replace_all = tool_input.get("replace_all", False)

        log.info("[edit-fix] === Intercepted Edit ===")
        log.info("[edit-fix]   file_path:   %s", file_path)
        log.info("[edit-fix]   replace_all: %s", replace_all)
        log.info("[edit-fix]   old_string:  %s", old_string[:200])
        log.info("[edit-fix]   new_string:  %s", new_string[:200])

        if not file_path or not old_string:
            log.info("[edit-fix]   → skipped (empty file_path or old_string)")
            return self._edit_raw_events

        fixed_old, fixed_new = fix_edit_old_string(
            file_path, old_string, new_string, replace_all=replace_all
        )

        # ── TEST MODE ──
        if self._test_mode:
            fixed_old = self._test_old
            fixed_new = self._test_new
            log.info("[edit-fix]   [TEST MODE] forcing old_string = %s", repr(fixed_old))
            log.info("[edit-fix]   [TEST MODE] forcing new_string = %s", repr(fixed_new))

        if fixed_old == old_string and fixed_new == new_string:
            # Nothing changed → emit original events
            log.info("[edit-fix]   → no fix needed or fix failed, passing through")
            return self._edit_raw_events

        log.info(
            "[edit-fix]   ✓ FIXED! %d → %d chars", len(old_string), len(fixed_old))
        log.info("[edit-fix]   fixed_old: %s", fixed_old[:200])
        log.info("[edit-fix]   fixed_new: %s", fixed_new[:200])

        # Build new tool input
        new_input = dict(tool_input)
        new_input["old_string"] = fixed_old
        new_input["new_string"] = fixed_new
        new_input_json = json.dumps(new_input, ensure_ascii=False)

        idx = self._edit_index
        return [
            _sse("content_block_start", {
                "type": "content_block_start",
                "index": idx,
                "content_block": {
                    "type": "tool_use",
                    "id": self._edit_tool_id,
                    "name": "Edit",
                },
            }),
            _sse("content_block_delta", {
                "type": "content_block_delta",
                "index": idx,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": new_input_json,
                },
            }),
            _sse("content_block_stop", {
                "type": "content_block_stop",
                "index": idx,
            }),
        ]


def _sse(event_type: str, data: dict) -> bytes:
    """Build a single SSE frame: ``event: …\\ndata: …\\n\\n``."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")

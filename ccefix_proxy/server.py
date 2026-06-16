"""Lightweight forward proxy that auto-fixes Edit tool old_string in SSE streams.

Usage
-----
::

    # Set the real upstream API endpoint
    export EDIT_FIX_UPSTREAM=https://api.anthropic.com   # or your GLM endpoint

    # Start the proxy
    python -m edit_fix_proxy --port 8080

    # Point Claude Code at the proxy
    export ANTHROPIC_BASE_URL=http://localhost:8080
    claude
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

from .stream_interceptor import StreamInterceptor

log = logging.getLogger("edit-fix-proxy")

# Headers that must not be forwarded hop-by-hop.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        # We decompress via aiohttp; don't echo upstream encoding.
        "content-encoding",
        "content-length",
    }
)


class EditFixProxy:
    """Async forward proxy backed by ``aiohttp``."""

    def __init__(self, upstream_url: str, listen_port: int = 8080,
                 test_mode: bool = False,
                 test_old: str = 'old_flag = "HELLO change"',
                 test_new: str = "hello mkp") -> None:
        self.upstream = upstream_url.rstrip("/")
        self.port = listen_port
        self.test_mode = test_mode
        self.test_old = test_old
        self.test_new = test_new
        self._session: aiohttp.ClientSession | None = None

    # ── lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", self._handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()
        log.info("Listening on 0.0.0.0:%d  →  %s", self.port, self.upstream)

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── request handler ───────────────────────────────────────────────

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        path = request.match_info.get("path", "")
        upstream_url = f"{self.upstream}/{path}"
        if request.query_string:
            upstream_url += f"?{request.query_string}"

        remote = request.remote or request.headers.get("X-Forwarded-For", "?")
        log.info(">>> %s %s from %s", request.method, path, remote)

        headers = _forward_headers(request.headers, self.upstream)

        body = await request.read()
        log.debug("    request body len=%d", len(body))

        is_stream_req = False
        if body:
            try:
                req_json = json.loads(body)
                is_stream_req = req_json.get("stream", False)
                log.debug("    model=%s stream=%s", req_json.get("model", "?"), is_stream_req)
            except (json.JSONDecodeError, ValueError):
                pass

        log.info(">>> forwarding to %s", upstream_url)
        try:
            upstream_resp = await self._session.request(
                method=request.method,
                url=upstream_url,
                headers=headers,
                data=body,
                timeout=aiohttp.ClientTimeout(total=600),
            )
        except Exception as exc:
            log.error("!!! Upstream request failed: %s", exc)
            return web.Response(status=502, text=f"Proxy error: {exc}")

        log.info("<<< upstream status=%d content-type=%s", upstream_resp.status, upstream_resp.headers.get("Content-Type", ""))

        ct = upstream_resp.headers.get("Content-Type", "")
        is_streaming = "text/event-stream" in ct or is_stream_req

        if is_streaming:
            log.info("<<< streaming mode (intercepting)")
            return await self._stream(request, upstream_resp)
        log.info("<<< passthrough mode")
        return await self._passthrough(request, upstream_resp)

    # ── streaming path (intercepted) ──────────────────────────────────

    async def _stream(
        self,
        request: web.Request,
        upstream: aiohttp.ClientResponse,
    ) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=upstream.status,
            headers=_forward_headers(upstream.headers, ""),
        )
        await resp.prepare(request)

        interceptor = StreamInterceptor(test_mode=self.test_mode,
                                        test_old=self.test_old,
                                        test_new=self.test_new)

        try:
            async for chunk in upstream.content.iter_any():
                for out in interceptor.feed_bytes(chunk):
                    await resp.write(out)
        except (ConnectionError, asyncio.CancelledError):
            pass

        try:
            await resp.write_eof()
        except (ConnectionError, ConnectionResetError):
            pass
        finally:
            upstream.release()

        return resp

    # ── non-streaming path (plain forward) ────────────────────────────

    async def _passthrough(
        self,
        request: web.Request,
        upstream: aiohttp.ClientResponse,
    ) -> web.Response:
        body = await upstream.read()
        upstream.release()
        return web.Response(
            status=upstream.status,
            headers=_forward_headers(upstream.headers),
            body=body,
        )


# ── helpers ────────────────────────────────────────────────────────────────


class _JsonlFormatter(logging.Formatter):
    """每条日志输出为一行 JSON。"""
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _forward_headers(headers, upstream_url: str = "") -> dict[str, str]:
    """Strip hop-by-hop headers and fix Host for forwarding."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() not in HOP_BY_HOP:
            out[k] = v
    # Replace Host with the upstream host
    if upstream_url:
        parsed = urlparse(upstream_url)
        if parsed.hostname:
            out["Host"] = parsed.hostname
    return out


# ── .env loader ────────────────────────────────────────────────────────────


def _load_dotenv() -> None:
    """Load .env file from the project root. Does NOT override existing env vars."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


# ── CLI ────────────────────────────────────────────────────────────────────


def main() -> None:
    # ── Load .env file (lowest priority) ─────────────────────────────
    _load_dotenv()

    parser = argparse.ArgumentParser(
        description="Claude Code Edit Fix Proxy – auto-fix Edit old_string in SSE streams",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("EDIT_FIX_PORT", "8080")),
        help="Listen port (env: EDIT_FIX_PORT, default: 8080)",
    )
    parser.add_argument(
        "--upstream",
        type=str,
        default=os.environ.get("EDIT_FIX_UPSTREAM", ""),
        help="Upstream API base URL (env: EDIT_FIX_UPSTREAM)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="DEBUG-level logging",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        default=os.environ.get("EDIT_FIX_TEST", "").lower() in ("1", "true", "yes"),
        help="Test mode: force all Edit old/new_string to fixed values (env: EDIT_FIX_TEST)",
    )
    parser.add_argument(
        "--test-old",
        type=str,
        default=os.environ.get("EDIT_FIX_TEST_OLD", 'old_flag = "HELLO change"'),
        help="Test mode old_string (env: EDIT_FIX_TEST_OLD)",
    )
    parser.add_argument(
        "--test-new",
        type=str,
        default=os.environ.get("EDIT_FIX_TEST_NEW", "hello mkp"),
        help="Test mode new_string (env: EDIT_FIX_TEST_NEW)",
    )
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    # Text: console + proxy.log
    text_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(text_fmt)
    log_file = os.path.join(log_dir, "proxy.log")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(text_fmt)

    # JSONL: 按 session 分文件 (session_YYYYMMDD_HHMMSS_<uid>.jsonl)
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    jsonl_file = os.path.join(log_dir, f"session_{session_id}.jsonl")
    jsonl_handler = logging.FileHandler(jsonl_file, encoding="utf-8")
    jsonl_handler.setFormatter(_JsonlFormatter())

    logging.basicConfig(
        level=level,
        handlers=[stream_handler, file_handler, jsonl_handler],
    )
    log.info("Session: %s | Log: %s | JSONL: %s", session_id, log_file, jsonl_file)

    if not args.upstream:
        print(
            "Error: --upstream is required.\n"
            "  Set EDIT_FIX_UPSTREAM or pass --upstream <url>\n"
            "  Example: --upstream https://api.anthropic.com",
            file=sys.stderr,
        )
        sys.exit(1)

    proxy = EditFixProxy(args.upstream, args.port,
                         test_mode=args.test,
                         test_old=args.test_old,
                         test_new=args.test_new)
    if args.test:
        log.info("TEST MODE enabled")
        log.info("  test_old = %s", repr(args.test_old))
        log.info("  test_new = %s", repr(args.test_new))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(proxy.start())
        loop.run_forever()
    except KeyboardInterrupt:
        log.info("Shutting down…")
    finally:
        loop.run_until_complete(proxy.stop())
        loop.close()

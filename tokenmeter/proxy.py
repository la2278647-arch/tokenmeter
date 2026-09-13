#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tokenmeter · 透明记账代理（OpenAI 兼容）。

业务代码零改动：把 OpenAI 客户端的 base_url 指到本代理，
所有 /v1/chat/completions 请求会被转发到上游真实 API，
同时自动解析 usage 字段写入本地 SQLite —— 用量、费用全部落账。

特性
----
- 非流式（JSON）响应：解析 usage 直接记账。
- 流式（SSE）响应：逐块透传，并强制注入 stream_options.include_usage=true，
  从末块拿到 usage 记账 —— 流式也不漏记。
- 支持任意上游（OpenAI / DeepSeek / 兼容网关），凭据由客户端原样透传。
- 项目归属：客户端可在请求体 meta.project 或 HTTP 头 X-Project 里标注项目。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .db import UsageDB, iso_now

# SSE 流式响应中 usage 的标记字段
STREAM_USAGE_FLAG = "stream_options"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ 工具
    @property
    def db(self) -> UsageDB:
        return self.server.db  # type: ignore[attr-defined]

    @property
    def upstream(self) -> str:
        return self.server.upstream  # type: ignore[attr-defined]

    def _json_body(self) -> Optional[dict]:
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _log_it(self, model: str, pt: int, ct: int, ms: float, status: str,
                error: str = "", project: str = "default", provider: str = "",
                ts: Optional[str] = None) -> None:
        try:
            self.db.record(model=model, provider=provider, project=project,
                           prompt_tokens=pt, completion_tokens=ct,
                           latency_ms=ms, status=status, error=error, ts=ts)
        except Exception as e:  # 记账失败绝不能拖垮转发
            print(f"[tokenmeter] 记账失败: {e}", file=sys.stderr)

    # ------------------------------------------------------------ 入口
    def do_POST(self):
        path = self.path.split("?")[0]
        if path.rstrip("/") == "/v1/chat/completions":
            self._handle_chat()
        else:
            self._send_json(404, {"error": {"message": f"unknown path {path}"}})

    def do_GET(self):
        self._send_json(200, {"ok": True, "service": "tokenmeter-proxy",
                              "upstream": self.upstream,
                              "db": self.db.path})

    # ------------------------------------------------------------ 核心
    def _handle_chat(self):
        body = self._json_body()
        if body is None:
            self._send_json(400, {"error": {"message": "invalid json body"}})
            return

        model = body.get("model", "")
        project = (self.headers.get("X-Project")
                   or (body.get("meta") or {}).get("project")
                   or "default")
        stream = bool(body.get("stream"))

        # 流式记账需要服务端回传 usage：强制注入 include_usage
        if stream:
            so = body.get(STREAM_USAGE_FLAG)
            if not isinstance(so, dict):
                so = {}
            so["include_usage"] = True
            body[STREAM_USAGE_FLAG] = so

        upstream_url = self.upstream.rstrip("/") + "/v1/chat/completions"
        req = urllib.request.Request(
            upstream_url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": self.headers.get("Authorization", ""),
                "Content-Type": "application/json",
                "Accept": self.headers.get("Accept", "application/json"),
            },
            method="POST")

        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.server.timeout) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if stream or "text/event-stream" in ctype:
                    self._relay_stream(resp, model, project, t0, body)
                else:
                    self._relay_json(resp, model, project, t0)
        except urllib.error.HTTPError as e:
            # 上游错误原样回传（4xx/5xx），并把失败也记一笔
            ms = (time.time() - t0) * 1000
            err_body = b""
            try:
                err_body = e.read()
            except Exception:
                pass
            self._log_it(model, 0, 0, ms, "error",
                         error=f"upstream {e.code}", project=project)
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            ms = (time.time() - t0) * 1000
            self._log_it(model, 0, 0, ms, "error", error=str(e)[:200], project=project)
            self._send_json(502, {"error": {"message": f"upstream error: {e}"}})

    def _relay_json(self, resp, model: str, project: str, t0: float):
        data = resp.read()
        ms = (time.time() - t0) * 1000
        usage = {}
        try:
            parsed = json.loads(data.decode("utf-8"))
            usage = parsed.get("usage") or {}
            self._log_it(model, usage.get("prompt_tokens", 0),
                         usage.get("completion_tokens", 0), ms, "ok", project=project)
        except Exception:
            self._log_it(model, 0, 0, ms, "ok", project=project)
        self.send_response(resp.status)
        for k, v in resp.headers.items():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _relay_stream(self, resp, model: str, project: str, t0: float, body: dict):
        """透传 SSE 流；从 `data: {...}` 块里解析 usage，末块后记账。"""
        self.send_response(resp.status)
        for k, v in resp.headers.items():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.end_headers()

        pt = ct = 0
        buf = b""
        try:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                buf += chunk
                # 逐事件行找 usage
                for line in buf.split(b"\n"):
                    if line.startswith(b"data: "):
                        try:
                            evt = json.loads(line[6:].decode("utf-8"))
                        except Exception:
                            continue
                        u = evt.get("usage")
                        if isinstance(u, dict):
                            pt = u.get("prompt_tokens", pt)
                            ct = u.get("completion_tokens", ct)
                buf = buf[-8192:]  # 只留尾部，避免长流内存膨胀
        except Exception as e:
            print(f"[tokenmeter] 流式透传中断: {e}", file=sys.stderr)
        finally:
            ms = (time.time() - t0) * 1000
            self._log_it(model, pt, ct, ms, "ok", project=project)


def run_proxy(*, db_path: Optional[str] = None, host: str = "127.0.0.1",
              port: int = 8787, upstream: str = "https://api.openai.com",
              timeout: float = 300.0) -> None:
    """启动透明记账代理（阻塞）。"""
    db = UsageDB(db_path or UsageDB.DEFAULT_DB_PATH)
    server = ThreadingHTTPServer((host, port), _Handler)
    server.db = db            # type: ignore[attr-defined]
    server.upstream = upstream  # type: ignore[attr-defined]
    server.timeout = timeout  # type: ignore[attr-defined]
    print("=" * 62)
    print("  tokenmeter · 透明记账代理")
    print("=" * 62)
    print(f"  监听      http://{host}:{port}/v1/chat/completions")
    print(f"  上游      {upstream}")
    print(f"  数据库    {db.path}")
    print(f"  用法      OpenAI(base_url='http://{host}:{port}/v1')")
    print("-" * 62)
    print("  Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
    finally:
        server.server_close()
        db.close()


__all__ = ["run_proxy"]

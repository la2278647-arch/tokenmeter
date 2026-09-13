#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tokenmeter · Web 仪表盘（零依赖）。

纯标准库 HTTP 服务 + 内嵌单页前端：
  - 前端只有原生 JS + SVG 手绘图表，无 npm、无 CDN、无构建步骤
  - JSON API 由本文件提供
  - 只监听 127.0.0.1，数据不出本机
"""

from __future__ import annotations

import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .db import UsageDB

_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def _iso_days_ago(days: int) -> str:
    import datetime
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days)).isoformat(timespec="seconds")


def _q(args: dict, name: str, default=None):
    v = args.get(name)
    return v[0] if v else default


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def db(self) -> UsageDB:
        return self.server.db  # type: ignore[attr-defined]

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    # ------------------------------------------------------------ 路由
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        args = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._serve_file("index.html", "text/html; charset=utf-8")
        elif path == "/api/summary":
            self._json(200, self.db.summary(
                since=_q(args, "since"), until=_q(args, "until"),
                project=_q(args, "project"), model=_q(args, "model")))
        elif path == "/api/by-model":
            self._json(200, self.db.group_by("model",
                        since=_q(args, "since"), until=_q(args, "until"),
                        project=_q(args, "project"), model=_q(args, "model")))
        elif path == "/api/by-project":
            self._json(200, self.db.group_by("project",
                        since=_q(args, "since"), until=_q(args, "until"),
                        project=_q(args, "project"), model=_q(args, "model")))
        elif path == "/api/daily":
            days = int(_q(args, "days", "30"))
            self._json(200, self.db.daily(
                days=days, since=_q(args, "since"), until=_q(args, "until"),
                project=_q(args, "project"), model=_q(args, "model")))
        elif path == "/api/recent":
            limit = int(_q(args, "limit", "30"))
            self._json(200, self.db.recent(
                limit=limit, since=_q(args, "since"), until=_q(args, "until"),
                project=_q(args, "project"), model=_q(args, "model")))
        elif path == "/api/prices":
            self._json(200, {"prices": self.db.all_prices(),
                             "fallback": {"input": 1.0, "output": 1.0}})
        else:
            self._json(404, {"error": f"unknown path {path}"})

    def _serve_file(self, name: str, ctype: str):
        try:
            with open(os.path.join(_WEB_DIR, name), "rb") as f:
                self._send(200, f.read(), ctype)
        except FileNotFoundError:
            self._json(404, {"error": f"{name} not found"})


def run_server(*, db_path: Optional[str] = None, host: str = "127.0.0.1",
               port: int = 8765) -> None:
    """启动仪表盘服务（阻塞）。"""
    db = UsageDB(db_path or UsageDB.DEFAULT_DB_PATH)
    server = ThreadingHTTPServer((host, port), _Handler)
    server.db = db  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    print("=" * 62)
    print("  tokenmeter · Web 仪表盘")
    print("=" * 62)
    print(f"  访问      {url}")
    print(f"  数据库    {db.path}")
    print("-" * 62)
    print("  Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
    finally:
        server.server_close()
        db.close()


__all__ = ["run_server"]

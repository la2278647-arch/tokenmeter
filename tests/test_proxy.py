#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tokenmeter 端到端测试：透明代理自动记账。

用本地假上游（模拟 OpenAI 兼容 API）验证：
  1. 非流式请求 → 解析 usage 记账
  2. 流式（SSE）请求 → 透传并解析末块 usage 记账
  3. 项目归属从 X-Project 头 / meta.project 读取
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tokenmeter.db import UsageDB  # noqa: E402


def make_fake_upstream(stream: bool):
    """构造一个返回固定 usage 的假 OpenAI 端点。"""

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            usage = {"prompt_tokens": 111, "completion_tokens": 22,
                     "total_tokens": 133}
            if body.get("stream"):
                chunks = [
                    b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
                    b"data: " + json.dumps({"choices": [], "usage": usage}).encode()
                    + b"\n\n",
                    b"data: [DONE]\n\n",
                ]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(sum(len(c) for c in chunks)))
                self.end_headers()
                for c in chunks:
                    self.wfile.write(c)
            else:
                out = {"id": "cmpl-test", "model": body.get("model"),
                       "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                       "usage": usage}
                data = json.dumps(out).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        def log_message(self, *a):
            pass

    return H


class TestProxyE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 假上游
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), make_fake_upstream(stream=False))
        cls.up_port = cls.upstream.server_address[1]
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()

        # 被测代理（用临时 DB）
        cls.dir = tempfile.mkdtemp(prefix="tokenmeter-proxy-test-")
        cls.db_path = os.path.join(cls.dir, "proxy.db")
        from tokenmeter.proxy import _Handler, run_proxy
        cls.Handler = _Handler

        import tokenmeter.proxy as proxy_mod
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.db = UsageDB(cls.db_path)
        server.upstream = f"http://127.0.0.1:{cls.up_port}"
        server.timeout = 60.0
        cls.proxy = server
        cls.proxy_port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.proxy.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def _post(self, body: dict, headers: dict = None) -> bytes:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.proxy_port}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()

    def test_non_stream_records_usage(self):
        self._post({"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]},
                   {"X-Project": "e2e-app"})
        rows = UsageDB(self.db_path).recent(limit=5)
        self.assertGreaterEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["prompt_tokens"], 111)
        self.assertEqual(r["completion_tokens"], 22)
        self.assertEqual(r["project"], "e2e-app")
        self.assertEqual(r["status"], "ok")

    def test_stream_records_usage_from_tail_chunk(self):
        self._post({"model": "gpt-4o-mini", "stream": True,
                    "messages": [{"role": "user", "content": "x"}]},
                   {"X-Project": "e2e-stream"})
        rows = UsageDB(self.db_path).recent(limit=5)
        hit = [r for r in rows if r["project"] == "e2e-stream"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["prompt_tokens"], 111)
        self.assertEqual(hit[0]["completion_tokens"], 22)

    def test_meta_project_fallback(self):
        self._post({"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}],
                    "meta": {"project": "meta-proj"}})
        rows = UsageDB(self.db_path).recent(limit=5)
        self.assertTrue(any(r["project"] == "meta-proj" for r in rows))


if __name__ == "__main__":
    unittest.main()

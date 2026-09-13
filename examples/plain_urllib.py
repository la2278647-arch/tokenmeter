#!/usr/bin/env python3
"""纯标准库调用示例：不依赖 openai SDK，直接打透明代理。

演示 X-Project 头按项目归类。
"""

import json
import urllib.request

PROXY = "http://127.0.0.1:8787/v1/chat/completions"
API_KEY = "sk-your-real-key"   # 会被原样透传给上游

body = {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 16,
}
req = urllib.request.Request(
    PROXY, data=json.dumps(body).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
        "X-Project": "my-cli-script",   # ← 按项目归类
    },
    method="POST")
with urllib.request.urlopen(req, timeout=60) as r:
    print(json.load(r))

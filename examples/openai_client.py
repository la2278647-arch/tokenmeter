"""tokenmeter 接入示例：OpenAI 客户端通过透明代理记账。

运行前：
  1. 启动代理:  python -m tokenmeter proxy --upstream https://api.openai.com --port 8787
  2. 设置环境变量:  set OPENAI_API_KEY=sk-xxx   (Windows) 或 export (Linux/macOS)
  3. 运行本示例:  python examples/openai_client.py
"""

import os

from openai import OpenAI

client = OpenAI(
    # 唯一改动：base_url 指向 tokenmeter 本地代理
    base_url="http://127.0.0.1:8787/v1",
    api_key=os.environ["OPENAI_API_KEY"],
)

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "用一句话介绍你自己"}],
)

print("回复:", resp.choices[0].message.content)
print("用量:", resp.usage)

# 然后运行:  python -m tokenmeter report --recent 5
# 就能看到这次调用的 token 与费用已经被自动记下。

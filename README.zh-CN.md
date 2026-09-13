# tokenmeter · 中文说明

**本地优先的 LLM 用量与费用监控工具** —— 纯 Python 标准库，零第三方依赖。

把应用接上 GPT / Claude / DeepSeek 或任何 OpenAI 兼容接口后，三周过去账单来了，
却不知道哪个模型、哪个项目、哪天烧光了预算？`tokenmeter` 用本地 SQLite 帮你全部记下来：
一条命令的 CLI、一个 Web 仪表盘、一个可选的透明记账代理（业务代码零改动）。

## 快速上手

```bash
# 无需安装，clone 即用
git clone https://github.com/la2278647-arch/tokenmeter.git
cd tokenmeter

# 记一笔
python -m tokenmeter record --model gpt-4o --project web-app \
    --prompt-tokens 8500 --completion-tokens 1200

# 看报告
python -m tokenmeter report --group model --daily --recent 10

# Web 仪表盘
python -m tokenmeter serve
# → http://127.0.0.1:8765/
```

## 透明代理模式（杀手级功能）

业务代码一行不改，只要把 `base_url` 指到本地代理：

```python
from openai import OpenAI
client = OpenAI(
    base_url="http://127.0.0.1:8787/v1",   # 经 tokenmeter 转发
    api_key="sk-你的真实key",               # 原样透传，不落盘
)
resp = client.chat.completions.create(model="gpt-4o", messages=[...])
# 完成 —— 这次调用的 token 与费用已自动记入本地库
```

启动代理：`python -m tokenmeter proxy --upstream https://api.openai.com --port 8787`

流式响应同样支持：自动注入 `include_usage`，从流末块取用量，SSE 不漏记。
按项目归类：请求头加 `X-Project: my-service` 即可。

## 特性一览

- **SQLite 存储**：数据全在 `~/.tokenmeter/usage.db`，不出本机
- **CLI**：record / report / prices / serve / proxy / clear
- **透明代理**：转发 + 记账一体，支持流式
- **Web 仪表盘**：零依赖 HTML+SVG 图表，暗色主题
- **内置价格表**：17 个主流模型开箱即用，可覆盖为你的合同价
- **报告导出**：终端表格 / JSON / CSV / Markdown
- **默认私有**：只监听 127.0.0.1，无遥测、无账号、无云

## 开发测试

```bash
python -m unittest discover -s tests -v   # 12 项测试，含代理记账端到端
```

## License

MIT © la2278647-arch

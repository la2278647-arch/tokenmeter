#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tokenmeter · 模型价格表管理与费用估算。

内置 2026 年主流模型的公开价格（USD / 1M tokens），
用户可随时用 `tokenmeter prices set` 覆盖为自己的真实合同价。
未收录模型按输入/输出同价 1.00 USD / 1M 兜底，并标 * 提示。
"""

from __future__ import annotations

from typing import Dict, Tuple

# 未知模型的兜底价（USD / 1M tokens）：输入输出同价，方便先估后改
FALLBACK_INPUT = 1.00
FALLBACK_OUTPUT = 1.00


def estimate_cost(input_per_mtok: float, output_per_mtok: float,
                  prompt_tokens: int, completion_tokens: int) -> float:
    """按单模型价格估算一次调用费用（USD）。"""
    return (prompt_tokens / 1_000_000.0) * input_per_mtok + \
           (completion_tokens / 1_000_000.0) * output_per_mtok


def format_price_row(model: str, prices: Dict[str, Tuple[float, float]]) -> str:
    inp, out = prices.get(model, (FALLBACK_INPUT, FALLBACK_OUTPUT))
    star = "" if model in prices else " *"
    return f"  {model:<24} 输入 ${inp:>6.2f} /1M   输出 ${out:>6.2f} /1M{star}"


__all__ = ["estimate_cost", "format_price_row", "FALLBACK_INPUT", "FALLBACK_OUTPUT"]

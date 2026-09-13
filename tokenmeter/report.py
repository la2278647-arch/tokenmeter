#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tokenmeter · 汇总报告。

把 SQLite 里的用量聚合成人类可读的报告：
  - 终端表格（纯文本，零依赖）
  - JSON / CSV / Markdown 导出
"""

from __future__ import annotations

import csv
import io
import json
from typing import Dict, List, Optional


def _fmt_num(n) -> str:
    return f"{n:,}"


def _fmt_usd(v: float) -> str:
    return f"${v:,.4f}"


def _table(headers: List[str], rows: List[List[str]]) -> str:
    """对齐的纯文本表格（自适应列宽）。"""
    if not rows:
        return "  (无数据)"
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))
    line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    out = [line]
    out.append("|" + "|".join(f" {h:<{widths[i]}} " for i, h in enumerate(headers)) + "|")
    out.append(line)
    for r in rows:
        out.append("|" + "|".join(f" {c:<{widths[i]}} " for i, c in enumerate(r)) + "|")
    out.append(line)
    return "\n".join(out)


def render_summary(s: Dict) -> str:
    return (
        "  总请求      " + _fmt_num(s["requests"]) + "\n"
        "  总输入      " + _fmt_num(s["prompt_tokens"]) + " tok\n"
        "  总输出      " + _fmt_num(s["completion_tokens"]) + " tok\n"
        "  总消耗      " + _fmt_num(s["total_tokens"]) + " tok\n"
        "  估算费用    " + _fmt_usd(s["cost_usd"]) + "\n"
        "  平均延迟    " + (f"{s['avg_latency_ms']:.1f} ms" if s["avg_latency_ms"] else "-") + "\n"
        "  失败请求    " + _fmt_num(s["errors"])
    )


def render_group_rows(title: str, rows: List[Dict], top: bool = True) -> str:
    if not rows:
        return f"  {title}: (无数据)"
    headers = ["维度", "请求", "输入tok", "输出tok", "总tok", "费用(USD)"]
    table_rows = [
        [r["key"], _fmt_num(r["requests"]), _fmt_num(r["prompt_tokens"]),
         _fmt_num(r["completion_tokens"]), _fmt_num(r["total_tokens"]),
         _fmt_usd(r["cost_usd"])] for r in rows
    ]
    return f"  {title}\n" + _table(headers, table_rows)


def render_daily(rows: List[Dict]) -> str:
    if not rows:
        return "  (无数据)"
    headers = ["日期(UTC)", "请求", "输入tok", "输出tok", "总tok", "费用(USD)"]
    table_rows = [
        [r["day"], _fmt_num(r["requests"]), _fmt_num(r["prompt_tokens"]),
         _fmt_num(r["completion_tokens"]), _fmt_num(r["total_tokens"]),
         _fmt_usd(r["cost_usd"])] for r in rows
    ]
    return _table(headers, table_rows)


def render_recent(rows: List[Dict]) -> str:
    if not rows:
        return "  (无数据)"
    headers = ["时间(UTC)", "模型", "项目", "输入", "输出", "总tok", "费用", "延迟ms", "状态"]
    table_rows = []
    for r in rows:
        table_rows.append([
            r["ts"][:19], r["model"], r["project"],
            _fmt_num(r["prompt_tokens"]), _fmt_num(r["completion_tokens"]),
            _fmt_num(r["total_tokens"]), _fmt_usd(r["cost"]),
            f"{r['latency_ms']:.0f}" if r["latency_ms"] else "-",
            r["status"],
        ])
    return _table(headers, table_rows)


# ---------------------------------------------------------------- 导出
def to_csv(rows: List[Dict], path: Optional[str] = None) -> str:
    """把 recent() 的记录导出为 CSV；path 为空时返回字符串。"""
    fieldnames = ["ts", "model", "provider", "project", "prompt_tokens",
                  "completion_tokens", "total_tokens", "cost", "latency_ms",
                  "status", "error"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    text = buf.getvalue()
    if path:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(text)
    return text


def to_json(rows: List[Dict], path: Optional[str] = None) -> str:
    text = json.dumps(rows, ensure_ascii=False, indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return text


def to_markdown(rows: List[Dict], path: Optional[str] = None) -> str:
    """把 recent() 的记录导出为 Markdown 表格。"""
    if not rows:
        text = "_(无数据)_\n"
    else:
        lines = [
            "| 时间(UTC) | 模型 | 项目 | 输入 | 输出 | 总tok | 费用(USD) | 状态 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            lines.append(
                f"| {r['ts'][:19]} | {r['model']} | {r['project']} "
                f"| {_fmt_num(r['prompt_tokens'])} | {_fmt_num(r['completion_tokens'])} "
                f"| {_fmt_num(r['total_tokens'])} | {_fmt_usd(r['cost'])} | {r['status']} |")
        text = "\n".join(lines) + "\n"
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return text


__all__ = ["render_summary", "render_group_rows", "render_daily", "render_recent",
           "to_csv", "to_json", "to_markdown"]

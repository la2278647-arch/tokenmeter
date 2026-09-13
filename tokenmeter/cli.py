#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tokenmeter · 命令行入口。

子命令：
  record  手动记一条用量
  report  汇总报告（终端表格 / json / csv / md）
  serve   启动 Web 仪表盘
  proxy   启动透明记账代理
  prices  查看 / 设置模型价格
  clear   清空用量记录
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .db import DEFAULT_DB_PATH, UsageDB
from .report import (render_daily, render_group_rows, render_recent,
                     render_summary, to_csv, to_json, to_markdown)

VERSION = "0.1.0"


def _db(args) -> UsageDB:
    return UsageDB(args.db)


# ---------------------------------------------------------------- record
def cmd_record(args) -> int:
    db = _db(args)
    pid = db.record(
        model=args.model, provider=args.provider or "", project=args.project,
        prompt_tokens=args.prompt_tokens, completion_tokens=args.completion_tokens,
        latency_ms=args.latency_ms, status=args.status, error=args.error or "",
        ts=args.ts)
    print(f"已记录 #{pid}  {args.model}  "
          f"in={args.prompt_tokens:,} out={args.completion_tokens:,} "
          f"project={args.project}")
    db.close()
    return 0


# ---------------------------------------------------------------- report
def cmd_report(args) -> int:
    db = _db(args)
    since = args.since
    print("=" * 62)
    print("  tokenmeter · 用量报告")
    if since:
        print(f"  统计范围  自 {since} 起")
    print("=" * 62)

    s = db.summary(since=since, project=args.project, model=args.model)
    print(render_summary(s))
    print()

    if args.group in ("model", "project", "provider"):
        rows = db.group_by(args.group, since=since, project=args.project,
                           model=args.model, limit=args.limit)
        title = {"model": "按模型", "project": "按项目",
                 "provider": "按服务商"}[args.group]
        print(render_group_rows(title, rows))
        print()

    if args.daily:
        rows = db.daily(days=args.days, since=since, project=args.project,
                        model=args.model)
        print("  按天汇总 (UTC)")
        print(render_daily(rows))
        print()

    if args.recent:
        rows = db.recent(limit=args.recent, since=since, project=args.project,
                         model=args.model)
        print("  最近记录")
        print(render_recent(rows))
        print()

    # 导出
    if args.export:
        rows = db.recent(limit=10 ** 9, since=since, project=args.project,
                         model=args.model)
        if args.export.endswith(".csv"):
            to_csv(rows, args.export)
        elif args.export.endswith(".md"):
            to_markdown(rows, args.export)
        else:
            to_json(rows, args.export)
        print(f"  已导出 {len(rows)} 条 → {args.export}")
    db.close()
    return 0


# ---------------------------------------------------------------- serve / proxy
def cmd_serve(args) -> int:
    from .server import run_server
    run_server(db_path=args.db, host=args.host, port=args.port)
    return 0


def cmd_proxy(args) -> int:
    from .proxy import run_proxy
    run_proxy(db_path=args.db, host=args.host, port=args.port,
              upstream=args.upstream, timeout=args.timeout)
    return 0


# ---------------------------------------------------------------- prices
def cmd_prices(args) -> int:
    db = _db(args)
    if args.set:
        model, inp, out = args.set[0], args.set[1], args.set[2]
        db.set_price(model, float(inp), float(out))
        print(f"已设置 {model}: 输入 ${inp}/1M, 输出 ${out}/1M")
    else:
        print(f"  {'模型':<26} 输入($/1M)   输出($/1M)")
        print("  " + "-" * 56)
        for p in db.all_prices():
            print(f"  {p['model']:<26} {p['input_per_mtok']:>10.2f}  {p['output_per_mtok']:>10.2f}")
        print("  * 未收录模型按 $1.00/1M 兜底估算")
    db.close()
    return 0


# ---------------------------------------------------------------- clear
def cmd_clear(args) -> int:
    db = _db(args)
    n = db.clear()
    print(f"已清空 {n} 条用量记录（价格表保留）")
    db.close()
    return 0


# ---------------------------------------------------------------- 参数树
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="tokenmeter",
        description="本地优先的 LLM 用量与费用监控（零第三方依赖）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--version", action="version", version=f"tokenmeter {VERSION}")
    ap.add_argument("--db", default=DEFAULT_DB_PATH,
                    help="SQLite 数据库路径")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record", help="手动记一条用量")
    p.add_argument("--model", required=True)
    p.add_argument("--provider", default="")
    p.add_argument("--project", default="default")
    p.add_argument("--prompt-tokens", type=int, default=0)
    p.add_argument("--completion-tokens", type=int, default=0)
    p.add_argument("--latency-ms", type=float, default=0.0)
    p.add_argument("--status", default="ok")
    p.add_argument("--error", default="")
    p.add_argument("--ts", default=None, help="ISO-8601 时间，缺省为 now")
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("report", help="汇总报告")
    p.add_argument("--since", default=None, help="只统计该 ISO 时间之后的记录")
    p.add_argument("--group", choices=["model", "project", "provider", "none"],
                   default="model")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--daily", action="store_true", help="输出按天汇总")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--recent", type=int, default=0, help="输出最近 N 条明细")
    p.add_argument("--project", default=None, help="只看某项目")
    p.add_argument("--model", default=None, help="只看某模型")
    p.add_argument("--export", default=None, help="导出到文件（.json/.csv/.md）")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("serve", help="启动 Web 仪表盘")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("proxy", help="启动透明记账代理")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--upstream", default="https://api.openai.com",
                   help="上游 OpenAI 兼容端点")
    p.add_argument("--timeout", type=float, default=300.0)
    p.set_defaults(fn=cmd_proxy)

    p = sub.add_parser("prices", help="查看 / 设置模型价格")
    p.add_argument("--set", nargs=3, metavar=("MODEL", "IN", "OUT"),
                   help="设置某模型输入/输出每 1M token 价格（USD）")
    p.set_defaults(fn=cmd_prices)

    p = sub.add_parser("clear", help="清空用量记录")
    p.set_defaults(fn=cmd_clear)

    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

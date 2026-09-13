#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tokenmeter · 本地 SQLite 存储层。

用量记录 + 模型价格表，全部落在用户自己的 SQLite 文件里，
数据不出本机 —— 这是 tokenmeter 与云端仪表盘最本质的区别。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------- 默认位置
DEFAULT_DB_PATH = os.path.join(os.path.expanduser("~"), ".tokenmeter", "usage.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,                -- ISO-8601 UTC
    model         TEXT    NOT NULL,
    provider      TEXT    DEFAULT '',
    project       TEXT    DEFAULT 'default',
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens  INTEGER DEFAULT 0,
    cost          REAL    DEFAULT 0.0,             -- 按价格表估算，USD
    latency_ms    REAL    DEFAULT 0.0,
    status        TEXT    DEFAULT 'ok',            -- ok | error
    error         TEXT    DEFAULT '',
    meta          TEXT    DEFAULT '{}'             -- JSON 附加信息（如请求 ID）
);
CREATE INDEX IF NOT EXISTS idx_usage_ts      ON usage(ts);
CREATE INDEX IF NOT EXISTS idx_usage_model   ON usage(model);
CREATE INDEX IF NOT EXISTS idx_usage_project ON usage(project);

CREATE TABLE IF NOT EXISTS prices (
    model          TEXT PRIMARY KEY,
    input_per_mtok REAL NOT NULL,                  -- 每 1M 输入 token 价格（USD）
    output_per_mtok REAL NOT NULL,                 -- 每 1M 输出 token 价格（USD）
    updated_at     TEXT NOT NULL
);
"""

# ---------------------------------------------------------------- 内置价格表
# 2026 年主流 API 的公开价格（USD / 1M tokens），仅作估算默认值，
# 随时可用 `tokenmeter prices set <model> <in> <out>` 覆盖。
BUILTIN_PRICES: Dict[str, Tuple[float, float]] = {
    "gpt-4o":                (2.50, 10.00),
    "gpt-4o-mini":           (0.15, 0.60),
    "gpt-4.1":               (2.00, 8.00),
    "gpt-4.1-mini":          (0.40, 1.60),
    "o3-mini":               (1.10, 4.40),
    "claude-3-5-sonnet":     (3.00, 15.00),
    "claude-3-5-haiku":      (0.80, 4.00),
    "claude-sonnet-4":       (3.00, 15.00),
    "deepseek-chat":         (0.27, 1.10),
    "deepseek-reasoner":     (0.55, 2.19),
    "gemini-1.5-pro":        (1.25, 5.00),
    "gemini-2.0-flash":      (0.10, 0.40),
    "qwen-turbo":            (0.20, 0.60),
    "qwen-plus":             (0.40, 1.20),
    "qwen-max":              (2.40, 9.60),
    "mistral-large":         (2.00, 6.00),
    "llama-3.3-70b":         (0.59, 0.79),
}


def iso_now() -> str:
    """当前 UTC 时间的 ISO-8601 字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UsageDB:
    """线程安全的 SQLite 访问器。

    - 每连接各自打开 SQLite（sqlite3 连接不能跨线程共享），用锁串行化写。
    - WAL 模式提升并发读；-wal/-shm 文件与主库同目录。
    """

    def __init__(self, path: str = DEFAULT_DB_PATH, autoseed: bool = True):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        if autoseed:
            self.seed_prices()

    # ------------------------------------------------------------ 内部工具
    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ 价格表
    def seed_prices(self) -> int:
        """写入内置价格表（不覆盖用户已自定义的条目）。返回新增条数。"""
        now = iso_now()
        added = 0
        for model, (inp, out) in BUILTIN_PRICES.items():
            with self._lock:
                cur = self._conn.execute(
                    "SELECT 1 FROM prices WHERE model=?", (model,))
                if cur.fetchone() is None:
                    self._conn.execute(
                        "INSERT INTO prices(model,input_per_mtok,output_per_mtok,updated_at)"
                        " VALUES(?,?,?,?)",
                        (model, inp, out, now))
                    added += 1
        self._conn.commit()
        return added

    def set_price(self, model: str, input_per_mtok: float, output_per_mtok: float) -> None:
        self._execute(
            "INSERT INTO prices(model,input_per_mtok,output_per_mtok,updated_at)"
            " VALUES(?,?,?,?)"
            " ON CONFLICT(model) DO UPDATE SET"
            "  input_per_mtok=excluded.input_per_mtok,"
            "  output_per_mtok=excluded.output_per_mtok,"
            "  updated_at=excluded.updated_at",
            (model, float(input_per_mtok), float(output_per_mtok), iso_now()))

    def get_price(self, model: str) -> Optional[Tuple[float, float]]:
        row = self._query("SELECT input_per_mtok,output_per_mtok FROM prices WHERE model=?", (model,))
        if row:
            return (row[0][0], row[0][1])
        # 未收录的模型 → 按输入/输出同价 1.00 USD / 1M 兜底，并给出可辨识标志
        return None

    def all_prices(self) -> List[Dict]:
        rows = self._query("SELECT model,input_per_mtok,output_per_mtok,updated_at FROM prices ORDER BY model")
        return [dict(r) for r in rows]

    def estimate_cost(self, model: str, pt: int, ct: int) -> float:
        """按价格表估算一次调用的费用（USD）；未收录模型记 0 且不报错。"""
        price = self.get_price(model)
        if not price:
            return 0.0
        inp, out = price
        return (pt / 1_000_000.0) * inp + (ct / 1_000_000.0) * out

    # ------------------------------------------------------------ 用量记录
    def record(self, *, model: str, provider: str = "", project: str = "default",
               prompt_tokens: int = 0, completion_tokens: int = 0,
               latency_ms: float = 0.0, status: str = "ok", error: str = "",
               ts: Optional[str] = None, cost: Optional[float] = None,
               meta: Optional[Dict] = None) -> int:
        """写入一条用量记录，返回自增 id。cost 缺省时按价格表估算。"""
        pt = max(0, int(prompt_tokens))
        ct = max(0, int(completion_tokens))
        if cost is None:
            cost = self.estimate_cost(model, pt, ct)
        if ts is None:
            ts = iso_now()
        extra = json.dumps(meta or {}, ensure_ascii=False)
        cur = self._execute(
            "INSERT INTO usage(ts,model,provider,project,prompt_tokens,"
            "  completion_tokens,total_tokens,cost,latency_ms,status,error,meta)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, model, provider, project, pt, ct, pt + ct, float(cost),
             float(latency_ms), status, error, extra))
        return cur.lastrowid

    # ------------------------------------------------------------ 查询
    def summary(self, since: Optional[str] = None, until: Optional[str] = None,
                project: Optional[str] = None, model: Optional[str] = None) -> Dict:
        """全局汇总：请求数、token、费用、平均延迟。"""
        where, params = self._filters(since, until, project, model)
        row = self._query(
            f"SELECT COUNT(*) n, COALESCE(SUM(prompt_tokens),0) pt,"
            f" COALESCE(SUM(completion_tokens),0) ct, COALESCE(SUM(total_tokens),0) tt,"
            f" COALESCE(SUM(cost),0) cost,"
            f" AVG(CASE WHEN latency_ms>0 THEN latency_ms END) avg_ms"
            f" FROM usage WHERE status='ok'{where}", params)[0]
        return {
            "requests": row[0], "prompt_tokens": row[1], "completion_tokens": row[2],
            "total_tokens": row[3], "cost_usd": round(row[4], 4),
            "avg_latency_ms": round(row[5], 1) if row[5] else 0.0,
            "errors": self._count_errors(since, until, project, model),
        }

    def _count_errors(self, since, until, project, model) -> int:
        where, params = self._filters(since, until, project, model)
        row = self._query(
            f"SELECT COUNT(*) FROM usage WHERE status!='ok'{where}", params)[0]
        return row[0]

    def group_by(self, column: str, since: Optional[str] = None,
                 until: Optional[str] = None, project: Optional[str] = None,
                 model: Optional[str] = None, limit: int = 20) -> List[Dict]:
        """按 model / project / provider 分组汇总。"""
        if column not in ("model", "project", "provider"):
            raise ValueError(f"不支持的维度: {column}")
        where, params = self._filters(since, until, project, model)
        rows = self._query(
            f"SELECT {column} k, COUNT(*) n,"
            f" COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct,"
            f" COALESCE(SUM(total_tokens),0) tt, COALESCE(SUM(cost),0) cost"
            f" FROM usage WHERE status='ok'{where}"
            f" GROUP BY {column} ORDER BY tt DESC LIMIT ?",
            params + (limit,))
        return [{"key": r[0], "requests": r[1], "prompt_tokens": r[2],
                 "completion_tokens": r[3], "total_tokens": r[4],
                 "cost_usd": round(r[5], 4)} for r in rows]

    def daily(self, days: int = 30, since: Optional[str] = None,
              until: Optional[str] = None, project: Optional[str] = None,
              model: Optional[str] = None) -> List[Dict]:
        """按天汇总（UTC），时间轴从最早记录到 now 或 days 天窗口。"""
        where, params = self._filters(since, until, project, model)
        rows = self._query(
            f"SELECT substr(ts,1,10) day, COUNT(*) n,"
            f" COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct,"
            f" COALESCE(SUM(total_tokens),0) tt, COALESCE(SUM(cost),0) cost"
            f" FROM usage WHERE status='ok'{where}"
            f" GROUP BY day ORDER BY day", params)
        return [{"day": r[0], "requests": r[1], "prompt_tokens": r[2],
                 "completion_tokens": r[3], "total_tokens": r[4],
                 "cost_usd": round(r[5], 4)} for r in rows]

    def recent(self, limit: int = 50, since: Optional[str] = None,
               until: Optional[str] = None, project: Optional[str] = None,
               model: Optional[str] = None) -> List[Dict]:
        where, params = self._filters(since, until, project, model)
        rows = self._query(
            f"SELECT ts,model,provider,project,prompt_tokens,completion_tokens,"
            f" total_tokens,cost,latency_ms,status,error,meta"
            f" FROM usage WHERE 1=1{where} ORDER BY id DESC LIMIT ?",
            params + (limit,))
        out = []
        for r in rows:
            d = dict(zip(("ts", "model", "provider", "project", "prompt_tokens",
                          "completion_tokens", "total_tokens", "cost",
                          "latency_ms", "status", "error", "meta"), r))
            try:
                d["meta"] = json.loads(d["meta"])
            except Exception:
                d["meta"] = {}
            out.append(d)
        return out

    # ------------------------------------------------------------ 过滤器
    @staticmethod
    def _filters(since: Optional[str], until: Optional[str],
                 project: Optional[str], model: Optional[str]) -> Tuple[str, tuple]:
        parts, params = [], []
        if since:
            parts.append("ts >= ?")
            params.append(since)
        if until:
            parts.append("ts <= ?")
            params.append(until)
        if project:
            parts.append("project = ?")
            params.append(project)
        if model:
            parts.append("model = ?")
            params.append(model)
        where = (" AND " + " AND ".join(parts)) if parts else ""
        return where, tuple(params)

    # ------------------------------------------------------------ 管理
    def clear(self) -> int:
        """清空全部用量记录，返回删除条数（价格表保留）。"""
        cur = self._execute("DELETE FROM usage")
        return cur.rowcount

    def export_json(self, since=None, until=None, project=None, model=None) -> str:
        rows = self.recent(limit=10 ** 9, since=since, until=until,
                           project=project, model=model)
        return json.dumps({"exported_at": iso_now(), "count": len(rows), "records": rows},
                          ensure_ascii=False, indent=2)


__all__ = ["UsageDB", "DEFAULT_DB_PATH", "BUILTIN_PRICES", "iso_now"]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tokenmeter 单元测试（纯标准库 unittest，无第三方依赖）。"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tokenmeter.db import UsageDB  # noqa: E402
from tokenmeter.report import to_csv, to_json, to_markdown  # noqa: E402


class TestUsageDB(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tokenmeter-test-")
        self.db_path = os.path.join(self.dir, "test.db")
        self.db = UsageDB(self.db_path)

    def tearDown(self):
        self.db.close()

    def test_record_and_summary(self):
        self.db.record(model="gpt-4o", project="app-a",
                       prompt_tokens=1000, completion_tokens=500)
        self.db.record(model="gpt-4o-mini", project="app-a",
                       prompt_tokens=2000, completion_tokens=100)
        self.db.record(model="deepseek-chat", project="app-b",
                       prompt_tokens=500, completion_tokens=50)
        s = self.db.summary()
        self.assertEqual(s["requests"], 3)
        self.assertEqual(s["prompt_tokens"], 3500)
        self.assertEqual(s["completion_tokens"], 650)
        self.assertEqual(s["total_tokens"], 4150)
        # gpt-4o: 1000/1M*2.5 + 500/1M*10 = 0.0075
        # gpt-4o-mini: 2000/1M*0.15 + 100/1M*0.6 = 0.00036
        # deepseek-chat: 500/1M*0.27 + 50/1M*1.1 = 0.00019
        self.assertAlmostEqual(s["cost_usd"], 0.0075 + 0.00036 + 0.00019, places=4)

    def test_group_by_model(self):
        self.db.record(model="gpt-4o", prompt_tokens=1000, completion_tokens=0)
        self.db.record(model="gpt-4o", prompt_tokens=2000, completion_tokens=0)
        self.db.record(model="gpt-4o-mini", prompt_tokens=3000, completion_tokens=0)
        rows = self.db.group_by("model")
        self.assertEqual(len(rows), 2)
        by = {r["key"]: r for r in rows}
        self.assertEqual(by["gpt-4o"]["total_tokens"], 3000)
        self.assertEqual(by["gpt-4o-mini"]["total_tokens"], 3000)

    def test_estimate_cost(self):
        # gpt-4o: in 2.5 / out 10 per 1M
        self.assertAlmostEqual(self.db.estimate_cost("gpt-4o", 1_000_000, 0), 2.5)
        self.assertAlmostEqual(self.db.estimate_cost("gpt-4o", 0, 1_000_000), 10.0)
        # 未收录模型 → 0（不报错）
        self.assertEqual(self.db.estimate_cost("brand-new-model", 100, 100), 0.0)

    def test_set_price(self):
        self.db.set_price("my-model", 3.0, 6.0)
        self.assertEqual(self.db.get_price("my-model"), (3.0, 6.0))
        self.db.set_price("my-model", 1.5, 3.0)
        self.assertEqual(self.db.get_price("my-model"), (1.5, 3.0))

    def test_seed_prices_idempotent(self):
        first = self.db.seed_prices()
        second = self.db.seed_prices()
        self.assertEqual(second, 0)  # 已有条目不重复写

    def test_daily(self):
        self.db.record(model="gpt-4o", prompt_tokens=1000, completion_tokens=0,
                       ts="2026-09-01T00:00:00+00:00")
        self.db.record(model="gpt-4o", prompt_tokens=2000, completion_tokens=0,
                       ts="2026-09-02T00:00:00+00:00")
        rows = self.db.daily()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["day"], "2026-09-01")
        self.assertEqual(rows[0]["total_tokens"], 1000)

    def test_filters(self):
        self.db.record(model="gpt-4o", project="p1", prompt_tokens=1000)
        self.db.record(model="gpt-4o", project="p2", prompt_tokens=2000)
        s = self.db.summary(project="p1")
        self.assertEqual(s["requests"], 1)
        self.assertEqual(s["total_tokens"], 1000)

    def test_clear(self):
        self.db.record(model="gpt-4o", prompt_tokens=10)
        n = self.db.clear()
        self.assertEqual(n, 1)
        self.assertEqual(self.db.summary()["requests"], 0)

    def test_export_formats(self):
        self.db.record(model="gpt-4o", project="x", prompt_tokens=100)
        rows = self.db.recent(limit=10)
        csv_text = to_csv(rows)
        self.assertIn("model", csv_text)
        self.assertIn("gpt-4o", csv_text)
        js = json.loads(to_json(rows))
        self.assertEqual(js[0]["model"], "gpt-4o")
        md = to_markdown(rows)
        self.assertIn("|", md)


if __name__ == "__main__":
    unittest.main()

"""v8.58（t226u）：修理 / 卖垃圾失败不停止 → 截图 + 日志 → 回最近的中心（当前地图没有中心 → 就地）接着刷怪；
修理失败 10 分钟内不再去，卖垃圾失败 30 分钟内不再去（背包满照旧只捡钱）；耐久 ≤ 5% 修不了也只提醒不停止。"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.navigation import bearing_to  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_v855 import RepBase, ro, TOWN, ZONE, ROOT, _rd  # noqa: E402


def kinds(g):
    return [k for k, _ in g.events]


class Defaults(unittest.TestCase):
    def test_default(self):
        self.assertEqual(DEFAULT_CONFIG["repair"]["fail_retry_s"], 600.0)


class WalkFail(RepBase):
    def setUp(self):
        super().setUp()
        self.cfg["repair"].update(use_hearth=False, stuck_max=2)

    def stuck_ticks(self, n=60, pos=(42.0, 61.0), dur=15.0):
        for _ in range(n):
            o = ro(pos=pos, mp=ZONE, dur=dur)
            o.facing = bearing_to(pos, (40.0, 60.0))
            self.t(o)

    def test_stuck_returns_to_grind_and_cools_down(self):
        self.stuck_ticks()
        self.assertFalse(self.g.stopped)
        self.assertIsNone(self.g.rp)
        self.assertTrue(any("回城修理失败" in m and "不停止" in m for m in self.logs))
        self.assertIn("repair_fail", kinds(self.g))          # 截图
        self.assertIn(self.g.state, (State.RETURNING, State.SEARCHING))
        i = next(j for j, m in enumerate(self.logs) if "回城修理失败" in m)
        self.assertIn("离中心 #1", self.logs[i + 1])            # 回最近的中心
        # 10 分钟内耐久还低也不再去
        self.g.state = State.SEARCHING
        self.stuck_ticks(20)
        self.assertIsNone(self.g.rp)
        self.clock.t += 600
        self.g.state = State.SEARCHING
        for _ in range(8):
            self.t(ro(pos=(42.0, 61.0), mp=ZONE))
        self.assertIsNotNone(self.g.rp)

    def test_segment_timeout_no_stop(self):
        self.cfg["repair"]["seg_timeout_s"] = 5
        for _ in range(8):
            self.t(ro(pos=(42.0, 61.0)))
        self.clock.t += 10
        self.t(ro(pos=(40.5, 60.5)))
        self.assertFalse(self.g.stopped)
        self.assertTrue(any("走了 5 秒还没走完" in m for m in self.logs))
        self.assertEqual(self.g.state, State.RETURNING)

    def test_no_center_grinds_in_place(self):
        for _ in range(8):
            self.t(ro(pos=(42.0, 61.0), mp=777))
        self.assertFalse(self.g.stopped)
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertTrue(any("就地接着打" in m for m in self.logs))

    def test_bag_trip_fail_keeps_gold_only(self):
        self.g.bag_full = True
        self.stuck_ticks(dur=80)
        self.assertFalse(self.g.stopped)
        self.assertTrue(self.g.bag_full)                     # 照旧只捡钱
        self.assertTrue(any("卖垃圾失败" in m and "30 分钟" in m for m in self.logs))
        self.g.state = State.SEARCHING
        self.clock.t += 900
        for _ in range(10):
            self.t(ro(pos=(42.0, 61.0), dur=80))
        self.assertIsNone(self.g.rp)
        self.clock.t += 1000
        for _ in range(8):
            self.t(ro(pos=(42.0, 61.0), dur=80))
        self.assertIsNotNone(self.g.rp)


class HearthFail(RepBase):
    def test_merchant_fail_in_town_grinds_in_place(self):
        self.start_cast()
        self.land()
        self.walk_to_vendor()
        for _ in range(40):
            self.t(ro(pos=(63.0, 75.0), mp=TOWN, form="caster"))
        self.assertFalse(self.g.stopped)
        self.assertIsNone(self.g.rp)
        self.assertEqual(self.g.state, State.SEARCHING)       # 城里没有中心 → 就地
        self.assertIn("repair_fail", kinds(self.g))

    def test_interrupted_too_often_no_stop(self):
        self.g.rp_attempts = 3
        for _ in range(8):
            self.t(ro())
        self.assertFalse(self.g.stopped)
        self.assertTrue(any("读炉石被怪打断 3 次" in m for m in self.logs))
        self.assertNotIn("h", self.keys())

    def test_low_5pct_no_stop(self):
        self.k["vendor_target"] = ""
        for _ in range(10):
            self.t(ro(dur=3))
        self.assertFalse(self.g.stopped)
        self.assertTrue(any("不停止，接着打" in m for m in self.logs))
        self.assertIn("repair_low", kinds(self.g))


class Strings(unittest.TestCase):
    def test_files(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertRegex(app, r"熊德自动打怪 v8\.(5[8-9]|[6-9]\d)")
        self.assertIn('"fail_retry_s"', app)
        self.assertTrue(re.search(r"v8\.58.*不再停止", _rd(os.path.join(ROOT, "README_中文.md"))))
        self.assertIn("v8.58", _rd(os.path.join(ROOT, "bot", "runner.py")))
        self.assertNotIn("self.stop(", _rd(os.path.join(ROOT, "bot", "repair.py")))


if __name__ == "__main__":
    unittest.main()

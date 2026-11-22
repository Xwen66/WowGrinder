"""v8.56（t224u）：修理可以不用炉石（repair.use_hearth，默认开 = v8.55）。关掉：先走到最近的中心 →
回程路线倒着走到修理商 → 选修理商 + 互动 → 修好 → Esc → 回程路线正着走回来 → 回中心；路上只还手不主动找怪。"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.repair import REPAIR_DEFAULTS  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_v855 import RepBase, ro, TOWN, ZONE, ROOT, _rd  # noqa: E402


class Defaults(unittest.TestCase):
    def test_default_on(self):
        self.assertTrue(DEFAULT_CONFIG["repair"]["use_hearth"])
        self.assertTrue(REPAIR_DEFAULTS["use_hearth"])


class Walk(RepBase):
    def setUp(self):
        super().setUp()
        self.cfg["repair"]["use_hearth"] = False
        self.k["hearth"] = ""                                   # 走路模式不需要炉石键

    def go(self, pos, mp=ZONE, **kw):
        self.t(ro(pos=pos, mp=mp, **kw))

    def start(self, **kw):
        for _ in range(8):
            self.go((42.0, 61.0), **kw)
        self.assertEqual(self.g.state, State.REPAIR)
        self.assertNotIn("h", self.keys())
        self.assertEqual(self.g.rp["phase"], "to_vendor")
        pts = self.g.rp["pts"]
        self.assertEqual([p[4] for p in pts], ["中心 #1", "路线点 #6", "路线点 #5", "路线点 #4", "路线点 #3"])

    def to_vendor(self):
        self.go((40.0, 60.0))                                   # 先到最近的中心
        self.go((41.0, 60.0))                                   # 回程最后一个点
        self.go((80.0, 10.0))                                   # 野外这边的地图边上
        self.go((80.1, 9.5))                                    # 下一个点在城里 → 朝原方向走
        self.assertTrue(any("下一个点在地图 1453" in m for m in self.logs))
        self.go((70.0, 90.0), mp=TOWN)
        self.go((63.0, 75.0), mp=TOWN)
        self.assertEqual(self.g.rp["phase"], "vendor")

    def test_full_walk_flow(self):
        self.start()
        self.assertTrue(any("走路去修理（不用炉石）" in m for m in self.logs))
        self.to_vendor()
        self.inp.history.clear()
        self.go((63.0, 75.0), mp=TOWN)
        self.assertEqual(self.keys()[:2], ["n", "`"])
        self.go((63.0, 75.0), mp=TOWN, merchant=True)
        self.go((63.0, 75.0), mp=TOWN, merchant=True, dur=100)
        self.assertIn("esc", self.keys())
        self.assertEqual(self.g.rp["phase"], "back")
        self.go((70.0, 90.0), mp=TOWN, dur=100)
        self.go((80.0, 10.0), dur=100)
        self.go((41.0, 60.0), dur=100)
        self.assertIsNone(self.g.rp)
        self.assertEqual(self.g.state, State.RETURNING)
        self.assertFalse(self.g.stopped)
        self.assertNotIn("h", self.keys())

    def test_hearth_cooldown_irrelevant(self):
        for _ in range(8):
            self.go((42.0, 61.0), cd=50, hearth=False)
        self.assertEqual(self.g.state, State.REPAIR)
        self.assertFalse(any("炉石还要" in m for m in self.logs))

    def test_fight_back_then_resume_no_seek(self):
        self.cfg["repair"]["ignore_combat"] = False         # v8.57：老行为（被打就还手）
        self.start()
        self.go((40.0, 60.0))
        self.go((41.0, 60.0), combat=True, thp=90)
        self.assertTrue(self.g.rp["paused"])
        self.assertTrue(any("先还手" in m for m in self.logs))
        self.g.state = State.APPROACHING                       # 打完以后状态机想去接近别的怪 → 不去，接着走路线
        self.go((41.0, 60.0))
        self.assertFalse(self.g.rp["paused"])
        self.assertEqual(self.g.state, State.REPAIR)
        self.assertGreaterEqual(self.g.rp["wi"], 1)              # 不回头去中心

    def test_no_return_route_stops_at_5(self):
        self.cfg["repair"]["vendor_i"] = 5
        for _ in range(8):
            self.go((42.0, 61.0))
        self.assertIsNone(self.g.rp)
        self.assertTrue(any("修理商之后走回刷怪区的路线点" in m for m in self.logs))
        self.assertFalse(any("炉石键" in m and "缺" in m for m in self.logs))
        for _ in range(8):
            self.go((42.0, 61.0), dur=4)
        self.assertTrue(self.g.stopped)

    def test_no_center_on_map_stops(self):
        for _ in range(8):
            self.go((42.0, 61.0), mp=777)
        self.assertTrue(self.g.stopped)
        self.assertIn("没有中心", self.g.stop_reason)

    def test_stuck_stops(self):
        from bot.navigation import bearing_to
        self.cfg["repair"]["stuck_max"] = 2
        for _ in range(60):
            o = ro(pos=(42.0, 61.0), mp=ZONE)
            o.facing = bearing_to((42.0, 61.0), (40.0, 60.0))
            self.t(o)
        self.assertTrue(self.g.stopped)
        self.assertIn("卡住", self.g.stop_reason)

    def test_segment_timeout_stops(self):
        self.cfg["repair"]["seg_timeout_s"] = 5
        self.start()
        self.clock.t += 10
        self.go((40.5, 60.5))
        self.assertTrue(self.g.stopped)
        self.assertIn("走了 5 秒还没走完", self.g.stop_reason)


class HearthUnchanged(RepBase):
    def test_hearth_mode_still_presses_hearth(self):
        for _ in range(8):
            self.t(ro())
        self.assertIn("h", self.keys())
        self.assertEqual(self.g.rp["phase"], "cast")


class Strings(unittest.TestCase):
    def test_files(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertRegex(app, r"熊德自动打怪 v8\.5[6-9]")
        self.assertIn('"use_hearth"', app)
        self.assertIn("使用炉石回城", app)
        self.assertTrue(re.search(r"v8\.56.*不用炉石", _rd(os.path.join(ROOT, "README_中文.md"))))
        self.assertIn("v8.56", _rd(os.path.join(ROOT, "bot", "runner.py")))


if __name__ == "__main__":
    unittest.main()

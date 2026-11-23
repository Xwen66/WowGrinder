"""v8.65（t244u/t245u）：到了中心找不到怪 —— 每步转视角 200 → 350ms（≈63°），转满 180°（以前 360°），
走之前这一轮还没按过 Tab 就再按一次（Tab 暂停中也按），还没怪 → 直接去下一个中心。旧默认值自动改，改过的不动。"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG, _merge, _migrate  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_v840 import TabBase, tobs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def mig(loaded):
    return _migrate(_merge(DEFAULT_CONFIG, copy.deepcopy(loaded)), loaded)


class Config(unittest.TestCase):
    def test_defaults(self):
        s = DEFAULT_CONFIG["scan"]
        self.assertEqual((s["tap_ms"], s["spin_deg"]), (350, 180))
        self.assertTrue(s["spin_home"])

    def test_migration_old_defaults(self):
        cfg = mig({"scan": {"tap_ms": 200, "settle_ms": 500, "spin_deg": 360.0}, "meta": {"migrations": []}})
        self.assertEqual((cfg["scan"]["tap_ms"], cfg["scan"]["spin_deg"], cfg["scan"]["settle_ms"]), (350, 180.0, 500))
        self.assertIn("scan_v865", cfg["meta"]["migrations"])
        # 迁移只做一次：之后自己改回 200 / 360 不会再被改
        cfg["scan"].update(tap_ms=200, spin_deg=360)
        cfg2 = mig(cfg)
        self.assertEqual((cfg2["scan"]["tap_ms"], cfg2["scan"]["spin_deg"]), (200, 360))
        self.assertEqual(cfg2["meta"]["migrations"].count("scan_v865"), 1)

    def test_migration_keeps_custom(self):
        cfg = mig({"scan": {"tap_ms": 260, "spin_deg": 270}})
        self.assertEqual((cfg["scan"]["tap_ms"], cfg["scan"]["spin_deg"]), (260, 270))

    def test_gui_readme_title(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertIn("约 350ms ≈ 63°", app)
        self.assertIn("默认 180（v8.65：转半圈就走）", app)
        self.assertRegex(app, r"熊德自动打怪 v8\.(6[5-9]|[7-9]\d)")
        self.assertIn("v8.65", _rd(os.path.join(ROOT, "README_中文.md")))


class HalfSpin(TabBase):
    def setUp(self):
        super().setUp()
        self.cfg["scan"].update(tap_ms=350, settle_ms=500, spin_deg=180, spin_home=True)
        self.cfg["tab_search"].update(miss_limit=3, miss_backoff_s=30.0)
        self.g.set_centers([(50.0, 50.0, 0), (53.0, 50.0, 0)])
        self.g.ci, self.g.home = 0, (50.0, 50.0, 0)

    def run_until_travel(self, secs=30, o=None):
        end = self.clock.t + secs
        while self.clock.t < end and not self.g.travel:
            self.step(o or tobs(), 0.3)

    def test_half_circle_then_next_center(self):
        self.run_until_travel()
        self.assertTrue(self.g.travel)
        self.assertEqual(self.g.ci, 1)
        self.assertEqual(self.keys().count(self.k["scan_turn"]), 3)          # 3 × 350ms × 180°/s = 189°
        self.assertIn(350.0, [float(h) for h in self.inp.holds if h is not None])
        self.assertTrue(any("找怪转了约 189° 没找到能打的怪 → 走去下一个中心 #2" in m for m in self.logs))
        self.assertGreaterEqual(self.tab_count(), 1)                          # 这一轮按过 Tab
        self.assertFalse(any("走之前再按一次 Tab" in m for m in self.logs))   # 已经按过 → 不用补

    def test_tab_paused_gets_one_extra_tab(self):
        self.g.ts_cd_until = self.clock.t + 100
        self.g.ts_backoff = True
        self.run_until_travel()
        self.assertTrue(self.g.travel)
        self.assertEqual(self.tab_count(), 1)
        self.assertEqual(sum("走之前再按一次 Tab" in m for m in self.logs), 1)
        i_tab = max(i for i, m in enumerate(self.logs) if "走之前再按一次 Tab" in m)
        i_go = max(i for i, m in enumerate(self.logs) if "走去下一个中心" in m)
        self.assertLess(i_tab, i_go)

    def test_extra_tab_finds_mob_no_travel(self):
        self.g.ts_cd_until = self.clock.t + 100
        self.g.ts_backoff = True
        end = self.clock.t + 30
        while self.clock.t < end and not any("走之前再按一次 Tab" in m for m in self.logs):
            self.step(tobs(), 0.3)
        self.step(tobs(thp=100, kind="enemy", rng="melee"), 0.3)
        self.step(tobs(thp=100, kind="enemy", rng="melee"), 0.3)
        self.assertFalse(self.g.travel)
        self.assertEqual(self.g.spin_deg, 0.0)
        self.assertFalse(any("走去下一个中心" in m for m in self.logs))

    def test_full_circle_if_user_sets_360(self):
        self.cfg["scan"]["spin_deg"] = 360
        self.run_until_travel(60)
        self.assertEqual(self.keys().count(self.k["scan_turn"]), 6)          # 6 × 63° = 378°

    def test_single_center_walks_back(self):
        self.g.set_centers([])
        self.g.home = (50.0, 50.0, 0)
        end = self.clock.t + 30
        while self.clock.t < end and self.g.state != State.RETURNING:
            self.step(tobs(pos=(50.9, 50.0)), 0.3)
        self.assertEqual(self.g.state, State.RETURNING)
        self.assertEqual(self.keys().count(self.k["scan_turn"]), 3)


if __name__ == "__main__":
    unittest.main()

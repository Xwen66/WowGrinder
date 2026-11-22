"""v8.59（t227u）：A. 删掉“选修理商键”；B. 到修理商点先按清除目标键再按互动键；
C. 商人窗口没开 → 原地转身（0, +45, -45, +90, -90, +135, -135, 180）再试，8 个方向都不开 → v8.58 失败不停止。"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG, _merge, _migrate, key_conflicts  # noqa: E402
from bot.repair import RepairMixin, REPAIR_DEFAULTS  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_v855 import RepBase, ro, TOWN, ROOT, _rd  # noqa: E402


class Config(unittest.TestCase):
    def test_vendor_target_gone(self):
        self.assertNotIn("vendor_target", DEFAULT_CONFIG["keys"])
        old = {"keys": {"vendor_target": "`", "interact": "`"}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, copy.deepcopy(old)), copy.deepcopy(old))
        self.assertNotIn("vendor_target", cfg["keys"])
        self.assertFalse(any("选修理商" in m for m in key_conflicts(cfg)))
        self.assertEqual((REPAIR_DEFAULTS["vendor_tries"], REPAIR_DEFAULTS["vendor_turn_deg"]), (8, 45.0))

    def test_offsets(self):
        self.assertEqual([RepairMixin._rp_vendor_offset(i, 45) for i in range(8)],
                         [0, 45, -45, 90, -90, 135, -135, 180])


class Vendor(RepBase):
    def at_vendor(self, form="caster"):
        self.start_cast()
        self.land()
        self.walk_to_vendor()
        self.inp.history.clear()

    def v(self, n=1, **kw):
        kw.setdefault("form", "caster")
        for _ in range(n):
            self.t(ro(pos=(63.0, 75.0), mp=TOWN, **kw))

    def test_clear_then_interact_then_turn(self):
        self.at_vendor()
        self.v()
        ct = self.k["clear_target"]
        self.assertEqual(self.keys()[:2], [ct, "`"])
        self.assertNotIn("n", self.keys())
        self.inp.history.clear()
        self.v(7)                                              # 3 秒后第 2 次：先转身
        ks = self.keys()
        self.assertIn(ks[0], ("left", "right"))
        self.assertEqual(ks[1:3], [ct, "`"])
        self.assertEqual(self.g.rp["turn_off"], 45)
        self.assertTrue(any("清目标" in m and "第 2/8 次" in m for m in self.logs))

    def test_turn_sides_alternate_and_fail_after_8(self):
        self.at_vendor()
        self.v(80)
        ks = self.keys()
        self.assertEqual(ks.count("`"), 8)
        self.assertEqual(ks.count(self.k["clear_target"]), 8)
        turns = [k for k in ks if k in ("left", "right")]
        self.assertGreaterEqual(len(turns), 7)
        self.assertIn("left", turns)
        self.assertIn("right", turns)
        self.assertIsNone(self.g.rp)
        self.assertFalse(self.g.stopped)
        self.assertTrue(any("转了 8 个方向" in m for m in self.logs))

    def test_opens_on_third_try(self):
        self.at_vendor()
        self.v(14)
        self.assertEqual(self.keys().count("`"), 3)
        self.v(1, merchant=True)
        self.assertEqual(self.g.rp["phase"], "repairing")

    def test_cancel_form_fallback_kept(self):
        self.k["cancel_form"] = "v"
        self.at_vendor()
        self.v(7, form="cat")
        self.assertIn("v", self.keys())

    def test_no_clear_key_still_interacts(self):
        self.k["clear_target"] = ""
        self.at_vendor()
        self.v()
        self.assertEqual(self.keys(), ["`"])
        self.assertTrue(any("清除目标键”没填" in m for m in self.logs))


class Strings(unittest.TestCase):
    def test_files(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertRegex(app, r"熊德自动打怪 v8\.(59|[6-9]\d)")
        self.assertNotIn('"vendor_target"', app)
        self.assertNotIn("vendor_target", _rd(os.path.join(ROOT, "bot", "repair.py")))
        self.assertNotIn("vendor_target", _rd(os.path.join(ROOT, "bot", "runner.py")))
        self.assertIn("v8.59 不再需要“选修理商键”", _rd(os.path.join(ROOT, "README_中文.md")))


if __name__ == "__main__":
    unittest.main()

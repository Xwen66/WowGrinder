"""v8.36 的“战后放回春术”开关 → v8.54（t220u）整个删掉：
战后血量 ≥ 生命之触线 → 不按回春术、不变形，直接继续打；低于照样生命之触；战斗中治疗不变。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.state_machine import State  # noqa: E402
from test_state_machine import obs  # noqa: E402
from test_v830 import PostBase  # noqa: E402


class TestPostNoRejuv(PostBase):
    """v8.54（t220u）：战后回春术整个删掉；生命之触线以上 → 不按回春术、不变形，直接继续打。"""
    def test_no_rejuv_above_line(self):
        self.fight(88)
        self.inp.history.clear()
        self.after(88, n=10)
        ks = self.keys()
        self.assertEqual(ks.count("g"), 0)
        self.assertNotIn("v", ks)                                  # 不取消变形
        self.assertNotIn("e", ks)
        self.assertNotEqual(self.g.state, State.RESTING)
        self.assertEqual(sum("≥ 生命之触线" in m and "不治疗" in m for m in self.logs), 1)   # 只写一次

    def test_logs_once_per_fight(self):
        self.fight(88)
        self.after(88, n=6)
        self.clock.t += 20
        self.fight(85)
        self.after(85, n=6)
        self.assertEqual(sum("≥ 生命之触线" in m for m in self.logs), 2)

    def test_healing_touch_below_line_still(self):
        self.fight(50)
        self.inp.history.clear()
        for _ in range(4):
            self.clock.t += 0.4
            self.g.tick(obs(hp=50, kind="none", combat=False, form="cat", mana=80, casting=False))
        self.assertIn("e", self.keys())
        self.assertNotIn("g", self.keys())
        self.assertEqual(self.g.post_kind, "heal")

    def test_combat_heal_unchanged(self):
        self.cfg["heal"]["heal_pct"] = 40
        self.fight(88)
        self.clock.t += 0.5
        self.g.tick(obs(hp=30, thp=50, combat=True, form="cat", mana=90, rng="melee", aggro=True))
        self.assertEqual(self.g.state, State.HEALING)

    def test_post_off_casts_nothing(self):
        self.cfg["heal"]["post_enabled"] = False
        self.fight(88)
        self.inp.history.clear()
        self.after(88, n=10)
        self.assertNotIn("g", self.keys())
        self.assertNotIn("e", self.keys())

    def test_old_settings_ignored(self):                          # 旧配置里残留回春设置也不会放回春术
        self.cfg["heal"].update(post_rejuv_enabled=True, post_rejuv_below_pct=100)
        self.k["rejuvenation"] = "g"
        self.fight(88)
        self.inp.history.clear()
        self.after(88, n=10)
        self.assertNotIn("g", self.keys())


class TestSources(unittest.TestCase):
    def test_all_sources_compile(self):                          # 启动日志/界面改过：至少要能编译
        import glob
        import py_compile
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for f in glob.glob(os.path.join(root, "bot", "*.py")) + glob.glob(os.path.join(root, "gui", "*.py")):
            py_compile.compile(f, doraise=True)

    def test_runner_imports(self):
        import bot.runner  # noqa: F401


if __name__ == "__main__":
    unittest.main()

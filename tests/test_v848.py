"""v8.48（t195u）：背包满了还拾取（只拿到钱，物品不等，关拾取窗口），不剥皮，记住捡过的尸体不再捡。背包没满时不变。"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402

P = (50.0, 50.0)


def o(pos=P, **kw):
    kw.setdefault("form", "cat")
    ob = obs(**kw)
    ob.pos = pos
    return ob


class V848(Base):
    LEGACY = False
    MODE = "click"

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.cfg["heal"]["post_enabled"] = False

    def f(self):
        return self.keys().count("f")

    def esc(self):
        return self.keys().count("esc")

    def full(self):
        self.g.bag_full = True

    def ticks(self, n=8, dt=0.3, **kw):
        for _ in range(n):
            self.g.tick(o(**kw))
            self.clock.t += dt


class Defaults(unittest.TestCase):
    def test_defaults(self):
        l = DEFAULT_CONFIG["loot"]
        self.assertTrue(l["full_bag_money_only"])
        self.assertEqual((l["full_bag_memory_s"], l["full_bag_memory_dist"]), (300, 0.15))   # v8.49：去掉了关窗口键


class Detect(V848):
    def test_detection_switches_to_money_only(self):
        for _ in range(10):
            self.ticks(n=12, kind="dead", lootable=True)
            if self.g.bag_full:
                break
        self.assertTrue(self.g.bag_full)
        self.assertTrue(any("以后只捡钱" in m for m in self.logs))
        self.assertIn("只捡钱", self.g.status_label())
        n = self.f()
        self.ticks(n=10, kind="dead", lootable=True)          # 触发检测的那具：记住了，不再捡
        self.assertEqual(self.f(), n)


class MoneyOnly(V848):
    def test_fresh_kill_one_press_no_skin(self):
        self.full()
        t0 = self.clock.t
        self.g.loot(o(kind="dead", lootable=True), count_kill=True)
        self.assertEqual(self.f(), 1)
        self.assertLessEqual(self.clock.t - t0, 1.8)          # 等 0.7 + 0.5 秒（+按键随机间隔），不等物品
        self.assertNotEqual(self.g.state, State.SKINNING)
        self.assertFalse(any(m.startswith("🔪") for m in self.logs))
        self.assertTrue(any(m.startswith("🎒💰 背包满") for m in self.logs))

    def test_never_esc(self):                                  # v8.49：不再按 Esc（以前 40% 会打开游戏菜单）
        self.full()
        self.g.loot(o(kind="dead", lootable=True), count_kill=True)
        self.ticks(n=10, kind="dead", lootable=True)          # 还选着这具 → 不按 Esc、不再捡
        self.assertEqual(self.esc(), 0)
        self.assertEqual(self.f(), 1)

    def test_no_target_no_esc(self):
        self.full()
        self.g.loot(o(kind="dead", lootable=True), count_kill=True)
        self.ticks(n=10, kind="none")
        self.assertEqual(self.esc(), 0)

    def test_casting_no_esc(self):
        self.full()
        self.g.loot(o(kind="dead", lootable=True), count_kill=True)
        self.ticks(n=3, kind="dead", lootable=True, casting=True)
        self.assertEqual(self.esc(), 0)

    def test_remembered_corpse_not_relooted(self):
        self.full()
        self.g.loot(o(kind="dead", lootable=True), count_kill=True)
        self.clock.t += 20
        self.ticks(n=10, pos=(50.05, 50.0), kind="dead", lootable=True)
        self.assertEqual(self.f(), 1)
        self.assertIn(self.k["clear_target"], self.keys())

    def test_other_corpse_is_looted(self):
        self.full()
        self.g.loot(o(kind="dead", lootable=True), count_kill=True)
        self.clock.t += 20
        self.ticks(n=10, pos=(52.0, 50.0), kind="dead", lootable=True)
        self.assertEqual(self.f(), 2)

    def test_memory_expires(self):
        self.full()
        self.g.loot(o(kind="dead", lootable=True), count_kill=True)
        self.clock.t += 301
        self.ticks(n=10, kind="dead", lootable=True)
        self.assertEqual(self.f(), 2)

    def test_no_pos_uses_time(self):
        self.full()
        self.g.loot(o(pos=None, kind="dead", lootable=True), count_kill=True)
        self.clock.t += 10
        self.ticks(n=8, pos=None, kind="dead", lootable=True)
        self.assertEqual(self.f(), 1)

    def test_in_combat_pending_then_loot(self):
        self.full()
        self.g.loot(o(kind="dead", lootable=True, combat=True), count_kill=True)
        self.assertEqual(self.f(), 0)
        self.assertEqual(self.g.pending_loot, 1)
        self.ticks(n=10, kind="dead", lootable=True)
        self.assertEqual(self.f(), 1)
        self.assertFalse(any("🔪 剥皮：" in m for m in self.logs))

    def test_empty_corpse_skipped(self):
        self.full()
        self.g.loot(o(kind="dead", lootable=False), count_kill=True)
        self.assertEqual(self.f(), 0)

    def test_toggle_off_old_behaviour(self):
        self.cfg["loot"]["full_bag_money_only"] = False
        self.full()
        self.g.loot(o(kind="dead", lootable=True), count_kill=True)
        self.ticks(n=10, kind="dead", lootable=True)
        self.assertEqual(self.f(), 0)
        self.assertIn("只打怪", self.g.status_label())

    def test_loot_disabled(self):
        self.cfg["loot"]["enabled"] = False
        self.full()
        self.g.loot(o(kind="dead", lootable=True), count_kill=True)
        self.assertEqual(self.f(), 0)


class NotFull(V848):
    def test_normal_loot_and_skin_unchanged(self):
        self.g.loot(o(kind="dead", lootable=True), count_kill=True)
        self.assertEqual(self.f(), 2)                        # 拾取 + 剥皮
        self.assertEqual(self.g.state, State.SKINNING)
        self.assertEqual(self.esc(), 0)
        self.assertEqual(self.g.fb_mem, [])


class Strings(unittest.TestCase):
    def test_gui_readme(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "gui", "app.py"), encoding="utf-8") as fh:
            app = fh.read()
        self.assertTrue(re.search(r"熊德自动打怪 v8\.(4\d|[5-9]\d)", app))      # v8.49 起标题版本在新测试里查
        self.assertIn('"full_bag_money_only"', app)
        with open(os.path.join(root, "README_中文.md"), encoding="utf-8") as fh:
            self.assertIn("v8.48 背包满只捡钱", fh.read())


if __name__ == "__main__":
    unittest.main()

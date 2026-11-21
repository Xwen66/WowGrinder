"""v8.29（t155u）：连按互动键没反应 N 次 → 认定背包满，本次运行关掉拾取/剥皮，只打怪。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402


class BagBase(Base):
    LEGACY = False
    MODE = "click"

    def setUp(self):
        super().setUp()
        self.cfg["loot"]["full_bag_money_only"] = False      # v8.48 前的行为：背包满只打怪

    def fpress(self):
        return self.keys().count("f")

    def round(self, lootable=True, casting=False, kind="dead", combat=False, ticks=12, dt=0.3):
        """一轮：看到选中的尸体 → 拾取(+剥皮) → 等读条。返回这一轮按了几次 f。"""
        n0 = self.fpress()
        for _ in range(ticks):
            self.g.tick(obs(kind=kind, lootable=lootable, casting=casting, combat=combat, form="cat"))
            self.clock.t += dt
        return self.fpress() - n0

    def bag_logs(self):
        return [m for m in self.logs if "背包满了" in m]


class TestBagFull(BagBase):
    def test_full_after_five_unresponsive_presses(self):
        for _ in range(8):
            self.round()
            if self.g.bag_full:
                break
        self.assertTrue(self.g.bag_full)
        self.assertEqual(self.fpress(), 5)               # 正好 5 次（第 3 轮只按拾取看反应）
        self.assertEqual(len(self.bag_logs()), 1)
        self.assertIn("🎒 连按互动键 5 次没反应 → 认定背包满了，关掉拾取和剥皮，只打怪", self.bag_logs()[0])
        self.assertIn("背包满", self.g.status_label())
        n = self.fpress()
        for _ in range(4):                               # 之后再看到这具尸体 / 新杀的怪：不按互动键
            self.round()
        self.g.loot(obs(kind="dead", lootable=True, form="cat"), count_kill=True)
        self.assertEqual(self.fpress(), n)
        self.assertEqual(self.g.pending_loot, 0)

    def test_skinning_cast_resets(self):
        for _ in range(6):
            self.round(casting=False, ticks=3)
            self.round(casting=True, ticks=10)         # 有剥皮读条 = 有反应
        self.assertFalse(self.g.bag_full)

    def test_looted_corpse_resets(self):
        # 非野兽：拾取成功（尸体变成不可拾取），剥皮没读条 → 每具只记 0~1 次，不会误判
        for _ in range(6):
            self.g.loot(obs(kind="dead", lootable=True, form="cat"), count_kill=True)
            for _ in range(12):
                self.g.tick(obs(kind="dead", lootable=False, form="cat"))
                self.clock.t += 0.3
            self.g.tick(obs(kind="none", combat=True, form="cat"))   # 打下一只
            self.clock.t += 1
        self.assertFalse(self.g.bag_full)

    def test_not_lootable_not_counted(self):
        # 已经捡过/没东西的尸体：不按拾取；剥皮没读条也看不到“可拾取” → 不算
        for _ in range(10):
            self.round(lootable=False)
        self.assertFalse(self.g.bag_full)
        self.assertEqual(self.g.bag_noresp, 0)

    def test_no_target_info_not_counted(self):
        for _ in range(5):
            self.g.loot(obs(kind="none", form="cat"), count_kill=True)
            for _ in range(12):
                self.g.tick(obs(kind="none", form="cat"))
                self.clock.t += 0.3
        self.assertFalse(self.g.bag_full)

    def test_combat_resets_and_no_interact_in_combat(self):
        self.round()
        self.round()
        self.assertEqual(self.g.bag_noresp, 4)
        n = self.fpress()
        self.round(combat=True, kind="dead")             # 战斗中：不按互动键，计数清零
        self.assertEqual(self.fpress(), n)
        self.assertEqual(self.g.bag_noresp, 0)
        self.assertFalse(self.g.bag_full)

    def test_toggle_off(self):
        self.cfg["loot"]["bagfull_detect"] = False
        for _ in range(6):
            self.round()
        self.assertFalse(self.g.bag_full)
        self.assertGreaterEqual(self.fpress(), 12)          # 关了就跟以前一样一直按

    def test_custom_count(self):
        self.cfg["loot"]["bagfull_presses"] = 3
        for _ in range(5):
            self.round()
            if self.g.bag_full:
                break
        self.assertTrue(self.g.bag_full)
        self.assertEqual(self.fpress(), 3)
        self.assertIn("连按互动键 3 次没反应", self.bag_logs()[0])

    def test_two_corpses_option(self):
        self.cfg["loot"]["bagfull_two_corpses"] = True
        for _ in range(4):
            self.round()
        self.assertFalse(self.g.bag_full)
        self.assertTrue(self.g.bag_skip)
        n = self.fpress()
        self.round()                                     # 放弃的这具尸体：不再按
        self.assertEqual(self.fpress(), n)
        self.assertTrue(any("放弃这具尸体" in m for m in self.logs))
        self.g.tick(obs(kind="none", combat=True, form="cat"))       # 打下一只 → 新尸体
        self.clock.t += 1
        for _ in range(6):
            self.round()
            if self.g.bag_full:
                break
        self.assertTrue(self.g.bag_full)

    def test_new_session_resets(self):
        for _ in range(4):
            self.round()
        self.assertTrue(self.g.bag_full)
        from bot.state_machine import Grinder
        g2 = Grinder(self.cfg, self.inp, log=self.logs.append, clock=self.clock)
        self.assertFalse(g2.bag_full)

    def test_skin_off_still_detects(self):
        self.cfg["loot"]["skin"] = False
        for _ in range(12):
            self.round(ticks=14)
            if self.g.bag_full:
                break
        self.assertTrue(self.g.bag_full)
        self.assertEqual(self.fpress(), 5)


class TestBagFullStationary(BagBase):
    MODE = "stationary_tab"

    def test_stationary_full(self):
        for _ in range(8):
            self.round()
            if self.g.bag_full:
                break
        self.assertTrue(self.g.bag_full)
        n = self.fpress()
        for _ in range(3):
            self.round()
        self.assertEqual(self.fpress(), n)


if __name__ == "__main__":
    unittest.main()

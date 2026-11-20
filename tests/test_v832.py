"""v8.32（t159u）：不在战斗中看到能打的怪的插件标记 → 先打（拾取/补 buff/回中心打完再做）；
回血没到线、读条中、跑尸、范围外不打。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from test_v828 import Click828, mplate  # noqa: E402

M = [mplate(960, 500)]


class TestV832(Click828):
    def setUp(self):
        super().setUp()
        self.k["interact"] = self.k["loot"] = "`"

    def test_default_on(self):
        self.assertTrue(DEFAULT_CONFIG["nameplate"]["engage_any"])

    def test_corpse_loot_postponed_two_frames(self):
        self.tick(kind="dead", lootable=True, plates=M)          # 第 1 帧：先确认，不拾取也不点
        self.assertEqual(self.clicks_at(), [])
        self.assertNotIn("`", self.keys())
        self.tick(kind="dead", lootable=True, plates=M)          # 第 2 帧：点怪，尸体记下
        self.assertEqual(self.clicks_at(), ["click(960,500)"])
        self.assertNotIn("`", self.keys())
        self.assertEqual(self.g.pending_loot, 1)
        self.assertTrue(any("非战斗看到 1 个能打的怪 → 先打" in m and "尸体记下" in m for m in self.logs))

    def test_one_frame_flicker_then_loot(self):
        self.tick(kind="dead", lootable=True, plates=M)
        self.tick(kind="dead", lootable=True)                    # 标记没了 → 照常拾取
        self.assertIn("`", self.keys())
        self.assertEqual(self.clicks_at(), [])

    def test_toggle_off_loots_first(self):
        self.cfg["nameplate"]["engage_any"] = False
        self.tick(kind="dead", lootable=True, plates=M)
        self.assertIn("`", self.keys())
        self.assertEqual(self.clicks_at(), [])

    def test_pending_corpses_postponed(self):
        self.g.pending_loot, self.g.pending_loot_t = 2, self.clock.t
        self.tick(plates=M)
        self.tick(plates=M)
        self.assertEqual(self.clicks_at(), ["click(960,500)"])
        self.assertNotIn("`", self.keys())
        self.assertEqual(self.g.pending_loot, 2)

    def test_spin_wait_postponed(self):
        self.g.spin_wait_until = self.clock.t + 10
        self.tick(plates=M)
        self.tick(plates=M)
        self.assertEqual(self.clicks_at(), ["click(960,500)"])

    def test_buff_postponed(self):
        self.cfg["buffs"]["enabled"] = True
        self.tick(plates=M, form="cat")
        self.assertEqual(self.clicks_at(), ["click(960,500)"])
        self.assertNotIn(self.k["thorns"], self.keys())

    def test_need_rest_not_interrupted(self):
        self.cfg["heal"]["post_enabled"] = False
        self.cfg["heal"]["rest_below_pct"] = 60
        self.tick(hp=40, plates=M)
        self.tick(hp=40, plates=M)
        self.assertEqual(self.clicks_at(), [])

    def test_casting_not_interrupted(self):
        self.tick(kind="dead", lootable=True, plates=M, casting=True)
        self.tick(kind="dead", lootable=True, plates=M, casting=True)
        self.assertEqual(self.clicks_at(), [])

    def test_top_strip_ignored(self):
        self.cfg["nameplate"]["far_names"] = False
        self.tick(kind="dead", lootable=True, plates=[mplate(960, 150)])
        self.tick(kind="dead", lootable=True, plates=[mplate(960, 150)])
        self.assertEqual(self.clicks_at(), [])

    def test_template_source_not_trusted(self):
        self.tick(kind="dead", lootable=True, plates=M)
        x = self.o(kind="dead", lootable=True, plates=M)
        x.plate_source = "template"
        self.clock.t += 0.15
        self.g.tick(x)
        self.assertEqual(self.clicks_at(), [])

    def test_outside_area_not_engaged(self):
        self.cfg["area"]["enabled"] = True
        self.cfg["area"]["radius"] = 1.0
        self.g.home = (10.0, 10.0, 0)
        for _ in range(2):
            x = self.o(kind="dead", lootable=True, plates=M)
            x.pos = (20.0, 20.0)
            self.clock.t += 0.15
            self.g.tick(x)
        self.assertEqual(self.clicks_at(), [])

    def test_corpse_run_not_interrupted(self):
        x = self.o(plates=M, dead=True)
        x.death_info = True
        self.g.tick(x)
        self.clock.t += 0.2
        self.g.tick(x)
        self.assertEqual(self.clicks_at(), [])


if __name__ == "__main__":
    unittest.main()

"""v8.62（t234u/t235u）：战斗中调朝向 → 先右键点“当前目标”的姓名板标记（近战内也点），角色自动朝向；
看不到标记 / 怪贴在脚下（近战 + 标记在中间 + 面向错误）→ 后退一大步（600ms）再点；战斗中不再左右盲转，不按互动键。"""
import copy
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class NP(Base):
    MODE = "click"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["face_click"]["nameplate_first"] = True
        self.cfg["buffs"]["enabled"] = False

    def fight(self):
        self.g.tick(obs(thp=100))
        self.g.tick(obs(hp=95, thp=90, combat=True, form="bear"))
        self.assertEqual(self.g.state, State.COMBAT)
        self.inp.history.clear()

    def o(self, n=None, marker=(1300, 500), state="shown", rng="melee", facing=True, error=None, thp=80, aggro=True):
        o = obs(hp=90, thp=thp, combat=True, form="bear", err_facing=facing,
                error=("facing" if facing else error), rng=rng, aggro=aggro)
        o.marker, o.marker_center, o.frame_size, o.facing_count = marker, True, (1920, 1080), n
        o.marker_state = state
        return o

    def turns(self, ks):
        return [k for k in ks if k in (self.k["turn_left"], self.k["turn_right"])]

    def clicks(self, ks):
        return [k for k in ks if k.startswith("click(")]

    # ---- 面向错误
    def test_facing_in_melee_clicks_current_target_plate(self):
        self.fight()
        self.clock.t += 1.0
        self.g.tick(self.o(1))
        ks = self.keys()
        self.assertEqual(self.clicks(ks), ["click(1300,500)"])
        self.assertFalse(self.turns(ks))
        self.assertNotIn(self.k["interact"], ks)
        self.assertTrue(any("右键点当前目标姓名板" in m and "不盲转" in m for m in self.logs))

    def test_no_new_error_waits(self):
        self.fight()
        self.clock.t += 1.0
        self.g.tick(self.o(1))
        for _ in range(4):                     # 报错灯还亮，但计数没变 → 是旧的，不再点
            self.clock.t += 1.0
            self.g.tick(self.o(1))
        self.assertEqual(len(self.clicks(self.keys())), 1)

    def test_under_feet_backs_once_then_clicks(self):
        self.fight()
        self.clock.t += 1.0
        self.g.tick(self.o(1, marker=(960, 500)))       # 标记在中间 + 近战 + 面向错误 = 怪在脚下
        ks = self.keys()
        self.assertEqual([k for k in ks if k == "s"], ["s"])
        self.assertFalse(self.clicks(ks))
        self.assertIn(600, self.inp.holds)
        self.assertTrue(any("怪贴在脚下" in m and "600ms" in m for m in self.logs))
        self.clock.t += 1.0
        self.g.tick(self.o(2, marker=(960, 500)))
        ks = self.keys()
        self.assertEqual(ks.count("s"), 1)               # 只后退一次
        self.assertEqual(self.clicks(ks), ["click(960,500)"])
        self.assertFalse(self.turns(ks))

    def test_back_hold_ms(self):
        self.fight()
        self.clock.t += 1.0
        self.g.tick(self.o(1, marker=None, state="no_plate"))
        self.assertIn("s", self.keys())
        self.assertTrue(any("目标没有姓名板" in m and "后退一步" in m for m in self.logs))
        self.assertIn(600, self.inp.holds)

    def test_no_marker_after_back_never_blind_turns(self):
        self.fight()
        for i in range(1, 12):
            self.clock.t += 1.0
            self.g.tick(self.o(i, marker=None, state="no_plate"))
        ks = self.keys()
        self.assertFalse(self.turns(ks))
        rounds = sum("还是没修好" in m for m in self.logs)
        self.assertLessEqual(ks.count("s"), rounds + 1)   # 每轮（最多 4 次动作）只后退一次
        self.assertNotIn(self.k["interact"], ks)
        self.assertFalse(any("盲转到" in m for m in self.logs))

    def test_unconfirmed_marker_not_clicked(self):
        self.fight()
        self.clock.t += 1.0
        self.g.tick(self.o(1, state="unknown"))        # 插件没确认标记挂在当前目标上 → 不点（免得换目标）
        ks = self.keys()
        self.assertFalse(self.clicks(ks))
        self.assertIn("s", ks)
        self.assertFalse(self.turns(ks))

    def test_bounded_give_up_keeps_aggro_target(self):
        self.fight()
        for i in range(1, 20):
            self.clock.t += 1.0
            self.g.tick(self.o(i))
        ks = self.keys()
        self.assertFalse(self.turns(ks))
        self.assertTrue(any("右键姓名板/后退 4 次还是没修好" in m for m in self.logs))
        self.assertTrue(any("正在打我 → 不换目标" in m for m in self.logs))
        self.assertNotIn("tab", ks)

    def test_respects_right_click_interval(self):
        self.cfg["move"]["click_interval_s"] = 3.0
        self.fight()
        self.g.last_rclick = self.clock.t
        self.clock.t += 1.0
        self.g.tick(self.o(1))
        self.assertFalse(self.clicks(self.keys()))
        self.assertFalse(self.turns(self.keys()))
        self.clock.t += 2.5
        self.g.tick(self.o(2))
        self.assertEqual(len(self.clicks(self.keys())), 1)

    # ---- 距离太远（补救）
    def test_range_error_in_melee_clicks_not_turns(self):
        self.fight()
        self.clock.t += 0.5
        self.g.tick(self.o(None, marker=(960, 500), facing=False, error="range"))
        ks = self.keys()
        self.assertEqual(self.clicks(ks), ["click(960,500)"])     # 距离太远不算“贴脚下”，直接点（不后退）
        self.assertNotIn("s", ks)
        self.assertFalse(self.turns(ks))
        self.assertTrue(any("🔧 距离太远 → 补救第 1 次：右键点当前目标姓名板" in m for m in self.logs))

    def test_range_error_no_marker_backs_then_attack_key(self):
        self.fight()
        for i in range(4):
            self.clock.t += 1.6
            self.g.tick(self.o(None, marker=None, state="no_plate", facing=False, error="range"))
        ks = self.keys()
        self.assertFalse(self.turns(ks))
        self.assertEqual(ks.count("s"), 1)
        self.assertIn(self.k["attack"], ks)
        self.assertNotIn(self.k["interact"], ks)

    def test_damage_resets_round(self):
        self.fight()
        self.clock.t += 0.5
        self.g.tick(self.o(None, facing=False, error="range"))
        self.assertEqual(self.g.np_n, 1)
        self.clock.t += 1.6
        self.g.tick(self.o(None, facing=False, error=None, thp=60))   # 打出伤害
        self.clock.t += 0.1
        self.g.tick(self.o(None, facing=False, error=None, thp=60))
        self.assertEqual(self.g.np_n, 0)

    def test_off_switch_keeps_old_behavior(self):
        self.cfg["face_click"]["nameplate_first"] = False
        self.fight()
        self.clock.t += 0.5
        self.g.tick(self.o(None, marker=(960, 500), facing=False, error="range"))
        self.assertTrue(self.turns(self.keys()))                  # 旧做法：近战内盲转 + 开怪键


class Strings(unittest.TestCase):
    def test_defaults(self):
        fc = DEFAULT_CONFIG["face_click"]
        self.assertEqual((fc["nameplate_first"], fc["np_back_ms"], fc["np_max"]), (True, 600, 4))

    def test_gui_readme(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertRegex(app, r"熊德自动打怪 v8\.(6[2-9]|[7-9]\d)")
        self.assertIn('"nameplate_first"', app)
        self.assertIn('"np_back_ms"', app)
        self.assertTrue(re.search(r"v8\.62.*姓名板", _rd(os.path.join(ROOT, "README_中文.md"))))


if __name__ == "__main__":
    unittest.main()

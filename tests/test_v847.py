"""v8.47（t189u/t190u）少死：
A. 多怪变熊默认关；B. 一直按变猎豹预留；C. 实测变身耗蓝不再被治疗/回春/复活污染（3 秒内不测、中位数、上限）；
D/E. 战后治疗：< 60% 生命之触 / 60-85% 回春术 / ≥ 85% 不治疗；F. 法力 ≥ 50% 才开怪（被打照常迎战）。"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402
from test_v8_heal_log import TestV827PostFightHeal, pobs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class V847(Base):
    MODE = "click"
    LEGACY = False

    def setUp(self):
        super().setUp()
        h, d = self.cfg["heal"], DEFAULT_CONFIG["heal"]
        for k in ("post_ht_below_pct", "post_rejuv_below_pct", "pull_min_mana_pct", "post_rejuv_enabled"):
            h[k] = d[k]                                        # v8.47 新默认
        self.cfg["multi_bear"] = copy.deepcopy(DEFAULT_CONFIG["multi_bear"])
        self.cfg["cat"]["combat_form"] = "猎豹"
        self.cfg["buffs"]["enabled"] = False


# ---------------------------------------------------------------- 默认值
class Defaults(unittest.TestCase):
    def test_defaults(self):
        h = DEFAULT_CONFIG["heal"]
        self.assertFalse(DEFAULT_CONFIG["multi_bear"]["enabled"])
        self.assertEqual((h["post_ht_below_pct"], h["post_rejuv_below_pct"], h["pull_min_mana_pct"],
                          h["reserve_learn_cap_pct"], h["post_rejuv_enabled"], h["post_enabled"]), (60, 85, 50, 8, True, True))


# ---------------------------------------------------------------- A/B. 不变熊，按猎豹预留
class NoBear(V847):
    def test_fast_drop_stays_cat(self):
        self.g.tick(obs(hp=100, thp=100, form="cat", mana=90))
        hp = 100
        for _ in range(12):
            self.clock.t += 0.5
            hp -= 4
            self.g.tick(obs(hp=hp, thp=90, combat=True, form="cat", mana=90, rng="melee", aggro=True))
        self.assertFalse(any("变熊抗" in m for m in self.logs))
        self.assertNotIn(self.k["bear_form"], self.keys())
        self.assertEqual(self.g._cform(), "cat")

    def test_reserve_is_cat(self):
        self.cfg["heal"].update(reserve_cat_pct=30, reserve_bear_pct=45)
        self.assertEqual(self.g._reserve(), 30)


# ---------------------------------------------------------------- C. 实测变身耗蓝
class ShiftLearn(V847):
    def setUp(self):
        super().setUp()
        self.cfg["heal"].update(reserve_cat_pct=30, reserve_learn_cap_pct=8)

    def shift(self, before, after, prev_cast=False):
        self.clock.t += 0.3
        self.g._learn_mana(obs(form="caster", mana=before, casting=prev_cast))
        self.clock.t += 0.3
        self.g._learn_mana(obs(form="cat", mana=after, casting=False))

    def test_normal_measure(self):
        self.shift(80, 48)
        self.assertEqual(self.g.mana_cost["shift_cat"], 32)
        self.assertEqual(self.g._reserve(), 34)

    def test_skip_after_heal(self):
        self.g.spell_t = self.clock.t + 0.3
        self.shift(75, 27)                     # 12:04 那种：治疗之触还在读 + 变身 = 48%
        self.assertNotIn("shift_cat", self.g.mana_cost)
        self.assertEqual(self.g._reserve(), 30)

    def test_skip_while_casting(self):
        self.shift(71, 23, prev_cast=True)
        self.assertNotIn("shift_cat", self.g.mana_cost)

    def test_skip_inflight_heal(self):
        self.g.heal_inflight = (self.clock.t, 71)
        self.shift(71, 23)
        self.assertNotIn("shift_cat", self.g.mana_cost)

    def test_median_of_3(self):
        self.clock.t += 10
        for b, a in ((80, 51), (80, 40), (80, 49)):           # 29, 40(坏), 31 → 中位数 31
            self.shift(b, a)
            self.clock.t += 10
        self.assertEqual(self.g.mana_cost["shift_cat"], 31)

    def test_cap(self):
        self.shift(94, 49)                     # 45%（13:03 复活后那种）→ 最多 30 + 8
        self.assertEqual(self.g.mana_cost["shift_cat"], 38)
        self.assertEqual(self.g._reserve(), 40)

    def test_spell_t_set_while_healing(self):
        self.g.state = State.HEALING
        self.g.tick(obs(hp=30, thp=50, combat=True, form="caster", mana=60))
        self.assertGreaterEqual(self.g.spell_t, self.clock.t - 0.01)


# ---------------------------------------------------------------- D/E. 战后治疗分档
class PostHeal(Base):
    MODE, LEGACY, POST = "click", False, True
    end_fight = TestV827PostFightHeal.end_fight

    def setUp(self):
        super().setUp()
        self.cfg["cat"]["combat_form"] = "猎豹"
        self.k.update(cat_form="z", cancel_form="v", healing_touch="e", rejuvenation="g", interact="`", loot="`",
                      cat_builder="1", cat_finisher="2", target_attacker="x")
        self.cfg["buffs"]["enabled"] = False
        self.cfg["loot"]["enabled"] = False
        h, d = self.cfg["heal"], DEFAULT_CONFIG["heal"]
        for k in ("post_ht_below_pct", "post_rejuv_below_pct", "pull_min_mana_pct", "post_rejuv_enabled"):
            h[k] = d[k]

    def test_below_60_healing_touch(self):
        ks = self.end_fight(55)
        self.assertIn("e", ks)
        self.assertNotIn("g", ks)

    def test_60_to_85_rejuv(self):
        ks = self.end_fight(70)
        self.assertIn("g", ks)
        self.assertNotIn("e", ks)

    def test_85_and_up_no_heal(self):
        ks = self.end_fight(88)
        self.assertFalse(set(ks) & {"e", "g"})
        self.assertNotEqual(self.g.state, State.RESTING)

    def test_line_configurable(self):
        self.cfg["heal"]["post_rejuv_below_pct"] = 95
        ks = self.end_fight(88)
        self.assertIn("g", ks)


# ---------------------------------------------------------------- F. 法力 ≥ 50% 才开怪
class PullFloor(V847):
    def setUp(self):
        super().setUp()
        self.cfg["heal"]["rest_below_pct"] = 0

    def test_full_hp_low_mana_rests(self):
        self.g.tick(obs(hp=100, mana=40, form="cat"))
        self.assertEqual((self.g.state, self.g.rest_mode), (State.RESTING, "mana"))
        self.assertTrue(any("开怪线 50%" in m for m in self.logs))
        for _ in range(10):
            self.clock.t += 1
            self.g.tick(obs(hp=100, mana=45, form="cat", plates=[]))
        self.assertEqual(self.keys(), [])
        self.clock.t += 1
        self.g.tick(obs(hp=100, mana=50, form="cat"))
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertTrue(any("💤 回蓝完成" in m for m in self.logs))

    def test_target_selected_not_engaged(self):
        self.g.tick(obs(hp=100, thp=100, mana=40, form="cat", rng="melee"))
        self.assertEqual(self.g.state, State.RESTING)
        self.assertNotIn(self.k["attack"], self.keys())

    def test_mana_ok_pulls(self):
        self.g.tick(obs(hp=100, mana=55, form="cat"))
        self.assertNotEqual(self.g.state, State.RESTING)

    def test_floor_zero_disables(self):
        self.cfg["heal"]["pull_min_mana_pct"] = 0
        self.g.tick(obs(hp=100, mana=20, form="cat"))
        self.assertNotEqual(self.g.state, State.RESTING)

    def test_attacked_fights_back(self):
        self.g.tick(obs(hp=100, mana=40, form="cat"))
        self.clock.t += 1
        self.g.tick(obs(hp=95, thp=100, mana=40, combat=True, form="cat", aggro=True, rng="melee"))
        self.assertNotEqual(self.g.state, State.RESTING)
        self.assertTrue(any("回蓝时被打" in m for m in self.logs))

    def test_in_combat_no_floor(self):
        self.assertFalse(self.g._pull_mana_low(obs(hp=100, mana=10, combat=True)))
        self.assertFalse(self.g._need_mana_rest(obs(hp=100, mana=10, combat=True), self.clock.t, False))

    def test_low_hp_rule_still_needs_more(self):
        need = self.g._mr_need()                              # 预留 0（测试）+ 治疗之触 20 + 10 = 30
        self.cfg["heal"]["pull_min_mana_pct"] = 20
        self.g.tick(obs(hp=50, mana=25, form="cat"))
        self.assertEqual(self.g.rest_mode, "mana")
        self.clock.t += 5
        self.g.tick(obs(hp=50, mana=need - 2, form="cat"))
        self.assertEqual(self.g.state, State.RESTING)
        self.clock.t += 5
        self.g.tick(obs(hp=50, mana=need + 1, form="cat"))
        self.assertEqual(self.g.state, State.SEARCHING)

    def test_floor_waits_beyond_120s(self):
        self.g.tick(obs(hp=100, mana=30, form="cat"))
        self.clock.t += 200
        self.g.tick(obs(hp=100, mana=40, form="cat"))
        self.assertEqual(self.g.state, State.RESTING)
        self.clock.t += 101
        self.g.tick(obs(hp=100, mana=40, form="cat"))
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertTrue(any("回蓝 300 秒还没到" in m for m in self.logs))

    def test_no_tab_while_walking(self):
        self.assertTrue(self.g._pull_mana_low(obs(hp=100, mana=30)))
        self.g.ts_pend = (self.clock.t, "move")
        self.assertFalse(self.g._ts_moving(obs(hp=100, mana=30, thp=100), self.clock.t + 1, 1.0, "走回中心的路上"))
        self.g.travel_look_t = -1e9
        self.assertFalse(self.g._travel_look(obs(hp=100, mana=30), self.clock.t + 1))


# ---------------------------------------------------------------- GUI / README
class Strings(unittest.TestCase):
    def test_gui_and_readme(self):
        with open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8") as f:
            app = f.read()
        self.assertIn("熊德自动打怪 v8.47", app)
        self.assertIn('"post_rejuv_below_pct"', app)
        self.assertIn('"pull_min_mana_pct"', app)
        with open(os.path.join(ROOT, "README_中文.md"), encoding="utf-8") as f:
            rd = f.read()
        self.assertIn("v8.47 少死", rd)
        self.assertLess(rd.index("v8.47 少死"), rd.index("v8.46 提高效率"))


if __name__ == "__main__":
    unittest.main()

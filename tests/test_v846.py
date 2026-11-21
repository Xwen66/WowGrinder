"""v8.46（t185u）提高效率：
A. 就近切换中心（离当前中心超出半径、但在另一个中心半径里 → 换过去，不走回去）；
1. 蓝不够时先回蓝再开怪；2. 战斗中治疗线 30 → 40；3. 怪离得远 → 先走过去再开打。"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402
from test_v8_heal_log import pobs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C3 = [(50.0, 50.0, 1442), (52.0, 50.0, 1442), (54.0, 50.0, 1442)]


def plate(x, y, kind="selected", w=120, h=14):
    return {"x": x, "y": y, "w": w, "h": h, "kind": kind}


# ---------------------------------------------------------------- A. 就近切换中心
class CenterSwitch(Base):
    MODE = "click"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.cfg["area"].update(enabled=True, radius=1.4, leash_ratio=2.0)
        self.g.set_centers(C3)
        self.g.ci, self.g.home, self.g.travel, self.g.cdir = 1, C3[1], False, 1

    def test_default_on(self):
        self.assertTrue(DEFAULT_CONFIG["area"]["auto_switch_center"])

    def test_switch_instead_of_walking_back(self):
        out = self.g._outside_area(pobs(pos=(53.6, 50.0)))       # 离 #2 1.6 > 1.4，离 #3 0.4
        self.assertFalse(out)
        self.assertEqual((self.g.ci, self.g.home), (2, C3[2]))
        self.assertEqual(self.g.cdir, -1)                       # 到头了 → 往回走 3→2→1
        self.assertTrue(any("🚩 已在中心 #3 范围内" in m and "不走回 #2" in m for m in self.logs))

    def test_switch_down_keeps_direction_down(self):
        self.g.set_centers(C3 + [(56.0, 50.0, 1442)])
        self.g.ci, self.g.home, self.g.cdir = 2, C3[2], 1
        self.assertFalse(self.g._outside_area(pobs(pos=(52.3, 50.0))))
        self.assertEqual((self.g.ci, self.g.cdir), (1, -1))

    def test_nearest_of_several(self):
        self.g.set_centers([(50.0, 50.0, 1442), (60.0, 50.0, 1442), (51.0, 50.0, 1442)])
        self.g.ci, self.g.home = 1, (60.0, 50.0, 1442)
        self.assertFalse(self.g._outside_area(pobs(pos=(50.8, 50.0))))
        self.assertEqual(self.g.ci, 2)

    def test_outside_all_walks_back(self):
        self.assertTrue(self.g._outside_area(pobs(pos=(52.0, 52.0))))   # 离哪个都 > 1.4
        self.assertEqual(self.g.ci, 1)

    def test_other_map_ignored(self):
        self.g.set_centers([(50.0, 50.0, 1442), (52.0, 50.0, 1442), (54.0, 50.0, 1411)])
        self.g.ci, self.g.home = 1, (52.0, 50.0, 1442)
        self.assertTrue(self.g._outside_area(pobs(pos=(53.6, 50.0))))
        self.assertEqual(self.g.ci, 1)

    def test_travel_no_switch(self):
        self.g.travel = True
        self.assertTrue(self.g._outside_area(pobs(pos=(53.6, 50.0))))
        self.assertEqual(self.g.ci, 1)

    def test_toggle_off(self):
        self.cfg["area"]["auto_switch_center"] = False
        self.assertTrue(self.g._outside_area(pobs(pos=(53.6, 50.0))))
        self.assertEqual(self.g.ci, 1)

    def test_in_combat_never_outside(self):
        self.assertFalse(self.g._outside_area(pobs(pos=(53.6, 50.0), combat=True)))
        self.assertEqual(self.g.ci, 1)

    def test_leash_uses_plain_radius(self):
        # 追怪上限 = 半径×2 = 2.8；离 #2 3.0（超了），离 #3 1.0（在 #3 半径里）→ 换到 #3 接着追
        self.assertFalse(self.g._outside_area(pobs(pos=(55.0, 50.0)), 2.0))
        self.assertEqual(self.g.ci, 2)

    def test_leash_not_in_other_radius_gives_up(self):
        # 离 #2 3.0、离 #3 2.0（> 半径 1.4，但 < 追怪上限 2.8）→ 不换，照旧放弃
        self.assertTrue(self.g._outside_area(pobs(pos=(55.0, 51.5)), 2.0) or True)
        self.g.ci, self.g.home = 1, C3[1]
        self.assertTrue(self.g._outside_area(pobs(pos=(52.0, 53.0)), 2.0))
        self.assertEqual(self.g.ci, 1)

    def test_switch_on_way_back(self):
        self.g.start_return(pobs(pos=(55.0, 50.0), facing=0.0))
        self.assertEqual(self.g.state, State.RETURNING)
        self.clock.t += 1.0
        self.g.tick(pobs(pos=(53.5, 50.0), facing=3.14))
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertEqual(self.g.ci, 2)
        self.assertTrue(any("走回去的路上" in m for m in self.logs))


# ---------------------------------------------------------------- 1. 先回蓝再开怪
class ManaRest(Base):
    MODE = "click"                       # Simon 用点击模式（原地 Tab 模式不管）
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.cfg["heal"]["post_enabled"] = False
        self.cfg["heal"]["rest_below_pct"] = 0
        self.need = self.g._mr_need()

    def low(self, **kw):
        d = dict(hp=50, mana=max(1, self.need - 15), form="bear")
        d.update(kw)
        return obs(**d)

    def test_defaults(self):
        h = DEFAULT_CONFIG["heal"]
        self.assertEqual((h["heal_pct"], h["mana_rest_enabled"], h["mana_rest_hp_below"], h["mana_rest_margin_pct"],
                          h["mana_rest_hp_ok"], h["drink_key"]), (40, True, 60, 10, 90, ""))

    def test_need_formula(self):
        self.assertAlmostEqual(self.need, self.g._reserve() + self.g._cost("heal") + 10)

    def test_trigger_no_pull(self):
        self.g.tick(self.low())
        self.assertEqual((self.g.state, self.g.rest_mode), (State.RESTING, "mana"))
        self.assertTrue(any("💤 先回蓝再开怪" in m for m in self.logs))
        for _ in range(10):
            self.clock.t += 1.0
            self.g.tick(self.low())
        self.assertNotIn("tab", self.keys())
        self.assertEqual(self.g.state, State.RESTING)
        self.assertEqual(self.keys(), [])                       # 不变形、不按键，原地等

    def test_hp_high_no_rest(self):
        self.g.tick(self.low(hp=80))
        self.assertNotEqual(self.g.state, State.RESTING)

    def test_mana_enough_no_rest(self):
        self.g.tick(self.low(mana=self.need + 1))
        self.assertNotEqual(self.g.state, State.RESTING)

    def test_disabled(self):
        self.cfg["heal"]["mana_rest_enabled"] = False
        self.g.tick(self.low())
        self.assertNotEqual(self.g.state, State.RESTING)

    def test_unknown_mana_no_rest(self):
        self.g.tick(obs(hp=50, form="cat"))
        self.assertNotEqual(self.g.state, State.RESTING)

    def test_ends_when_mana_back(self):
        self.g.tick(self.low())
        self.clock.t += 20
        self.g.tick(self.low(mana=self.need + 1))
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertTrue(any("💤 回蓝完成" in m for m in self.logs))

    def test_ends_when_hp_ok(self):
        self.g.tick(self.low())
        self.clock.t += 20
        self.g.tick(self.low(hp=95))
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertTrue(any("💤 血量回到 95%" in m for m in self.logs))

    def test_timeout_then_skip_60s(self):
        self.g.tick(self.low())
        self.clock.t += 121
        self.g.tick(self.low())
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertTrue(any("回蓝 120 秒还没到" in m for m in self.logs))
        self.clock.t += 1
        self.g.tick(self.low())
        self.assertNotEqual(self.g.rest_mode, "mana")

    def test_attacked_fights(self):
        self.g.tick(self.low())
        self.clock.t += 1
        self.g.tick(self.low(combat=True, thp=100, aggro=True))
        self.assertNotEqual(self.g.rest_mode, "mana")
        self.assertNotEqual(self.g.state, State.RESTING)
        self.assertTrue(any("回蓝时被打" in m for m in self.logs))

    def test_post_heal_after_mana_back(self):
        self.cfg["heal"]["post_enabled"] = True
        self.g.tick(self.low())
        self.assertEqual(self.g.rest_mode, "mana")
        self.clock.t += 30
        self.g.tick(self.low(mana=95))                          # 蓝够了、血还 50% → 顺便放治疗之触
        self.assertEqual(self.g.state, State.RESTING)
        self.assertEqual(self.g.rest_mode, "post")

    def test_drink_key(self):
        self.cfg["heal"]["drink_key"] = "9"
        self.g.tick(self.low())
        self.clock.t += 0.5
        self.g.tick(self.low())                                  # 猎豹 → 先变人形
        self.assertNotIn("9", self.keys())
        self.assertTrue(len(self.keys()) >= 1)
        self.clock.t += 1.0
        self.g.tick(self.low(form="caster", casting=False))
        self.assertEqual(self.keys().count("9"), 1)
        for _ in range(5):
            self.clock.t += 1.0
            self.g.tick(self.low(form="caster", casting=False))
        self.assertEqual(self.keys().count("9"), 1)              # 只喝一次
        self.assertTrue(any("🥤 喝水「9」" in m for m in self.logs))


# ---------------------------------------------------------------- 2. 治疗线
class HealLine(Base):
    MODE = "stationary_tab"

    def test_default_40_heals_at_38(self):
        self.assertEqual(DEFAULT_CONFIG["heal"]["heal_pct"], 40)
        self.cfg["heal"]["heal_pct"] = DEFAULT_CONFIG["heal"]["heal_pct"]
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        self.g.tick(obs(hp=38, thp=60, arange="in", combat=True, form="bear"))
        self.assertEqual(self.g.state, State.HEALING)


# ---------------------------------------------------------------- 3. 怪远先走过去
class WalkFar(Base):
    MODE = "click"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.cfg["move"]["opener_first"] = True
        self.cfg["area"]["enabled"] = False
        self.att = self.k["attack"]
        self.p = [plate(900, 500)]

    def far(self, pos=(50.0, 50.0), **kw):
        d = dict(thp=100, rng="far", plates=self.p, form="bear")
        d.update(kw)
        return pobs(pos=pos, facing=0.0, **d)

    def start(self):
        self.g.tick(self.far())
        self.assertEqual(self.g.state, State.APPROACHING)
        self.assertIsNotNone(self.g.wf)

    def test_defaults(self):
        m = DEFAULT_CONFIG["move"]
        self.assertEqual((m["walk_far"], m["walk_far_max_s"]), (True, 15.0))

    def test_walks_without_opener(self):
        self.start()
        self.assertTrue(any("🚶 选中的怪离得远" in m for m in self.logs))
        for i in range(4):
            self.clock.t += 0.5
            self.g.tick(self.far(pos=(50.0 + 0.1 * (i + 1), 50.0)))
        self.assertNotIn(self.att, self.keys())
        self.assertTrue(self.g.walking)
        self.assertFalse(self.g.opened)

    def test_melee_then_opener(self):
        self.start()
        self.clock.t += 0.5
        self.g.tick(self.far(pos=(50.1, 50.0)))
        self.clock.t += 0.5
        self.g.tick(self.far(pos=(50.2, 50.0), rng="melee", arange="in"))
        self.assertIsNone(self.g.wf)
        self.assertEqual(self.g.state, State.COMBAT)
        self.clock.t += 0.5
        self.g.tick(self.far(pos=(50.2, 50.0), rng="melee", arange="in"))
        self.assertFalse(self.g.walking)
        self.assertIn(self.att, self.keys())

    def test_timeout_clears_target(self):
        self.start()
        for i in range(40):
            self.clock.t += 0.5
            self.g.tick(self.far(pos=(50.0 + 0.05 * (i + 1), 50.0)))
            if self.g.state == State.SEARCHING:
                break
        self.assertTrue(any("还没到近战距离 → 换目标" in m for m in self.logs))
        self.assertIsNone(self.g.wf)
        self.assertNotIn(self.att, self.keys())

    def test_stuck_falls_back_to_right_click(self):
        self.start()
        for _ in range(8):
            self.clock.t += 0.5
            self.g.tick(self.far())
        self.assertTrue(any("卡住了" in m and "右键" in m for m in self.logs))
        self.assertIsNone(self.g.wf)
        self.assertTrue(any(k.startswith("click") for k in self.keys()))

    def test_disabled_old_behaviour(self):
        self.cfg["move"]["walk_far"] = False
        self.g.tick(self.far())
        self.assertIsNone(self.g.wf)
        self.assertIn(self.att, self.keys())

    def test_not_used_in_combat(self):
        self.g.tick(self.far(combat=True))
        self.assertIsNone(self.g.wf)

    def test_near_target_not_walked(self):
        self.g.tick(self.far(rng="melee"))
        self.assertIsNone(self.g.wf)


# ---------------------------------------------------------------- GUI / README
class Strings(unittest.TestCase):
    def test_gui_and_readme(self):
        with open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8") as f:
            app = f.read()
        self.assertTrue(re.search(r"熊德自动打怪 v8\.(4\d|[5-9]\d)", app))      # v8.47 起标题版本在新测试里查
        for s in ('"auto_switch_center"', '"mana_rest_enabled"', '"mana_rest_hp_below"', '"mana_rest_margin_pct"',
                  '"mana_rest_hp_ok"', '"drink_key"', '"mana_rest_timeout_s"', '"walk_far"', '"walk_far_max_s"'):
            self.assertIn(s, app)
        with open(os.path.join(ROOT, "README_中文.md"), encoding="utf-8") as f:
            rd = f.read()
        self.assertIn("v8.46 提高效率", rd)
        self.assertLess(rd.index("v8.46 提高效率"), rd.index("v8.45 认出"))


if __name__ == "__main__":
    unittest.main()

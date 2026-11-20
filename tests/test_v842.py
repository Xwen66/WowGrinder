"""v8.42（t178u 选项 a）：治疗时多留蓝，保证治完能变回猎豹。
19:07 实况：第 2 个治疗之触按 5 秒超时“结束”时游戏里还在读（蓝还没扣，显示 62%），bot 以为还剩 62%、治疗之触约 15%
（实测时把读条期间回的蓝算进去了，测低了）→ 放了第 3 个 → 62% → 42% → 27%，变猎豹要 ~29%，卡在人形一直按 z。
现在：① 实测耗蓝 = 读条中最高法力 − 读完后；按最近 3 次里最贵的算；② 超时结束但还在读 → 那个的蓝算“还没扣”；
③ 治疗后法力要 ≥ 变身预留 + 余量（默认 3%）；④ 蓝不够变身时不连按变身键，等回蓝（每 5 秒试一次）。"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SimonBase(Base):
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.cfg["cat"]["combat_form"] = "猎豹"
        self.cfg["heal"].update(reserve_cat_pct=30, reserve_bear_pct=30, heal_cost_pct=20, heal_to_pct=90,
                                heal_pct=30)
        self.k.update({"cat_form": "z", "bear_form": "q", "cancel_form": "v", "healing_touch": "e"})
        self.g.mana_cost["shift_cat"] = 29.0          # 当时实测变猎豹 ~29% → 预留 31%

    def t(self, dt=0.3, **kw):
        kw.setdefault("combat", True)
        kw.setdefault("thp", 100)
        self.clock.t += dt
        self.g.tick(obs(**kw))

    def engage(self):
        self.g.tick(obs(thp=100, arange="in", form="cat", mana=69))
        self.t(thp=100, form="cat", mana=69, arange="in", aggro=True)
        self.inp.history.clear()

    def first_heal(self):
        """第 1 个：69% 开始，读条中回到 77%，读完 57%（真实耗蓝 20%，旧算法测成 14%）。"""
        self.t(hp=28, form="cat", mana=69)
        self.assertEqual(self.g.state, State.HEALING)
        self.t(hp=28, form="caster", casting=False, mana=69)
        self.t(hp=28, form="caster", casting=False, mana=70)
        for m in (71, 73, 75, 77):
            self.t(dt=0.7, hp=28, form="caster", casting=True, mana=m)
        self.t(hp=59, form="caster", casting=False, mana=57)


class TestRegression1907(SimonBase):
    def test_no_third_heal_when_second_still_casting(self):
        self.engage()
        self.first_heal()
        self.assertEqual(self.g._cost("heal"), 20)                     # 77 → 57
        self.assertEqual(self.g.state, State.HEALING)                  # 57 − 20 = 37 ≥ 31 + 3 → 放第 2 个
        self.t(hp=59, form="caster", casting=False, mana=57)
        for m in (57, 57, 58, 58, 59, 62):
            self.t(dt=1.0, hp=45, form="caster", casting=True, mana=m)
        n_e = self.keys().count("e")
        for _ in range(3):                                            # 读条超时（游戏里还在读，蓝还没扣）
            self.t(dt=0.5, hp=37, form="caster", casting=True, mana=62)
            if self.g.state != State.HEALING:
                break
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertEqual(self.keys().count("e"), n_e)                  # 没放第 3 个
        self.assertTrue(any(m.startswith("💧 留蓝变猎豹") and "不再治疗" in m for m in self.logs))
        self.assertTrue(any("还在读" in m and "还没扣" in m for m in self.logs))
        # 第 2 个读完：62 → 42，蓝够变猎豹 → 按 z
        self.inp.history.clear()
        self.t(hp=72, form="caster", casting=False, mana=42)
        self.t(hp=72, form="caster", casting=False, mana=42)
        self.assertIn("z", self.keys())

    def test_learned_cost_uses_peak(self):
        self.engage()
        self.first_heal()
        self.assertEqual(self.g.mana_cost["heal"], 20)


class TestShiftWait(SimonBase):
    def test_no_z_spam_when_mana_too_low(self):
        self.engage()
        self.g.state = State.COMBAT
        self.g._enter_combat(self.clock.t)
        self.inp.history.clear()
        for _ in range(30):                                           # 10 秒，法力 27% < 29%
            self.t(hp=85, form="caster", mana=27, arange="in", aggro=True)
        self.assertLessEqual(self.keys().count("z"), 3)
        self.assertTrue(any("不够变猎豹" in m for m in self.logs))
        self.inp.history.clear()
        self.t(hp=85, form="caster", mana=30, arange="in", aggro=True)
        self.t(hp=85, form="caster", mana=30, arange="in", aggro=True)
        self.assertIn("z", self.keys())                               # 蓝够了马上变


class TestMargin(SimonBase):
    def test_margin_blocks_heal_and_configurable(self):
        self.engage()
        self.g.mana_cost.pop("shift_cat")                             # 预留 = 设置 30
        o = obs(hp=25, thp=100, combat=True, form="cat", mana=52)     # 52 − 20 = 32 < 30 + 3
        self.assertIn("变猎豹需要 33%", self.g._mana_short(o, "heal"))
        self.cfg["heal"]["reserve_margin_pct"] = 0
        self.assertIsNone(self.g._mana_short(o, "heal"))

    def test_combat_start_heal_skipped_logs_droplet(self):
        self.engage()
        self.t(hp=25, form="cat", mana=50, arange="in", aggro=True)
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertTrue(any(m.startswith("💧 留蓝变猎豹") for m in self.logs))

    def test_default(self):
        self.assertEqual(DEFAULT_CONFIG["heal"]["reserve_margin_pct"], 3)


class TestStrings(unittest.TestCase):
    def test_gui_readme(self):
        with open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertTrue(re.search(r"熊德自动打怪 v8\.(4[2-9]|[5-9]\d)", src))
        self.assertIn('"reserve_margin_pct"', src)
        self.assertIn("变身余量(%)（v8.42）", src)
        with open(os.path.join(ROOT, "README_中文.md"), encoding="utf-8") as f:
            self.assertTrue("v8.42" in f.read())


if __name__ == "__main__":
    unittest.main()

"""v8.41（t178u）：死了没跑尸 —— 原因是 bot 先报“目标血量 25.0 秒没有下降”自动停止了，停了以后被怪打死。
① 战斗中治疗的时间不再算进这 25 秒；② 战斗补救开着（默认）时不再停止，只提醒、重新计时（README 一直写“关闭战斗补救时”才停）。"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class StallBase(Base):
    def start_fight(self):
        self.g.tick(obs(thp=100))
        self.g.tick(obs(thp=95, combat=True, form="bear", rng="melee"))
        self.assertEqual(self.g.state, State.COMBAT)

    def hold(self, secs, **kw):
        kw.setdefault("form", "bear")
        for _ in range(int(secs)):
            self.clock.t += 1
            self.g.tick(obs(thp=95, combat=True, **kw))
            if self.g.stopped:
                break


class TestStallClick(StallBase):
    def test_recovery_on_never_stops(self):
        self.start_fight()
        self.hold(80, rng="melee")
        self.assertFalse(self.g.stopped)          # 补救会换怪/继续打，不停

    def test_stuck_in_caster_no_mana_never_stops(self):
        """19:07 的情况：治疗完蓝不够变猎豹，一直在人形按变身键，目标不掉血 → 以前 25 秒后停 bot。"""
        self.start_fight()
        self.inp.history.clear()
        self.hold(60, form="caster", rng="melee", mana=27)
        self.assertFalse(self.g.stopped)
        self.assertIn(self.k["bear_form"], self.keys())       # 还在试着变回战斗形态
        self.assertEqual(self.g.state, State.COMBAT)
        self.assertTrue(any("秒没有下降" in m and "不停止" in m for m in self.logs))

    def test_recovery_off_still_stops(self):
        self.cfg["recover"]["enabled"] = False
        self.start_fight()
        self.hold(40, rng="melee")
        self.assertFalse(self.g.stopped)                       # v8.43：补救关着也不停
        self.assertTrue(any("没有下降" in m and "不停止" in m for m in self.logs))

    def test_heal_time_not_counted(self):
        self.cfg["recover"]["enabled"] = False
        self.start_fight()
        self.t_fight = self.clock.t
        self.hold(10, rng="melee")
        self.assertFalse(self.g.stopped)
        # 战斗治疗：连放几个治疗之触，共约 18 秒（以前这段时间也算进 25 秒）
        self.cfg["heal"]["heal_to_pct"] = 90
        self.g.tick(obs(hp=25, thp=95, combat=True, form="bear"))
        self.assertEqual(self.g.state, State.HEALING)
        t_end = self.clock.t + 18
        while self.clock.t < t_end and self.g.state == State.HEALING:
            self.clock.t += 0.3
            self.g.tick(obs(hp=40, thp=95, combat=True, form="caster", casting=False, mana=100))
            for _ in range(10):
                self.clock.t += 0.3
                self.g.tick(obs(hp=40, thp=95, combat=True, form="caster", casting=True, mana=100))
        self.cfg["heal"]["heal_to_pct"] = 0
        for _ in range(40):
            if self.g.state != State.HEALING:
                break
            self.clock.t += 0.5
            self.g.tick(obs(hp=95, thp=95, combat=True, form="caster", casting=False, mana=100))
        self.assertGreater(self.clock.t - self.t_fight, 25)      # 开打到治疗完已超过 25 秒
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertFalse(self.g.stopped)
        self.hold(15, rng="melee")                             # 治疗完 15 秒：10 + 治疗 + 15 > 25，但不该停
        self.assertFalse(self.g.stopped)
        self.hold(20, rng="melee")                             # 治疗完 35 秒没掉血 → 提醒（v8.43 起不停）
        self.assertFalse(self.g.stopped)
        self.assertTrue(any("没有下降" in m and "不停止" in m for m in self.logs))


class TestStallStationary(StallBase):
    MODE = "stationary_tab"

    def test_recovery_on_never_stops(self):
        self.g.tick(obs(thp=100))
        self.g.tick(obs(thp=95, combat=True, form="bear", rng="melee"))
        self.hold(80, rng="melee")
        self.assertFalse(self.g.stopped)

    def test_stuck_in_caster_never_stops(self):
        self.g.tick(obs(thp=100))
        self.g.tick(obs(thp=95, combat=True, form="bear", rng="melee"))
        self.hold(60, form="caster", rng="melee", mana=27)
        self.assertFalse(self.g.stopped)


class TestStrings(unittest.TestCase):
    def test_title_readme(self):
        with open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertTrue(re.search(r"熊德自动打怪 v8\.(4[1-9]|[5-9]\d)", src))
        self.assertIn("v8.43 起不停止", src)
        with open(os.path.join(ROOT, "README_中文.md"), encoding="utf-8") as f:
            rd = f.read()
        self.assertIn("v8.41", rd)


if __name__ == "__main__":
    unittest.main()

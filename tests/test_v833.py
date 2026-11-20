"""v8.33（t163u）：快死不停止（开着跑尸时继续打）、预留变身蓝不变、治疗日志修正、
巡逻卡住 3 次不停止、多中心来回走 1→2→3→2→1→2…"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402
from test_v8_heal_log import TestV823PatrolClick, TestV825PatrolStuck, pobs  # noqa: E402


def dobs(info=True, **kw):
    o = obs(**kw)
    o.death_info = info
    return o


class NearDeath(Base):
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.cfg.setdefault("corpse", {})["enabled"] = True
        self.g.tick(obs(thp=100, arange="in"))
        self.g.fight_heals = 1
        self.g.last_heal_time = self.clock.t
        self.inp.history.clear()

    def low(self, info=True, n=1):
        for _ in range(n):
            self.clock.t += 0.5
            self.g.tick(dobs(info=info, hp=10, thp=60, combat=True, arange="in", aggro=True))

    def test_corpse_on_keeps_fighting_logs_once_no_beep(self):
        self.low(n=4)
        self.assertFalse(self.g.stopped)
        self.assertNotEqual(self.g.state, State.STOPPED)
        self.assertEqual(sum("很危险 → 继续打；死了自动跑尸" in m for m in self.logs), 1)
        self.assertEqual(self.beeps, [])

    def test_logs_again_next_fight(self):
        self.low()
        self.clock.t += 1
        self.g.state = State.SEARCHING
        self.g.tick(dobs(hp=100))                      # 脱战 → 新的一场
        self.g.fight_heals = 1
        self.low()
        self.assertEqual(sum("很危险" in m for m in self.logs), 2)

    def test_corpse_off_still_stops(self):
        self.cfg["corpse"]["enabled"] = False
        self.low()
        self.assertTrue(self.g.stopped)
        self.assertEqual(self.g.state, State.STOPPED)

    def test_old_addon_still_stops(self):
        self.low(info=False)
        self.assertTrue(self.g.stopped)


class ReserveV833(Base):
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.cfg.setdefault("corpse", {})["enabled"] = True
        self.g._has_plate_templates = lambda now: False
        self.cfg["cat"]["combat_form"] = "猎豹"
        self.cfg["heal"].update(reserve_cat_pct=30, reserve_bear_pct=30, heal_cost_pct=20, heal_to_pct=90)
        self.k.update({"cat_form": "f2", "cat_builder": "7", "cat_finisher": "8", "bear_form": "z", "cancel_form": "z"})
        self.g.tick(obs(thp=100, arange="in", form="cat"))
        self.inp.history.clear()

    def hit(self, info=True, hp=10):
        self.clock.t += 0.5
        self.g.tick(dobs(info=info, hp=hp, thp=60, combat=True, form="cat", mana=29, arange="in", aggro=True))

    def test_reserve_kept_below_critical(self):
        self.hit()
        self.assertNotIn(self.k["healing_touch"], self.keys())
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertTrue(any("预留" in m for m in self.logs))

    def test_no_misleading_cooldown_line(self):
        self.hit(hp=22)
        self.hit(hp=20)
        self.assertFalse(any("离上次治疗才" in m for m in self.logs), self.logs)

    def test_no_beep_with_corpse_run(self):
        self.hit()
        self.assertEqual(self.beeps, [])
        self.assertTrue(any("死了自动跑尸" in m for m in self.logs))

    def test_beep_without_corpse_run(self):
        self.cfg["corpse"]["enabled"] = False
        self.hit()
        self.assertEqual(self.beeps, [1])

    def test_cooldown_line_still_when_really_on_cooldown(self):
        self.g.last_heal_time = self.clock.t
        self.g.tick(dobs(hp=20, thp=60, combat=True, form="cat", mana=90, arange="in", aggro=True))
        self.assertTrue(any("离上次治疗才" in m for m in self.logs))


class PingPong(TestV823PatrolClick):
    def seq(self, n, map_id=1442):
        out = []
        for _ in range(n):
            nxt = self.g._next_center(pobs(pos=(50.0, 50.0), map_id=map_id))
            if nxt is None:
                out.append(None)
                break
            self.g.ci = nxt[0]
            out.append(nxt[0])
        return out

    def test_three_centers(self):
        self.g.ci, self.g.cdir = 0, 1
        self.assertEqual(self.seq(8), [1, 2, 1, 0, 1, 2, 1, 0])

    def test_skip_off_map(self):
        self.g.set_centers([(50.0, 50.0, 1442), (51.0, 50.0, 1442), (52.0, 50.0, 9), (53.0, 50.0, 1442)])
        self.g.ci, self.g.cdir = 0, 1
        self.assertEqual(self.seq(6), [1, 3, 1, 0, 1, 3])

    def test_off_map_at_end_flips(self):
        self.g.set_centers([(50.0, 50.0, 1442), (51.0, 50.0, 1442), (52.0, 50.0, 9)])
        self.g.ci, self.g.cdir = 0, 1
        self.assertEqual(self.seq(4), [1, 0, 1, 0])

    def test_two_and_one(self):
        self.g.set_centers(self.C[:2])
        self.g.ci = 0
        self.assertEqual(self.seq(4), [1, 0, 1, 0])
        self.g.set_centers(self.C[:1])
        self.assertEqual(self.seq(1), [None])

    def test_start_middle_goes_up(self):
        self.tick((52.0, 50.0))
        self.assertEqual((self.g.ci, self.g.cdir), (1, 1))
        self.spin((52.0, 50.0))
        self.assertEqual(self.g.ci, 2)

    def test_start_first_goes_up(self):
        self.tick((50.0, 50.0))
        self.assertEqual((self.g.ci, self.g.cdir), (0, 1))

    def test_start_last_goes_down(self):
        self.tick((54.0, 50.0))
        self.assertEqual((self.g.ci, self.g.cdir), (2, -1))
        self.assertTrue(any("来回走" in m for m in self.logs))

    def test_f8_resets(self):
        self.tick((54.0, 50.0))
        self.g.request_area("home")
        self.tick((54.0, 50.0))
        self.assertEqual((self.g.ci, self.g.cdir), (0, 1))

    def test_f7_keeps_direction(self):
        self.tick((54.0, 50.0))
        self.g.request_area("add")
        self.tick((55.0, 50.0))
        self.assertEqual(len(self.g.centers), 4)
        self.assertEqual(self.g.cdir, -1)

    def test_set_centers_clamps(self):
        self.g.ci, self.g.cdir = 2, -1
        self.g.set_centers(self.C[:2])
        self.assertEqual(self.g.ci, 1)
        self.assertIn(self.g.cdir, (1, -1))
        self.g.ci = 0
        self.g.set_centers(self.C)
        self.assertEqual((self.g.ci, self.g.cdir), (0, 1))


class StuckV833(TestV825PatrolStuck):
    def test_stuck_goes_on_same_direction(self):
        self.g.set_centers(TestV823PatrolClick.C)
        self.tick((50.0, 50.0))
        self.spin((50.0, 50.0))
        self.assertEqual((self.g.ci, self.g.travel, self.g.cdir), (1, True, 1))
        ok = self.stuck_until((51.0, 50.0), lambda: self.g.ci == 2)
        self.assertTrue(ok, self.logs[-5:])
        self.assertFalse(self.g.stopped)
        self.assertTrue(any("照原方向改去中心 #3/3" in m for m in self.logs))

    def test_one_center_stuck_searches_then_blocks_return(self):
        self.g.set_centers([])
        self.cfg["area"].update(center_x=50.0, center_y=50.0, map_id=1442, home_on_start=False)
        self.g.home = (50.0, 50.0, 1442)
        self.g.travel = False
        self.g.start_return(pobs(pos=(52.0, 50.0), facing=90.0))
        ok = self.stuck_until((52.0, 50.0), lambda: self.g.state == State.SEARCHING)
        self.assertTrue(ok)
        self.assertFalse(self.g.stopped)
        self.assertGreater(self.g.return_block_until, self.clock.t + 50)
        for _ in range(5):                                 # 60 秒内不再走回中心
            self.clock.t += 1
            self.g.tick(pobs(pos=(52.0, 50.0), facing=90.0))
            self.assertNotEqual(self.g.state, State.RETURNING)


# 只跑本文件新加的测试（继承来的旧测试在原文件里已经跑过）
for _cls, _base in ((PingPong, TestV823PatrolClick), (StuckV833, TestV825PatrolStuck)):
    for _n in dir(_base):
        if _n.startswith("test_") and _n not in _cls.__dict__:
            setattr(_cls, _n, None)


if __name__ == "__main__":
    unittest.main()

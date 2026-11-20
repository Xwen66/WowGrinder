import copy
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.navigation import angle_diff, bearing_to, distance, forward_step, turn_command  # noqa: E402
from bot.state_machine import Grinder, State  # noqa: E402
from tests.test_state_machine import Clock, FakeInput, obs  # noqa: E402

N, W, S, E = 0.0, math.pi / 2, math.pi, 3 * math.pi / 2


class TestGeometry(unittest.TestCase):
    def test_bearing_cardinals(self):
        c = (50, 50)
        self.assertAlmostEqual(bearing_to(c, (50, 40)), N)        # y 变小 = 北
        self.assertAlmostEqual(bearing_to(c, (40, 50)), W)        # x 变小 = 西
        self.assertAlmostEqual(bearing_to(c, (50, 60)), S)
        self.assertAlmostEqual(bearing_to(c, (60, 50)), E)
        self.assertAlmostEqual(bearing_to(c, (40, 40)), math.pi / 4)  # 西北

    def test_forward_step_matches_bearing(self):
        for f in (N, W, S, E, 1.0, 4.0):
            p = forward_step((50, 50), f, 1.0)
            self.assertAlmostEqual(bearing_to((50, 50), p), f % (2 * math.pi), places=6)

    def test_angle_diff_wrap(self):
        self.assertAlmostEqual(angle_diff(0.1, 2 * math.pi - 0.1), 0.2)
        self.assertAlmostEqual(angle_diff(2 * math.pi - 0.1, 0.1), -0.2)
        self.assertAlmostEqual(angle_diff(S, N), math.pi)

    def test_turn_command(self):
        self.assertEqual(turn_command(math.pi / 2, 180), ("left", 500.0))
        side, ms = turn_command(-math.pi / 4, 180)
        self.assertEqual((side, round(ms)), ("right", 250))
        self.assertEqual(turn_command(math.pi, 90, max_ms=1500)[1], 1500)

    def test_distance(self):
        self.assertAlmostEqual(distance((0, 0), (3, 4)), 5)


class World:
    """简单的游戏模拟：转向键按住时按真实转速转，按住 W 时按速度前进。"""

    def __init__(self, pos, facing, true_rate_dps=150, speed=0.7, blocked=False):
        self.pos, self.facing = pos, facing
        self.rate = math.radians(true_rate_dps)
        self.speed = speed          # 地图单位/秒
        self.blocked = blocked
        self.walking = False


class SimInput(FakeInput):
    def __init__(self, cfg, clock, world):
        super().__init__(cfg, clock)
        self.world = world

    def _advance(self, dt):
        w = self.world
        if "w" in self.held and not w.blocked:
            w.pos = forward_step(w.pos, w.facing, w.speed * dt)

    def sleep(self, seconds):
        super().sleep(seconds)
        self._advance(seconds)

    def press(self, key, hold_ms=None, delay=True):
        w = self.world
        if hold_ms:
            if key == "left":
                w.facing = (w.facing + w.rate * hold_ms / 1000) % (2 * math.pi)
            elif key == "right":
                w.facing = (w.facing - w.rate * hold_ms / 1000) % (2 * math.pi)
            self._advance(hold_ms / 1000)
            self.clock.t += hold_ms / 1000
        super().press(key, hold_ms=hold_ms, delay=delay)


class TestReturnToCenter(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(DEFAULT_CONFIG)
        self.cfg["move"]["mode"] = "click"   # 区域回中心只在点击移动模式下
        a = self.cfg["area"]
        a.update(enabled=True, center_x=50, center_y=50, radius=3, map_id=1423, home_on_start=False)
        self.clock = Clock()

    def run_sim(self, world, ticks=600, combat_at=None, map_id=1423):
        inp = SimInput(self.cfg, self.clock, world)
        logs = []
        g = Grinder(self.cfg, inp, log=logs.append, clock=self.clock)
        states = []
        for i in range(ticks):
            o = obs(hp=100, combat=(combat_at is not None and i >= combat_at))
            o.pos, o.facing, o.map_id = world.pos, world.facing, map_id
            g.tick(o)
            states.append(g.state)
            if g.stopped or (i > 0 and g.state == State.SEARCHING and states[-2] == State.RETURNING):
                break
            inp.sleep(0.15)
        return g, inp, logs, states

    def test_walks_back_with_wrong_turn_rate(self):
        # 起点在中心东南，背对中心；真实转速 150°/s，配置 180°/s → 靠反复读朝向修正
        w = World(pos=(56.0, 55.0), facing=S)
        g, inp, logs, states = self.run_sim(w)
        self.assertIn(State.RETURNING, states)
        self.assertEqual(g.state, State.SEARCHING, logs)
        self.assertLessEqual(distance(w.pos, (50, 50)), 1.5 + 0.2)
        self.assertNotIn("w", inp.held)          # 到达后松开 W
        self.assertIn("down:w", inp.history)

    def test_inside_radius_no_return(self):
        w = World(pos=(51.0, 50.5), facing=N)
        g, inp, logs, states = self.run_sim(w, ticks=3)
        self.assertNotIn(State.RETURNING, states)

    def test_combat_interrupts_return(self):
        w = World(pos=(60.0, 50.0), facing=N)
        g, inp, logs, states = self.run_sim(w, ticks=12, combat_at=8)
        self.assertNotIn("w", inp.held)
        self.assertNotEqual(g.state, State.RETURNING)
        self.assertTrue(any("被打" in m for m in logs))

    def test_stuck_jump_then_search_here(self):                    # v8.33：卡住不再停止 → 就地找怪
        w = World(pos=(56.0, 50.0), facing=E + math.pi, blocked=True)  # 面向西（朝中心）但走不动
        g, inp, logs, states = self.run_sim(w, ticks=400)
        self.assertNotEqual(g.state, State.STOPPED)
        self.assertTrue(any("就地找怪" in m for m in logs), logs[-5:])
        self.assertGreaterEqual(inp.history.count("space"), 2)

    def test_wrong_map_disables(self):
        w = World(pos=(70.0, 70.0), facing=N)
        g, inp, logs, states = self.run_sim(w, ticks=2, map_id=999)
        self.assertNotIn(State.RETURNING, states)
        self.assertTrue(any("地图" in m for m in logs))

    def test_no_position_disables(self):
        inp = FakeInput(self.cfg, self.clock)
        logs = []
        g = Grinder(self.cfg, inp, log=logs.append, clock=self.clock)
        g.tick(obs(hp=100))          # pos=None
        self.assertNotEqual(g.state, State.RETURNING)
        self.assertTrue(any("读不到坐标" in m for m in logs))

    def test_target_cleared_before_return(self):
        inp = FakeInput(self.cfg, self.clock)
        g = Grinder(self.cfg, inp, log=lambda *_: None, clock=self.clock)
        o = obs(hp=100, thp=100)
        o.pos, o.facing, o.map_id = (60, 60), N, 1423
        g.tick(o)
        self.assertEqual(g.state, State.RETURNING)
        self.assertEqual(inp.history, [inp.cfg["keys"]["clear_target"]])

    def test_disabled_by_default(self):
        self.assertFalse(DEFAULT_CONFIG["area"]["enabled"])


if __name__ == "__main__":
    unittest.main()

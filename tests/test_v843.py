"""v8.43（t180u）：去掉 5 种自动停止 —— 出错太多、死亡、治疗后血太低、目标血量 25 秒不掉、鼠标甩到左上角。
只有 F12 / 停止按钮 / 关窗口能停。"""
import copy
import os
import re
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.input import StopRequested  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402
from test_v831 import dobs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_bot(on_observe):
    """跑真的 BotRunner._run（假截图/识别/按键）。on_observe(n, runner) 抛异常 = 出错；返回 obs。"""
    import bot.runner as R
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["log"]["enabled"] = False
    cfg["misc"]["capture_delay_s"] = 0
    cfg["misc"]["window_check"] = False
    logs, slept, calls = [], [], {"n": 0}
    box = {}

    class Cap:
        monitor = {"left": 0, "top": 0}

        def __init__(self, *_):
            pass

        def grab(self):
            return None

        def close(self):
            pass

    class Det:
        def __init__(self, *_):
            pass

        def observe(self, frame, want_nameplates=True):
            calls["n"] += 1
            return on_observe(calls["n"], box["runner"])

        def fresh_plates(self, *_):
            return []

    class Inp:
        def __init__(self, cfg, stop_event, log):
            self.stop_event = stop_event

        def sleep(self, s):
            if self.stop_event.is_set():
                raise StopRequested()
            slept.append(s)

        def release_all(self):
            pass

        def key_up(self, *_):
            pass

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return lambda *a, **k: 1e9

    saved = (R.ScreenCapture, R.Detector, R.InputController)
    R.ScreenCapture, R.Detector, R.InputController = Cap, Det, Inp
    try:
        runner = R.BotRunner(cfg, logs.append)
        box["runner"] = runner
        runner._run()
    finally:
        R.ScreenCapture, R.Detector, R.InputController = saved
    return logs, slept, calls["n"]


class TestErrors(unittest.TestCase):
    def test_backoff_values(self):
        from bot.runner import err_backoff
        self.assertEqual([err_backoff(i) for i in range(1, 8)], [2, 4, 8, 16, 30, 30, 30])

    def test_never_stops_on_errors_with_backoff(self):
        def ob(n, runner):
            if n <= 40:
                raise RuntimeError(f"坏帧 {n}")
            runner.stop()                                  # 相当于按 F12
            return obs(hp=100)
        logs, slept, n = run_bot(ob)
        self.assertEqual(n, 41)
        self.assertEqual(sum("不会停止" in m for m in logs), 40)
        self.assertFalse(any("出错停止" in m for m in logs))
        waits = [s for s in slept if s >= 2]
        self.assertEqual(waits[:6], [2, 4, 8, 16, 30, 30])
        self.assertLessEqual(max(waits), 30)

    def test_f12_stop_still_stops(self):
        def ob(n, runner):
            if n == 5:
                runner.stop()
            return obs(hp=100)
        logs, slept, n = run_bot(ob)
        self.assertEqual(n, 5)
        self.assertTrue(any(m.startswith("已停止") for m in logs))


class TestFailsafe(unittest.TestCase):
    def test_failsafe_always_off(self):
        from bot.input import InputController
        import threading
        fake = types.ModuleType("pyautogui")
        fake.FAILSAFE = True
        saved = sys.modules.get("pyautogui")
        sys.modules["pyautogui"] = fake
        try:
            cfg = copy.deepcopy(DEFAULT_CONFIG)
            cfg["safety"]["failsafe"] = True
            inp = InputController(cfg, threading.Event(), log=lambda *_: None)
            inp._pg = None
            inp.pg
            self.assertFalse(fake.FAILSAFE)
        finally:
            if saved is None:
                sys.modules.pop("pyautogui", None)
            else:
                sys.modules["pyautogui"] = saved


class TestDeath(Base):
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.cfg["corpse"]["enabled"] = False

    def test_dead_waits_without_keys_then_continues(self):
        self.g.tick(obs(hp=100))
        self.inp.history.clear()
        for _ in range(20):
            self.clock.t += 0.5
            self.g.tick(dobs(dead=True, hp=0))
        self.assertFalse(self.g.stopped)
        self.assertEqual(self.keys(), [])
        self.assertEqual(sum("死亡（跑尸关着）→ 不停止" in m for m in self.logs), 1)
        self.clock.t += 0.5
        self.g.tick(obs(hp=100))
        self.assertTrue(any("活过来了" in m for m in self.logs))
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertFalse(self.g.dead_idle)
        # 第二次死亡再写一次
        self.clock.t += 0.5
        self.g.tick(dobs(dead=True, hp=0))
        self.assertEqual(sum("死亡（跑尸关着）→ 不停止" in m for m in self.logs), 2)

    def test_zero_hp_no_stop(self):
        for _ in range(10):
            self.clock.t += 0.5
            self.g.tick(obs(hp=0))
        self.assertFalse(self.g.stopped)


class TestDanger(Base):
    def test_critical_after_heal_no_stop_warn_once(self):
        self.cfg["corpse"]["enabled"] = False
        self.g.tick(obs(thp=100))
        self.g.tick(obs(thp=95, combat=True, form="bear", rng="melee"))
        self.run_heal(hp=25)
        for _ in range(5):
            self.clock.t += 0.5
            self.g.tick(obs(hp=10, thp=70, combat=True, form="bear"))
        self.assertFalse(self.g.stopped)
        self.assertEqual(sum("治疗过后" in m and "不停止" in m for m in self.logs), 1)
        self.assertEqual(self.beeps, [1])


class TestStall(Base):
    def test_stall_never_stops_even_recovery_off(self):
        self.cfg["recover"]["enabled"] = False
        self.g.tick(obs(thp=100))
        self.g.tick(obs(thp=95, combat=True, form="bear", rng="melee"))
        for _ in range(80):
            self.clock.t += 1
            self.g.tick(obs(thp=95, combat=True, form="bear", rng="melee"))
        self.assertFalse(self.g.stopped)
        self.assertGreaterEqual(sum("没有下降" in m and "不停止" in m for m in self.logs), 2)


class TestStrings(unittest.TestCase):
    def test_gui_readme(self):
        with open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertTrue(re.search(r"熊德自动打怪 v8\.(4[3-9]|[5-9]\d)", src))
        self.assertNotIn("死亡时自动停止", src)
        self.assertNotIn("鼠标甩到左上角急停", src)
        with open(os.path.join(ROOT, "README_中文.md"), encoding="utf-8") as f:
            rd = f.read()
        self.assertTrue("v8.43 去掉自动停止" in rd and "七、怎么停" in rd)


if __name__ == "__main__":
    unittest.main()

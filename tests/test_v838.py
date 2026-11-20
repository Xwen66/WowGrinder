"""v8.38（t170u）：长时间不间断打怪。
1. 战斗形态=猎豹时，判断同时被多只怪打 → 临时变熊抗（重殴键），只剩 1 只/脱战 → 变回猎豹（GUI 开关，默认开）；
2. 回中心 / 去中心超时 → 不停止，照原方向去下一个中心（只有 1 个中心 → 就地找怪，60 秒后再回）；
3. 跑尸卡住 → 后退 + 越绕越大（60/90/120/150/180°，记住走不通的朝向），不再因为超时停止；
4. 程序出错 → 记日志、等 2 秒继续，10 分钟内超过 5 次才停止。"""
import copy
import math
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG, _merge, _migrate  # noqa: E402
from bot.corpse import DETOURS  # noqa: E402
from bot.input import StopRequested  # noqa: E402
from bot.multibear import MB_DEFAULTS  # noqa: E402
from bot.navigation import distance  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import obs  # noqa: E402
from test_v830 import PostBase  # noqa: E402
import test_v831 as V831  # noqa: E402
import test_v8_heal_log as H  # noqa: E402

dobs, pobs = V831.dobs, H.pobs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- 1. 多只怪 → 变熊
class MultiBear(PostBase):
    def setUp(self):
        super().setUp()
        self.k.update(bear_form="q", cancel_form="v")

    def tk(self, hp, thp=80, combat=True, form="cat", mana=90, aggro=True, dt=0.5, kind=None, **kw):
        self.clock.t += dt
        self.g.tick(obs(hp=hp, thp=thp, combat=combat, form=form, mana=mana, rng="melee", aggro=aggro,
                        kind=kind, **kw))

    def start_fight(self):
        self.g.tick(obs(hp=100, thp=100, form="cat", mana=90))
        self.tk(100, thp=95)

    def test_defaults(self):
        mb = DEFAULT_CONFIG["multi_bear"]
        self.assertTrue(mb["enabled"])
        self.assertEqual((mb["fast_drop_pct"], mb["maul_key"], mb["min_mana_pct"]), (20, "1", 40))
        self.assertEqual(set(MB_DEFAULTS), set(mb))
        self.assertEqual(DEFAULT_CONFIG["heal"]["heal_pct"], 30)          # 治疗线不变

    def test_fast_drop_shifts_to_bear(self):
        self.start_fight()
        for hp in (92, 85, 78):
            self.tk(hp)
        self.assertTrue(self.g.mb_active)
        self.assertIn("q", self.keys())
        self.assertNotIn("v", self.keys())
        self.assertTrue(any("🐻 同时被多只怪打" in m and "掉血" in m for m in self.logs))
        self.assertEqual(self.g._cform(), "bear")
        # 熊形态下的循环：按重殴键 1，不按猎豹终结技 2
        self.inp.history.clear()
        for _ in range(6):
            self.tk(76, form="bear")
        self.assertIn("1", self.keys())
        self.assertNotIn("2", self.keys())
        self.assertNotIn("z", self.keys())

    def test_single_mob_slow_drop_stays_cat(self):
        self.start_fight()
        hp = 100
        for _ in range(30):
            hp -= 1.0                                     # 0.5 秒掉 1% → 5 秒 10%
            self.tk(hp)
        self.assertFalse(self.g.mb_active)
        self.assertNotIn("q", self.keys())

    def test_other_mob_hitting_me(self):
        self.start_fight()
        self.tk(97, aggro=False)                          # 选中的怪没在打我，但我在掉血
        self.tk(96, aggro=False)
        self.assertTrue(self.g.mb_active)
        self.assertTrue(any("有别的怪在打我" in m for m in self.logs))

    def test_disabled_or_bear_mode(self):
        self.cfg["multi_bear"]["enabled"] = False
        self.start_fight()
        for hp in (90, 80, 70):
            self.tk(hp)
        self.assertFalse(self.g.mb_active)
        self.assertNotIn("q", self.keys())

    def test_bear_combat_form_ignored(self):
        self.cfg["cat"]["combat_form"] = "熊"
        self.g.tick(obs(hp=100, thp=100, form="bear", mana=90))
        for hp in (90, 80, 70):
            self.tk(hp, form="bear")
        self.assertFalse(self.g.mb_active)

    def test_low_mana_no_bear(self):
        self.start_fight()
        for hp in (90, 80, 70):
            self.tk(hp, mana=30)
        self.assertFalse(self.g.mb_active)
        self.assertTrue(any("不变熊" in m for m in self.logs))

    def test_below_heal_line_heals_first(self):
        self.start_fight()
        self.tk(25)                                       # 一下掉到治疗线以下 → 先治疗，不变熊
        self.assertFalse(self.g.mb_active)
        self.assertEqual(self.g.state, State.HEALING)

    def test_back_to_cat_after_combat(self):
        self.cfg["heal"]["post_enabled"] = False          # 战后治疗开着时，回春术宏自己会退出熊形态
        self.start_fight()
        for hp in (90, 80, 70):
            self.tk(hp)
        self.assertTrue(self.g.mb_active)
        self.inp.history.clear()
        for _ in range(8):
            self.tk(70, thp=0, kind="none", combat=False, form="bear")
        self.assertFalse(self.g.mb_active)
        self.assertIn("z", self.keys())
        self.assertTrue(any("脱战了 → 变回猎豹" in m for m in self.logs))

    def test_back_to_cat_when_one_left(self):
        self.start_fight()
        for hp in (90, 80, 70):
            self.tk(hp)
        self.assertTrue(self.g.mb_active)
        self.g.kills += 1                                 # 杀了一只
        self.inp.history.clear()
        for _ in range(30):                               # 15 秒没再判断到多只（不掉血）
            self.tk(70, form="bear")
        self.assertFalse(self.g.mb_active)
        self.assertIn("z", self.keys())
        self.assertTrue(any("只剩 1 只了" in m for m in self.logs))

    def test_stays_bear_without_kill(self):
        self.start_fight()
        for hp in (90, 80, 70):
            self.tk(hp)
        for _ in range(40):
            self.tk(70, form="bear")
        self.assertTrue(self.g.mb_active)

    def test_heal_in_bear_returns_to_bear(self):
        self.start_fight()
        for hp in (90, 80, 70):
            self.tk(hp)
        self.assertTrue(self.g.mb_active)
        self.inp.history.clear()
        self.tk(25, form="bear")                          # 熊形态血低 → 退熊（v）→ 治疗之触（e）
        self.assertEqual(self.g.state, State.HEALING)
        self.assertIn("v", self.keys())
        self.tk(25, form="caster", casting=False)
        self.assertIn("e", self.keys())

    def test_stray_bear_cancel_for_heal(self):
        # 变回猎豹没成功、还在熊形态时要治疗：取消键 v 是单独的键 → 照样能退出变形
        self.assertTrue(self.g._cancel_distinct())
        self.assertTrue(self.g._in_any_form(obs(form="bear")))
        self.k["cancel_form"] = "z"
        self.assertFalse(self.g._in_any_form(obs(form="bear")))

    def test_death_resets(self):
        self.start_fight()
        for hp in (90, 80, 70):
            self.tk(hp)
        self.assertTrue(self.g.mb_active)
        self.g.tick(dobs(dead=True))
        self.assertFalse(self.g.mb_active)
        self.assertEqual(self.g._cform(), "cat")

    def test_gui_and_title(self):
        with open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertRegex(src, r"熊德自动打怪 v8\.(3[8-9]|[4-9]\d)")   # 版本号往上走
        self.assertIn('"multi_bear"', src)
        self.assertIn("被多只怪打时变熊", src)
        with open(os.path.join(ROOT, "README_中文.md"), encoding="utf-8") as f:
            readme = f.read()
        self.assertIn("v8.38", readme)

    def test_old_config_gets_section(self):
        loaded = {"cat": {"combat_form": "猎豹"}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertTrue(cfg["multi_bear"]["enabled"])
        self.assertEqual(cfg["safety"]["error_retry_max"], 5)


# ---------------------------------------------------------------- 2. 回中心超时 → 去下一个中心
class ReturnTimeout(H.TestV825PatrolStuck):
    def wander(self, cond, n=400, step=1.0):
        """一直在走（位置在变，不算卡住），但走不到。"""
        for i in range(n):
            self.clock.t += step
            pos = (53.0 + (0.1 if i % 2 else 0.0), 53.0)
            self.g.tick(pobs(pos=pos, facing=0.0))
            if cond():
                return True
        return False

    def test_travel_timeout_goes_next(self):
        self.tick((50.0, 50.0))
        self.spin((50.0, 50.0))
        self.assertEqual((self.g.ci, self.g.travel), (1, True))
        ok = self.wander(lambda: self.g.ci == 0)
        self.assertTrue(ok, self.logs[-5:])
        self.assertFalse(self.g.stopped)
        self.assertTrue(any("超过 300 秒还没到" in m and "改去中心 #1/2" in m for m in self.logs))
        self.assertEqual(self.g.state, State.RETURNING)

    def test_one_center_timeout_searches(self):
        self.g.set_centers([])
        self.cfg["area"].update(center_x=50.0, center_y=50.0, map_id=1442, home_on_start=False)
        self.g.home = (50.0, 50.0, 1442)
        self.g.travel = False
        self.g.start_return(pobs(pos=(53.0, 53.0), facing=0.0))
        ok = self.wander(lambda: self.g.state == State.SEARCHING, n=200)
        self.assertTrue(ok, self.logs[-5:])
        self.assertFalse(self.g.stopped)
        self.assertTrue(any("回中心：超过 90 秒还没到" in m and "就地找怪" in m for m in self.logs))
        self.assertGreater(self.g.return_block_until, self.clock.t + 50)


# ---------------------------------------------------------------- 3. 跑尸：越绕越大，不停止
class CorpseDetour(V831.CorpseBase):
    def ghost(self, pos, face=0.0):
        self.g.tick(dobs(ghost=True, pos=pos, corpse=self.corpse, facing=face))

    def stuck_ticks(self, n, pos=(50.0, 50.0)):
        """位置一直不变 → 不断卡住。"""
        for _ in range(n):
            self.clock.t += 0.5
            self.ghost(pos)

    def test_no_stop_long_run(self):
        self.g.tick(dobs(dead=True))
        self.ghost(self.pos)
        self.stuck_ticks(2600)                            # 约 22 分钟一直卡着
        self.assertFalse(self.g.stopped)
        self.assertEqual(self.g.state, State.CORPSE)
        self.assertEqual(sum("不停止，继续绕路试" in m for m in self.logs), 2)
        self.assertTrue(any("一点都没变近" in m for m in self.logs))
        self.assertEqual(self.beeps, [])

    def test_escalation(self):
        self.k["back"] = "s"
        self.g.tick(dobs(dead=True))
        self.ghost(self.pos)
        self.stuck_ticks(80)
        logs = [m for m in self.logs if "跑尸卡住了" in m]
        self.assertIn("跳 + 平移", logs[0])
        self.assertIn("后退", logs[1])
        self.assertIn("绕 60°", logs[1])
        self.assertTrue(any("绕 90°" in m for m in logs))
        self.assertGreaterEqual(self.g.cr["detour_i"], 5)   # 60/-60/90/-90/120… 轮着试
        self.assertIn("s", self.keys())
        holds = [h for h in self.inp.holds if h and h >= 2000]
        self.assertGreaterEqual(max(holds), 4000)          # 越卡走得越久
        self.assertLessEqual(max(holds), 6000)

    def test_progress_resets_level(self):
        self.g.tick(dobs(dead=True))
        self.ghost(self.pos)
        self.stuck_ticks(30)
        self.assertGreater(self.g.cr["lvl"], 1)
        self.clock.t += 0.5
        self.ghost((50.0, 48.5))                          # 离尸体近了 1.5
        self.assertEqual(self.g.cr["lvl"], 0)
        self.assertEqual(self.g.cr["blocked"], [])

    def test_blocked_heading_skipped(self):
        self.g.tick(dobs(dead=True))
        self.ghost(self.pos)
        cr = self.g.cr
        o = dobs(ghost=True, pos=self.pos, corpse=self.corpse, facing=0.0)
        from bot.navigation import bearing_to
        direct = bearing_to(self.pos, self.corpse)
        cr["blocked"] = [(direct + math.radians(60)) % (2 * math.pi)]
        cr["detour_i"] = 0
        self.assertEqual(self.g._cr_detour(o), -60)        # +60° 记住走不通 → 跳过
        self.assertEqual(DETOURS[:4], (60, -60, 90, -90))

    def test_still_reaches_corpse(self):
        self.turn_hook()
        self.g.tick(dobs(dead=True))
        self.ghost(self.pos, self.face)
        self.sim_run()
        self.assertLess(distance(self.pos, self.corpse), 0.6)


# ---------------------------------------------------------------- 4. 程序出错 → 等 2 秒继续
class ErrorRetry(unittest.TestCase):
    def run_bot(self, fail_times):
        import bot.runner as R
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["log"]["enabled"] = False
        cfg["misc"]["capture_delay_s"] = 0
        cfg["misc"]["window_check"] = False
        logs = []
        calls = {"n": 0}

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
                if calls["n"] <= fail_times:
                    raise RuntimeError(f"坏帧 {calls['n']}")
                runner.stop_event.set()
                return obs(hp=100)

            def fresh_plates(self, *_):
                return []

        class Inp:
            def __init__(self, cfg, stop_event, log):
                self.stop_event = stop_event
                self.slept = []

            def sleep(self, s):
                if self.stop_event.is_set():
                    raise StopRequested()
                self.slept.append(s)

            def release_all(self):
                pass

            def key_up(self, *_):
                pass

            def __getattr__(self, name):                   # 其他按键方法：什么都不做
                if name.startswith("__"):
                    raise AttributeError(name)
                return lambda *a, **k: 1e9

        saved = (R.ScreenCapture, R.Detector, R.InputController)
        R.ScreenCapture, R.Detector, R.InputController = Cap, Det, Inp
        try:
            runner = R.BotRunner(cfg, logs.append)
            runner._run()
        finally:
            R.ScreenCapture, R.Detector, R.InputController = saved
        return logs, calls["n"]

    def test_retries_then_continues(self):
        logs, n = self.run_bot(3)
        self.assertEqual(sum("2 秒后继续" in m for m in logs), 3)
        self.assertFalse(any("出错停止" in m for m in logs))
        self.assertEqual(n, 4)

    def test_stops_after_too_many(self):
        logs, n = self.run_bot(100)
        self.assertEqual(sum("2 秒后继续" in m for m in logs), 5)
        self.assertTrue(any("出错停止" in m and "超过 5 次" in m for m in logs))
        self.assertEqual(n, 6)


# 只跑本文件新加的测试（继承来的旧测试在原文件里已经跑过）
for _cls, _base in ((ReturnTimeout, H.TestV825PatrolStuck),):
    for _n in dir(_base):
        if _n.startswith("test_") and _n not in _cls.__dict__:
            setattr(_cls, _n, None)


if __name__ == "__main__":
    unittest.main()

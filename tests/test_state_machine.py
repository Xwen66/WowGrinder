import copy
import math
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.detector import Observation  # noqa: E402
from bot.input import InputController, StopRequested  # noqa: E402
from bot.state_machine import Grinder, State  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeBackend:
    def __init__(self):
        self.events = []

    def keyDown(self, k):
        self.events.append(("down", k))

    def keyUp(self, k):
        self.events.append(("up", k))

    def click(self, button="left"):
        self.events.append(("click", button))

    def moveTo(self, x, y, duration=0):
        self.events.append(("move", x, y))


class FakeInput(InputController):
    """真实 InputController 的逻辑，但不真的等待；sleep 推进假时钟。"""

    def __init__(self, cfg, clock):
        super().__init__(cfg, threading.Event(), log=lambda *_: None, backend=FakeBackend())
        self.clock = clock
        self.holds = []

    def sleep(self, seconds):
        if self.stop_event.is_set():
            raise StopRequested()
        self.clock.t += seconds

    def since_last(self, key):
        t = self.last_press.get(key)
        return float("inf") if t is None else self.clock.t - t

    def press(self, key, hold_ms=None, delay=True):
        self.holds.append(hold_ms)
        super().press(key, hold_ms=0, delay=delay)
        self.last_press[key.strip().lower()] = self.clock.t


def skill(name, key, prio, cd=1500):
    return {"name": name, "image": "", "key": key, "priority": prio, "threshold": 0.8,
            "cooldown_ms": cd, "enabled": True}


def plate(x, y, kind="unselected", w=120, h=14):
    return {"x": x, "y": y, "w": w, "h": h, "score": 0.9, "adj": 0.9, "kind": kind}


def obs(hp=100, thp=0, kind=None, combat=False, dead=False, skills=(), plates=(), checked=False,
        source="addon", lootable=None, aggro=False, rng="unknown", error=None, form="unknown", casting=None, mana=None,
        arange="unknown", err_facing=False, err_far=False, since_damage=None, hp_readable=True, attack_slot=None):
    plates = list(plates)
    if kind is None:
        kind = "enemy" if thp > 1 else "none"
    return Observation(player_hp=hp, target_hp=thp, target_exists=kind != "none", target_kind=kind,
                       in_combat=combat, player_dead=dead, ready_skills=list(skills),
                       nameplates=plates, selected_checked=checked,
                       selected_plate=next((p for p in plates if p["kind"] == "selected"), None),
                       source=source, lootable=lootable, range=rng, error=error, form=form,
                       casting=casting, mana=mana, attack_range=arange, err_facing=err_facing, err_far=err_far,
                       since_damage=since_damage, hp_readable=hp_readable, attack_slot=attack_slot,
                       aggro=aggro)


class Base(unittest.TestCase):
    RGAP = 0.0
    MODE = "click"      # 旧的点击移动模式测试；原地模式见 TestStationary
    POST = False
    LEGACY = True       # v8.2 之前的行为：不剥皮、低于“回血到”就回血（新行为见 TestV82）
    def setUp(self):
        self.cfg = copy.deepcopy(DEFAULT_CONFIG)
        self.cfg["move"]["mode"] = self.MODE
        if self.LEGACY:
            self.cfg["loot"]["skin"] = False
            self.cfg["heal"]["rest_below_pct"] = 90
            self.cfg["keys"]["clear_target"] = "esc"      # v8.3 前的默认
            self.cfg["stationary"]["plate_click"] = False
            self.cfg["stationary"]["clear_far"] = False
        self.cfg["keys"]["target_attacker"] = ""              # v8.9：旧测试不用这个键（新测试自己设 x）
        self.cfg["humanize"]["move_enabled"] = False         # v8.10：旧测试不做随机小动作/休息
        self.cfg["humanize"]["break_enabled"] = False
        self.cfg["heal"]["reserve_cat_pct"] = 0                # v8.17：旧测试不预留变身法力（新测试自己设）
        self.cfg["heal"]["reserve_bear_pct"] = 0
        self.cfg["move"]["click_interval_s"] = self.RGAP          # v8.24：旧测试不限右键间隔（新测试设 3 秒）
        self.cfg["heal"]["post_enabled"] = self.POST              # v8.27：旧测试用旧的“低于线才回血”（新测试开战后治疗）
        self.cfg["keys"]["interact"] = self.cfg["keys"]["loot"] = "f"   # v8.27 前的默认互动键
        n = self.cfg["nameplate"]                                  # v8.28：旧测试不用 点后验证/拉黑/顶部不点/连续 2 帧/远处红名/先停稳
        n.update(verify_s=0, bad_s=0, skip_top_pct=0, patrol_frames=1, far_names=False, stop_settle_s=0)
        # v8.50：旧测试用旧的找怪顺序（Tab → 标记 → 红名）、红名走路按 patrol_frames 停、不做“界面红名”检测（新行为见 test_v850）
        n.update(plates_first=False, far_click_frames=n["patrol_frames"], red_static_turns=0, red_walk_static_s=0)
        self.cfg["tab_search"]["far_walk_tab"] = True
        if self.LEGACY:
            self.cfg["heal"]["heal_to_pct"] = 0                # v8.11 前：战斗治疗只放一个
        # v8.47：旧测试保留旧默认（战后 <70% 生命之触 / 其余回春 / 满血不治疗；没有开怪蓝线；多怪变熊开），新行为见 test_v847
        self.cfg["heal"].update(post_ht_below_pct=70, post_rejuv_below_pct=100, pull_min_mana_pct=0)
        self.cfg["multi_bear"]["enabled"] = True
        self.clock = Clock()
        self.inp = FakeInput(self.cfg, self.clock)
        self.logs = []
        self.beeps = []
        self.g = Grinder(self.cfg, self.inp, log=self.logs.append, clock=self.clock,
                         beep=lambda: self.beeps.append(1))
        self.k = self.cfg["keys"]

    def keys(self):
        return list(self.inp.history)

    def run_heal(self, hp=29, thp=70, **kw):
        """v8 分步治疗：熊 → 退熊 → 人形按治疗之触 → 读条 → 读完 → 变熊。"""
        self.g.tick(obs(hp=hp, thp=thp, combat=True, form="bear", **kw))
        self.clock.t += 0.2
        self.g.tick(obs(hp=hp, thp=thp, combat=True, form="caster", casting=False, **kw))
        self.clock.t += 0.2
        self.g.tick(obs(hp=hp, thp=thp, combat=True, form="caster", casting=True, **kw))
        self.clock.t += 3.0
        self.g.tick(obs(hp=hp + 30, thp=thp, combat=True, form="caster", casting=False, **kw))

    def clicks(self):
        return [e for e in self.inp._pg.events if e[0] == "click"]


class TestSearch(Base):
    def test_right_click_nearest_unselected(self):
        self.cfg["nameplate"]["click_offset_y"] = 20
        self.g.tick(obs(plates=[plate(100, 100), plate(900, 400), plate(1500, 200)]))
        self.assertEqual(self.keys(), ["click(960,427)"])     # (900+60, 400+7+20)
        self.assertEqual(self.clicks(), [("click", "right")])

    def test_scan_turn_when_no_plates(self):
        self.g.tick(obs())
        self.assertEqual(self.keys(), ["right"])
        self.assertEqual(self.inp.holds[-1], 200)

    def test_scan_until_plate_then_click(self):
        for _ in range(3):
            self.g.tick(obs())
        self.assertEqual(self.g.scan_turns, 3)
        self.g.tick(obs(plates=[plate(900, 400)]))
        self.assertTrue(self.keys()[-1].startswith("click"))
        self.assertEqual(self.g.scan_turns, 0)

    def test_scan_never_gives_up(self):
        for _ in range(100):
            self.g.tick(obs())
        self.assertFalse(self.g.stopped)                     # v8.6：转视角没有次数上限
        self.assertTrue(any("转了 80 次视角" in m for m in self.logs))

    def test_numpad_key_configurable(self):
        self.k["scan_turn"] = "num6"
        self.g.tick(obs())
        self.assertEqual(self.keys(), ["num6"])

    def test_click_fails_then_turn(self):
        p = [plate(900, 400)]
        for _ in range(3):
            self.g.tick(obs(plates=p))
        self.g.tick(obs(plates=p))          # 第 3 次失败 → 转一下视角
        self.assertEqual(self.keys()[-1], "right")
        self.g.tick(obs(plates=p))          # 然后重新允许点击
        self.assertTrue(self.keys()[-1].startswith("click"))

    def test_click_fails_tab_fallback(self):
        self.cfg["nameplate"]["tab_fallback"] = True
        p = [plate(900, 400)]
        for _ in range(4):
            self.g.tick(obs(plates=p))
        self.assertEqual(self.keys()[-1], "tab")

    def test_in_combat_without_target_presses_tab(self):
        self.g.tick(obs(combat=True, plates=[plate(900, 400)]))
        self.assertEqual(self.keys(), ["tab"])

    def test_friendly_target_cleared(self):
        self.g.tick(obs(thp=100, kind="friendly"))
        self.assertEqual(self.keys(), ["esc"])

    def test_rest_before_searching(self):
        self.g.tick(obs(hp=80))
        self.assertEqual(self.g.state, State.RESTING)

    def test_no_target_never_stops(self):
        self.cfg["scan"]["max_turns"] = 10 ** 6
        for _ in range(200):
            self.clock.t += 1
            self.g.tick(obs())
            if self.g.stopped:
                break
        self.assertFalse(self.g.stopped)                  # v8.5：找不到怪不再停止
        self.assertTrue(any("没找到能打的怪" in m and "不会自动停止" in m for m in self.logs))


class TestApproachCombat(Base):
    def acquire(self):
        sel = [plate(880, 380, "selected", 140, 16)]
        self.g.tick(obs(thp=100, plates=sel, checked=True))
        return sel

    def test_acquire_right_clicks_selected_plate(self):
        self.acquire()
        self.assertEqual(self.g.state, State.APPROACHING)
        self.assertEqual(self.keys(), ["click(950,388)"])
        self.assertEqual(self.clicks(), [("click", "right")])

    def test_acquire_without_plate_uses_interact(self):
        self.g.tick(obs(thp=100))
        self.assertEqual(self.keys(), [self.k["interact"]])

    def test_approach_to_combat_and_rotation(self):
        sel = self.acquire()
        self.clock.t += 0.5
        self.g.tick(obs(thp=100, plates=sel, checked=True))     # 还没到重按间隔
        self.assertEqual(len(self.keys()), 1)
        self.clock.t += 2
        self.g.tick(obs(thp=100, plates=sel, checked=True))     # 2 秒后再右键
        self.assertEqual(len(self.keys()), 2)
        mangle = skill("裂伤", "2", 1)
        self.g.tick(obs(thp=100, combat=True, rng="melee", form="bear", skills=[(mangle, 1.0)]))
        self.assertEqual(self.g.state, State.COMBAT)
        self.assertEqual(self.keys()[-1], self.k["attack"])          # v8.21：先按开怪键
        self.clock.t += 1.6
        self.g.tick(obs(thp=95, combat=True, rng="melee", form="bear", skills=[(mangle, 1.0)]))
        self.assertEqual(self.keys()[-1], "2")                         # 之后照常输出

    def test_approach_facing_error_reengages(self):
        sel = self.acquire()
        self.clock.t += 0.3
        self.g.tick(obs(thp=100, plates=sel, checked=True, error="facing"))
        self.assertEqual(len(self.clicks()), 1)              # v8.24：刚点过，不马上再点
        self.clock.t += 1.0
        self.g.tick(obs(thp=100, plates=sel, checked=True, error="facing"))
        self.assertEqual(len(self.clicks()), 2)              # 过了间隔还报面向错误 → 再点

    def test_approach_timeout(self):
        sel = self.acquire()
        self.clock.t += 13
        self.g.tick(obs(thp=100, plates=sel, checked=True))
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertEqual(self.keys()[-1], "esc")

    def test_offscreen_target_scan_turns(self):
        self.g.tick(obs(thp=100, checked=True))   # 有目标但看不到选中姓名板
        self.assertEqual(self.keys(), ["right"])

    def test_combat_not_bear_shifts(self):
        self.acquire()
        self.g.tick(obs(thp=90, combat=True, form="caster", rng="melee"))
        self.assertEqual(self.keys()[-1], self.k["bear_form"])

    def test_combat_heal_healing_touch_only(self):
        self.acquire()
        self.inp.history.clear()
        self.run_heal()
        self.assertEqual(self.keys(), [self.k["cancel_form"], self.k["healing_touch"], self.k["bear_form"]])
        self.assertEqual(self.k["healing_touch"], "5")
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertTrue(any("开始读条" in m for m in self.logs))

    def test_no_heal_above_30(self):
        self.acquire()
        self.inp.history.clear()
        self.g.tick(obs(hp=35, thp=70, combat=True, form="bear", rng="melee"))
        self.assertNotIn(self.k["healing_touch"], self.keys())

    def test_critical_after_heal_stops_and_beeps(self):
        self.acquire()
        self.run_heal(hp=25)                                          # 治疗一次
        self.assertEqual(self.g.fight_heals, 1)
        self.clock.t += 0.5
        self.g.tick(obs(hp=12, thp=70, combat=True, form="bear"))
        self.assertFalse(self.g.stopped)                              # v8.43：不停止，提醒 + 响一下
        self.assertTrue(any("治疗过后" in m and "不停止" in m for m in self.logs))
        self.assertEqual(self.beeps, [1])

    def test_critical_without_heal_heals_first(self):
        self.acquire()
        self.g.tick(obs(hp=10, thp=70, combat=True, form="bear"))
        self.assertFalse(self.g.stopped)
        self.assertEqual(self.g.state, State.HEALING)
        self.clock.t += 0.2
        self.g.tick(obs(hp=10, thp=70, combat=True, form="caster", casting=False))
        self.assertIn(self.k["healing_touch"], self.keys())

    def test_hp_unreadable_in_combat_logs_loudly(self):
        self.acquire()
        self.g.tick(obs(hp=0, thp=70, combat=True, form="bear", hp_readable=False))
        self.assertFalse(any("读不到自己血量" in m for m in self.logs))   # 3 秒内先不喊
        self.clock.t += 3.1
        self.g.tick(obs(hp=0, thp=70, combat=True, form="bear", hp_readable=False))
        self.assertTrue(any("读不到自己血量" in m for m in self.logs))
        self.assertFalse(self.g.stopped)              # 读不到 ≠ 死亡
        self.assertEqual(self.beeps, [1])

    def test_stuck_stop_without_recovery(self):
        """v8.43：补救关着也不停止，只提醒。"""
        self.cfg["recover"]["enabled"] = False
        self.acquire()
        self.g.tick(obs(thp=100, combat=True, form="bear"))
        for _ in range(40):
            self.clock.t += 1
            self.g.tick(obs(thp=100, combat=True, form="bear"))
        self.assertFalse(self.g.stopped)
        self.assertTrue(any("没有下降" in m and "不停止" in m for m in self.logs))


class TestRecovery(Base):
    def start_fight(self):
        self.g.tick(obs(thp=100))                                   # 选中 → 按 F 走过去
        self.g.tick(obs(thp=95, combat=True, form="bear", rng="melee"))   # 掉血 → 战斗，有伤害
        self.assertEqual(self.g.state, State.COMBAT)
        self.inp.history.clear()

    def test_facing_error_never_presses_interact(self):
        """v8.20：面向错误不按互动键（没标记 → 转身/扫一圈）。"""
        self.start_fight()
        self.g.tick(obs(thp=95, combat=True, form="bear", rng="melee", err_facing=True))
        self.assertNotIn(self.k["interact"], self.keys())
        self.assertTrue(set(self.keys()) & {self.k["turn_left"], self.k["turn_right"]})

    def test_out_of_range_never_presses_interact(self):
        """v8.22：战斗中够不到也不按互动键；没有标记 → 转一点 + 开怪键。"""
        self.start_fight()
        self.g.tick(obs(thp=95, combat=True, form="bear", arange="out"))
        self.assertNotIn(self.k["interact"], self.keys())
        self.assertEqual(self.keys()[-1], self.k["attack"])

    def test_no_damage_2s_triggers(self):
        self.start_fight()
        mangle = skill("重殴", "4", 1)
        self.clock.t += 1.0
        self.g.tick(obs(thp=95, combat=True, form="bear", rng="melee", skills=[(mangle, 1)]))
        self.assertEqual(self.keys(), ["4"])                         # 1 秒：正常打
        self.clock.t += 1.5
        self.g.tick(obs(thp=95, combat=True, form="bear", rng="melee", skills=[(mangle, 1)]))
        self.assertEqual(self.keys()[-1], self.k["attack"])          # 2.5 秒没伤害 → 补救（v8.22 不按互动键）
        self.assertNotIn(self.k["interact"], self.keys())

    def test_first_damage_4s(self):
        self.g.tick(obs(thp=100))
        self.g.tick(obs(thp=100, combat=True, form="bear", rng="melee"))   # 被怪打进战斗，没伤害
        self.inp.history.clear()
        self.clock.t += 3
        self.g.tick(obs(thp=100, combat=True, form="bear", rng="melee"))
        self.assertNotIn(self.k["interact"], self.keys())
        self.clock.t += 1.5
        self.g.tick(obs(thp=100, combat=True, form="bear", rng="melee"))
        self.assertEqual(self.keys()[-1], self.k["attack"])
        self.assertNotIn(self.k["interact"], self.keys())

    def test_escalation_then_skip(self):
        self.start_fight()
        bad = dict(thp=95, combat=True, form="bear", arange="out")
        self.g.tick(obs(**bad))                     # 1: 转一点 + 开怪键（没标记）
        self.clock.t += 0.5
        self.g.tick(obs(**bad))                     # 等效果
        self.assertEqual(len(self.keys()), 2)
        self.clock.t += 1.2
        self.g.tick(obs(**bad))                     # 2
        self.clock.t += 1.6
        self.g.tick(obs(**bad))                     # 3
        self.assertNotIn(self.k["interact"], self.keys())
        self.clock.t += 1.6
        self.g.tick(obs(**bad))                     # 3 次无效 → 清目标、跳过
        self.assertEqual(self.keys()[-1], "esc")
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertGreater(self.g.skip_until, self.clock.t)

    def test_damage_resets_failures(self):
        self.start_fight()
        self.g.tick(obs(thp=95, combat=True, form="bear", arange="out"))
        self.clock.t += 1.6
        self.g.tick(obs(thp=95, combat=True, form="bear", arange="out"))
        self.assertEqual(self.g.rec_fails, 1)
        self.clock.t += 0.5
        self.g.tick(obs(thp=80, combat=True, form="bear", rng="melee"))   # 掉血了 → 成功
        self.assertEqual(self.g.rec_fails, 0)

    def test_addon_since_damage_counts(self):
        self.g.tick(obs(thp=100))
        self.g.tick(obs(thp=100, combat=True, form="bear", rng="melee", since_damage=math.inf))
        self.clock.t += 5
        self.inp.history.clear()
        # 目标血条没变（比如血很厚），但插件战斗日志说 0.5 秒前打出了伤害 → 不补救
        self.g.tick(obs(thp=100, combat=True, form="bear", rng="melee", since_damage=0.5))
        self.assertNotIn(self.k["interact"], self.keys())

    def test_skipped_plate_not_clicked(self):
        sel = [plate(900, 400, "selected")]
        self.g.tick(obs(thp=100, plates=sel, checked=True))
        self.g.tick(obs(thp=95, combat=True, form="bear", rng="melee", plates=sel, checked=True))
        self.g.skip_until = self.clock.t + 30
        self.g.skip_pos = (960, 407)
        self.g.state = State.SEARCHING
        self.inp.history.clear()
        self.g.tick(obs(plates=[plate(905, 402), plate(300, 300)]))
        self.assertEqual(self.keys(), ["click(360,307)"])

    def test_recovery_disabled_uses_old_reengage(self):
        self.cfg["recover"]["enabled"] = False
        sel = [plate(880, 380, "selected", 140, 16)]
        self.g.tick(obs(thp=100, plates=sel, checked=True))
        self.g.tick(obs(thp=90, combat=True, form="bear", rng="melee"))
        self.clock.t += 2.5
        self.g.tick(obs(thp=80, combat=True, form="bear", rng="melee", error="range", plates=sel, checked=True))
        self.assertEqual(len(self.clicks()), 2)


class TestStationary(Base):
    MODE = "stationary_tab"

    def setUp(self):
        super().setUp()
        self.g.rng.seed(1)
        self.walk = {"w", "s", "a", "d", "q", "e", "f", self.k["interact"], self.k["back"], self.k["forward"]}

    def assertNoWalking(self):
        self.assertFalse(set(self.keys()) & self.walk, self.keys())
        self.assertEqual(self.clicks(), [])

    def test_default_mode_is_stationary(self):
        self.assertEqual(DEFAULT_CONFIG["move"]["mode"], "stationary_tab")
        self.assertFalse(self.g.wants_nameplates())       # plate_click 关着
        self.cfg["stationary"]["plate_click"] = True
        self.assertTrue(self.g.wants_nameplates())        # v8.3：没目标时要找姓名板

    def test_no_target_presses_tab(self):
        self.g.tick(obs())
        self.assertEqual(self.keys(), ["tab"])
        self.clock.t += 0.2
        self.g.tick(obs())                    # Tab 间隔没到
        self.assertEqual(self.keys(), ["tab"])

    def test_in_range_target_attacks(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.assertEqual(self.keys(), [self.k["attack"]])
        self.assertEqual(self.g.state, State.COMBAT)

    def test_out_of_range_cycles_then_pauses_and_turns(self):
        for _ in range(6):
            self.g.tick(obs(thp=100, arange="out"))
            self.clock.t += 0.7
        self.assertEqual(self.keys(), ["tab"] * 6)
        t0 = self.clock.t
        self.g.tick(obs(thp=100, arange="out"))         # 第 7 次：暂停 2-4 秒 + 原地转一点
        self.assertEqual(self.keys()[-1], self.k["scan_turn"])
        pause = self.g.tab_pause_until - t0
        self.assertTrue(2.0 <= pause <= 4.0, pause)
        self.clock.t += 1.0
        self.g.tick(obs(thp=100, arange="out"))
        self.assertEqual(len(self.keys()), 7)          # 暂停中不按
        self.clock.t += 3.5
        self.g.tick(obs(thp=100, arange="out"))
        self.assertEqual(self.keys()[-1], "tab")
        self.assertNoWalking()

    def test_turn_search_toggle_off(self):
        self.cfg["stationary"]["turn_search"] = False
        for _ in range(7):
            self.g.tick(obs())
            self.clock.t += 0.7
        self.assertEqual(self.keys(), ["tab"] * 6)

    def test_dead_tapped_friendly_tab(self):
        for kind in ("dead", "tapped", "friendly"):
            self.inp.history.clear()
            self.clock.t += 1
            self.g.tick(obs(thp=0 if kind == "dead" else 100, kind=kind, lootable=False))
            self.assertEqual(self.keys(), ["tab"], kind)

    def test_far_target_attacking_me_is_kept(self):
        self.g.tick(obs(thp=100, arange="out", combat=True, aggro=True))
        self.assertEqual(self.keys(), [self.k["attack"]])
        self.assertEqual(self.g.state, State.COMBAT)
        self.inp.history.clear()
        self.clock.t += 1
        self.g.tick(obs(thp=100, arange="out", combat=True, aggro=True, form="bear"))
        self.assertNotIn("tab", self.keys())           # 怪在走过来：等

    def test_combat_target_far_not_attacking_me_tabs(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        self.clock.t += 1
        self.g.tick(obs(thp=95, arange="out", combat=True, aggro=False, form="bear"))
        self.assertEqual(self.keys(), ["tab"])

    def test_in_combat_without_target_tabs(self):
        self.g.tick(obs(combat=True))
        self.assertEqual(self.keys(), ["tab"])

    def test_facing_error_turns_in_place(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        self.inp.holds.clear()
        for i in range(6):            # 第 1 跳只记下计数器初值
            self.clock.t += 0.9
            self.g.tick(self.bad(i))
        L, R = self.k["turn_left"], self.k["turn_right"]
        # 看不到标记：左 45 → 右 45 → 左 90 → 右 90 → 身后 180（相对开始朝向），180°/s
        self.assertEqual(self.keys(), [L, R, L, R, R])
        self.assertEqual([round(h) for h in self.inp.holds], [250, 500, 750, 1000, 500])
        self.assertNoWalking()

    def bad(self, n, **kw):
        o = obs(thp=100, arange="in", combat=True, form="bear", **kw)
        o.facing_count = n % 8       # v0.7：面向错误计数器每次 +1
        return o

    def test_old_facing_count_not_retriggered(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.clock.t += 0.9
        self.g.tick(self.bad(0))      # 第一次看到计数器：只记下，不算新报错
        self.clock.t += 0.9
        self.g.tick(self.bad(1))
        self.assertEqual(len(self.keys()), 2)        # 攻击 + 转一次
        self.clock.t += 0.9
        self.g.tick(self.bad(1))      # 计数没变 → 不再转（以前 1.5 秒灯会重复触发）
        self.assertEqual(len(self.keys()), 2)

    def test_marker_right_turns_once_toward(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear(); self.inp.holds.clear()
        self.clock.t += 0.5
        o = obs(thp=100, arange="in", combat=True, form="bear")
        o.marker, o.frame_size = (1920 - 20 - 1, 400), (1920, 1080)     # 贴右边
        self.g.tick(o)
        self.assertEqual(self.keys(), [self.k["turn_right"]])
        self.assertEqual(round(self.inp.holds[0]), 500)                 # 90° / 180°/s
        self.clock.t += 0.6
        o2 = obs(thp=100, arange="in", combat=True, form="bear")
        o2.marker, o2.frame_size = (1000, 400), (1920, 1080)            # 已在正前方
        self.g.tick(o2)
        self.assertEqual(len(self.keys()), 1)

    def test_marker_bottom_means_behind_turns_180(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear(); self.inp.holds.clear()
        self.clock.t += 0.5
        o = obs(thp=100, arange="in", combat=True, form="bear")
        o.marker, o.frame_size = (900, 1070), (1920, 1080)
        self.g.tick(o)
        self.assertEqual(self.keys(), [self.k["turn_left"]])
        self.assertGreater(self.inp.holds[0], 900)
        self.assertTrue(any("身后" in m for m in self.logs))

    def test_marker_front_angle(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear(); self.inp.holds.clear()
        self.clock.t += 0.5
        o = obs(thp=100, arange="in", combat=True, form="bear")
        o.marker, o.frame_size = (160, 400), (1920, 1080)    # 左边约 -40°，小于 60° 不主动转
        self.g.tick(o)
        self.assertEqual(self.keys(), [])
        o = self.bad(1)
        o.marker, o.frame_size = (160, 400), (1920, 1080)
        self.g.tick(self.bad(0))
        self.clock.t += 0.5
        self.g.tick(o)                                       # 有面向错误 → 按标记转过去
        self.assertEqual(self.keys(), [self.k["turn_left"]])
        self.assertAlmostEqual(self.inp.holds[0], math.degrees(math.atan(800 / 960)) / 180 * 1000, delta=5)

    def test_turn_rate_calibration(self):
        self.g.tick(obs(thp=100, arange="in"))
        o = obs(thp=100, arange="in", combat=True, form="bear")
        o.marker, o.frame_size, o.facing = (1900, 400), (1920, 1080), 3.0
        self.clock.t += 0.5
        self.g.tick(o)                              # 右转 90°，按住 500ms
        self.clock.t += 0.6
        o2 = obs(thp=100, arange="in", combat=True, form="bear")
        o2.marker, o2.frame_size, o2.facing = (960, 400), (1920, 1080), 3.0 - math.radians(120)   # 实际转了 120° → 240°/s
        self.g.tick(o2)
        self.assertAlmostEqual(self.g.turn_rate, 210, delta=1)

    def test_turn_key_not_bound_warns(self):
        self.g.tick(obs(thp=100, arange="in"))
        o = obs(thp=100, arange="in", combat=True, form="bear")
        o.marker, o.frame_size, o.facing = (1900, 400), (1920, 1080), 3.0
        self.clock.t += 0.5
        self.g.tick(o)
        self.clock.t += 0.6
        o.marker = (1900, 400)
        self.g.tick(o)
        self.assertTrue(any("朝向没变" in m for m in self.logs))

    def test_facing_gives_up_with_tab(self):
        self.g.tick(obs(thp=100, arange="in"))
        for i in range(7):
            self.clock.t += 0.9
            self.g.tick(self.bad(i))
        self.assertEqual(self.keys()[-1], "tab")
        self.assertIn(("facing_failed", "盲转（面向错误） 5 次失败"), self.g.events)
        self.assertEqual(self.g.state, State.SEARCHING)

    def test_damage_resets_facing(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.clock.t += 0.9
        self.g.tick(obs(thp=100, arange="in", combat=True, form="bear", err_facing=True))
        self.assertEqual(self.g.face_attempts, 1)
        self.clock.t += 0.5
        self.g.tick(obs(thp=90, arange="in", combat=True, form="bear"))
        self.assertEqual(self.g.face_attempts, 1)          # v8.13：掉血不算修好（荆棘术反伤）
        self.clock.t += 5.0
        self.g.tick(obs(thp=80, arange="in", combat=True, form="bear"))
        self.assertEqual(self.g.face_attempts, 0)          # 5 秒没有新的面向错误 → 算修好

    def test_no_damage_turns_in_place(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        self.clock.t += 3.8
        self.g.tick(obs(thp=100, arange="in", combat=True, form="bear"))
        self.assertEqual(self.keys(), [])                 # v8：原地模式 4.5 秒才算打不出伤害（熊约 2.5 秒挥一下）
        self.clock.t += 0.8
        self.g.tick(obs(thp=100, arange="in", combat=True, form="bear"))
        self.assertEqual(self.keys(), [self.k["turn_left"]])

    def test_autoattack_off_presses_attack(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        self.clock.t += 2.5
        o = obs(thp=90, arange="in", combat=True, form="bear")
        o.autoattack = False
        self.g.tick(o)
        self.assertEqual(self.keys(), [self.k["attack"]])

    def test_unknown_range_waits_then_tabs(self):
        self.g.tick(obs(thp=100))                       # 判断不了距离 → 按开怪键，等
        self.assertEqual(self.g.state, State.APPROACHING)
        self.clock.t += 3
        self.g.tick(obs(thp=100))
        self.assertEqual(self.keys(), [self.k["attack"]] * 2)   # 2.5 秒没进战斗 → 再按一次开怪键
        self.clock.t += 5.5
        self.g.tick(obs(thp=100))
        self.assertEqual(self.keys()[-1], "tab")
        self.assertEqual(self.g.state, State.SEARCHING)

    def test_unknown_range_mob_comes(self):
        self.g.tick(obs(thp=100))
        self.clock.t += 2
        self.g.tick(obs(thp=100, combat=True, form="bear"))
        self.assertEqual(self.g.state, State.COMBAT)

    def test_full_kill_heal_cycle_no_walking(self):
        mangle = skill("重殴", "4", 1)
        self.g.tick(obs())                               # Tab
        self.g.tick(obs(thp=100, arange="in"))           # 攻击
        self.clock.t += 1
        self.g.tick(obs(hp=60, thp=50, arange="in", combat=True, form="bear", skills=[(mangle, 1)]))
        self.clock.t += 1
        self.run_heal(hp=25, thp=20, arange="in")                                # 治疗之触
        self.assertIn(self.k["healing_touch"], self.keys())
        self.clock.t += 1
        self.g.tick(obs(hp=50, thp=0, kind="dead", lootable=False, form="bear"))
        self.assertEqual(self.g.kills, 1)
        self.assertEqual(self.g.state, State.RESTING)
        self.walk.discard("f")   # 拾取键 f 只在可拾取时按；这里不可拾取
        self.assertNoWalking()

    def test_area_return_disabled(self):
        self.cfg["area"].update(enabled=True, center_x=10, center_y=10, radius=1)
        o = obs()
        o.pos, o.facing = (50.0, 50.0), 0.0
        self.g.tick(o)
        self.assertNotEqual(self.g.state, State.RETURNING)
        self.assertEqual(self.keys(), ["tab"])

    def test_critical_beep_still_works(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.run_heal(hp=25, arange="in")
        self.clock.t += 0.5
        self.g.tick(obs(hp=10, thp=70, arange="in", combat=True, form="bear"))
        self.assertFalse(self.g.stopped)                              # v8.43：不停止，照样响
        self.assertEqual(self.beeps, [1])


class TestAfterFight(Base):
    def kill(self, hp=70, lootable=True, source="addon"):
        self.g.tick(obs(thp=100, source=source))
        self.g.tick(obs(thp=50, combat=True, form="bear" if source == "addon" else "unknown", source=source))
        self.inp.history.clear()
        if source == "addon":
            self.g.tick(obs(hp=hp, thp=0, kind="dead", lootable=lootable, combat=False, form="bear"))
        else:
            self.g.tick(obs(hp=hp, thp=0, source=source))
            self.g.tick(obs(hp=hp, thp=0, source=source))

    def test_kill_loot_then_rest_to_90(self):
        self.kill(hp=70)
        self.assertEqual(self.g.kills, 1)
        self.assertEqual(self.keys(), [self.k["loot"], "esc"])
        self.assertEqual(self.g.state, State.RESTING)
        self.inp.history.clear()
        self.g.tick(obs(hp=70, form="bear"))                 # 先取消熊形态
        self.g.tick(obs(hp=70, form="caster"))               # 治疗之触
        self.g.tick(obs(hp=72, form="caster", casting=True))  # 读条中不按
        self.clock.t += 1
        self.g.tick(obs(hp=72, form="caster"))               # 读条时间没到，不按
        self.clock.t += 2.5
        self.g.tick(obs(hp=82, form="caster"))               # 再一发
        self.g.tick(obs(hp=91, form="caster"))               # ≥90 → 变熊
        ht = self.k["healing_touch"]
        self.assertEqual(self.keys(), [self.k["bear_form"], ht, ht, self.k["bear_form"]])
        self.assertEqual(self.g.state, State.SEARCHING)

    def test_no_rest_when_healthy(self):
        self.kill(hp=95)
        self.assertEqual(self.g.state, State.SEARCHING)

    def test_not_lootable_skips_loot(self):
        self.kill(hp=95, lootable=False)
        self.assertEqual(self.keys(), ["esc"])


    def test_rest_interrupted_by_combat(self):
        self.kill(hp=60)
        self.g.tick(obs(hp=60, form="caster"))
        self.inp.history.clear()
        self.g.tick(obs(hp=55, form="caster", combat=True))
        self.assertEqual(self.keys(), [self.k["bear_form"]])
        self.assertEqual(self.g.state, State.SEARCHING)

    def test_rest_low_mana_waits(self):
        self.kill(hp=60)
        self.inp.history.clear()
        self.g.tick(obs(hp=60, form="caster", mana=5))
        self.assertEqual(self.keys(), [])

    def test_rest_timeout(self):
        self.kill(hp=60)
        self.clock.t += 91
        self.g.tick(obs(hp=60, form="caster"))
        self.assertFalse(self.g.stopped)                     # v8.6：不停止，变回熊继续
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertEqual(self.keys()[-1], self.k["bear_form"])
        self.assertTrue(any("不等了" in m for m in self.logs))
        self.clock.t += 1
        self.g.tick(obs(hp=60, form="bear"))
        self.assertNotEqual(self.g.state, State.RESTING)     # 90 秒内不再脱战回血（不会无限回血）
        self.clock.t += 95
        self.g.tick(obs(hp=60, form="bear"))
        self.assertEqual(self.g.state, State.RESTING)

    def test_screen_mode_kill_and_rest(self):
        self.kill(hp=70, source="screen")
        self.assertEqual(self.g.kills, 1)
        self.assertEqual(self.keys(), [self.k["loot"]])   # 截图模式不知道能否拾取 → 照样按；没目标了不按 Esc（v8.1）
        self.assertEqual(self.g.state, State.RESTING)
        self.inp.history.clear()
        self.g.tick(obs(hp=70, source="screen"))   # 不知道形态，按记忆认为是熊 → 取消
        self.g.tick(obs(hp=70, source="screen"))   # 治疗之触
        self.assertEqual(self.keys(), [self.k["bear_form"], self.k["healing_touch"]])


class TestSafety(Base):
    def test_death(self):
        self.g.tick(obs(dead=True))                                   # v8.43：死了不停止，等待
        self.assertNotEqual(self.g.state, State.STOPPED)
        self.assertFalse(self.inp.stop_event.is_set())
        self.assertTrue(any("死亡" in m and "不停止" in m for m in self.logs))

    def test_zero_hp(self):
        self.g.tick(obs(hp=0))
        self.clock.t += 2.5
        self.g.tick(obs(hp=0))
        self.assertNotEqual(self.g.state, State.STOPPED)
        self.assertTrue(self.g.dead_idle)

    def test_zero_hp_blip(self):
        self.g.tick(obs(hp=0))
        self.clock.t += 1
        self.g.tick(obs(hp=95))
        self.clock.t += 1.5
        self.g.tick(obs(hp=0))
        self.assertNotEqual(self.g.state, State.STOPPED)

    def test_emergency_stop_interrupts_heal(self):
        self.g.state = State.COMBAT
        self.inp.stop_event.set()
        with self.assertRaises(StopRequested):
            self.g.tick(obs(hp=10, thp=50, combat=True))

    def test_combo_key(self):
        self.inp.press("ctrl+1")
        self.assertEqual(self.inp._pg.events, [("down", "ctrl"), ("down", "1"), ("up", "1"), ("up", "ctrl")])

    def test_wants_nameplates(self):
        self.assertTrue(self.g.wants_nameplates())
        self.g.state = State.COMBAT
        self.g.last_obs = obs(thp=50, combat=True, rng="melee")
        self.assertFalse(self.g.wants_nameplates())
        self.g.last_obs = obs(thp=50, combat=True, error="facing")
        self.assertTrue(self.g.wants_nameplates())


if __name__ == "__main__":
    unittest.main()

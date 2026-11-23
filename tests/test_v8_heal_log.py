"""v8：战后回血/战斗治疗的根因修复、日志系统、姓名板标记。"""
import math
import os
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_state_machine import Base, obs, plate  # noqa: E402
from bot.state_machine import State  # noqa: E402
from bot.config import DEFAULT_CONFIG, _merge, _migrate  # noqa: E402
from bot.logfile import SessionLog, ShotSaver, obs_snapshot  # noqa: E402
from bot.marker import find_marker, marker_bearing  # noqa: E402


class TestPostFightRest(Base):
    MODE = "stationary_tab"

    def fight_and_kill(self, hp=60, combat_after=True):
        self.g.tick(obs(thp=100, arange="in"))
        self.clock.t += 1
        self.g.tick(obs(hp=hp, thp=40, arange="in", combat=True, form="bear"))
        self.clock.t += 2.6     # v8.17：2.5 秒内掉过血 = 可能还有别的怪在打 → 不回血；这里超过 2.5 秒
        self.inp.history.clear()
        # 怪死了，但“战斗中”还残留（以前：直接 Tab，回血被跳过）
        self.g.tick(obs(hp=hp, thp=0, kind="dead", lootable=False, combat=combat_after, form="bear"))

    def test_lingering_combat_still_rests(self):
        self.fight_and_kill()
        self.assertEqual(self.g.kills, 1)
        self.assertEqual(self.g.state, State.RESTING)
        self.assertNotIn("tab", self.keys())
        self.clock.t += 0.3
        self.g.tick(obs(hp=60, combat=True, form="bear"))            # 还在战斗状态，没掉血 → 退熊回血
        self.assertEqual(self.keys()[-1], self.k["cancel_form"])
        self.clock.t += 0.3
        self.g.tick(obs(hp=60, combat=True, form="caster", casting=False))
        self.assertEqual(self.keys()[-1], self.k["healing_touch"])
        self.assertEqual(self.g.state, State.RESTING)

    def test_recent_hit_blocks_rest_in_combat(self):
        """v8.17：多只怪：杀完一只，但 2.5 秒内还在掉血 → 不变人形回血，先找打我的怪。"""
        self.g.tick(obs(thp=100, arange="in"))
        self.clock.t += 1
        self.g.tick(obs(hp=60, thp=40, arange="in", combat=True, form="bear"))
        self.clock.t += 1
        self.g.tick(obs(hp=60, thp=0, kind="dead", lootable=False, combat=True, form="bear"))
        self.assertNotEqual(self.g.state, State.RESTING)
        self.assertEqual(self.g.pending_loot, 1)

    def test_dead_target_in_search_rests_before_tab(self):
        self.g.last_kill_t = self.clock.t
        self.g.tick(obs(hp=50, thp=0, kind="dead", lootable=False, combat=True, form="bear"))
        self.assertEqual(self.g.state, State.RESTING)
        self.assertNotIn("tab", self.keys())

    def test_being_hit_aborts_rest(self):
        self.fight_and_kill()
        self.clock.t += 0.3
        self.g.tick(obs(hp=60, combat=True, form="caster", casting=False))
        self.clock.t += 0.3
        self.g.tick(obs(hp=52, combat=True, form="caster"))            # 掉了 8% → 有怪在打
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertTrue(any("回血时被打" in m for m in self.logs))

    def test_after_grace_in_combat_logs_skip_reason(self):
        self.cfg["heal"]["heal_pct"] = 30          # v8.46 默认改 40；这里测的是 30~40 之间的日志
        self.g.last_kill_t = self.clock.t - 30
        self.g.tick(obs(hp=35, combat=True, form="bear"))
        self.assertTrue(any("不回血" in m for m in self.logs))
        self.assertTrue(any(e[0] == "heal_skipped" for e in self.g.events))

    def test_mana_zero_does_not_block(self):
        self.fight_and_kill(combat_after=False)
        self.clock.t += 0.3
        self.g.tick(obs(hp=60, form="caster", casting=False, mana=0))
        self.assertEqual(self.keys()[-1], self.k["healing_touch"])

    def test_mana_unknown_does_not_block(self):
        self.fight_and_kill(combat_after=False)
        self.clock.t += 0.3
        self.g.tick(obs(hp=60, form="caster", casting=False, mana=None))
        self.assertEqual(self.keys()[-1], self.k["healing_touch"])

    def test_rest_retries_quickly_when_cast_did_not_start(self):
        self.fight_and_kill(combat_after=False)
        self.clock.t += 0.3
        self.g.tick(obs(hp=60, form="caster", casting=False))
        n = self.keys().count("5")
        self.clock.t += 0.8                                            # 公共冷却：没开始读条 → 0.7 秒后重按
        self.g.tick(obs(hp=60, form="caster", casting=False))
        self.assertEqual(self.keys().count("5"), n + 1)


class TestCombatHeal(Base):
    MODE = "stationary_tab"

    def engage(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()

    def test_retry_until_cast_starts(self):
        self.engage()
        self.g.tick(obs(hp=25, thp=60, combat=True, form="bear"))
        self.clock.t += 0.2
        self.g.tick(obs(hp=25, thp=60, combat=True, form="caster", casting=False))
        self.clock.t += 0.3
        self.g.tick(obs(hp=25, thp=60, combat=True, form="caster", casting=False))   # 0.7 秒没到
        self.assertEqual(self.keys().count("5"), 1)
        self.clock.t += 0.5
        self.g.tick(obs(hp=25, thp=60, combat=True, form="caster", casting=False))   # 再按
        self.assertEqual(self.keys().count("5"), 2)
        self.clock.t += 0.2
        self.g.tick(obs(hp=25, thp=60, combat=True, form="caster", casting=True))
        self.assertEqual(self.g.heal_phase, "wait")

    def test_gives_up_after_max_presses(self):
        self.engage()
        self.g.tick(obs(hp=25, thp=60, combat=True, form="bear"))
        for _ in range(8):
            self.clock.t += 0.8
            self.g.tick(obs(hp=25, thp=60, combat=True, form="caster", casting=False))
        self.assertEqual(self.keys().count("5"), 5)
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertEqual(self.keys()[-1], self.k["bear_form"])
        self.assertTrue(any("都没开始读条" in m for m in self.logs))

    def test_cannot_leave_bear_warns(self):
        self.engage()
        for _ in range(4):
            self.g.tick(obs(hp=25, thp=60, combat=True, form="bear"))
            self.clock.t += 1.0
        self.assertNotIn("5", self.keys())
        self.assertTrue(any("还在熊形态" in m for m in self.logs))
        self.assertNotEqual(self.g.state, State.HEALING)

    def test_heal_line_skip_logged_between_30_and_40(self):
        self.cfg["heal"]["heal_pct"] = 30          # v8.46 默认改 40
        self.engage()
        self.g.tick(obs(hp=35, thp=60, arange="in", combat=True, form="bear"))
        self.assertTrue(any("不治疗" in m and "35%" in m for m in self.logs))
        self.assertTrue(any(e[0] == "heal_skipped" for e in self.g.events))

    def test_cooldown_skip_logged(self):
        self.engage()
        self.g.last_heal_time = self.clock.t - 1
        self.g.tick(obs(hp=20, thp=60, arange="in", combat=True, form="bear"))
        self.assertTrue(any("离上次治疗" in m for m in self.logs))

    def test_key_presses_have_reasons(self):
        notes = []
        self.inp.on_key = notes.append
        self.g.tick(obs())
        self.assertTrue(any("按键 tab" in n and "Tab 选怪" in n for n in notes), notes)
        self.clock.t += 1
        self.g.tick(obs(thp=100, arange="in"))
        self.assertTrue(any("按键 1" in n and "开普通攻击" in n for n in notes), notes)

    def test_stop_event(self):
        self.g.tick(obs(dead=True))
        self.assertEqual(self.g.events[-1][0], "death")


class TestLogFiles(unittest.TestCase):
    def test_session_log_ms_and_prune(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(25):
                open(os.path.join(d, f"wowgrinder_2020010{i:02d}_000000.log"), "w").close()
            log = SessionLog(d, keep=20)
            log.write("你好")
            log.close()
            files = [f for f in os.listdir(d) if f.endswith(".log")]
            self.assertEqual(len(files), 20)
            text = open(log.path, encoding="utf-8").read()
            self.assertRegex(text, r"^\d\d:\d\d:\d\d\.\d{3} 你好")

    def test_rotate_by_size(self):
        with tempfile.TemporaryDirectory() as d:
            log = SessionLog(d, keep=20, max_mb=0.0005)
            for _ in range(100):
                log.write("x" * 50)
            log.close()
            self.assertGreater(len(os.listdir(d)), 1)

    def test_shots(self):
        with tempfile.TemporaryDirectory() as d:
            frame = np.zeros((1080, 1920, 3), np.uint8)
            paths = ShotSaver(d).save(frame, "death")
            self.assertEqual(len(paths), 2)
            self.assertTrue(all(os.path.getsize(p) > 0 for p in paths))

    def test_snapshot_line(self):
        o = obs(hp=55, thp=40, combat=True)
        o.marker, o.marker_state = (1234, 567), "shown"
        line = obs_snapshot(o)
        self.assertIn("血 55%", line)
        self.assertIn("(1220,560)", line)


class TestMarker(unittest.TestCase):
    def draw(self, img, x, y):
        img[y:y + 12, x:x + 20] = 0
        img[y + 2:y + 10, x + 2:x + 10] = (255, 255, 0)
        img[y + 2:y + 10, x + 10:x + 18] = (255, 0, 255)

    def test_find_under_gamma_and_ignore_status_blocks(self):
        from bot.addon_reader import apply_gamma
        img = np.full((1080, 1920, 3), 60, np.uint8)
        img[0:10, 0:240] = (255, 255, 0)
        img[0:10, 100:110] = (255, 0, 255)          # 左上角状态块不算
        self.draw(img, 1400, 300)
        m = find_marker(apply_gamma(img, 1.2))
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m[0], 1410, delta=1)
        self.assertAlmostEqual(m[1], 306, delta=2)

    def test_none_when_absent(self):
        self.assertIsNone(find_marker(np.zeros((1080, 1920, 3), np.uint8)))

    def test_bearing(self):
        self.assertEqual(marker_bearing(960, 400, 1920, 1080)[1], "front")
        self.assertAlmostEqual(marker_bearing(1920 - 1, 400, 1920, 1080, edge_px=0)[0], 45, delta=0.2)
        self.assertEqual(marker_bearing(10, 400, 1920, 1080), (-90.0, "side_edge"))
        self.assertEqual(marker_bearing(960, 1075, 1920, 1080), (180.0, "behind"))


class TestConfigV8(unittest.TestCase):
    def test_cancel_form_filled_from_bear_key(self):
        loaded = {"keys": {"bear_form": "f3", "cancel_form": ""}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(cfg["keys"]["cancel_form"], "f3")
        self.assertEqual(cfg["stationary"]["no_damage_s"], 4.5)
        self.assertTrue(cfg["log"]["enabled"])

    def test_cancel_form_kept(self):
        loaded = {"keys": {"cancel_form": "f2"}}
        self.assertEqual(_migrate(_merge(DEFAULT_CONFIG, loaded), loaded)["keys"]["cancel_form"], "f2")


if __name__ == "__main__":
    unittest.main()


class TestV81Bugs(Base):
    """Simon 实测（2026-11-15 晚）的 3 个问题。"""
    MODE = "stationary_tab"

    def test_no_esc_after_kill_when_target_already_gone(self):
        # 日志 20:02:24：怪死后目标已经没了，旧版照样按 Esc → 打开游戏菜单
        self.g.tick(obs(thp=100, arange="in"))
        self.clock.t += 1
        self.g.tick(obs(hp=80, thp=30, arange="in", combat=True, form="bear"))
        self.inp.history.clear()
        for _ in range(3):
            self.clock.t += 0.2
            self.g.tick(obs(hp=80, thp=0, kind="none", combat=True, form="bear"))
        self.assertEqual(self.g.kills, 1)
        self.assertNotIn("esc", self.keys())

    def test_stationary_never_presses_esc_after_loot(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.clock.t += 1
        self.g.tick(obs(hp=95, thp=30, arange="in", combat=True, form="bear"))
        self.inp.history.clear()
        self.g.tick(obs(hp=95, thp=0, kind="dead", lootable=True, form="bear"))
        self.assertNotIn("esc", self.keys())

    def test_clear_target_guard_click_mode(self):
        self.cfg["move"]["mode"] = "click"
        self.g._clear_target(obs())                       # 没目标 → 不按
        self.assertEqual(self.keys(), [])
        self.g._clear_target(obs(thp=0, kind="dead"))     # 有（死）目标 → 按
        self.assertEqual(self.keys(), ["esc"])

    def test_game_menu_closed_once_then_stop(self):
        o = obs(); o.game_menu = True
        self.g.tick(o)
        self.assertEqual(self.keys(), ["esc"])
        self.clock.t += 0.5
        self.g.tick(o)
        self.assertEqual(self.keys(), ["esc"])            # 2 秒内不重复按
        self.clock.t += 2.5
        self.g.tick(o)
        for _ in range(20):                               # v8.6：2 次关不掉 → 暂停，不再按任何键，也不停止
            self.clock.t += 2.5
            self.g.tick(o)
        self.assertEqual(self.keys(), ["esc", "esc"])
        self.assertFalse(self.g.stopped)
        self.assertEqual(sum("暂停" in m for m in self.logs), 1)
        self.clock.t += 1
        self.g.tick(obs())                                # 菜单关了 → 继续
        self.assertTrue(any("继续" in m for m in self.logs))
        self.assertEqual(self.keys()[-1], "tab")

    def test_ranged_target_gets_opener_not_tab(self):
        # 日志 20:00:22：目标 enemy、距离 ranged/unknown → 旧版“目标够不到”一直 Tab
        self.g.tick(obs(thp=100, rng="ranged", form="caster"))
        self.assertEqual(self.keys(), [self.k["attack"]])
        self.assertEqual(self.g.state, State.APPROACHING)
        self.assertTrue(any("拉怪" in m for m in self.logs))
        self.clock.t += 1.5
        self.g.tick(obs(thp=100, rng="ranged", combat=True, aggro=True, form="caster"))
        self.assertEqual(self.g.state, State.COMBAT)

    def test_far_target_still_tabs(self):
        self.g.tick(obs(thp=100, rng="far"))
        self.assertEqual(self.keys(), ["tab"])            # clear_far 关着（旧行为）
        self.assertTrue(any("30 码外" in m for m in self.logs))

    def test_pull_gives_up_after_wait(self):
        self.g.tick(obs(thp=100, rng="ranged"))
        self.clock.t += 2.6
        self.g.tick(obs(thp=100, rng="ranged"))
        self.clock.t += 6
        self.g.tick(obs(thp=100, rng="ranged"))
        self.assertEqual(self.keys(), [self.k["attack"], self.k["attack"], "tab"])

    def test_marker_front_no_damage_does_not_sweep(self):
        # 日志 20:02:03：标记在正前方 (1040,360)，没面向错误，只是没伤害 → 旧版仍然盲转 ±45°
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        self.clock.t += 6
        o = obs(thp=10, arange="in", combat=True, form="bear")
        o.marker, o.frame_size = (1040, 360), (1920, 1080)
        self.g.tick(o)
        self.assertNotIn(self.k["turn_left"], self.keys())
        self.assertNotIn(self.k["turn_right"], self.keys())


class TestReaderMenu(unittest.TestCase):
    def test_menu_block(self):
        import copy
        from bot.addon_reader import AddonReader, paint_test_frame
        from test_addon_reader import FULL
        r = AddonReader(copy.deepcopy(DEFAULT_CONFIG))
        self.assertFalse(r.read(paint_test_frame(FULL)).game_menu)
        self.assertTrue(r.read(paint_test_frame({**FULL, 24: "white"})).game_menu)


class TestV82(Base):
    """v8.2：拾取 + 剥皮；高血量杀完怪不变人形。"""
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.k["interact"] = "g"
        self.k["loot"] = "g"

    def fight_then_kill(self, hp):
        self.g.tick(obs(thp=100, arange="in", form="bear"))
        self.clock.t += 1
        self.g.tick(obs(hp=hp, thp=30, arange="in", combat=True, form="bear"))
        self.inp.history.clear()
        for _ in range(5):                                  # 日志里：怪死后目标自动没了
            self.clock.t += 0.2
            self.g.tick(obs(hp=hp, thp=0, kind="none", combat=True, form="bear"))
            if self.g.kills:
                break
        self.assertEqual(self.keys(), [])                   # v8.4：还在战斗中 → 先不捡
        self.assertEqual(self.g.pending_loot, 1)
        if self.g.state == State.SEARCHING:
            self.clock.t += 0.5
            self.g.tick(obs(hp=hp, kind="none", combat=False, form="bear"))   # 脱战 → 捡

    def test_loot_then_skin_with_interact_key(self):
        self.fight_then_kill(95)
        self.assertEqual(self.g.kills, 1)
        self.assertEqual(self.keys(), ["g", "g"])          # 拾取 + 剥皮
        self.assertEqual(self.g.state, State.SKINNING)
        # 读条中不 Tab
        self.clock.t += 0.5
        self.g.tick(obs(hp=95, kind="none", casting=True, form="bear"))
        self.clock.t += 1.5
        self.g.tick(obs(hp=95, kind="none", casting=True, form="bear"))
        self.assertNotIn("tab", self.keys())
        self.assertEqual(self.g.state, State.SKINNING)
        self.clock.t += 0.5
        self.g.tick(obs(hp=95, kind="none", casting=False, form="bear"))
        self.assertTrue(any("剥皮完成" in m for m in self.logs))
        self.assertEqual(self.g.state, State.SEARCHING)
        self.clock.t += 0.3
        self.g.tick(obs(hp=95, kind="none", form="bear"))
        self.assertEqual(self.keys()[-1], "tab")
        self.assertNotIn(self.k["bear_form"], self.keys())   # 一直是熊，没变人形

    def test_skin_aborted_when_attacked(self):
        self.fight_then_kill(95)
        self.assertEqual(self.g.state, State.SKINNING)
        self.clock.t += 0.5
        self.g.tick(obs(hp=95, kind="none", casting=True, combat=True, form="bear"))   # 又进战斗了
        self.assertTrue(any("剥皮时进入战斗" in m for m in self.logs))
        self.assertNotEqual(self.g.state, State.SKINNING)
        self.assertEqual(self.g.pending_loot, 1)            # 这具尸体脱战后再捡

    def test_skin_off(self):
        self.cfg["loot"]["skin"] = False
        self.fight_then_kill(95)
        self.assertEqual(self.keys(), ["g"])
        self.assertEqual(self.g.state, State.SEARCHING)

    def test_interact_falls_back_to_loot_key_and_warns_rightclick(self):
        self.k["interact"] = ""
        self.k["loot"] = "rightclick"
        self.fight_then_kill(95)
        self.assertEqual(self.keys(), ["rightclick", "rightclick"])
        self.assertTrue(any("鼠标右键" in m for m in self.logs))

    def test_high_hp_kill_stays_bear(self):
        # 日志 20:16:44：杀完怪血量 81%，因为“回血到 90%”也被当成开始线 → 变人形放治疗再变熊
        self.fight_then_kill(81)
        for _ in range(4):
            self.clock.t += 1.0
            self.g.tick(obs(hp=81, kind="none", form="bear"))
        self.assertNotEqual(self.g.state, State.RESTING)
        self.assertNotIn(self.k["bear_form"], self.keys())
        self.assertNotIn(self.k["cancel_form"], self.keys())
        self.assertNotIn(self.k["healing_touch"], self.keys())

    def test_low_hp_kill_rests_after_skin(self):
        self.fight_then_kill(40)
        for _ in range(4):
            self.clock.t += 1.0
            self.g.tick(obs(hp=40, kind="none", form="bear"))
        self.assertEqual(self.g.state, State.RESTING)

    def test_rest_below_cannot_exceed_rest_to(self):
        self.cfg["heal"]["rest_below_pct"] = 99
        self.cfg["heal"]["rest_to_pct"] = 70
        self.assertEqual(self.g._rest_below(), 70)


class TestV83(Base):
    """v8.3：没目标左键点姓名板；太远按清除目标键（F3）。"""
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.g._has_plate_templates = lambda now: True

    def clicks_at(self):
        return [e for e in self.inp._pg.events if e[0] in ("click", "move")]

    def test_click_nearest_plate_when_no_target(self):
        self.g.tick(obs(plates=[plate(1500, 300), plate(900, 480), plate(300, 700)]))
        ev = self.clicks_at()
        self.assertIn(("move", 960, 487), ev)              # 最靠近屏幕中间的 (900+60, 480+7)
        self.assertIn(("click", "left"), ev)
        self.assertNotIn("tab", self.keys())
        self.assertTrue(any("左键点姓名板" in m and "匹配度 0.90" in m for m in self.logs))
        # 选上了 → 按开怪键
        self.clock.t += 0.4
        self.g.tick(obs(thp=100, rng="ranged"))
        self.assertEqual(self.keys()[-1], self.k["attack"])
        self.assertNotIn("tab", self.keys())

    def test_restore_mouse(self):
        self.inp._pg.position = lambda: (100, 900)
        self.g.tick(obs(plates=[plate(900, 480)]))
        moves = [e for e in self.inp._pg.events if e[0] == "move"]
        self.assertEqual(moves[-1], ("move", 100, 900))

    def test_no_template_uses_tab_and_logs_once(self):
        self.g._has_plate_templates = lambda now: False
        self.g.tick(obs(plates=[plate(900, 480)]))
        self.assertEqual(self.keys(), ["tab"])
        self.clock.t += 1
        self.g.tick(obs())
        self.assertEqual(sum("没录" in m for m in self.logs), 1)

    def test_ui_area_plate_ignored(self):
        self.g.tick(obs(plates=[plate(40, 40)]))
        self.assertEqual(self.keys(), ["tab"])
        self.assertEqual(self.clicks_at(), [])

    def test_failed_clicks_fall_back_to_tab(self):
        n = 0
        for _ in range(12):
            self.g.tick(obs(plates=[plate(900, 480), plate(1200, 400), plate(600, 400), plate(1000, 200)]))
            self.clock.t += 0.5
            if "tab" in self.keys():
                break
            n += 1
        self.assertIn("tab", self.keys())
        self.assertEqual(len([e for e in self.inp._pg.events if e[0] == "click"]), 3)
        self.assertTrue(any("改按 Tab" in m for m in self.logs))

    def test_far_target_clears_with_f3_then_clicks_plate(self):
        self.assertEqual(self.k["clear_target"], "f3")
        self.g.tick(obs(thp=100, rng="far"))
        self.assertEqual(self.keys(), ["f3"])
        self.assertTrue(any("30 码外" in m and "f3" in m for m in self.logs))
        self.clock.t += 0.5
        self.g.tick(obs(plates=[plate(900, 480)]))
        self.assertIn(("click", "left"), self.clicks_at())
        self.assertNotIn("esc", self.keys())

    def test_far_clear_does_not_loop_forever(self):
        for _ in range(40):
            self.g.tick(obs(thp=100, rng="far"))
            self.clock.t += 0.3
            self.g.tick(obs())
            self.clock.t += 0.7
        self.assertTrue(any("没找到能打的怪" in m for m in self.logs))


class TestV83Migration(unittest.TestCase):
    def test_esc_becomes_f3_once(self):
        loaded = {"keys": {"clear_target": "esc"}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(cfg["keys"]["clear_target"], "f3")
        self.assertIn("clear_target_f3", cfg["meta"]["migrations"])
        # 之后用户自己又改回 esc → 不再改
        loaded2 = {"keys": {"clear_target": "esc"}, "meta": cfg["meta"]}
        cfg2 = _migrate(_merge(DEFAULT_CONFIG, loaded2), loaded2)
        self.assertEqual(cfg2["keys"]["clear_target"], "esc")

    def test_other_key_untouched(self):
        loaded = {"keys": {"clear_target": "f5"}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(cfg["keys"]["clear_target"], "f5")


class TestV83HitWhileLooting(Base):
    MODE = "stationary_tab"
    LEGACY = False

    def test_dead_lootable_target_skipped_when_being_hit(self):
        # 日志 20:31:39：剥皮被打断后又回去捡尸体，没去打正在打我的怪
        self.g.tick(obs(hp=80, thp=0, kind="dead", lootable=True, combat=True))
        self.clock.t += 0.3
        self.g.tick(obs(hp=74, thp=0, kind="dead", lootable=True, combat=True))   # 掉血 → 被打
        self.clock.t += 0.7
        self.g.tick(obs(hp=70, thp=0, kind="dead", lootable=True, combat=True))
        self.assertNotIn(self.k["interact"], self.keys())
        self.assertEqual(set(self.keys()), {"tab"})


class TestV84LootAfterCombat(Base):
    """v8.4：Simon：战斗中一只怪死了另一只还活着，结果在战斗中剥皮了 → 只在脱战后捡/剥。"""
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.k["interact"] = "g"
        self.cfg["buffs"]["enabled"] = False

    def kill(self, hp=90, combat=True):
        self.g.tick(obs(thp=100, arange="in", form="bear"))
        self.clock.t += 1
        self.g.tick(obs(hp=hp, thp=30, arange="in", combat=True, form="bear"))
        for _ in range(5):
            self.clock.t += 0.2
            self.g.tick(obs(hp=hp, thp=0, kind="none", combat=combat, form="bear"))
            if self.g.kills >= 1 and self.g.state != State.COMBAT:
                break

    def test_two_mobs_loot_after_combat_ends(self):
        self.kill()
        self.assertEqual(self.g.pending_loot, 1)
        self.assertNotIn("g", self.keys())
        self.assertTrue(any("先不拾取/剥皮" in m for m in self.logs))
        # 第二只怪在打我 → Tab 选它、打死（还是战斗中）
        self.clock.t += 0.7
        self.g.tick(obs(hp=85, combat=True, form="bear"))
        self.assertEqual(self.keys()[-1], "tab")
        self.clock.t += 0.3
        self.g.tick(obs(hp=83, thp=100, arange="in", combat=True, aggro=True, form="bear"))
        self.assertEqual(self.g.state, State.COMBAT)
        self.clock.t += 1
        self.g.tick(obs(hp=80, thp=20, arange="in", combat=True, aggro=True, form="bear"))
        for _ in range(5):
            self.clock.t += 0.2
            self.g.tick(obs(hp=80, thp=0, kind="none", combat=True, form="bear"))
            if self.g.kills >= 2:
                break
        self.assertEqual(self.g.kills, 2)
        self.assertEqual(self.g.pending_loot, 2)
        self.assertNotIn("g", self.keys())
        # 脱战 → 捡第 1 具（拾取+剥皮）
        self.inp.history.clear()
        self.clock.t += 0.5
        self.g.tick(obs(hp=80, kind="none", combat=False, form="bear"))
        self.assertEqual(self.keys(), ["g", "g"])
        self.assertEqual(self.g.state, State.SKINNING)
        self.clock.t += 3
        self.g.tick(obs(hp=80, kind="none", combat=False, form="bear", casting=False))
        self.clock.t += 0.3
        self.g.tick(obs(hp=80, kind="none", combat=False, form="bear"))     # 第 2 具
        self.assertEqual(self.keys(), ["g", "g", "g", "g"])
        self.assertEqual(self.g.pending_loot, 0)

    def test_out_of_combat_kill_loots_immediately(self):
        self.kill(combat=False)
        self.assertIn("g", self.keys())
        self.assertEqual(self.g.pending_loot, 0)


def bobs(thorns="no", motw="no", **kw):
    o = obs(**kw)
    o.buff_thorns, o.buff_motw = thorns, motw
    return o


class TestV84Buffs(Base):
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.g._has_plate_templates = lambda now: False

    def test_missing_buffs_leave_bear_cast_both_return(self):
        self.g.tick(bobs(form="bear", mana=80))
        self.assertEqual(self.g.state, State.BUFFING)
        self.assertNotIn("tab", self.keys())
        self.clock.t += 0.2
        self.g.tick(bobs(form="bear", mana=80))
        self.assertEqual(self.keys(), [self.k["cancel_form"]])
        self.clock.t += 0.5
        self.g.tick(bobs(form="caster", mana=80))
        self.assertEqual(self.keys()[-1], "r")
        self.clock.t += 1.0
        self.g.tick(bobs("yes", "no", form="caster", mana=75))
        self.assertTrue(any("荆棘术 已经上了" in m for m in self.logs))
        self.clock.t += 1.0
        self.g.tick(bobs("yes", "no", form="caster", mana=75))
        self.assertEqual(self.keys()[-1], "t")
        self.clock.t += 1.0
        self.g.tick(bobs("yes", "yes", form="caster", mana=70))
        self.clock.t += 0.2
        self.g.tick(bobs("yes", "yes", form="caster", mana=70))
        self.assertEqual(self.keys()[-1], self.k["bear_form"])
        self.assertEqual(self.g.state, State.SEARCHING)

    def test_has_buffs_no_shift(self):
        self.g.tick(bobs("yes", "yes", form="bear", mana=80))
        self.assertEqual(self.keys(), ["tab"])

    def test_low_mana_skips_and_retries_later(self):
        self.g.tick(bobs(form="bear", mana=10))
        self.assertEqual(self.keys(), ["tab"])
        self.assertTrue(any("法力 10%" in m for m in self.logs))
        self.clock.t += 31
        self.g.tick(bobs(form="bear", mana=80))
        self.assertEqual(self.g.state, State.BUFFING)

    def test_not_in_combat_only(self):
        self.g.tick(bobs(form="bear", mana=80, combat=True))
        self.assertNotEqual(self.g.state, State.BUFFING)

    def test_retry_limit(self):
        self.cfg["buffs"]["motw"] = False
        self.g.tick(bobs(form="caster", mana=80))
        for _ in range(12):
            self.clock.t += 1.0
            self.g.tick(bobs(form="caster", mana=80))
        self.assertEqual(self.keys().count("r"), 3)
        self.assertTrue(any("都没上" in m for m in self.logs))
        self.assertEqual(self.g.state, State.SEARCHING)

    def test_attacked_aborts(self):
        self.g.tick(bobs(form="caster", mana=80))
        self.clock.t += 0.3
        self.g.tick(bobs(form="caster", mana=80, combat=True, thp=100, aggro=True))
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertEqual(self.keys()[-1], self.k["bear_form"])

    def test_buff_after_rest_while_caster(self):
        self.g.state = State.RESTING
        self.g.rest_start = self.clock.t
        self.g.tick(bobs(hp=95, form="caster", mana=60))
        self.assertEqual(self.g.state, State.BUFFING)
        self.assertNotIn(self.k["bear_form"], self.keys())
        self.clock.t += 0.2
        self.g.tick(bobs(hp=95, form="caster", mana=60))
        self.assertEqual(self.keys()[-1], "r")             # 直接放，不用再退熊

    def test_unknown_uses_timer(self):
        self.cfg["buffs"]["motw"] = False
        self.g.tick(bobs("unknown", form="caster", mana=80))
        self.clock.t += 0.1
        self.g.tick(bobs("unknown", form="caster", mana=80))
        self.clock.t += 2.5
        self.g.tick(bobs("unknown", form="caster", mana=80))
        self.clock.t += 0.2
        self.g.tick(bobs("unknown", form="caster", mana=80))
        self.assertEqual(self.keys().count("r"), 1)
        self.assertEqual(self.g.state, State.SEARCHING)
        self.clock.t += 60
        self.g.tick(bobs("unknown", form="bear", mana=80))
        self.assertNotEqual(self.g.state, State.BUFFING)   # 9 分钟内不再补
        self.clock.t += 9 * 60
        self.g.tick(bobs("unknown", form="bear", mana=80))
        self.assertEqual(self.g.state, State.BUFFING)

    def test_reader_buff_blocks(self):
        import copy
        from bot.addon_reader import AddonReader, paint_test_frame
        from test_addon_reader import FULL
        r = AddonReader(copy.deepcopy(DEFAULT_CONFIG))
        self.assertIsNone(r.read(paint_test_frame(FULL)).buff_thorns)     # 旧插件
        st = r.read(paint_test_frame({**FULL, 25: "green", 26: "yellow"}, v08=True))
        self.assertEqual((st.buff_thorns, st.buff_motw), ("yes", "low"))
        st = r.read(paint_test_frame({**FULL, 25: "black", 26: "blue"}, v08=True))
        self.assertEqual((st.buff_thorns, st.buff_motw), ("no", "unlearned"))


class TestV85NoIdleStop(Base):
    MODE = "stationary_tab"
    LEGACY = False

    def test_stationary_no_target_10min_keeps_searching(self):
        self.g._has_plate_templates = lambda now: False
        self.cfg["scan"]["max_turns"] = 10 ** 6
        for _ in range(600):
            self.clock.t += 1
            self.g.tick(bobs("yes", "yes", form="bear", mana=80))
        self.assertFalse(self.g.stopped)
        self.assertGreater(self.keys().count("tab"), 50)
        self.assertGreaterEqual(sum("不会自动停止" in m for m in self.logs), 9)


class TestV86Facing(Base):
    """v8.6 面向 5 项改动。日志 20:57:41–49：标记在正前方、怪一直掉血，却每次报错都左转 45°。"""
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        self.inp.holds.clear()

    def o(self, n, marker=None, since=None, thp=100):
        o = obs(thp=thp, arange="in", combat=True, form="bear", aggro=True, since_damage=since)
        o.facing_count = n % 8
        if marker:
            o.marker, o.frame_size = marker, (1920, 1080)
        return o

    def turns(self):
        return [k for k in self.keys() if k in (self.k["turn_left"], self.k["turn_right"])]

    def test_v813_every_facing_error_turns_even_if_target_loses_hp(self):
        # v8.13 日志 23:07:11/14/17：+7°、每 3 秒一次面向错误、怪在掉血（荆棘术）→ 以前当误报。现在每次都转，角度逐次加大
        for i in range(4):
            self.clock.t += 3.0
            self.g.tick(self.o(i, marker=(1060, 380), since=0.5, thp=60 - i))
        self.assertEqual(self.turns(), [self.k["turn_right"]] * 3)
        self.assertEqual([round(h) for h in self.inp.holds if h and h > 60][-3:], [83, 139, 222])   # 15°/25°/40°
        self.assertFalse(any("误报" in m for m in self.logs))

    def test_v813_zero_marker_alternates(self):
        for i in range(3):
            self.clock.t += 1.0
            self.g.tick(self.o(i, marker=(960, 380), since=0.5, thp=60 - i))
        self.assertEqual(self.turns(), [self.k["turn_right"], self.k["turn_left"]])

    def test_center_marker_no_damage_small_turn_toward_marker(self):
        self.clock.t += 0.9
        self.g.tick(self.o(0, marker=(1100, 400)))
        self.clock.t += 0.9
        self.g.tick(self.o(1, marker=(1100, 400)))
        self.assertEqual(self.turns(), [self.k["turn_right"]])          # 标记偏右 → 右转
        self.assertEqual(round(self.inp.holds[-1]), 83)                 # v8.13：第一次 15° / 180°/s
        self.assertTrue(any("往右转 15°" in m for m in self.logs))

    def test_turn_press_has_no_random_pause(self):
        self.clock.t += 0.9
        self.g.tick(self.o(0, marker=(1100, 400)))
        self.clock.t += 0.9
        t0 = self.clock.t
        self.g.tick(self.o(1, marker=(1100, 400)))
        self.assertAlmostEqual(self.g.face_last, t0, places=3)        # 没有按键后的随机停顿
        self.assertGreater(self.g.fast_until, t0)                       # 转完 1 秒内 50ms 循环

    def test_offcenter_marker_short_no_damage_trigger(self):
        # 标记偏右约 34°（x=1680），没报错、没伤害：2.6 秒就转（以前要 4.5 秒）
        self.g.combat_start = self.clock.t
        self.g.state = State.COMBAT
        self.clock.t += 2.6
        self.g.tick(self.o(0, marker=(1680, 400)))
        self.assertEqual(self.turns(), [self.k["turn_right"]])

    def test_centered_marker_keeps_long_trigger(self):
        self.g.combat_start = self.clock.t
        self.g.state = State.COMBAT
        self.clock.t += 2.6
        self.g.tick(self.o(0, marker=(980, 400)))
        self.assertEqual(self.turns(), [])

    def test_sweep_starts_toward_marker_side(self):
        self.g.marker_side = 1                                          # 刚才看到标记偏右
        for i in range(2):
            self.clock.t += 0.9
            self.g.tick(self.o(i))                                      # 现在看不到标记
        self.assertEqual(self.turns(), [self.k["turn_right"]])

    def test_sweep_alternates_start_when_no_clue(self):
        self.clock.t += 0.9
        self.g.tick(self.o(0))
        self.clock.t += 0.9
        self.g.tick(self.o(1))
        first = self.turns()[0]
        self.g._face_reset()
        self.clock.t += 0.9
        self.g.tick(self.o(2))
        self.assertNotEqual(self.turns()[-1], first)                    # 不再每次都先左


class TestV86Migration(unittest.TestCase):
    def test_face_defaults_updated_once(self):
        loaded = {"stationary": {"face_settle_s": 0.5, "face_interval_s": 0.8}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual((cfg["stationary"]["face_settle_s"], cfg["stationary"]["face_interval_s"]), (0.25, 0.5))
        loaded = {"stationary": {"face_settle_s": 0.7, "face_interval_s": 1.0}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual((cfg["stationary"]["face_settle_s"], cfg["stationary"]["face_interval_s"]), (0.7, 1.0))


class TestV86Init(Base):
    MODE = "stationary_tab"
    LEGACY = False

    def test_wants_nameplates_in_combat_fresh(self):
        self.g.state = State.COMBAT
        self.g.last_obs = obs(thp=100, combat=True)
        self.assertFalse(self.g.wants_nameplates())       # 以前漏了 face_need 初始化会报错


class TestV87HealFirst(Base):
    """v8.7：Simon：战斗结束后先加血，再拾取/剥皮。顺序：回血 → 补 buff（人形）→ 拾取/剥皮（人形）→ 变熊。"""
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.k["interact"] = "g"
        self.g._has_plate_templates = lambda now: False

    def kill(self, hp, combat=False, buffs=("yes", "yes")):
        self.g.tick(bobs(*buffs, thp=100, arange="in", form="bear", mana=90))
        self.clock.t += 1
        self.g.tick(bobs(*buffs, hp=hp, thp=30, arange="in", combat=True, form="bear", mana=90))
        for _ in range(5):
            self.clock.t += 0.2
            self.g.tick(bobs(*buffs, hp=hp, kind="none", combat=combat, form="bear", mana=90))
            if self.g.kills:
                break
        self.assertEqual(self.g.kills, 1)

    def run_until(self, make, cond, n=60, dt=0.5):
        for _ in range(n):
            self.clock.t += dt
            self.g.tick(make())
            if cond():
                return True
        return False

    def test_low_hp_heal_then_loot_in_caster_then_bear(self):
        self.kill(40)
        self.assertNotIn("g", self.keys())
        self.assertEqual(self.g.pending_loot, 1)
        self.assertEqual(self.g.state, State.RESTING)
        self.assertTrue(any("先回血，回完再拾取" in m for m in self.logs))
        self.inp.history.clear()
        self.clock.t += 0.5
        self.g.tick(bobs("yes", "yes", hp=40, kind="none", form="bear", mana=90))     # 退熊
        self.clock.t += 0.5
        self.g.tick(bobs("yes", "yes", hp=40, kind="none", form="caster", mana=90))   # 治疗之触
        self.clock.t += 3
        self.g.tick(bobs("yes", "yes", hp=95, kind="none", form="caster", mana=70))   # 回满 → 人形拾取
        self.assertTrue(self.g.caster_hold)
        self.clock.t += 0.3
        self.g.tick(bobs("yes", "yes", hp=95, kind="none", form="caster", mana=70))
        self.assertTrue(self.run_until(lambda: bobs("yes", "yes", hp=95, kind="none", form="caster", mana=70),
                                       lambda: self.k["bear_form"] in self.keys()[1:]))
        ks = self.keys()
        self.assertEqual(ks[0], self.k["cancel_form"])
        self.assertIn(self.k["healing_touch"], ks)
        gi = ks.index("g")
        self.assertLess(ks.index(self.k["healing_touch"]), gi)                # 先加血
        self.assertEqual(ks[gi:gi + 2], ["g", "g"])                            # 拾取 + 剥皮
        self.assertEqual(ks[gi + 2], self.k["bear_form"])                      # 捡完才变熊（只变一次）
        self.assertEqual(ks.count(self.k["bear_form"]), 2)                     # 退熊 1 次 + 变熊 1 次（取消键=熊键）

    def test_low_hp_with_missing_buffs_order(self):
        self.kill(40, buffs=("no", "no"))
        self.inp.history.clear()
        st = {"t": "no", "m": "no", "hp": 40, "form": "bear"}
        def mk():
            return bobs(st["t"], st["m"], hp=st["hp"], kind="none", form=st["form"], mana=80)
        self.clock.t += 0.5; self.g.tick(mk())                                 # 退熊
        st["form"] = "caster"
        self.clock.t += 0.5; self.g.tick(mk())                                 # 治疗
        st["hp"] = 95
        self.clock.t += 3; self.g.tick(mk())                                   # 回满 → 补 buff
        self.assertEqual(self.g.state, State.BUFFING)
        self.clock.t += 0.3; self.g.tick(mk())                                 # r
        st["t"] = "yes"
        self.clock.t += 1; self.g.tick(mk())
        self.clock.t += 1; self.g.tick(mk())                                   # t
        st["m"] = "yes"
        self.assertTrue(self.run_until(mk, lambda: self.k["bear_form"] in self.keys()[1:]))
        ks = [k for k in self.keys() if k in ("r", "t", "g", self.k["bear_form"], self.k["healing_touch"])]
        order = []
        for k in ks:
            if not order or order[-1] != k:
                order.append(k)
        self.assertEqual(order, [self.k["bear_form"], self.k["healing_touch"], "r", "t", "g", self.k["bear_form"]])
        self.assertTrue(any("顺序" in m or "少变一次形" in m for m in self.logs))

    def test_high_hp_loots_immediately(self):
        self.kill(90)
        self.assertEqual(self.keys()[-2:], ["g", "g"])
        self.assertEqual(self.g.pending_loot, 0)

    def test_attacked_during_heal_fight_then_heal_then_loot(self):
        self.kill(40)
        self.clock.t += 0.5
        self.g.tick(bobs("yes", "yes", hp=40, kind="none", form="bear", mana=90))
        self.clock.t += 0.5
        self.g.tick(bobs("yes", "yes", hp=40, kind="none", form="caster", mana=90))
        self.clock.t += 0.5
        self.g.tick(bobs("yes", "yes", hp=32, kind="none", form="caster", mana=90, combat=True))   # 被打
        self.assertTrue(any("回血时被打" in m for m in self.logs))
        self.assertEqual(self.g.pending_loot, 1)                               # 尸体还记着
        self.assertNotIn("g", self.keys())

    def test_expiry_counts_from_heal_end(self):
        self.g.pending_loot = 1
        self.g.pending_loot_t = self.clock.t - 200
        self.g.rest_end_t = self.clock.t - 5
        self.g.state = State.SEARCHING
        self.g.tick(bobs("yes", "yes", hp=95, kind="none", form="bear", mana=90))
        self.assertIn("g", self.keys())


class TestV88GreyAttacker(Base):
    """v8.8：日志 21:15:47–21:16:19：灰名怪（插件报“被别人抢了”）在打我，旧版一直 Tab 找别的怪。"""
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.g._has_plate_templates = lambda now: False

    def test_tapped_target_attacking_me_is_fought(self):
        self.g.tick(obs(hp=60, thp=74, kind="tapped", combat=True, aggro=True, rng="melee", form="bear"))
        self.assertNotIn("tab", self.keys())
        self.assertEqual(self.g.state, State.COMBAT)
        self.assertTrue(any("当敌人打" in m for m in self.logs))
        self.clock.t += 1
        self.g.tick(obs(hp=58, thp=70, kind="tapped", combat=True, aggro=True, rng="melee", form="bear"))
        self.assertNotIn("tab", self.keys())

    def test_tapped_hitting_me_without_aggro_flag_but_melee_and_hp_drop(self):
        self.g.tick(obs(hp=60, thp=74, kind="tapped", combat=True, rng="melee", form="bear"))
        self.clock.t += 0.5
        self.g.tick(obs(hp=55, thp=74, kind="tapped", combat=True, rng="melee", form="bear"))
        self.assertEqual(self.g.state, State.COMBAT)

    def test_really_tapped_by_others_not_attacking_still_skipped(self):
        self.g.tick(obs(hp=90, thp=74, kind="tapped", combat=False, rng="ranged", form="bear"))
        self.assertEqual(self.keys(), ["tab"])

    def test_being_hit_in_caster_shifts_to_bear_once(self):
        self.g.tick(obs(hp=60, combat=True, form="caster"))
        self.clock.t += 0.2
        self.g.tick(obs(hp=55, combat=True, form="caster"))
        self.clock.t += 0.2
        self.g.tick(obs(hp=52, combat=True, form="caster"))
        self.assertEqual(self.keys().count(self.k["bear_form"]), 1)

    def test_rest_aborts_when_tapped_mob_hits(self):
        self.g.state = State.RESTING
        self.g.rest_start = self.clock.t
        self.g.last_kill_t = self.clock.t
        self.g.tick(obs(hp=50, thp=74, kind="tapped", combat=True, aggro=True, rng="melee", form="caster"))
        self.assertNotEqual(self.g.state, State.RESTING)
        self.assertTrue(any("回血时被打" in m for m in self.logs))

    def test_facing_give_up_does_not_tab_when_attacked(self):
        self.g.tick(obs(thp=100, arange="in", aggro=True, combat=True, form="bear"))
        self.g.face_attempts = 6
        self.g.last_obs = obs(thp=90, arange="in", aggro=True, combat=True, form="bear")
        self.g._face_give_up(self.clock.t, "转身")
        self.assertNotIn("tab", self.keys())
        self.assertEqual(self.g.state, State.COMBAT)


class TestV89TargetAttacker(Base):
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.cfg["keys"]["target_attacker"] = "x"
        self.g._has_plate_templates = lambda now: False

    def test_no_target_in_combat_presses_x_then_tab(self):
        for i in range(8):
            self.g.tick(obs(hp=80 - i, combat=True, form="bear"))
            self.clock.t += 0.9
        ks = self.keys()
        self.assertEqual(ks[:2], ["x", "x"])
        self.assertIn("tab", ks[2:])
        self.assertTrue(any("选择攻击我的怪键" in m for m in self.logs))

    def test_x_then_target_acquired_logs_and_fights(self):
        self.g.tick(obs(hp=80, combat=True, form="bear"))
        self.assertEqual(self.keys(), ["x"])
        self.clock.t += 0.3
        self.g.tick(obs(hp=78, thp=100, kind="enemy", combat=True, aggro=True, rng="melee", form="bear"))
        self.assertEqual(self.g.state, State.COMBAT)
        self.assertTrue(any("后选中目标" in m for m in self.logs))

    def test_blank_key_uses_tab(self):
        self.cfg["keys"]["target_attacker"] = ""
        self.g.tick(obs(hp=80, combat=True, form="bear"))
        self.assertEqual(self.keys(), ["tab"])

    def test_out_of_combat_no_target_does_not_press_x(self):
        self.g.tick(obs(hp=95, combat=False, form="bear"))
        self.assertNotIn("x", self.keys())


class TestV810Humanize(Base):
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.g._has_plate_templates = lambda now: False
        self.cfg["humanize"]["move_enabled"] = True
        self.cfg["humanize"]["break_enabled"] = True

    def test_idle_small_move_forward_back(self):
        self.g.tick(obs(hp=100, form="bear"))
        self.clock.t += 125
        self.g.tick(obs(hp=100, form="bear"))
        ks = self.keys()
        self.assertTrue(("w" in ks and "s" in ks), ks)
        self.assertTrue(any("随机小动作" in m for m in self.logs))

    def test_no_move_in_combat(self):
        self.g.tick(obs(hp=100, form="bear"))
        self.clock.t += 125
        self.g.tick(obs(hp=90, combat=True, form="bear"))
        self.assertNotIn("w", self.keys())
        self.assertNotIn("s", self.keys())

    def test_break_starts_pauses_and_resumes(self):
        self.cfg["humanize"]["move_enabled"] = False
        self.g.tick(obs(hp=100, form="bear"))
        self.clock.t += 19 * 60
        self.inp.pressed.clear() if hasattr(self.inp, "pressed") else None
        self.g.tick(obs(hp=100, form="bear"))
        self.assertIsNotNone(self.g.break_until)
        self.assertIn("随机休息", self.g.status_label())
        n = len(self.keys())
        for _ in range(10):
            self.clock.t += 5
            self.g.tick(obs(hp=100, form="bear"))
        self.assertEqual(len(self.keys()), n)          # 休息时不按键
        self.clock.t += 200
        self.g.tick(obs(hp=100, form="bear"))
        self.assertIsNone(self.g.break_until)
        self.assertTrue(any("随机休息结束" in m for m in self.logs))

    def test_attacked_during_break_fights_and_freezes_countdown(self):
        self.cfg["humanize"]["move_enabled"] = False
        self.g.tick(obs(hp=100, form="bear"))
        self.clock.t += 19 * 60
        self.g.tick(obs(hp=100, form="bear"))
        rem = self.g.break_remaining()
        self.clock.t += 1
        self.g.tick(obs(hp=90, thp=100, kind="enemy", combat=True, aggro=True, rng="melee", arange="in", form="bear"))
        self.assertEqual(self.g.state, State.COMBAT)
        for _ in range(5):
            self.clock.t += 1
            self.g.tick(obs(hp=88, thp=80, kind="enemy", combat=True, aggro=True, rng="melee", arange="in", form="bear"))
        self.assertGreater(self.g.break_remaining(), rem - 2.5)


class TestV811HealChain(Base):
    """v8.11：战斗中治疗回复线 90%：没回到就接着放治疗之触。"""
    MODE = "stationary_tab"

    def setUp(self):
        super().setUp()
        self.cfg["heal"]["heal_to_pct"] = 90

    def engage(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()

    def cast_once(self, hp_after, mana=80):
        self.clock.t += 0.3
        self.g.tick(obs(hp=max(hp_after - 20, 5), thp=60, combat=True, form="caster", casting=False, mana=mana))
        self.clock.t += 0.3
        self.g.tick(obs(hp=max(hp_after - 20, 5), thp=60, combat=True, form="caster", casting=True, mana=mana))
        self.clock.t += 3.1
        self.g.tick(obs(hp=hp_after, thp=60, combat=True, form="caster", casting=False, mana=mana))

    def test_keeps_casting_until_line(self):
        self.engage()
        self.g.tick(obs(hp=25, thp=60, combat=True, form="bear", mana=80))
        self.cast_once(55)
        self.assertEqual(self.g.state, State.HEALING)
        self.assertNotIn(self.k["bear_form"], self.keys()[1:])
        self.cast_once(75)
        self.assertEqual(self.g.state, State.HEALING)
        self.cast_once(95)
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertEqual(self.keys().count("5"), 3)
        self.assertEqual(self.keys()[-1], self.k["bear_form"])
        self.assertTrue(any("→ 75%" in m for m in self.logs))

    def test_stops_on_low_mana(self):
        self.engage()
        self.g.tick(obs(hp=25, thp=60, combat=True, form="bear", mana=12))
        self.cast_once(50, mana=5)
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertTrue(any("法力" in m and "不再治疗" in m for m in self.logs))

    def test_chain_max(self):
        self.cfg["heal"]["heal_chain_max"] = 2
        self.engage()
        self.g.tick(obs(hp=25, thp=60, combat=True, form="bear", mana=80))
        self.cast_once(40)
        self.cast_once(50)
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertEqual(self.keys().count("5"), 2)

    def test_critical_after_heal_still_danger_stops(self):
        self.engage()
        self.g.tick(obs(hp=25, thp=60, combat=True, form="bear", mana=80))
        self.cast_once(10)
        self.clock.t += 0.2
        self.g.tick(obs(hp=10, thp=60, combat=True, form="bear", mana=80))
        self.assertFalse(self.g.stopped)                              # v8.43：不停止，提醒
        self.assertTrue(any("治疗过后" in m and "不停止" in m for m in self.logs))


class TestV812AddonLost(Base):
    """v8.12 日志 22:45:15：WowGrinder 界面挡住游戏 → 截图识别读到血量 0 → 误判死亡停止。"""
    MODE = "stationary_tab"
    LEGACY = False

    def test_screen_fallback_after_addon_does_not_stop(self):
        self.cfg["buffs"]["enabled"] = False
        self.g.tick(obs(hp=80, thp=60, arange="in", combat=True, form="bear"))
        self.inp.history.clear()
        for _ in range(20):
            self.clock.t += 0.5
            self.g.tick(obs(hp=0, source="screen"))
        self.assertFalse(self.g.stopped)
        self.assertEqual(self.keys(), [])
        self.assertTrue(any("读不到插件色块" in m for m in self.logs))

    def test_addon_hp_zero_still_stops(self):
        self.g.tick(obs(hp=80, form="bear"))
        for _ in range(20):
            self.clock.t += 0.5
            self.g.tick(obs(hp=0, form="bear"))
        self.assertFalse(self.g.stopped)                              # v8.43：当成死了 → 等待，不停止
        self.assertTrue(self.g.dead_idle)

    def test_screen_only_user_unchanged(self):
        for _ in range(20):
            self.clock.t += 0.5
            self.g.tick(obs(hp=0, source="screen"))
        self.assertFalse(self.g.stopped)                              # v8.43
        self.assertTrue(self.g.dead_idle)


class TestV812Window(unittest.TestCase):
    def test_own_window_not_game(self):
        import os
        from bot import window
        orig = window.foreground_info
        try:
            window.foreground_info = lambda: ("python.exe", os.getpid(), "WowGrinder 熊德自动打怪")
            self.assertFalse(window.game_in_foreground("World of Warcraft,魔兽世界,WoW"))
            window.foreground_info = lambda: ("chrome.exe", 1, "部落睡袋_魔兽世界 - Google Chrome")
            self.assertFalse(window.game_in_foreground("World of Warcraft,魔兽世界,WoW"))
            window.foreground_info = lambda: ("wow.exe", 1, "魔兽世界")
            self.assertTrue(window.game_in_foreground("World of Warcraft,魔兽世界,WoW"))
        finally:
            window.foreground_info = orig


class TestV814ClickSameAsTab(Base):
    """v8.14：点姓名板选到的目标和 Tab 选到的走同一套流程；换目标时也先点姓名板。"""
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.cfg["loot"]["skin"] = False
        self.g._has_plate_templates = lambda now: True

    def clicks(self):
        return [e for e in self.inp._pg.events if e[0] == "click"]

    def test_dead_target_next_pick_clicks_plate_not_tab(self):
        self.g.tick(obs(thp=0, kind="dead", plates=[plate(900, 480)]))
        self.assertEqual(len(self.clicks()), 1)
        self.assertNotIn("tab", self.keys())
        self.assertTrue(any("目标已死" in m and "点姓名板" in m for m in self.logs))

    def test_clicked_far_target_cleared_and_plate_not_reclicked(self):
        self.g.tick(obs(plates=[plate(900, 480)]))
        self.clock.t += 0.4
        self.g.tick(obs(thp=100, rng="far"))
        self.assertIn(self.k["clear_target"], self.keys())
        self.clock.t += 1.0
        self.g.tick(obs(plates=[plate(900, 480)]))
        self.assertEqual(len(self.clicks()), 1)            # 8 秒内不再点同一个
        self.assertIn("tab", self.keys())

    def test_clicked_ranged_target_pulls_like_tab(self):
        self.g.tick(obs(plates=[plate(900, 480)]))
        self.clock.t += 0.4
        self.g.tick(obs(thp=100, rng="ranged"))
        self.assertEqual(self.g.state, State.APPROACHING)
        self.assertEqual(self.keys()[-1], self.k["attack"])
        self.clock.t += 2.6
        self.g.tick(obs(thp=100, rng="ranged"))
        self.assertEqual(self.keys().count(self.k["attack"]), 2)    # 2.5 秒后再按一次开怪键（跟 Tab 一样）

    def test_clicks_count_toward_max_tabs_round(self):
        self.cfg["stationary"]["plate_max_fails"] = 99
        self.cfg["stationary"]["pause_min_s"] = self.cfg["stationary"]["pause_max_s"] = 100.0
        xs = [300 + 150 * i for i in range(10)]
        for i in range(10):
            self.clock.t += 0.9
            self.g.tick(obs(plates=[plate(x, 480) for x in xs]))
        self.assertTrue(any("没找到能打的怪，等" in m for m in self.logs))
        self.assertEqual(len(self.clicks()), 6)            # 一轮 6 次（点 + Tab 共用），然后等

    def test_clicked_tapped_not_attacking_switches(self):
        self.g.tick(obs(plates=[plate(900, 480)]))
        self.clock.t += 0.8
        self.g.tick(obs(thp=100, kind="tapped", rng="ranged", plates=[plate(500, 480)]))
        self.assertTrue(any("后目标不能打" in m for m in self.logs))
        self.assertEqual(len(self.clicks()), 2)


def cobs(combo=None, energy=None, **kw):
    o = obs(**kw)
    o.combo, o.energy = combo, energy
    return o


class TestV815Cat(Base):
    """v8.15：战斗形态 = 猎豹。"""
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.g._has_plate_templates = lambda now: False
        self.cfg["cat"]["combat_form"] = "猎豹"
        self.k.update({"cat_form": "f2", "cat_builder": "7", "cat_finisher": "8", "cat_rake": "",
                       "cat_tigers_fury": "", "bear_form": "z", "cancel_form": "z"})

    def fight(self, n, **kw):
        self.g.tick(cobs(thp=100, arange="in", form="cat", **kw))
        for i in range(n):
            self.clock.t += 1.1
            self.g.tick(cobs(thp=90 - i, arange="in", combat=True, form="cat", aggro=True, since_damage=0.2, **kw))

    def test_timed_rotation_without_readings(self):
        self.fight(10)
        ks = [k for k in self.keys() if k in ("7", "8")]
        self.assertEqual(ks[:5], ["7", "7", "7", "7", "8"])          # 读不到连击点：4 次爪击 → 终结技
        self.assertEqual(self.keys()[0], self.k["attack"])          # 开怪键照旧
        skill_keys = {sk["key"] for sk in self.cfg["rotation"]["skills"]} - {"7", "8", self.k["attack"]}
        self.assertFalse(skill_keys & set(self.keys()))             # 熊的技能表不按

    def test_combo_reading_drives_finisher(self):
        self.cfg["cat"]["finisher_cp"] = 2
        self.fight(2, combo=2, energy=100)
        self.assertIn("8", self.keys())

    def test_low_energy_waits(self):
        self.fight(4, combo=0, energy=14.3)
        self.assertNotIn("7", self.keys())

    def test_in_bear_form_shifts_to_cat(self):
        self.g.tick(cobs(thp=100, arange="in", form="bear"))
        self.clock.t += 1.1
        self.g.tick(cobs(thp=90, arange="in", combat=True, form="bear", aggro=True))
        self.assertIn("f2", self.keys())
        self.assertNotIn("z", self.keys())

    def test_heal_cancels_with_cat_key_and_returns_to_cat(self):
        self.cfg["heal"]["heal_to_pct"] = 0
        self.g.tick(cobs(thp=100, arange="in", form="cat"))
        self.inp.history.clear()
        self.g.tick(cobs(hp=25, thp=60, combat=True, form="cat"))
        self.assertEqual(self.keys(), ["f2"])                       # 取消键=熊键 → 改按猎豹键退出
        self.clock.t += 0.3
        self.g.tick(cobs(hp=25, thp=60, combat=True, form="caster", casting=False))
        self.clock.t += 0.3
        self.g.tick(cobs(hp=25, thp=60, combat=True, form="caster", casting=True))
        self.clock.t += 3.1
        self.g.tick(cobs(hp=60, thp=60, combat=True, form="caster", casting=False))
        self.assertEqual(self.keys()[-1], "f2")
        self.assertNotIn("z", self.keys())

    def test_missing_keys_warn_not_crash(self):
        self.k.update({"cat_form": "", "cat_builder": "", "cat_finisher": ""})
        self.fight(3)
        self.assertTrue(any("没填" in m for m in self.logs))
        from bot.config import cat_missing
        self.assertEqual(len(cat_missing(self.cfg)), 3)

    def test_prowl_opener_when_melee(self):
        self.cfg["cat"]["use_prowl"] = True
        self.k.update({"cat_prowl": "4", "cat_opener": "5"})
        self.g.tick(cobs(thp=100, rng="melee", form="cat"))
        self.assertEqual(self.keys()[:2], ["4", self.k["attack"]])     # v8.21：潜行后也按开怪键

    def test_prowl_skipped_when_ranged(self):
        self.cfg["cat"]["use_prowl"] = True
        self.k.update({"cat_prowl": "4", "cat_opener": "5"})
        self.g.tick(cobs(thp=100, rng="ranged", form="cat"))
        self.assertNotIn("4", self.keys())
        self.assertIn(self.k["attack"], self.keys())

    def test_bear_default_unchanged(self):
        from bot.config import DEFAULT_CONFIG, is_cat
        self.assertFalse(is_cat(DEFAULT_CONFIG))


class TestV816WantsPlates(Base):
    MODE = "stationary_tab"
    LEGACY = False

    def test_wants_plates_with_dead_target_out_of_combat(self):
        self.g.last_obs = obs(thp=0, kind="dead")
        self.assertTrue(self.g.wants_nameplates())
        self.g.last_obs = obs(thp=100, rng="far")
        self.assertTrue(self.g.wants_nameplates())

    def test_no_plates_in_combat_search(self):
        self.g.last_obs = obs(thp=100, combat=True)
        self.assertFalse(self.g.wants_nameplates())


def pobs(pos=None, facing=None, map_id=1442, **kw):
    o = obs(**kw)
    o.pos, o.facing, o.map_id = pos, facing, map_id
    return o


class TestV817ClickNoLootInCombat(Base):
    """v8.17 #1：点击模式多只怪 —— 战斗中不拾取/剥皮，马上按 x 换打我的怪；脱战后再从队列里捡。"""
    MODE = "click"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.k.update({"target_attacker": "x", "interact": "g", "loot": "g"})

    def kill_in_combat(self, hp_at_kill=80):
        self.g.tick(obs(thp=100))
        self.clock.t += 0.5
        self.g.tick(obs(hp=90, thp=50, combat=True, form="bear"))
        self.clock.t += 0.5
        self.g.tick(obs(hp=hp_at_kill, thp=10, combat=True, form="bear"))
        self.clock.t += 0.5
        self.inp.history.clear()
        self.g.tick(obs(hp=hp_at_kill - 3, thp=0, kind="dead", combat=True, form="bear"))

    def test_no_loot_or_skin_while_in_combat(self):
        self.kill_in_combat()
        self.assertEqual(self.g.kills, 1)
        self.assertNotIn("g", self.keys())
        self.assertEqual(self.g.pending_loot, 1)
        self.assertNotEqual(self.g.state, State.SKINNING)
        self.assertTrue(any("先不拾取" in m for m in self.logs))

    def test_switches_to_attacker_with_x_then_tab(self):
        self.kill_in_combat()
        self.inp.history.clear()
        for i in range(3):
            self.clock.t += 0.9
            self.g.tick(obs(hp=70 - 3 * i, combat=True, form="bear"))     # 还在掉血，没目标
        self.assertEqual(self.keys()[:2], ["x", "x"])                      # 先按 x
        self.assertIn(self.k["target"], self.keys())                       # 2 次不行再 Tab
        self.assertNotIn("g", self.keys())

    def test_x_picks_attacker_and_fights(self):
        self.kill_in_combat()
        self.clock.t += 0.9
        self.g.tick(obs(hp=70, combat=True, form="bear"))
        self.clock.t += 0.3
        self.g.tick(obs(hp=68, thp=100, combat=True, form="bear", aggro=True))
        self.assertIn(self.g.state, (State.APPROACHING, State.COMBAT))

    def test_loot_after_combat_ends(self):
        self.kill_in_combat()
        self.clock.t += 5
        self.inp.history.clear()
        self.g.tick(obs(hp=77, combat=False, form="bear"))
        self.assertIn("g", self.keys())
        self.assertEqual(self.g.pending_loot, 0)

    def test_residual_combat_waits_then_x(self):
        self.kill_in_combat()
        self.inp.history.clear()
        self.clock.t += 1
        self.g.tick(obs(hp=77, combat=True, form="bear"))          # 残留战斗、没掉血 → 等
        self.assertNotIn("x", self.keys())
        self.assertNotIn("g", self.keys())
        self.clock.t += 6
        self.g.tick(obs(hp=77, combat=True, form="bear"))          # 6 秒还在战斗 → 按 x
        self.assertIn("x", self.keys())

    def test_single_kill_out_of_combat_loots_now(self):
        self.g.tick(obs(thp=100))
        self.g.tick(obs(hp=95, thp=50, combat=True, form="bear"))
        self.inp.history.clear()
        self.g.tick(obs(hp=95, thp=0, kind="dead", lootable=True, combat=False, form="bear"))
        self.assertIn("g", self.keys())

    def test_skin_interrupted_by_combat_click_mode(self):
        self.g.tick(obs(thp=100))
        self.g.tick(obs(hp=95, thp=50, combat=True, form="bear"))
        self.g.tick(obs(hp=95, thp=0, kind="dead", lootable=True, combat=False, form="bear"))
        self.assertEqual(self.g.state, State.SKINNING)
        self.clock.t += 0.5
        self.g.tick(obs(hp=95, combat=True, form="bear"))
        self.assertEqual(self.g.pending_loot, 1)
        self.assertEqual(self.g.state, State.SEARCHING)


class TestV817StationaryNoLootInCombat(TestV817ClickNoLootInCombat):
    MODE = "stationary_tab"

    def setUp(self):
        super().setUp()
        self.g._has_plate_templates = lambda now: False

    def kill_in_combat(self, hp_at_kill=80):
        self.g.tick(obs(thp=100, arange="in"))
        self.clock.t += 0.5
        self.g.tick(obs(hp=90, thp=50, arange="in", combat=True, form="bear", aggro=True))
        self.clock.t += 0.5
        self.g.tick(obs(hp=hp_at_kill, thp=10, arange="in", combat=True, form="bear", aggro=True))
        self.clock.t += 0.5
        self.inp.history.clear()
        self.g.tick(obs(hp=hp_at_kill - 3, thp=0, kind="dead", combat=True, form="bear"))

    def test_x_picks_attacker_and_fights(self):
        self.kill_in_combat()
        self.clock.t += 0.9
        self.g.tick(obs(hp=70, combat=True, form="bear"))
        self.clock.t += 0.3
        self.g.tick(obs(hp=68, thp=100, combat=True, form="bear", aggro=True, arange="in"))
        self.assertEqual(self.g.state, State.COMBAT)

    def test_single_kill_out_of_combat_loots_now(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.g.tick(obs(hp=95, thp=50, arange="in", combat=True, form="bear"))
        self.inp.history.clear()
        self.g.tick(obs(hp=95, thp=0, kind="dead", lootable=True, combat=False, form="bear"))
        self.assertIn("g", self.keys())

    def test_skin_interrupted_by_combat_click_mode(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.g.tick(obs(hp=95, thp=50, arange="in", combat=True, form="bear"))
        self.g.tick(obs(hp=95, thp=0, kind="dead", lootable=True, combat=False, form="bear"))
        self.assertEqual(self.g.state, State.SKINNING)
        self.clock.t += 0.5
        self.g.tick(obs(hp=95, combat=True, form="bear"))
        self.assertEqual(self.g.pending_loot, 1)


class TestV817ManaReserve(Base):
    """v8.17 #2：预留变身法力（猎豹 30%）。"""
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.g._has_plate_templates = lambda now: False
        self.cfg["cat"]["combat_form"] = "猎豹"
        self.cfg["heal"].update(reserve_cat_pct=30, reserve_bear_pct=30, heal_cost_pct=20, heal_to_pct=90)
        self.k.update({"cat_form": "f2", "cat_builder": "7", "cat_finisher": "8", "bear_form": "z", "cancel_form": "z"})

    def engage(self):
        self.g.tick(obs(thp=100, arange="in", form="cat"))
        self.inp.history.clear()

    def test_no_combat_heal_when_it_would_eat_reserve(self):
        self.engage()
        self.g.tick(obs(hp=25, thp=60, combat=True, form="cat", mana=45, arange="in", aggro=True))
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertNotIn(self.k["healing_touch"], self.keys())
        self.assertTrue(any("不治疗" in m and "预留" in m for m in self.logs))

    def test_combat_heal_with_enough_mana(self):
        self.engage()
        self.g.tick(obs(hp=25, thp=60, combat=True, form="cat", mana=80, arange="in", aggro=True))
        self.assertEqual(self.g.state, State.HEALING)

    def test_heal_chain_stops_at_reserve_and_shifts_back(self):
        self.engage()
        self.g.tick(obs(hp=25, thp=60, combat=True, form="cat", mana=80))
        self.clock.t += 0.3
        self.g.tick(obs(hp=25, thp=60, combat=True, form="caster", casting=False, mana=80))
        self.clock.t += 0.3
        self.g.tick(obs(hp=25, thp=60, combat=True, form="caster", casting=True, mana=80))
        self.clock.t += 3.1
        self.g.tick(obs(hp=50, thp=60, combat=True, form="caster", casting=False, mana=45))
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertEqual(self.keys()[-1], "f2")                       # 变回猎豹
        self.assertTrue(any("预留" in m and "不再治疗" in m for m in self.logs))
        self.assertAlmostEqual(self.g.mana_cost["heal"], 35)          # 实测治疗耗蓝 80 → 45

    def test_learns_shift_cost(self):
        self.g.tick(obs(form="caster", mana=72))
        self.g.tick(obs(form="cat", mana=40))
        self.assertEqual(self.g.mana_cost["shift_cat"], 32)
        self.assertEqual(self.g._reserve(), 34)                       # 实测比设置高 → 实测+2

    def test_rest_shifts_back_and_waits_for_mana(self):
        self.g.start_rest(obs(hp=50, form="caster", mana=40))
        self.inp.history.clear()
        self.g.tick(obs(hp=50, form="caster", mana=40, casting=False))
        self.assertEqual(self.keys(), ["f2"])                          # 40−20 < 30 → 变回猎豹
        self.clock.t += 2
        self.g.tick(obs(hp=50, form="cat", mana=12, casting=False))
        self.assertEqual(self.keys(), ["f2"])                          # 在猎豹形态等回蓝
        self.assertEqual(self.g.state, State.RESTING)
        self.clock.t += 2
        self.g.tick(obs(hp=50, form="cat", mana=55, casting=False))
        self.assertEqual(self.keys(), ["f2", "f2"])                    # 蓝够了 → 退出形态（取消键=熊键 → 按猎豹键）去治疗
        self.assertFalse(self.g.bear_belief)

    def test_rest_low_mana_stays_caster(self):
        self.g.start_rest(obs(hp=50, form="caster", mana=10))
        self.inp.history.clear()
        self.g.tick(obs(hp=50, form="caster", mana=10, casting=False))
        self.assertEqual(self.keys(), [])                              # 连变身的蓝都不够 → 人形等回蓝

    def test_buff_skipped_when_short(self):
        self.cfg["buffs"]["enabled"] = True
        self.k.update({"thorns": "r", "motw": ""})
        o = obs(form="cat", mana=35)
        o.buff_thorns, o.buff_motw = "no", "yes"
        self.assertFalse(self.g._maybe_buff(o, self.clock.t))
        self.assertTrue(any("预留" in m for m in self.logs))
        o.mana = 80
        self.g.buff_retry_until.clear()
        self.assertTrue(self.g._maybe_buff(o, self.clock.t))

    def test_reserve_zero_disables(self):
        self.cfg["heal"]["reserve_cat_pct"] = 0
        self.assertIsNone(self.g._mana_short(obs(mana=5), "heal"))


class TestV817Area(Base):
    """v8.17 #3：范围中心（开始位置）、F9 检查、F8 设中心、原地模式也走回去。"""
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.g._has_plate_templates = lambda now: False
        self.cfg["area"].update(enabled=True, radius=1.0)

    def test_home_on_start_and_f9(self):
        self.g.tick(pobs(pos=(50.0, 50.0), facing=0.0))
        self.assertEqual(self.g.home, (50.0, 50.0, 1442))
        self.g.request_area("check")
        self.g.tick(pobs(pos=(50.4, 50.0), facing=0.0))
        self.assertTrue(any("在范围内" in m for m in self.logs))
        self.assertTrue(self.g.area_label().startswith("内 0.40/1"))
        self.g.request_area("check")
        self.g.tick(pobs(pos=(52.0, 50.0), facing=0.0, combat=True))
        self.assertTrue(any("在范围外" in m for m in self.logs))

    def test_f8_sets_home_and_saves(self):
        got = []
        self.g.on_home = lambda x, y, m: got.append((x, y, m))
        self.g.tick(pobs(pos=(50.0, 50.0), facing=0.0))
        self.g.request_area("home")
        self.g.tick(pobs(pos=(60.0, 40.0), facing=0.0, map_id=1413))
        self.assertEqual(self.g.home, (60.0, 40.0, 1413))
        self.assertEqual(got, [(60.0, 40.0, 1413)])

    def test_other_map_warns_with_f8_hint(self):
        self.g.home = (50.0, 50.0, 1413)
        self.g.tick(pobs(pos=(70.0, 70.0), facing=0.0, map_id=1442))
        self.assertNotEqual(self.g.state, State.RETURNING)
        self.assertTrue(any("不是中心所在地图" in m and "F8" in m for m in self.logs))

    def test_stationary_walks_back_when_outside(self):
        self.g.home = (50.0, 50.0, 1442)
        self.g.home_from_start = False
        self.g.tick(pobs(pos=(52.0, 50.0), facing=0.0))
        self.assertEqual(self.g.state, State.RETURNING)
        self.g.tick(pobs(pos=(52.0, 50.0), facing=0.0))     # 面北，中心在西 → 先原地左转
        self.assertIn(self.k["turn_left"], self.keys())

    def test_no_return_in_combat_or_when_disabled(self):
        self.g.home = (50.0, 50.0, 1442)
        self.g.tick(pobs(pos=(52.0, 50.0), facing=0.0, combat=True))
        self.assertNotEqual(self.g.state, State.RETURNING)
        self.cfg["area"]["enabled"] = False
        self.g.tick(pobs(pos=(52.0, 50.0), facing=0.0))
        self.assertNotEqual(self.g.state, State.RETURNING)

    def test_humanize_paused_while_returning(self):
        self.g.state = State.RETURNING
        self.assertFalse(self.g._humanize(pobs(pos=(52.0, 50.0)), self.clock.t))

    def test_heading_from_motion_when_facing_unreadable(self):
        self.g.home = (50.0, 50.0, 1442)
        self.g.walking = True
        self.g.mh_pos = (50.0, 52.0)
        h = self.g._motion_heading(pobs(pos=(50.0, 51.9)))
        self.assertAlmostEqual(h, 0.0)                       # 往北走（y 变小）→ 朝向 0

    def test_area_report_texts(self):
        from bot.navigation import area_label, area_report
        self.assertIn("读不到坐标", area_report(None, None, None, (1, 1, 0), 1, True))
        self.assertIn("还没有中心点", area_report((1, 1), 0.0, 5, None, 1, True))
        self.assertEqual(area_label((51.5, 50), 5, (50, 50, 5), 1.0, False), "外 1.50/1(关)")


class TestV817ClickLeash(Base):
    MODE = "click"
    LEGACY = False

    def test_approach_too_far_returns(self):
        self.cfg["buffs"]["enabled"] = False
        self.cfg["area"].update(enabled=True, radius=1.0, leash_ratio=1.3)
        self.g.home = (50.0, 50.0, 1442)
        self.g.home_from_start = False
        self.g.state = State.APPROACHING
        self.g.approach_start = self.clock.t
        self.g.tick(pobs(pos=(51.5, 50.0), facing=0.0, thp=100))
        self.assertEqual(self.g.state, State.RETURNING)


class TestV817FaceClick(Base):
    """v8.17（t138u）：面向错误 → 右键点目标姓名板（v0.10 标记在姓名板中间），不再先左右转。"""
    MODE = "stationary_tab"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        self.inp.holds.clear()

    def o(self, n, marker=(1000, 400), center=True, thp=100):
        o = obs(thp=thp, arange="in", combat=True, form="bear", aggro=True, since_damage=0.5)
        o.facing_count = n % 8
        o.marker_center = center
        if marker:
            o.marker, o.frame_size = marker, (1920, 1080)
        return o

    def clicks(self):
        return [k for k in self.keys() if k.startswith("click(")]

    def turns(self):
        return [k for k in self.keys() if k in (self.k["turn_left"], self.k["turn_right"])]

    def test_facing_error_right_clicks_marker_not_turn(self):
        self.clock.t += 1
        self.g.tick(self.o(0))
        self.clock.t += 1
        self.g.tick(self.o(1))                                  # 新的面向错误
        self.assertEqual(self.clicks(), ["click(1000,400)"])
        self.assertEqual(self.turns(), [])
        self.assertTrue(any("右键点目标姓名板" in m for m in self.logs))

    def test_fixed_after_quiet_5s(self):
        self.clock.t += 1
        self.g.tick(self.o(0))
        self.clock.t += 1
        self.g.tick(self.o(1))
        for _ in range(6):
            self.clock.t += 1
            self.g.tick(self.o(1))                              # 没有新的报错
        self.assertEqual(self.g.fclick_n, 0)
        self.assertTrue(any("面向修好了" in m for m in self.logs))
        self.assertEqual(self.turns(), [])

    def test_cooldown_and_fallback_after_two_clicks(self):
        self.clock.t += 1
        self.g.tick(self.o(0))
        n = 0
        for i in range(1, 6):
            self.clock.t += 0.5 if i == 2 else 1.0
            self.g.tick(self.o(i))
        # 第 1 次 1.0s 点；第 2 次 0.5s 后（冷却中，不点、不转）；之后 1.0s 第二次点；再报错 → 改用转身
        self.assertEqual(len(self.clicks()), 2)
        self.assertGreaterEqual(len(self.turns()), 1)
        self.assertTrue(any("先往标记那边转一下身" in m for m in self.logs))
        self.assertNotIn(self.k["interact"], self.keys())

    def test_never_clicks_without_marker(self):
        self.clock.t += 1
        self.g.tick(self.o(0, marker=None))
        self.clock.t += 1
        self.g.tick(self.o(1, marker=None))
        self.assertEqual(self.clicks(), [])
        self.assertGreaterEqual(len(self.turns()), 1)          # 原来的扫一圈

    def test_old_addon_marker_above_plate_does_not_click(self):
        self.clock.t += 1
        self.g.tick(self.o(0, center=False))
        self.clock.t += 1
        self.g.tick(self.o(1, center=False))
        self.assertEqual(self.clicks(), [])
        self.assertTrue(any("v0.10" in m for m in self.logs))

    def test_disabled_uses_turning(self):
        self.cfg["face_click"]["enabled"] = False
        self.clock.t += 1
        self.g.tick(self.o(0))
        self.clock.t += 1
        self.g.tick(self.o(1))
        self.assertEqual(self.clicks(), [])
        self.assertGreaterEqual(len(self.turns()), 1)


class TestV817FaceClickClickMode(Base):
    MODE = "click"
    LEGACY = False

    def test_click_mode_facing_light_right_clicks_marker(self):
        self.cfg["buffs"]["enabled"] = False
        self.g.tick(obs(thp=100))
        self.g.tick(obs(hp=95, thp=90, combat=True, form="bear"))
        self.assertEqual(self.g.state, State.COMBAT)
        self.inp.history.clear()
        self.clock.t += 0.5
        o = obs(hp=95, thp=90, combat=True, form="bear", err_facing=True, error="facing")
        o.marker, o.marker_center, o.frame_size, o.facing_count = (900, 300), True, (1920, 1080), 1
        self.g.tick(o)
        self.assertIn("click(900,300)", self.keys())
        self.clock.t += 0.5                                       # 报错灯还亮（旧的）→ 不再点、不按补救键
        o.facing_count = 1
        self.inp.history.clear()
        self.g.tick(o)
        self.assertFalse([k for k in self.keys() if k.startswith("click(")])


class TestV010Marker(unittest.TestCase):
    def test_reader_marker_center_flag(self):
        from bot.addon_reader import AddonReader, paint_test_frame
        from bot.config import DEFAULT_CONFIG as D
        import copy
        cfg = copy.deepcopy(D)
        for mc in (False, True):
            fr = paint_test_frame({}, size=(200, 400), v08=True, combo=1, energy_lvl=3, marker_center=mc)
            st = AddonReader(cfg).read(fr)
            self.assertTrue(st.ok, st.reason)
            self.assertEqual(st.marker_center, mc)
            self.assertEqual(st.combo, 1)

    def test_lua_marker_at_plate_center(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        lua = open(os.path.join(root, "addon", "BotStatus", "BotStatus.lua"), encoding="utf-8").read()
        toc = open(os.path.join(root, "addon", "BotStatus", "BotStatus.toc"), encoding="utf-8").read()
        self.assertIn('marker:SetPoint("CENTER", plate, "CENTER", 0, 0)', lua)
        self.assertIn("Paint(28, C.green)", lua)          # v0.11 签名（v0.10 = 白）
        self.assertRegex(toc, r"## Version: (0\.1[2-9]|0\.[2-9]\d)\.0")   # 插件版本往上走


class TestV010MarkerOnPlate(unittest.TestCase):
    def test_marker_found_at_plate_center_over_health_bar(self):
        from bot.marker import find_marker
        fr = np.full((1080, 1920, 3), (60, 90, 70), np.uint8)
        fr[392:406, 900:1020] = (0, 0, 200)                  # 红血条（姓名板）中心 (960, 399)
        fr[380:392, 930:990] = (255, 255, 255)               # 名字
        x0, y0 = 960 - 10, 399 - 6                           # 20x12 标记，中心对齐姓名板中心
        fr[y0:y0 + 12, x0:x0 + 20] = (0, 0, 0)
        fr[y0 + 2:y0 + 10, x0 + 2:x0 + 10] = (255, 255, 0)  # 青（BGR）
        fr[y0 + 2:y0 + 10, x0 + 10:x0 + 18] = (255, 0, 255)  # 洋红
        m = find_marker(fr)
        self.assertIsNotNone(m)
        self.assertLessEqual(abs(m[0] - 960), 2)
        self.assertLessEqual(abs(m[1] - 399), 2)


class TestV818ClickModeBuffs(Base):
    """v8.18（t139u）：点击模式脱战没目标时也补荆棘术/野性印记（日志 02:45:18–02:46:15 荆棘=no 一直没补）。"""
    MODE = "click"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = True
        self.k.update({"thorns": "r", "motw": "t", "bear_form": "q", "cancel_form": "z"})

    def bobs(self, thorns, motw, **kw):
        o = obs(**kw)
        o.buff_thorns, o.buff_motw = thorns, motw
        return o

    def test_click_mode_casts_missing_thorns(self):
        self.g.tick(self.bobs("no", "yes", form="bear", mana=100))
        self.assertEqual(self.g.state, State.BUFFING)
        self.clock.t += 1.1
        self.g.tick(self.bobs("no", "yes", form="bear", mana=100))      # 退出熊
        self.clock.t += 1.1
        self.g.tick(self.bobs("no", "yes", form="caster", mana=100))    # 按荆棘术
        self.assertIn("r", self.keys())
        self.assertNotIn("t", self.keys())

    def test_no_buff_in_combat_click_mode(self):
        self.g.tick(self.bobs("no", "no", form="bear", mana=100, combat=True, hp=90))
        self.assertNotEqual(self.g.state, State.BUFFING)

    def test_no_buff_when_target_alive(self):
        self.g.tick(self.bobs("no", "no", form="bear", mana=100, thp=100))
        self.assertNotEqual(self.g.state, State.BUFFING)


class TestV819SpinHomeClick(Base):
    """v8.19（t140u）：找怪转了 360° 还没找到怪 → 走回范围中心。"""
    MODE = "click"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.cfg["area"].update(enabled=True, radius=1.0)
        self.g.home = (50.0, 50.0, 1442)
        self.g.home_from_start = False

    def spin(self, ticks, pos=(50.8, 50.0), **kw):
        for _ in range(ticks):
            self.clock.t += 0.6
            self.g.tick(pobs(pos=pos, facing=0.0, **kw))
            if self.g.state == State.RETURNING:
                break

    def test_full_turn_walks_back(self):
        self.spin(12)
        self.assertEqual(self.g.state, State.RETURNING)
        self.assertEqual(self.keys().count(self.k["scan_turn"]), 10)        # 10 × 200ms × 180°/s = 360°
        self.assertTrue(any("走回范围中心" in m for m in self.logs))
        self.assertEqual(self.g.spin_deg, 0.0)

    def test_near_center_waits_instead(self):
        self.spin(11, pos=(50.1, 50.0))
        self.assertNotEqual(self.g.state, State.RETURNING)
        self.assertTrue(any("已经在中心附近" in m for m in self.logs))
        n = self.keys().count(self.k["scan_turn"])
        self.clock.t += 1
        self.g.tick(pobs(pos=(50.1, 50.0), facing=0.0))
        self.assertEqual(self.keys().count(self.k["scan_turn"]), n)          # 等待中不转
        self.clock.t += 3
        self.g.tick(pobs(pos=(50.1, 50.0), facing=0.0))
        self.assertEqual(self.keys().count(self.k["scan_turn"]), n + 1)      # 等完接着找

    def test_combat_resets_counter(self):
        self.spin(8)
        self.assertGreater(self.g.spin_deg, 200)
        self.g.tick(pobs(pos=(50.8, 50.0), facing=0.0, combat=True, hp=90))
        self.assertEqual(self.g.spin_deg, 0.0)

    def test_disabled(self):
        self.cfg["scan"]["spin_home"] = False
        self.spin(15)
        self.assertNotEqual(self.g.state, State.RETURNING)

    def test_combat_interrupts_walk_back(self):
        self.spin(12)
        self.assertEqual(self.g.state, State.RETURNING)
        self.g.tick(pobs(pos=(50.8, 50.0), facing=0.0, combat=True, hp=90))
        self.assertNotEqual(self.g.state, State.RETURNING)


class TestV819SpinHomeStationary(Base):
    MODE = "stationary_tab"
    LEGACY = False

    def test_tab_rounds_with_turns_then_walk_back(self):
        self.cfg["buffs"]["enabled"] = False
        self.g._has_plate_templates = lambda now: False
        self.cfg["area"].update(enabled=True, radius=1.0)
        self.g.home = (50.0, 50.0, 1442)
        self.g.home_from_start = False
        for _ in range(400):
            self.clock.t += 0.7
            self.g.tick(pobs(pos=(50.8, 50.0), facing=0.0))
            if self.g.state == State.RETURNING:
                break
        self.assertEqual(self.g.state, State.RETURNING)
        self.assertTrue(any("找怪转了约" in m for m in self.logs))
        self.assertIn("tab", self.keys())


class TestV820ClickFacingNoInteract(Base):
    """v8.20（t141u）：点击模式面向错误 → 右键点姓名板标记；不行就转身，绝不按互动键。"""
    MODE = "click"
    LEGACY = False
    RNG = "ranged"          # v8.24：近战距离内不右键（见 TestV824）

    def fight(self):
        self.cfg["buffs"]["enabled"] = False
        self.g.tick(obs(thp=100))
        self.g.tick(obs(hp=95, thp=90, combat=True, form="bear"))
        self.assertEqual(self.g.state, State.COMBAT)
        self.inp.history.clear()

    def o(self, n, marker=(900, 500), state="shown"):
        o = obs(hp=90, thp=80, combat=True, form="bear", err_facing=True, error="facing", rng=self.RNG)
        o.marker, o.marker_center, o.frame_size, o.facing_count = marker, True, (1920, 1080), n
        o.marker_state = state
        return o

    def test_like_log_032019(self):
        self.fight()
        for i in range(1, 12):
            self.clock.t += 1.0
            self.g.tick(self.o(i))
        ks = self.keys()
        self.assertNotIn(self.k["interact"], ks)
        clicks = [k for k in ks if k.startswith("click(")]
        turns = [k for k in ks if k in (self.k["turn_left"], self.k["turn_right"])]
        self.assertGreaterEqual(len(clicks), 4)          # 转身后又先右键点
        self.assertGreaterEqual(len(turns), 1)
        self.assertTrue(ks[0].startswith("click("))
        self.assertFalse(any("按互动键" in m for m in self.logs))

    def test_no_marker_logs_reason_and_turns(self):
        self.fight()
        for i in range(1, 3):
            self.clock.t += 1.0
            self.g.tick(self.o(i, marker=None, state="no_plate"))
        self.assertNotIn(self.k["interact"], self.keys())
        self.assertFalse([k for k in self.keys() if k.startswith("click(")])
        self.assertTrue(any("目标没有姓名板" in m for m in self.logs))
        self.assertTrue(set(self.keys()) & {self.k["turn_left"], self.k["turn_right"]})

    def test_range_error_right_clicks_marker(self):
        """v8.22：距离太远也右键点标记，不按互动键。"""
        self.fight()
        self.clock.t += 0.5
        o = obs(hp=90, thp=80, combat=True, form="bear", error="range")
        o.marker, o.marker_center, o.frame_size = (900, 300), True, (1920, 1080)
        self.g.tick(o)
        self.assertIn("click(900,300)", self.keys())
        self.assertNotIn(self.k["interact"], self.keys())


class TestV821OpenWithF(Base):
    """v8.21（t142u）：猎豹和熊都用开怪键 f 开怪（点击模式 + 原地模式）。"""
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.k["attack"], self.k["interact"] = "f", "g"

    def click_fight(self, form):
        self.cfg["move"]["mode"] = "click"
        self.g.tick(obs(thp=100))
        self.inp.history.clear()
        self.g.tick(obs(thp=100, combat=True, rng="melee", form=form))
        return self.keys()

    def test_click_bear_opens_with_f_once(self):
        ks = self.click_fight("bear")
        self.assertEqual(ks.count("f"), 1)
        self.assertTrue(any("开怪：按开怪键「f」（熊）" in m for m in self.logs))
        for _ in range(3):
            self.clock.t += 1.6
            self.g.tick(obs(thp=90, combat=True, rng="melee", form="bear"))
        self.assertEqual(sum("开怪：按开怪键" in m for m in self.logs), 1)

    def test_click_cat_opens_with_f(self):
        self.cfg["cat"]["combat_form"] = "猎豹"
        from bot.config import is_cat
        self.assertTrue(is_cat(self.cfg))
        ks = self.click_fight("cat")
        self.assertIn("f", ks)
        self.assertNotIn(self.k.get("cat_builder") or "zz", ks[:ks.index("f")])
        self.assertTrue(any("（猎豹）" in m for m in self.logs))

    def test_click_far_waits(self):
        self.cfg["move"]["mode"] = "click"
        self.g.tick(obs(thp=100))
        self.inp.history.clear()
        self.g.tick(obs(thp=100, combat=True, rng="far", form="bear"))
        self.assertFalse(any("开怪：按开怪键" in m for m in self.logs))

    def test_click_opener_can_be_disabled(self):
        self.cfg["move"]["open_with_attack"] = False
        self.assertNotIn("f", self.click_fight("bear"))

    def test_prowl_also_uses_f_and_needs_only_prowl_key(self):
        from bot.config import cat_missing
        self.cfg["cat"].update(combat_form="猎豹", use_prowl=True)
        self.k.update(cat_prowl="4", cat_opener="")
        self.assertFalse(any("潜行" in x for x in cat_missing(self.cfg)))


class TestV822NoInteractInCombat(Base):
    """v8.22（t143u）：挨打 + 距离太远/面向错误 → 右键点标记，战斗中/挨打时绝不按互动键 g。"""
    MODE = "click"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.k.update(attack="f", interact="g", loot="g", target_attacker="x")

    def o(self, hp, **kw):
        kw.setdefault("marker", (900, 400))
        mk = kw.pop("marker")
        o = obs(hp=hp, **kw)
        o.marker, o.marker_center, o.frame_size = mk, True, (1920, 1080)
        o.aggro = kw.get("combat", False)
        return o

    def run_like_log_033447(self, marker=(900, 400)):
        self.g.tick(obs(thp=100))                 # 脱战选怪走过去（这里可以按一次互动键）
        self.inp.history.clear()
        self.g.tick(self.o(95, thp=90, combat=True, form="bear", rng="melee", marker=marker))
        hp = 95
        for i in range(12):                       # 一边挨打，一边“距离太远”/面向错误
            self.clock.t += 0.5
            hp -= 1
            err = "range" if i % 3 else "facing"
            o = self.o(hp, thp=80, combat=True, form="bear", rng="ranged", error=err, marker=marker)
            o.err_facing = err == "facing"
            self.g.tick(o)
        return self.keys()

    def test_hit_and_too_far_no_g(self):
        ks = self.run_like_log_033447()
        self.assertNotIn("g", ks)
        self.assertTrue([k for k in ks if k.startswith("click(900,")])
        self.assertFalse(any("按互动键" in m for m in self.logs))

    def test_hit_and_too_far_no_marker_no_g(self):
        ks = self.run_like_log_033447(marker=None)
        self.assertNotIn("g", ks)
        self.assertTrue(any("不能右键点" in m or "目标" in m for m in self.logs))

    def test_kill_while_others_hit_me_no_g_then_x(self):
        self.g.tick(obs(thp=100))
        self.g.tick(self.o(95, thp=10, combat=True, form="bear", rng="melee"))
        self.inp.history.clear()
        self.clock.t += 0.5
        self.g.tick(self.o(90, thp=0, combat=True, form="bear", kind="dead"))   # 怪死了，还在挨打
        for _ in range(6):
            self.clock.t += 0.5
            self.g.tick(self.o(88, thp=0, combat=True, form="bear", kind="dead"))
        self.assertNotIn("g", self.keys())
        self.assertIn("x", self.keys())

    def test_combat_interact_interval_ignored(self):
        self.cfg["move"]["combat_interact_interval_s"] = 1.0
        ks = self.run_like_log_033447()
        self.assertNotIn("g", ks)

    def test_skin_refused_when_hit(self):
        o = self.o(80, thp=0, combat=True, form="bear", kind="dead")
        self.g.hp_drop = 5.0
        self.g._start_skin(o)
        self.assertNotIn("g", self.keys())


class TestV823PatrolClick(Base):
    """v8.23（t144u）：多中心巡逻 1→2→3→1。"""
    MODE = "click"
    LEGACY = False
    C = [(50.0, 50.0, 1442), (52.0, 50.0, 1442), (54.0, 50.0, 1442)]

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.k.update(target_attacker="x", interact="g", loot="g")
        self.cfg["area"].update(enabled=True, radius=0.5)
        self.g.set_centers(self.C)
        self.g.home = None
        self.saved = []
        self.g.on_centers = self.saved.append

    def tick(self, pos, **kw):
        self.clock.t += 0.6
        self.g.tick(pobs(pos=pos, facing=0.0, **kw))

    def spin(self, pos, n=12):
        for _ in range(n):
            self.tick(pos)
            if self.g.state == State.RETURNING:
                return

    def test_start_at_nearest_and_go_next(self):
        self.tick((53.9, 50.0))
        self.assertEqual(self.g.ci, 2)
        self.assertTrue(any("从最近的 #3" in m for m in self.logs))
        self.spin((53.9, 50.0))
        self.assertEqual(self.g.state, State.RETURNING)
        self.assertTrue(self.g.travel)
        self.assertEqual(self.g.ci, 1)                              # v8.33 来回走：3 → 2
        self.assertTrue(any("走去下一个中心 #2/3" in m for m in self.logs))

    def test_walk_arrive_then_search_there(self):
        self.tick((50.0, 50.0))
        self.spin((50.0, 50.0))
        self.assertEqual((self.g.ci, self.g.travel), (1, True))
        self.tick((51.0, 50.0))                                     # 路上：还在走，不算出范围
        self.assertEqual(self.g.state, State.RETURNING)
        self.tick((51.9, 50.0))
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertFalse(self.g.travel)
        self.assertTrue(any("到达中心 #2/3" in m for m in self.logs))
        self.spin((51.9, 50.0))                                     # 在 #2 又转一圈 → 去 #3
        self.assertEqual(self.g.ci, 2)

    def test_single_center_unchanged(self):
        self.g.set_centers([(50.0, 50.0, 1442)])
        self.g.home = None
        self.tick((50.05, 50.0))
        self.spin((50.05, 50.0), 12)
        self.assertNotEqual(self.g.state, State.RETURNING)
        self.assertTrue(any("已经在中心附近" in m for m in self.logs))

    def test_other_map_center_skipped(self):
        self.g.set_centers([(50.0, 50.0, 1442), (52.0, 50.0, 1413), (54.0, 50.0, 1442)])
        self.g.home = None
        self.tick((50.0, 50.0))
        self.spin((50.0, 50.0))
        self.assertEqual(self.g.ci, 2)
        self.assertTrue(any("中心 #2 在地图 1413" in m for m in self.logs))

    def test_no_position_no_walk(self):
        self.tick((50.0, 50.0))
        self.g.spin_due = True
        self.tick(None)
        self.assertNotEqual(self.g.state, State.RETURNING)
        self.assertTrue(any("读不到坐标" in m for m in self.logs))

    def test_sees_plate_on_the_way_stops_and_clicks(self):
        self.tick((50.0, 50.0))
        self.spin((50.0, 50.0))
        self.clock.t += 1.5
        self.inp.history.clear()
        self.tick((51.0, 50.0), plates=[plate(700, 300)], checked=True)
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertTrue(any("个能打的怪的标记" in m and "路上" in m for m in self.logs))
        self.tick((51.0, 50.0), plates=[plate(700, 300)], checked=True)   # 不走回去，先点姓名板
        self.assertTrue([k for k in self.keys() if k.startswith("click(")])
        self.assertNotEqual(self.g.state, State.RETURNING)

    def test_mob_on_the_way_no_leash_then_resume(self):
        self.tick((50.0, 50.0))
        self.spin((50.0, 50.0))
        self.clock.t += 1.5
        self.tick((51.0, 50.0), thp=100)                             # 选中了能打的怪
        self.assertEqual(self.g.state, State.SEARCHING)
        self.tick((51.0, 50.0), thp=100)
        self.assertEqual(self.g.state, State.APPROACHING)
        self.tick((51.0, 50.0), thp=100)                             # 离 #2 很远也不放弃（不算追出范围）
        self.assertEqual(self.g.state, State.APPROACHING)
        self.inp.history.clear()
        self.tick((51.0, 50.0), thp=90, combat=True, form="bear", rng="melee", hp=90)
        self.tick((51.0, 50.0), thp=50, combat=True, form="bear", rng="ranged", hp=85, error="range")
        self.assertNotIn("g", self.keys())                          # 战斗中不按 g
        self.tick((51.0, 50.0), thp=0, kind="dead", combat=False, hp=85)
        for _ in range(8):
            self.tick((51.0, 50.0), hp=85)
            if self.g.state == State.RETURNING:
                break
        self.assertEqual(self.g.state, State.RETURNING)              # 打完捡完接着走去 #2
        self.assertTrue(self.g.travel)
        self.assertEqual(self.g.ci, 1)

    def test_hotkeys_add_and_reset(self):
        self.g.set_centers([])
        self.g.home = (50.0, 50.0, 1442)
        self.g.request_area("add")
        self.tick((52.0, 51.0))
        self.assertEqual(len(self.g.centers), 2)                    # 原来的中心当 #1，新的 #2
        self.assertTrue(any("已添加中心 #2：52.00,51.00" in m for m in self.logs))
        self.g.request_area("add")
        self.tick((53.0, 51.0))
        self.assertEqual(len(self.g.centers), 3)
        self.assertEqual(len(self.saved[-1]), 3)
        self.g.request_area("home")
        self.tick((55.0, 55.0))
        self.assertEqual(self.g.centers, [(55.0, 55.0, 1442)])
        self.assertEqual(self.saved[-1], [[55.0, 55.0, 1442]])


class TestV823PatrolStationary(Base):
    MODE = "stationary_tab"
    LEGACY = False

    def test_tab_on_the_way_and_fight(self):
        self.cfg["buffs"]["enabled"] = False
        self.g._has_plate_templates = lambda now: False
        self.cfg["area"].update(enabled=True, radius=0.5)
        self.g.set_centers([(50.0, 50.0, 1442), (52.0, 50.0, 1442)])
        self.g.home = None
        for _ in range(400):
            self.clock.t += 0.7
            self.g.tick(pobs(pos=(50.0, 50.0), facing=0.0))
            if self.g.state == State.RETURNING:
                break
        self.assertTrue(self.g.travel)
        self.inp.history.clear()
        for _ in range(5):
            self.clock.t += 1.0
            self.g.tick(pobs(pos=(51.0, 50.0), facing=0.0))
        self.assertIn(self.k["target"], self.keys())                # 路上按 Tab 找怪
        self.clock.t += 1.0
        self.g.tick(pobs(pos=(51.0, 50.0), facing=0.0, thp=100, rng="ranged"))
        self.assertEqual(self.g.state, State.SEARCHING)
        self.inp.history.clear()
        self.g.tick(pobs(pos=(51.0, 50.0), facing=0.0, thp=100, rng="ranged"))
        self.assertIn(self.k["attack"], self.keys())                # 开怪


class TestV824RightClickInterval(Base):
    """v8.24（t145u）：任意两次右键至少隔 3 秒；点完先判断要不要再点；近战距离内不右键。"""
    MODE = "click"
    LEGACY = False
    RGAP = 3.0

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.k.update(target_attacker="x", interact="g", loot="g", attack="f")
        self.ctimes = []
        orig = self.inp.click_at

        def click_at(*a, **kw):
            self.ctimes.append(self.clock.t)
            return orig(*a, **kw)
        self.inp.click_at = click_at

    def gaps_ok(self):
        return all(b - a >= 3.0 - 1e-6 for a, b in zip(self.ctimes, self.ctimes[1:]))

    def o(self, pos=None, mk=(900, 300), **kw):
        o = obs(**kw)
        o.pos, o.map_id, o.facing = pos, 1442, 0.0
        o.marker, o.marker_center, o.frame_size = mk, True, (1920, 1080)
        return o

    def test_select_then_no_quick_second_click(self):
        self.g.tick(self.o(plates=[plate(700, 300)], checked=True))      # 点姓名板选怪（右键 = 走过去）
        self.assertEqual(len(self.ctimes), 1)
        self.clock.t += 0.4
        self.g.tick(self.o(pos=(50.0, 50.0), thp=100, rng="ranged"))     # 选上了 → 不马上再点
        self.assertEqual(len(self.ctimes), 1)
        self.assertEqual(self.g.state, State.APPROACHING)
        self.clock.t += 1.5
        self.g.tick(self.o(pos=(50.05, 50.0), thp=100, rng="ranged"))
        self.assertEqual(len(self.ctimes), 1)
        self.clock.t += 1.5
        self.g.tick(self.o(pos=(50.10, 50.0), thp=100, rng="ranged"))    # 3.4 秒：角色在走近 → 不点
        self.assertEqual(len(self.ctimes), 1)
        self.clock.t += 1.0
        self.g.tick(self.o(pos=(50.10, 50.0), thp=100, rng="ranged"))    # 不走了 → 再点一次
        self.assertEqual(len(self.ctimes), 2)
        self.assertTrue(any("角色没在走" in m for m in self.logs))
        self.assertTrue(self.gaps_ok())

    def test_approach_error_after_gap(self):
        self.g.tick(self.o(plates=[plate(700, 300)], checked=True))
        for i in range(10):
            self.clock.t += 0.5
            self.g.tick(self.o(pos=(50.0 + i * 0.05, 50.0), thp=100, rng="ranged", error="range"))
        self.assertGreaterEqual(len(self.ctimes), 2)
        self.assertTrue(self.gaps_ok())
        self.assertTrue(any("还在报距离太远" in m for m in self.logs))

    def fight(self):
        self.g.tick(self.o(thp=100))
        self.g.tick(self.o(hp=95, thp=90, combat=True, form="bear", rng="melee"))
        self.assertEqual(self.g.state, State.COMBAT)
        self.inp.history.clear()
        self.ctimes.clear()

    def test_melee_facing_error_turns_no_click_no_g(self):
        self.fight()
        for i in range(1, 8):
            self.clock.t += 0.7
            o = self.o(hp=90, thp=80, combat=True, form="bear", rng="melee", error="facing")
            o.err_facing, o.facing_count = True, i
            self.g.tick(o)
        # v8.26：贴脸标记在中间 → 后退 2 次后可以右键点一次（仍守 3 秒间隔）；绝不按 g
        self.assertLessEqual(len(self.ctimes), 2)
        self.assertTrue(self.gaps_ok())
        self.assertNotIn("g", self.keys())
        self.assertTrue(set(self.keys()) & {self.k["turn_left"], self.k["turn_right"], self.k["back"]})

    def test_ranged_errors_click_every_3s(self):
        self.fight()
        for i in range(1, 16):
            self.clock.t += 0.5
            err = "facing" if i % 2 else "range"
            o = self.o(hp=90, thp=80, combat=True, form="bear", rng="ranged", error=err)
            o.err_facing, o.facing_count = err == "facing", i
            self.g.tick(o)
        self.assertGreaterEqual(len(self.ctimes), 2)
        self.assertTrue(self.gaps_ok(), self.ctimes)
        self.assertNotIn("g", self.keys())

    def test_in_melee_option(self):
        self.cfg["face_click"]["in_melee"] = True
        self.fight()
        self.clock.t += 3.5
        o = self.o(hp=90, thp=80, combat=True, form="bear", rng="melee", error="facing")
        o.err_facing, o.facing_count = True, 1
        self.g.tick(o)
        self.assertEqual(len(self.ctimes), 1)


class TestV825MeleeBackStep(Base):
    """v8.25：近战距离内转过一次身还报面向错误 → 后退半步，之后扫一圈；绝不按互动键。"""
    MODE = "click"
    LEGACY = False
    RNG = "melee"
    fight = TestV820ClickFacingNoInteract.fight
    o = TestV820ClickFacingNoInteract.o

    def test_back_half_step_once(self):
        """标记偏一边：先往标记那边转；转完标记偏着还报错 → 后退半步一次（v8.25 行为保留）。"""
        self.k["back"] = "s"
        self.fight()
        for i in range(1, 5):
            self.clock.t += 1.0
            self.g.tick(self.o(i, marker=(1400, 500)))
        ks = self.keys()
        self.assertNotIn(self.k["interact"], ks)
        self.assertIn("s", ks)
        turns_before = [k for k in ks[:ks.index("s")] if k in (self.k["turn_left"], self.k["turn_right"])]
        self.assertGreaterEqual(len(turns_before), 1)
        self.assertEqual(ks.count("s"), 1)

    def test_back_disabled_by_zero(self):
        self.k["back"] = "s"
        self.cfg["face_click"]["melee_back_ms"] = 0
        self.fight()
        for i in range(1, 10):
            self.clock.t += 1.0
            self.g.tick(self.o(i, marker=(960, 500)))
        self.assertNotIn("s", self.keys())
        self.assertNotIn(self.k["interact"], self.keys())

    def test_ranged_no_back(self):
        self.k["back"] = "s"
        self.fight()
        for i in range(1, 10):
            self.clock.t += 1.0
            o = self.o(i, marker=(960, 500))
            o.range = "ranged"
            self.g.tick(o)
        self.assertNotIn("s", self.keys())


class TestV825PatrolStuck(Base):
    """v8.25：去下一个中心路上卡住（跳+平移 2 次没用）→ 改去下一个中心；单纯回中心卡住照旧停止。"""
    MODE = "click"
    LEGACY = False
    C = [(50.0, 50.0, 1442), (52.0, 50.0, 1442)]
    tick = TestV823PatrolClick.tick

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.k.update(target_attacker="x", interact="g", loot="g")
        self.cfg["area"].update(enabled=True, radius=0.5)
        self.g.set_centers(self.C)
        self.g.home = None

    spin = TestV823PatrolClick.spin

    def stuck_until(self, pos, cond, n=60):
        from bot.navigation import bearing_to
        for _ in range(n):
            self.clock.t += 1.0
            h = self.g.home
            f = bearing_to(pos, h) if h else 0.0
            self.g.tick(pobs(pos=pos, facing=f))
            if cond():
                return True
        return False

    def test_two_centers_go_back_to_previous(self):
        self.tick((50.0, 50.0))
        self.spin((50.0, 50.0))
        self.assertEqual((self.g.ci, self.g.travel), (1, True))
        ok = self.stuck_until((50.5, 50.0), lambda: self.g.ci == 0)
        self.assertTrue(ok, self.logs[-5:])
        self.assertFalse(self.g.stopped)
        self.assertTrue(any("去中心 #2 的路上卡住了" in m and "改去中心 #1/2" in m for m in self.logs))
        self.assertEqual(self.g.state, State.RETURNING)
        self.assertEqual(sum("尝试跳跃+平移" in m for m in self.logs), 2)

    def test_all_stuck_no_stop(self):                               # v8.33：每个中心都卡 → 不停止，就地找怪
        self.tick((50.0, 50.0))
        self.spin((50.0, 50.0))
        ok = self.stuck_until((51.0, 50.0), lambda: any("没有别的中心可去" in m for m in self.logs), n=200)
        self.assertTrue(ok, self.logs[-5:])
        self.assertFalse(self.g.stopped)

    def test_plain_return_stuck_no_stop(self):                      # v8.33：只有 1 个中心卡住 → 不停止
        self.g.set_centers([])
        self.cfg["area"].update(center_x=50.0, center_y=50.0, map_id=1442, home_on_start=False)
        self.g.home = (50.0, 50.0, 1442)
        self.g.travel = False
        self.g.start_return(pobs(pos=(52.0, 50.0), facing=90.0))
        ok = self.stuck_until((52.0, 50.0), lambda: any("就地找怪" in m for m in self.logs), n=60)
        self.assertTrue(ok, self.logs[-5:])
        self.assertFalse(self.g.stopped)
        self.assertEqual(self.g.state, State.SEARCHING)


class TestV825Cleanup(unittest.TestCase):
    """v8.25：删掉不用的设置项（迁移时去掉，其他值不变）；截图只留最新 N 张；文案按当前形态。"""
    GONE = {"keys": ["cat_opener"], "move": ["combat_interact_interval_s"], "heal": ["gcd_ms"],
            "safety": ["turn_enabled", "turn_every_tabs", "turn_hold_ms", "max_no_target_s", "max_idle_s"],
            "stationary": ["face_turn_ms", "face_recent_dmg_s", "face_small_deg", "face_small_tries"],
            "scan": ["max_turns"], "addon": ["tolerance"]}

    def test_removed_keys_dropped_values_kept(self):
        import copy
        loaded = copy.deepcopy(DEFAULT_CONFIG)
        for sec, names in self.GONE.items():
            for n in names:
                self.assertNotIn(n, DEFAULT_CONFIG[sec])
                loaded[sec][n] = 123
        loaded["keys"]["cat_finisher"] = "1"
        loaded["move"]["click_interval_s"] = 3.0
        loaded["area"]["centers"] = [[57.83, 53.86, 1413], [57.34, 53.61, 1413]]
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        for sec, names in self.GONE.items():
            for n in names:
                self.assertNotIn(n, cfg[sec])
        self.assertEqual(cfg["keys"]["cat_finisher"], "1")
        self.assertEqual(cfg["area"]["centers"], [[57.83, 53.86, 1413], [57.34, 53.61, 1413]])
        self.assertEqual(cfg["face_click"]["melee_back_ms"], 300)
        self.assertEqual(cfg["log"]["keep_shots"], 50)

    def test_shots_keep_newest(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(70):
                open(os.path.join(d, f"20261116_0400{i:02d}_x.png"), "w").close()
            s = ShotSaver(directory=d, keep=50)
            s.prune()
            left = sorted(os.listdir(d))
            self.assertEqual(len(left), 50)
            self.assertEqual(left[0], "20261116_040020_x.png")

    def test_wording_uses_current_form(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "bot", "state_machine.py"), encoding="utf-8") as f:
            src = f.read()
        bad = [ln for ln in src.splitlines() if ("log(" in ln or "_ctx(" in ln) and ("变回熊" in ln or "变熊" in ln)]
        self.assertEqual(bad, [])
        with open(os.path.join(root, "bot", "runner.py"), encoding="utf-8") as f:
            self.assertNotIn('f"v8.17：区域刷怪', f.read())


class TestV825GuiAdvanced(unittest.TestCase):
    """v8.25：调参项收进“高级设置”；点击模式隐藏“原地打怪”页。不写真的 config.json。"""
    def test_advanced_and_hidden_tab(self):
        if not sys.platform.startswith("linux") or not os.environ.get("DISPLAY"):
            self.skipTest("只在测试机的虚拟屏幕上跑")
        try:
            import tkinter as tk
            root = tk.Tk()
            root.destroy()
        except Exception:
            self.skipTest("没有图形界面")
        import copy
        import gui.app as A
        cfgs = copy.deepcopy(DEFAULT_CONFIG)
        cfgs["move"]["mode"] = "click"
        old = (A.load_config, A.save_config, A.App._start_hotkeys)
        A.load_config = lambda *a: copy.deepcopy(cfgs)
        A.save_config = lambda c, *a: None
        A.App._start_hotkeys = lambda self: None
        try:
            app = A.App()
            app.update_idletasks()
            for key in (("stationary", "face_small_steps"), ("safety", "tick_ms"), ("face_click", "cooldown_s")):
                self.assertIn(key, app.fields)
            for key in (("heal", "gcd_ms"), ("keys", "cat_opener"), ("safety", "turn_enabled")):
                self.assertNotIn(key, app.fields)
            tab = app._tab_outer["原地打怪"]
            self.assertEqual(app.nb.tab(tab, "state"), "hidden")
            app.fields[("move", "mode")][0].set("stationary_tab")
            self.assertEqual(app.nb.tab(tab, "state"), "normal")
            advs = [c._adv for c in app._pages if getattr(c, "_adv", None)]
            self.assertGreaterEqual(len(advs), 6)
            box, body, btn = advs[0]
            self.assertTrue(box.winfo_manager())
            self.assertFalse(getattr(body, "_shown", False))
            app._toggle_adv(body, btn)
            self.assertTrue(body._shown)
            cfg = app.collect()
            self.assertEqual(cfg["face_click"]["melee_back_ms"], 300)
            app.destroy()
        finally:
            A.load_config, A.save_config, A.App._start_hotkeys = old



class TestV826MeleeCentered(Base):
    """v8.26（t148u）：贴脸 + 标记在中间还报面向错误 → 不盲转：后退 2 次 → 右键点一次标记 → 循环；绝不按互动键。"""
    MODE = "click"
    LEGACY = False
    RNG = "melee"
    fight = TestV820ClickFacingNoInteract.fight
    o = TestV820ClickFacingNoInteract.o

    def run_errors(self, n, marker=(960, 500), step=1.0):
        self.k["back"] = "s"
        self.fight()
        for i in range(1, n + 1):
            self.clock.t += step
            self.g.tick(self.o(i, marker=marker))
        return self.keys()

    def acts(self, ks):
        return [k for k in ks if k == "s" or k.startswith("click(") or k in (self.k["turn_left"], self.k["turn_right"])
                or k == "tab"]

    def test_like_log_051710_no_blind_turn(self):
        ks = self.run_errors(6)
        self.assertNotIn(self.k["interact"], ks)
        self.assertEqual(self.acts(ks)[:6], ["s", "s", "click(960,500)", "s", "s", "click(960,500)"])
        self.assertFalse(any("盲转" in m for m in self.logs))
        self.assertTrue(any("不转身，后退一步" in m and "第 1/2 次" in m for m in self.logs))
        self.assertTrue(any("后退 2 次还是面向错误 → 右键点一次目标姓名板" in m for m in self.logs))

    def test_right_click_respects_interval(self):
        self.cfg["move"]["click_interval_s"] = 3.0
        self.g.last_rclick = 0.0
        self.k["back"] = "s"
        self.fight()
        self.g.last_rclick = self.clock.t          # 刚右键过（例如选怪那一下）
        for i in range(1, 4):
            self.clock.t += 0.7
            self.g.tick(self.o(i, marker=(960, 500)))
        ks = self.keys()
        self.assertEqual(ks.count("s"), 2)
        self.assertFalse([k for k in ks if k.startswith("click(")])      # 不到 3 秒：先不点
        self.clock.t += 2.0
        self.g.tick(self.o(4, marker=(960, 500)))
        self.assertTrue([k for k in self.keys() if k.startswith("click(")])

    def test_no_new_error_no_action(self):
        self.k["back"] = "s"
        self.fight()
        self.clock.t += 1.0
        self.g.tick(self.o(1, marker=(960, 500)))
        for _ in range(4):                          # 报错灯还亮着，但计数没变（旧的）→ 不再后退
            self.clock.t += 1.0
            self.g.tick(self.o(1, marker=(960, 500)))
        self.assertEqual(self.keys().count("s"), 1)

    def test_bounded_then_give_up(self):
        ks = self.run_errors(14)
        self.assertNotIn(self.k["interact"], ks)
        self.assertTrue(any("已经 3 轮还是报错" in m for m in self.logs))
        self.assertTrue(any("贴脸后退/右键" in m for m in self.logs))
        a = self.acts(ks)
        self.assertEqual(a[:9], ["s", "s", "click(960,500)"] * 3)
        self.assertFalse([k for k in a[:9] if k in (self.k["turn_left"], self.k["turn_right"])])

    def test_off_center_still_turns(self):
        ks = self.run_errors(1, marker=(1500, 500))
        self.assertTrue(set(ks) & {self.k["turn_right"]})
        self.assertNotIn("s", ks)

    def test_new_settings_default(self):
        import copy
        loaded = copy.deepcopy(DEFAULT_CONFIG)
        loaded["face_click"].pop("melee_backs")
        loaded["face_click"].pop("melee_cycles")
        loaded["face_click"]["melee_back_ms"] = 250
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual((cfg["face_click"]["melee_backs"], cfg["face_click"]["melee_cycles"]), (2, 3))
        self.assertEqual(cfg["face_click"]["melee_back_ms"], 250)


class TestV827PostFightHeal(Base):
    """v8.27（t149u）：每次战斗结束：血量 < 70% 按 e 生命之触（读完再打）；70-99% 按 g 回春术；满血不治疗；
    宏自带取消变形（不先按取消变形键）；法力会低于变身预留 → 跳过；治疗完马上变回猎豹。"""
    MODE = "click"
    LEGACY = False
    POST = True

    def setUp(self):
        super().setUp()
        self.cfg["cat"]["combat_form"] = "猎豹"
        self.k.update(cat_form="z", cancel_form="v", healing_touch="e", interact="`", loot="`",
                      cat_builder="1", cat_finisher="2", target_attacker="x")
        self.cfg["buffs"]["enabled"] = False
        self.cfg["loot"]["enabled"] = False

    def end_fight(self, hp, mana=80, n=6):
        self.g.tick(obs(hp=100, thp=100, form="cat", mana=mana))
        self.clock.t += 0.5
        self.g.tick(obs(hp=hp, thp=60, combat=True, form="cat", mana=mana, rng="melee"))
        self.clock.t += 0.5
        self.g.tick(obs(hp=hp, thp=10, combat=True, form="cat", mana=mana, rng="melee"))
        self.inp.history.clear()
        for _ in range(n):
            self.clock.t += 0.4
            self.g.tick(obs(hp=hp, kind="none", combat=False, form="cat", mana=mana, casting=False))
            if set(self.keys()) & {"e", "g"}:
                break
        return self.keys()

    def test_low_hp_healing_touch_cast_then_cat(self):
        ks = self.end_fight(55)
        self.assertIn("e", ks)
        self.assertNotIn("v", ks[:ks.index("e")])          # 不先按取消变形键（宏里自带）
        self.assertNotIn("g", ks)
        self.assertEqual(self.g.state, State.RESTING)
        self.clock.t += 0.3
        self.g.tick(obs(hp=55, kind="none", form="caster", mana=78, casting=True))
        self.clock.t += 1.0                                   # 读条中挨打：照样读完，不按别的键
        self.inp.history.clear()
        self.g.tick(obs(hp=48, thp=100, combat=True, aggro=True, form="caster", mana=78, casting=True))
        self.clock.t += 0.5
        self.g.tick(obs(hp=45, thp=100, combat=True, aggro=True, form="caster", mana=78, casting=True))
        self.assertEqual(self.keys(), [])
        self.assertTrue(any("读条中挨打 → 读完再打" in m for m in self.logs))
        self.clock.t += 1.5
        self.g.tick(obs(hp=80, thp=100, combat=True, aggro=True, form="caster", mana=60, casting=False))
        self.assertIn("z", self.keys())                       # 读完马上变回猎豹
        self.assertNotEqual(self.g.state, State.RESTING)
        self.assertTrue(any("生命之触读完" in m for m in self.logs))
        self.assertNotIn("`", self.keys())                    # 战斗中绝不按互动键

    def test_mid_hp_no_heal(self):                         # v8.54：战后回春术删掉了 → 生命之触线以上不治疗、不变形
        ks = self.end_fight(85)
        self.assertFalse(set(ks) & {"e", "g", "v"})
        self.assertNotEqual(self.g.state, State.RESTING)
        self.assertTrue(any("≥ 生命之触线 70% → 不治疗，直接继续打" in m for m in self.logs))

    def test_full_hp_no_heal(self):
        ks = self.end_fight(100)
        self.assertFalse(set(ks) & {"e", "g", "v"})
        self.assertNotEqual(self.g.state, State.RESTING)

    def test_low_mana_skips_to_keep_shift(self):
        self.cfg["heal"]["reserve_cat_pct"] = 30
        ks = self.end_fight(60, mana=40)
        self.assertFalse(set(ks) & {"e", "g", "v"})
        self.assertTrue(any("跳过生命之触，保住变猎豹的法力" in m for m in self.logs))

    def test_threshold_configurable(self):
        self.cfg["heal"]["post_ht_below_pct"] = 90
        ks = self.end_fight(85)
        self.assertIn("e", ks)

    def test_off_uses_old_rest(self):
        self.cfg["heal"]["post_enabled"] = False
        ks = self.end_fight(85)
        self.assertFalse(set(ks) & {"e", "g"})


class TestV827Keys(unittest.TestCase):
    """v8.27：互动键 g/· → `；回春术键 g；按 · 实际发 `（VK_OEM_3）。"""
    def test_migrate_interact_and_loot(self):
        import copy
        from bot.config import key_conflicts, norm_key
        loaded = copy.deepcopy(DEFAULT_CONFIG)
        loaded["keys"].update(interact="·", loot="g", healing_touch="e", cat_finisher="1")
        loaded["meta"]["migrations"] = []
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual((cfg["keys"]["interact"], cfg["keys"]["loot"]), ("`", "`"))
        self.assertNotIn("rejuvenation", cfg["keys"])                  # v8.54 战后回春术删掉了
        self.assertEqual(cfg["keys"]["healing_touch"], "e")
        self.assertEqual(cfg["keys"]["cat_finisher"], "1")
        self.assertTrue(cfg["heal"]["post_enabled"])
        self.assertEqual(cfg["heal"]["post_ht_below_pct"], 60)          # v8.47：70 → 60
        self.assertEqual(key_conflicts(cfg), [])
        again = _migrate(_merge(DEFAULT_CONFIG, cfg), cfg)    # 只改一次：之后用户自己改的键不再被改
        self.assertEqual(again["keys"]["interact"], "`")
        self.assertEqual([norm_key(x) for x in ("·", "Ｇ", "ｅ", "F7", "Tab", "`")], ["`", "g", "e", "f7", "tab", "`"])
        cfg["keys"]["thorns"] = "`"
        self.assertTrue(any("与目标互动" in c and "荆棘术" in c for c in key_conflicts(cfg)))

    def test_press_middle_dot_sends_backtick(self):
        import threading
        from bot.input import InputController
        sent = []

        class PG:
            def keyDown(self, k):
                sent.append(("down", k))

            def keyUp(self, k):
                sent.append(("up", k))

        cfg = copy_cfg()
        inp = InputController(cfg, threading.Event(), backend=PG())
        inp.press("·", hold_ms=1, delay=False)
        inp.press("Ｇ", hold_ms=1, delay=False)
        self.assertEqual(sent, [("down", "`"), ("up", "`"), ("down", "g"), ("up", "g")])


def copy_cfg():
    import copy
    return copy.deepcopy(DEFAULT_CONFIG)

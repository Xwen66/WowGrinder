"""v8.40（t173u-t175u）：Tab 找怪。找怪时先按 Tab，插件 v0.13 检查目标（灰名/精英/高 3 级以上/玩家不打，
追怪上限内才打）；合格 → 原来的开打流程；不合格/没选到 → 清目标、不转身、走原来的流程（屏幕标记 → 远处）。
移动中（巡逻/回中心/朝远处走）也定时按 Tab：合格 → 停下打；不合格 → 清目标接着走。"""
import copy
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.addon_reader import AddonReader, paint_test_frame  # noqa: E402
from bot.config import DEFAULT_CONFIG, _merge, _migrate  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs, plate  # noqa: E402

try:
    import lupa
except ImportError:
    lupa = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LUA = os.path.join(ROOT, "addon", "BotStatus", "BotStatus.lua")
TURN_KEYS = ("left", "right", "a", "d")


def tobs(thp=0, kind=None, player=False, elite=False, diff=0, skull=False, rare=False, tinfo=True,
         pos=(50.0, 50.0), plates=(), combat=False, rng="ranged", facing=0.0):
    o = obs(thp=thp, kind=kind, plates=plates, combat=combat, rng=rng)
    o.tinfo, o.t_player, o.t_elite, o.t_level_diff, o.t_skull, o.t_rare = tinfo, player, elite, diff, skull, rare
    o.pos, o.facing, o.map_id = pos, facing, 0
    o.frame_size = (1920, 1080)
    o.plate_source = "marker"
    o.death_info = True
    return o


def mplate(x=900, y=600):
    p = plate(x, y)
    p["from_marker"] = True
    return p


class TabBase(Base):
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.k["clear_target"] = "c"
        self.k["target"] = "tab"
        self.cfg["nameplate"]["engage_any"] = True
        a = self.cfg["area"]
        a.update(enabled=True, radius=1.5, leash_ratio=2.0)
        self.g.home = (50.0, 50.0, 0)
        self.n0 = 0

    def new_keys(self):
        h = self.keys()[self.n0:]
        self.n0 = len(self.keys())
        return h

    def step(self, o, dt=0.3):
        self.g.tick(o)
        self.clock.t += dt

    def tab_count(self):
        return self.keys().count("tab")


class TestDefaults(unittest.TestCase):
    def test_defaults_and_merge(self):
        t = DEFAULT_CONFIG["tab_search"]
        self.assertTrue(t["enabled"] and t["while_moving"])
        self.assertTrue(t["skip_tapped"] and t["skip_elite"] and t["skip_high"] and t["skip_player"])
        self.assertEqual(t["level_diff"], 3)
        self.assertTrue(1.0 <= t["move_interval_s"] <= 2.5)
        cfg = _migrate(_merge(DEFAULT_CONFIG, {"keys": {"target": "tab"}}), {"keys": {"target": "tab"}})
        self.assertEqual(cfg["tab_search"]["level_diff"], 3)
        self.assertEqual(DEFAULT_CONFIG["keys"]["target"], "tab")


class TestReaderV013(unittest.TestCase):
    def read(self, **tinfo):
        img = paint_test_frame({4: "red"}, tinfo=tinfo)
        st = AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(img)
        self.assertTrue(st.ok, st.reason)
        return st

    def test_roundtrip(self):
        st = self.read(player=False, elite=True, rare=False, diff=3)
        self.assertTrue(st.tinfo)
        self.assertEqual((st.t_player, st.t_elite, st.t_level_diff, st.t_skull), (False, True, 3, False))
        st = self.read(player=True, elite=False, rare=True, diff=-5)
        self.assertEqual((st.t_player, st.t_elite, st.t_rare, st.t_level_diff), (True, False, True, -5))
        st = self.read(player=False, elite=False, skull=True)
        self.assertTrue(st.t_skull)
        st = self.read(player=None, elite=None, diff=None)
        self.assertTrue(st.tinfo)
        self.assertIsNone(st.t_player)
        self.assertIsNone(st.t_elite)
        self.assertIsNone(st.t_level_diff)

    def test_v012_has_no_tinfo(self):
        img = paint_test_frame({4: "red"}, death={"np": 60})
        st = AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(img)
        self.assertTrue(st.death_info)
        self.assertFalse(st.tinfo)
        self.assertIsNone(st.t_player)


@unittest.skipIf(lupa is None, "没有 lupa")
class TestLuaV013(unittest.TestCase):
    def run_lua(self, cls="normal", tlevel=12, plevel=12, isplayer=False, target=True, secret=False):
        from test_addon_lua import STUBS, TestAddonLua
        L = lupa.LuaRuntime()
        L.execute(STUBS)
        L.execute("""
function GetCVar(k) return "60" end
function SetCVar() end
function UnitIsGhost() return false end
function GetCorpseRecoveryDelay() return 0 end
function StaticPopup_Visible() return false end
function UnitIsPlayer(u) return u == "target" and ISPLAYER or false end
function UnitClassification(u) if SECRET then error("secret") end return CLS end
function UnitLevel(u) if u == "target" then return TLEVEL end return PLEVEL end
""")
        g = L.globals()
        g.CLS, g.TLEVEL, g.PLEVEL, g.ISPLAYER, g.SECRET = cls, tlevel, plevel, isplayer, secret
        g.POSX, g.POSY, g.FACING, g.MAPID = 0.5, 0.5, 1.0, 1
        g.HASTARGET, g.TDEAD, g.COMBAT = target, False, False
        with open(LUA, encoding="utf-8") as f:
            L.execute(f.read())
        frame = g.BotStatusFrame
        frame.script_OnEvent(frame, "PLAYER_LOGIN")
        g.NOW = 103
        frame.script_OnUpdate(frame, 1.0)
        st = AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(TestAddonLua.paint(g))
        self.assertTrue(st.ok, st.reason)
        self.assertTrue(st.tinfo)
        return st

    def test_elite_and_level(self):
        st = self.run_lua(cls="elite", tlevel=15, plevel=12)
        self.assertEqual((st.t_elite, st.t_level_diff, st.t_player, st.t_skull), (True, 3, False, False))
        st = self.run_lua(cls="rareelite")
        self.assertTrue(st.t_elite)
        st = self.run_lua(cls="worldboss")
        self.assertTrue(st.t_elite)

    def test_rare_player_skull(self):
        st = self.run_lua(cls="rare", tlevel=10)
        self.assertEqual((st.t_elite, st.t_rare, st.t_level_diff), (False, True, -2))
        st = self.run_lua(isplayer=True)
        self.assertTrue(st.t_player)
        st = self.run_lua(tlevel=-1)
        self.assertTrue(st.t_skull)

    def test_unreadable_and_no_target(self):
        st = self.run_lua(secret=True)
        self.assertIsNone(st.t_elite)
        st = self.run_lua(target=False)
        self.assertIsNone(st.t_level_diff)


class TestSearchTab(TabBase):
    def test_tab_first_then_accept_and_engage(self):
        self.step(tobs(plates=[mplate()]))
        self.assertEqual(self.new_keys(), ["tab"])               # 屏幕上有标记也先按 Tab
        self.assertEqual(self.clicks(), [])
        self.step(tobs(thp=100, diff=2, rare=True, plates=[mplate()]))
        self.assertTrue(any("Tab 选中能打的怪" in m and "稀有" in m for m in self.logs))
        self.step(tobs(thp=100, diff=2))
        self.assertIn(self.g.state, (State.APPROACHING, State.COMBAT))
        self.assertNotIn("c", self.keys())

    def test_reject_then_next_then_clear_no_turn_fallback_markers(self):
        self.step(tobs(plates=[mplate()]))
        self.step(tobs(thp=100, elite=True, plates=[mplate()]))
        self.assertTrue(any("不合格：精英" in m and "再按一次 Tab" in m for m in self.logs))
        self.assertEqual(self.tab_count(), 2)
        self.step(tobs(thp=100, diff=5, plates=[mplate()]))
        self.assertTrue(any("比我高 5 级" in m and "清目标" in m for m in self.logs))
        self.assertIn("c", self.keys())
        self.clock.t += 1.2
        self.step(tobs(plates=[mplate()]))                    # 冷却中：不按 Tab，点屏幕标记
        self.assertEqual(self.tab_count(), 2)
        self.assertTrue(any(c[1] == "right" for c in self.clicks()))
        self.assertFalse(any(k in TURN_KEYS for k in self.keys()))

    def test_same_bad_mob_again_ends_cycle(self):
        self.cfg["tab_search"]["max_per_cycle"] = 5
        self.step(tobs())
        for _ in range(4):
            self.step(tobs(thp=100, player=True))
        self.assertEqual(self.tab_count(), 2)                   # 第 2 次又是同一个 → 不再按
        self.assertIn("c", self.keys())

    def test_filters(self):
        cases = [(dict(kind="tapped", thp=100), "灰名"), (dict(thp=100, player=True), "玩家"),
                 (dict(thp=100, elite=True), "精英"), (dict(thp=100, diff=3), "高 3 级"),
                 (dict(thp=100, skull=True, diff=None), "骷髅"), (dict(thp=100, player=None), "读不到是不是玩家"),
                 (dict(thp=100, elite=None), "读不到是不是精英"), (dict(thp=100, diff=None), "读不到等级"),
                 (dict(thp=100, kind="friendly"), "友方")]
        for kw, why in cases:
            self.g._ts_init()
            self.assertIsNotNone(self.g._ts_reject(tobs(**kw), 1.0), why)
            self.assertIn(why.replace("高 3 级", "高 3 级"), self.g._ts_reject(tobs(**kw), 1.0))
        for kw in (dict(thp=100, diff=2), dict(thp=100, diff=-4, rare=True)):
            self.assertIsNone(self.g._ts_reject(tobs(**kw), 1.0), kw)

    def test_filter_settings_off(self):
        t = self.cfg["tab_search"]
        t.update(skip_elite=False, skip_player=False, skip_high=False, level_diff=5)
        self.assertIsNone(self.g._ts_reject(tobs(thp=100, elite=True, player=True, diff=9), 1.0))
        t.update(skip_high=True)
        self.assertIsNone(self.g._ts_reject(tobs(thp=100, diff=4), 1.0))
        self.assertIsNotNone(self.g._ts_reject(tobs(thp=100, diff=5), 1.0))

    def test_no_target_falls_back_same_tick_flow(self):
        self.step(tobs(plates=[mplate()]))
        self.clock.t += 0.5
        self.step(tobs(plates=[mplate()]))
        self.assertTrue(any("Tab 没选到怪" in m for m in self.logs))
        self.assertTrue(any(c[1] == "right" for c in self.clicks()))
        self.assertNotIn("c", self.keys())
        self.assertFalse(any(k in TURN_KEYS for k in self.keys()))

    def test_old_addon_no_tab(self):
        for _ in range(3):
            self.step(tobs(tinfo=False, plates=[mplate()]))
        self.assertEqual(self.tab_count(), 0)
        self.assertEqual(sum("插件 v0.13" in m for m in self.logs), 1)
        self.assertTrue(any(c[1] == "right" for c in self.clicks()))

    def test_disabled(self):
        self.cfg["tab_search"]["enabled"] = False
        self.step(tobs(plates=[mplate()]))
        self.assertEqual(self.tab_count(), 0)

    def test_leash(self):
        # 我在范围外（1.5）但在追怪上限内（3.0）：Tab 选中的怪照打，不走回中心
        self.g.ts_accept_t = -1e9
        self.assertIsNone(self.g._ts_reject(tobs(thp=100, pos=(52.0, 50.0)), 1.0))
        self.assertIn("追怪上限外", self.g._ts_reject(tobs(thp=100, pos=(53.5, 50.0)), 1.0))
        self.g.ts_pend = (self.clock.t, "search")
        self.g.ts_n = 1
        self.step(tobs(thp=100, pos=(52.0, 50.0)))
        self.assertTrue(any("Tab 选中能打的怪" in m for m in self.logs))
        self.step(tobs(thp=100, pos=(52.0, 50.0)))
        self.assertNotEqual(self.g.state, State.RETURNING)

    def test_engage_first_waits_for_tab(self):
        self.step(tobs(plates=[mplate()]))
        self.assertEqual(self.clicks(), [])


class TestMovingTab(TabBase):
    def start_return(self, pos=(53.0, 50.0)):
        o = tobs(pos=pos, facing=1.5708)          # 朝西（中心在西边）
        self.g.start_return(o)
        return o

    def test_return_presses_tab_periodically_and_engages(self):
        self.start_return()
        x = 53.0
        for _ in range(12):
            x -= 0.05
            self.step(tobs(pos=(x, 50.0), facing=1.5708))
        n = self.tab_count()
        self.assertGreaterEqual(n, 1)
        self.assertLessEqual(n, 3)                                # 3.6 秒里约每 1.8 秒一次
        self.assertIn("w", self.inp.held)
        self.clock.t = self.g.ts_move_next + 0.05
        self.step(tobs(pos=(x, 50.0), facing=1.5708))            # 又按了一次 Tab
        self.step(tobs(thp=100, pos=(x, 50.0), facing=1.5708))
        self.assertTrue(any("走回中心的路上：Tab 选中能打的怪" in m for m in self.logs))
        self.assertNotIn("w", self.inp.held)
        self.assertEqual(self.g.state, State.SEARCHING)
        self.step(tobs(thp=100, pos=(52.5, 50.0), facing=1.5708))
        self.assertIn(self.g.state, (State.APPROACHING, State.COMBAT))

    def test_return_reject_keeps_walking(self):
        self.start_return()
        for _ in range(3):
            self.step(tobs(pos=(52.5, 50.0), facing=1.5708))
        self.assertGreaterEqual(self.tab_count(), 1)
        self.step(tobs(thp=100, elite=True, pos=(52.5, 50.0), facing=1.5708))
        self.assertTrue(any("不合格：精英" in m and "接着走" in m for m in self.logs))
        self.assertIn("c", self.keys())
        self.step(tobs(thp=100, elite=True, pos=(52.4, 50.0), facing=1.5708))   # 清目标还没生效
        self.assertEqual(self.g.state, State.RETURNING)
        self.assertIn("w", self.inp.held)
        self.assertFalse(any(k in TURN_KEYS for k in self.keys()))

    def test_patrol_travel(self):
        self.g.travel = True
        self.start_return()
        for _ in range(3):
            self.step(tobs(pos=(52.5, 50.0), facing=1.5708))
        self.step(tobs(thp=100, pos=(52.5, 50.0), facing=1.5708))
        self.assertTrue(any("去中心 #1 的路上：Tab 选中能打的怪" in m for m in self.logs))

    def test_beyond_leash_rejected_while_returning(self):
        self.start_return((54.0, 50.0))
        for _ in range(3):
            self.step(tobs(pos=(54.0, 50.0), facing=1.5708))
        self.step(tobs(thp=100, pos=(54.0, 50.0), facing=1.5708))
        self.assertTrue(any("追怪上限外" in m for m in self.logs))
        self.assertEqual(self.g.state, State.RETURNING)

    def test_while_moving_off(self):
        self.cfg["tab_search"]["while_moving"] = False
        self.start_return()
        for _ in range(10):
            self.step(tobs(pos=(52.5, 50.0), facing=1.5708))
        self.assertEqual(self.tab_count(), 0)

    def test_no_tab_in_combat_or_corpse(self):
        self.start_return()
        for _ in range(8):
            self.step(tobs(pos=(52.5, 50.0), facing=1.5708, combat=True, thp=0))
        self.assertLess(self.g.ts_last_press, 0)                 # 战斗中不按“移动中 Tab”（战斗没目标按 Tab 是原来的流程）
        self.assertEqual(self.g._ts_moving(tobs(), self.clock.t, 1.0, "x"), False)

    def test_far_walk_tab(self):
        self.cfg["nameplate"].update(far_names=True)
        self.g.far = {"t0": self.clock.t, "seen": self.clock.t, "pos": (50.0, 50.0), "pos_t": self.clock.t,
                      "steer_t": self.clock.t}
        self.g.walking = True
        self.inp.key_down("w")
        def far_obs(x, **kw):
            o = tobs(pos=(x, 50.0), **kw)
            o.red_names = [{"x": 950, "y": 100, "w": 40, "h": 12, "px": 100}]
            return o
        self.step(far_obs(50.0))
        self.assertEqual(self.tab_count(), 1)
        self.step(far_obs(50.1, thp=100, player=True))          # 不合格 → 清目标，接着朝远处走
        self.assertIsNotNone(self.g.far)
        self.assertTrue(any("朝远处走：Tab 选中的怪不合格：玩家" in m for m in self.logs))
        self.clock.t += 1.6
        self.step(far_obs(50.2))
        self.assertIsNotNone(self.g.far)
        self.assertEqual(self.tab_count(), 2)
        self.step(far_obs(50.3, thp=100))
        self.assertTrue(any("朝远处走：Tab 选中能打的怪" in m for m in self.logs))
        self.assertIsNone(self.g.far)


class TestGuiReadme(unittest.TestCase):
    def test_strings(self):
        src = open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8").read()
        self.assertTrue(re.search(r"熊德自动打怪 v8\.(4\d|[5-9]\d)", src))
        for s in ("Tab 找怪", "移动中也按 Tab", '("level_diff", "高几级以上不打"', '"move_interval_s"', '"tab_search", k, kind'):
            self.assertIn(s, src)
        rd = open(os.path.join(ROOT, "README_中文.md"), encoding="utf-8").read()
        self.assertIn("v8.40", rd)
        self.assertIn("v0.13", rd)
        lua = open(LUA, encoding="utf-8").read()
        self.assertTrue(re.search(r"local NUM_BLOCKS = (4[0-9])", lua))      # v0.14 起 42
        toc = open(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.toc"), encoding="utf-8").read()
        self.assertTrue(re.search(r"## Version: 0\.1[3-9]\.0", toc))


if __name__ == "__main__":
    unittest.main()

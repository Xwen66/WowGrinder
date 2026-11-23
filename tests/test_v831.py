"""v8.31（t158u）：自动跑尸（插件 v0.12：死亡/灵魂、尸体坐标、复活框）+ 姓名板显示距离调到最大。"""
import copy
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.addon_reader import AddonReader, paint_test_frame  # noqa: E402
from bot.config import DEFAULT_CONFIG, key_conflicts  # noqa: E402
from bot.navigation import distance, forward_step  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs, plate  # noqa: E402

try:
    import lupa
except ImportError:
    lupa = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LUA = os.path.join(ROOT, "addon", "BotStatus", "BotStatus.lua")


def dobs(dead=True, ghost=False, pos=(50.0, 50.0), corpse=(52.0, 50.0), facing=0.0, res=False, delay=0,
         plates=(), hp=100, mana=None, combat=False, form="caster", info=True, casting=False):
    o = obs(hp=hp, dead=dead, plates=plates, combat=combat, form=form, mana=mana, casting=casting)
    o.death_info, o.ghost, o.res_popup, o.res_delay = info, ghost, res, delay
    o.pos, o.facing, o.corpse_pos, o.np_dist = pos, facing, corpse, 60
    o.frame_size = (1920, 1080)
    return o


class TestReaderV012(unittest.TestCase):
    def test_roundtrip(self):
        img = paint_test_frame({}, pos=(41.0, 62.5), facing=1.0, map_id=10,
                               death={"ghost": True, "res": True, "corpse": (43.25, 60.5), "delay": 0, "np": 60,
                                      "ghost_marks": True})
        st = AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(img)
        self.assertTrue(st.ok, st.reason)
        self.assertTrue(st.death_info)
        self.assertTrue(st.player_dead and st.ghost and st.res_popup)
        self.assertFalse(st.release_popup)
        self.assertAlmostEqual(st.corpse_pos[0], 43.25, places=2)
        self.assertAlmostEqual(st.corpse_pos[1], 60.5, places=2)
        self.assertEqual((st.res_delay, st.np_dist), (0.0, 60))
        self.assertTrue(st.np_ok and st.ghost_marks)

    def test_old_addon_has_no_death_info(self):
        img = paint_test_frame({2: "red"}, enemy=(True, False, 0))
        st = AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(img)
        self.assertTrue(st.ok)
        self.assertTrue(st.player_dead)
        self.assertFalse(st.death_info)
        self.assertIsNone(st.corpse_pos)

    def test_unknown_corpse(self):
        img = paint_test_frame({}, death={"dead": True, "release": True, "corpse": None, "delay": None, "np": None})
        st = AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(img)
        self.assertTrue(st.death_info and st.player_dead and st.release_popup)
        self.assertFalse(st.ghost)
        self.assertIsNone(st.corpse_pos)
        self.assertIsNone(st.np_dist)


class CorpseBase(Base):
    MODE = "click"
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.k["spirit"] = "]"
        self.k["healing_touch"] = "e"
        self.pos, self.face = (50.0, 50.0), 0.0
        self.corpse = (50.0, 47.0)          # 正北 3 个单位

    def spirit(self):
        return self.keys().count("]")

    def sim_run(self, steps=200, plates=()):
        """模拟灵魂：按住 w 时朝 facing 前进；左右转改 facing；靠近尸体 0.5 内出现复活框。"""
        for _ in range(steps):
            res = distance(self.pos, self.corpse) < 0.5
            self.g.tick(dobs(ghost=True, pos=self.pos, corpse=self.corpse, facing=self.face, res=res,
                             plates=plates))
            self.clock.t += 0.2
            if "w" in self.inp.held:
                self.pos = forward_step(self.pos, self.face, 0.12)
            if self.g.cr is None or self.g.cr["phase"] not in ("run",):
                return
        return

    def turn_hook(self):
        """把 _turn 换成直接改朝向（测试里没有真的游戏）。"""
        orig = self.g._turn

        def turn(delta):
            orig(delta)
            self.face = (self.face + delta) % (2 * math.pi)
        self.g._turn = turn


class TestCorpseRun(CorpseBase):
    def test_full_flow(self):
        self.turn_hook()
        self.face = math.radians(90)                      # 朝西，尸体在北
        self.g.tick(dobs(dead=True))
        self.assertEqual(self.g.state, State.CORPSE)
        self.assertTrue(any("💀 死亡" in m for m in self.logs))
        for _ in range(10):                               # 前 5 秒不按
            self.clock.t += 0.4
            self.g.tick(dobs(dead=True))
        self.assertEqual(self.spirit(), 0)
        self.clock.t += 1.5
        self.g.tick(dobs(dead=True))
        self.assertEqual(self.spirit(), 1)                # 释放灵魂
        self.g.tick(dobs(ghost=True, pos=self.pos, corpse=self.corpse, facing=self.face))
        self.assertEqual(self.g.cr["phase"], "run")
        self.sim_run()
        self.assertLess(distance(self.pos, self.corpse), 0.6)
        self.assertNotIn("w", self.inp.held)
        self.assertEqual(self.g.cr["phase"], "check")
        self.inp.history.clear()
        for _ in range(6):                                # 转一圈看怪：没怪 → 复活
            self.g.tick(dobs(ghost=True, pos=self.pos, corpse=self.corpse, facing=self.face, res=True))
            self.clock.t += 0.3
        self.assertEqual(self.g.cr["phase"], "revive")
        self.assertTrue(any("附近没看到" in m for m in self.logs))
        self.assertEqual(self.spirit(), 1)                # 按了复活
        self.inp.history.clear()
        self.g.tick(dobs(dead=False, hp=50, mana=50))     # 复活了 → 回血回蓝
        self.assertEqual(self.g.cr["phase"], "rest")
        self.clock.t += 0.3
        self.g.tick(dobs(dead=False, hp=50, mana=50))
        self.assertIn("e", self.keys())                   # 血低先放治疗之触
        self.clock.t += 3
        self.g.tick(dobs(dead=False, hp=80, mana=60))
        self.assertIsNotNone(self.g.cr)
        self.clock.t += 3
        self.g.tick(dobs(dead=False, hp=95, mana=85))
        self.assertIsNone(self.g.cr)
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertFalse(self.g.stopped)

    def test_mobs_near_wait_then_revive(self):
        self.g.tick(dobs(dead=True))
        self.g.cr["phase"] = "check"
        self.g.cr["safe_t0"] = self.clock.t
        near = [plate(900, 700)]
        for _ in range(5):
            self.g.tick(dobs(ghost=True, res=True, plates=near))
            self.clock.t += 0.3
        self.assertEqual(self.g.cr["phase"], "wait")
        self.assertTrue(any("尸体附近看到" in m for m in self.logs))
        self.assertEqual(self.spirit(), 0)
        for _ in range(200):                              # 一直有怪 → 等满 30 秒照样复活
            self.g.tick(dobs(ghost=True, res=True, plates=near))
            self.clock.t += 0.5
            if self.g.cr["phase"] == "revive":
                break
        self.assertEqual(self.g.cr["phase"], "revive")
        self.assertTrue(any("照样复活" in m for m in self.logs))

    def test_far_plates_not_counted(self):
        self.g.tick(dobs(dead=True))
        self.g.cr["phase"] = "check"
        self.g.cr["safe_t0"] = self.clock.t
        far = [plate(900, 150)]                           # 屏幕上方 = 远处
        for _ in range(5):
            self.g.tick(dobs(ghost=True, res=True, plates=far))
        self.assertEqual(self.g.cr["phase"], "revive")

    def test_unlimited_deaths(self):
        for n in range(1, 6):
            self.g.tick(dobs(dead=True))
            self.assertEqual(self.g.deaths, n)
            self.g.tick(dobs(dead=False, hp=95, mana=90))      # 复活（比如别人拉）→ 回好了
            self.g.tick(dobs(dead=False, hp=95, mana=90))
            self.assertIsNone(self.g.cr)
        self.assertFalse(self.g.stopped)

    def test_disabled_stops_like_before(self):
        self.cfg["corpse"]["enabled"] = False
        self.g.tick(dobs(dead=True))
        self.assertFalse(self.g.stopped)                              # v8.43：跑尸关着 → 不停止，等活过来
        self.assertTrue(self.g.dead_idle)

    def test_old_addon_stops(self):
        self.g.tick(dobs(dead=True, info=False))
        self.assertFalse(self.g.stopped)                              # v8.43：不停止
        self.assertTrue(any("v0.12" in m for m in self.logs))

    def test_rest_interrupted_by_combat(self):
        self.g.tick(dobs(dead=True))
        self.g.tick(dobs(dead=False, hp=60, mana=40))
        self.assertEqual(self.g.cr["phase"], "rest")
        self.g.tick(dobs(dead=False, hp=55, mana=40, combat=True))
        self.assertIsNone(self.g.cr)
        self.assertTrue(any("复活后被打了" in m for m in self.logs))

    def test_rest_timeout(self):
        self.g.tick(dobs(dead=True))
        self.g.tick(dobs(dead=False, hp=95, mana=10))
        self.clock.t += 130
        self.g.tick(dobs(dead=False, hp=95, mana=30))
        self.assertIsNone(self.g.cr)

    def test_stuck_jump_then_detour(self):
        self.turn_hook()
        self.g.tick(dobs(dead=True))
        self.g.tick(dobs(ghost=True, pos=self.pos, corpse=self.corpse, facing=self.face))
        for _ in range(60):                               # 按着 w 但位置不变
            self.g.tick(dobs(ghost=True, pos=self.pos, corpse=self.corpse, facing=self.face))
            self.clock.t += 0.5
        self.assertTrue(any("跳 + 平移" in m for m in self.logs))
        self.assertTrue(any("绕 60°" in m for m in self.logs))
        self.assertFalse(self.g.stopped)

    def test_run_timeout_stops(self):
        # v8.38：跑尸超时不再停止（一直试），只写一次提醒
        self.g.tick(dobs(dead=True))
        self.g.tick(dobs(ghost=True, pos=None, corpse=None))
        self.clock.t += 700
        self.g.tick(dobs(ghost=True, pos=None, corpse=None))
        self.assertFalse(self.g.stopped)
        self.assertTrue(any("不停止，继续绕路试" in m for m in self.logs))

    def test_status_label_and_no_interact(self):
        self.g.tick(dobs(dead=True))
        self.assertIn("👻", self.g.status_label())
        self.assertNotIn("`", self.keys())

    def test_release_retry_warns(self):
        self.g.tick(dobs(dead=True))
        for _ in range(40):
            self.clock.t += 0.5
            self.g.tick(dobs(dead=True))
        self.assertGreaterEqual(self.spirit(), 3)
        self.assertTrue(any("RepopMe" in m for m in self.logs))

    def test_np_dist_logged_once(self):
        self.g.tick(dobs(dead=False))
        self.g.tick(dobs(dead=False))
        self.assertEqual(sum("姓名板显示距离 60 码" in m for m in self.logs), 1)

    def test_defaults_and_conflict(self):
        self.assertEqual(DEFAULT_CONFIG["keys"]["spirit"], "]")
        self.assertTrue(DEFAULT_CONFIG["corpse"]["enabled"])
        self.assertEqual(DEFAULT_CONFIG["corpse"]["release_delay_s"], 5.0)
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["keys"]["spirit"] = cfg["keys"]["thorns"]
        self.assertTrue(any("释放灵魂" in c for c in key_conflicts(cfg)))


@unittest.skipIf(lupa is None, "没有 lupa")
class TestLuaV012(unittest.TestCase):
    def run_lua(self, ghost=False, dead=False, corpse=(0.52, 0.48), popup=None, cvar_max=60, units=None):
        from test_addon_lua import STUBS, TestAddonLua
        L = lupa.LuaRuntime()
        L.execute(STUBS)
        L.execute("""
PRINTS = {}
function print(s) PRINTS[#PRINTS + 1] = s end
CVARS = { nameplateMaxDistance = "40" }
function GetCVar(k) return CVARS[k] end
function SetCVar(k, v) if k == "nameplateMaxDistance" then v = math.min(tonumber(v), CVAR_MAX) end; CVARS[k] = tostring(v) end
function UnitIsGhost(u) return u == "player" and GHOST or false end
local UD = UnitIsDeadOrGhost
function UnitIsDeadOrGhost(u) if u == "player" then return DEAD or GHOST end if UNITS and UNITS[u] then return UNITS[u].dead end return UD(u) end
function GetCorpseRecoveryDelay() return 0 end
function StaticPopup_Visible(w) return POPUP == w end
C_DeathInfo = { GetCorpseMapPosition = function(m)
  if not CX then return nil end
  return { GetXY = function() return CX, CY end } end }
UNITS = {}
local UE = UnitExists
function UnitExists(u) if UNITS[u] then return true end return UE(u) end
function UnitCanAttack(a, b) local u = UNITS[a] or UNITS[b]; if u then return u.atk end return true end
function UnitReaction(a, b) local u = UNITS[a] or UNITS[b]; if u then return u.react end return 4 end
function UnitIsPlayer(u) return false end
FRAMES = {}
local CF = CreateFrame
function CreateFrame(kind, name, parent)
  local f = CF(kind, name, parent)
  f.Show = function(self) self.shown = true end
  f.Hide = function(self) self.shown = false end
  if kind ~= "StatusBar" then f.SetPoint = function(self, a, rel, b, x, y) self.anchor = rel end end
  FRAMES[#FRAMES + 1] = f
  return f
end
C_NamePlate = { GetNamePlateForUnit = function(u)
  if UNITS[u] then return { name = u } end return nil end }
""")
        g = L.globals()
        g.GHOST, g.DEAD, g.CVAR_MAX = ghost, dead, cvar_max
        if corpse:
            g.CX, g.CY = corpse
        g.POPUP = popup
        for u, d in (units or {}).items():
            L.execute(f'UNITS["{u}"] = {{ atk = {str(d.get("atk", False)).lower()}, react = {d.get("react", 4)}, '
                      f'dead = false }}')
        g.POSX, g.POSY, g.FACING, g.MAPID = 0.5, 0.5, 1.0, 1
        g.HASTARGET, g.TDEAD, g.COMBAT = False, False, False
        with open(LUA, encoding="utf-8") as f:
            L.execute(f.read())
        frame = g.BotStatusFrame
        frame.script_OnEvent(frame, "PLAYER_LOGIN")
        g.NOW = 103
        frame.script_OnUpdate(frame, 1.0)
        shown = [f for f in g.FRAMES.values() if f.shown is True and f.anchor is not None
                 and not isinstance(f.anchor, (int, float)) and f.anchor != g.BotStatusFrame]
        img = TestAddonLua.paint(g)
        st = AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(img)
        return st, g, len(shown)

    def test_alive_cvar_max_and_signature(self):
        # v0.20（WowGrinder v8.66）：插件不再改姓名板距离 CVar，只读出当前值照旧报给程序
        st, g, _ = self.run_lua()
        self.assertTrue(st.ok, st.reason)
        self.assertTrue(st.death_info)
        self.assertFalse(st.player_dead)
        self.assertEqual(st.np_dist, 40)
        self.assertTrue(st.np_ok)
        self.assertEqual(g.CVARS["nameplateMaxDistance"], "40")        # 没被改
        self.assertTrue(any("姓名板显示距离" in str(p) and "40" in str(p) for p in g.PRINTS.values()))
        self.assertIsNone(st.corpse_pos)                       # 活着不报尸体

    def test_cvar_higher_allowed(self):
        st, g, _ = self.run_lua(cvar_max=100)
        self.assertEqual(st.np_dist, 40)                               # v0.20：客户端允许更大也不改
        self.assertEqual(g.CVARS["nameplateMaxDistance"], "40")

    def test_ghost_corpse_and_popup(self):
        st, g, _ = self.run_lua(ghost=True, popup="RECOVER_CORPSE")
        self.assertTrue(st.ghost and st.player_dead and st.res_popup)
        self.assertAlmostEqual(st.corpse_pos[0], 52.0, places=1)
        self.assertAlmostEqual(st.corpse_pos[1], 48.0, places=1)
        self.assertEqual(st.res_delay, 0)
        self.assertTrue(st.ghost_marks)

    def test_dead_release_popup(self):
        st, g, _ = self.run_lua(dead=True, popup="DEATH")
        self.assertTrue(st.player_dead and st.release_popup)
        self.assertFalse(st.ghost)

    def test_ghost_marks_hostile_only(self):
        units = {"nameplate1": {"react": 2}, "nameplate2": {"react": 4}, "nameplate3": {"atk": True, "react": 4}}
        _, _, n = self.run_lua(ghost=True, units=units)
        self.assertEqual(n, 2)                                 # 敌对 + 能打我的；中立的不算

    def test_toc(self):
        with open(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.toc"), encoding="utf-8") as f:
            self.assertRegex(f.read(), r"## Version: (0\.1[2-9]|0\.[2-9]\d)\.0")   # 插件版本往上走


if __name__ == "__main__":
    unittest.main()

"""用 lupa 在假的 WoW API 上跑 BotStatus.lua，截取它“画”的颜色，交给 Python 读取器解码。没装 lupa 就跳过。"""
import copy
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.addon_reader import AddonReader, apply_gamma  # noqa: E402
from bot.config import DEFAULT_CONFIG  # noqa: E402

try:
    import lupa
except ImportError:  # Simon 的电脑上没有 lupa，正常
    lupa = None

LUA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "addon", "BotStatus", "BotStatus.lua")

STUBS = r"""
TEXTURES = {}
BARS = {}
local function obj()
  local o = {}
  setmetatable(o, {__index = function(t, k) return function() end end})
  return o
end
function CreateFrame(kind, name, parent)
  local f = obj()
  f.SetScript = function(self, ev, fn) self["script_" .. ev] = fn end
  f.CreateTexture = function()
    local t = obj()
    t.SetVertexColor = function(self, r, g, b) self.rgb = {r, g, b} end
    t.SetPoint = function(self, a, rel, b, x, y) self.px, self.py = x, y end
    t.SetSize = function(self, w, h) self.w, self.h = w, h end
    TEXTURES[#TEXTURES + 1] = t
    return t
  end
  if kind == "StatusBar" then
    f.SetMinMaxValues = function(self, lo, hi) self.max = hi end
    f.SetValue = function(self, v) self.value = v end
    f.SetPoint = function(self, a, rel, b, x, y) self.px, self.py = x, y end
    f.SetSize = function(self, w, h) self.w, self.h = w, h end
    BARS[#BARS + 1] = f
  end
  if name then _G[name] = f end
  return f
end
function GetPhysicalScreenSize() return 1920, 1080 end
function InCombatLockdown() return false end
function UnitIsGhost() return false end
function UnitIsDeadOrGhost(u) return u == "target" and TDEAD or false end
function UnitAffectingCombat() return COMBAT end
function UnitExists(u) return u == "target" and HASTARGET end
function UnitCanAttack() return true end
function UnitIsUnit() return false end
function UnitCastingInfo() return nil end
function UnitChannelInfo() return nil end
function GetShapeshiftFormID() return 5 end
function GetUnitSpeed() return 0 end
function UnitHealth(u) if u == "player" then return 640 else return 330 end end
function UnitHealthMax(u) return 1000 end
function UnitPower(u, t) if t == 1 then return 12 else return 910 end end
function UnitPowerMax(u, t) if t == 1 then return 100 else return 1000 end end
function GetPlayerFacing() return FACING end
function GetActionInfo(slot) if ATTACKSLOT and slot == ATTACKSLOT then return "spell", 6603 end return nil end
function IsActionInRange(slot) if slot == ATTACKSLOT then return INRANGE end return nil end
function UnitGUID(u) if u == "player" then return "P-1" elseif u == "target" then return "T-1" end end
NOW = 100
function GetTime() return NOW end
CLEU = nil
function CombatLogGetCurrentEventInfo() return table.unpack(CLEU) end
ERR_BADATTACKFACING = "你面朝错误的方向！"
ERR_BADATTACKPOS = "你距离太远！"
SPELL_FAILED_LINE_OF_SIGHT = "目标不在视野中"
C_Map = {
  GetBestMapForUnit = function() return MAPID end,
  GetPlayerMapPosition = function(id, unit)
    if not POSX then return nil end
    return { GetXY = function() return POSX, POSY end }
  end,
}
SlashCmdList = {}
"""


@unittest.skipIf(lupa is None, "没有 lupa")
class TestAddonLua(unittest.TestCase):
    def read(self, img, gamma=1.0):
        return AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(apply_gamma(img, gamma))

    def test_attack_slot_range_and_errors(self):
        img = self.render(slot=37, inrange=True, events=[("UI_ERROR_MESSAGE", 50, "你面朝错误的方向！")])
        st = self.read(img, 1.2)
        self.assertTrue(st.ok, st.reason)
        self.assertEqual(st.attack_range, "in")
        self.assertTrue(st.attack_slot)
        self.assertFalse(st.err_facing)            # 报错在 t=100，读取时 t=103 → 已过 1.5 秒
        img = self.render(slot=37, inrange=False)
        self.assertEqual(self.read(img).attack_range, "out")
        img = self.render(slot=None)
        st = self.read(img)
        self.assertEqual(st.attack_range, "no_slot")
        self.assertFalse(st.attack_slot)

    def test_error_blocks_lit(self):
        L = self
        for msg, attr in (("你面朝错误的方向！", "err_facing"), ("你距离太远！", "err_far"), ("目标不在视野中", "err_los")):
            img = self.render_at(msg)
            st = L.read(img, 1.2)
            self.assertTrue(getattr(st, attr), msg)

    def render_at(self, msg):
        """报错发生在 t=102.5，读取在 t=103（1.5 秒内）。"""
        L = lupa.LuaRuntime()
        L.execute(STUBS)
        g = L.globals()
        g.POSX, g.POSY, g.FACING, g.MAPID, g.HASTARGET, g.TDEAD, g.COMBAT = 0.5, 0.5, 1.0, 1, True, False, True
        with open(LUA, encoding="utf-8") as f:
            L.execute(f.read())
        frame = g.BotStatusFrame
        g.NOW = 102.5
        frame.script_OnEvent(frame, "UI_ERROR_MESSAGE", 50, msg)
        g.NOW = 103
        frame.script_OnUpdate(frame, 1.0)
        return self.paint(g)

    def test_since_damage_from_combat_log(self):
        dmg = ("CLEU", (100.5, "SWING_DAMAGE", False, "P-1", "me", 0, 0, "T-1"))
        st = self.read(self.render(slot=1, inrange=True, combat=True, events=[dmg]), 1.2)
        self.assertEqual(st.since_damage, 3)        # 战斗日志在 t=100，读取在 t=103
        other = ("CLEU", (100.5, "SWING_DAMAGE", False, "P-1", "me", 0, 0, "X-9"))
        st = self.read(self.render(slot=1, combat=True, events=[other]))
        self.assertTrue(math.isinf(st.since_damage))   # 日志能用，但没打到当前目标
        st = self.read(self.render(slot=1, combat=True))
        self.assertIsNone(st.since_damage)              # 没收到战斗日志 → 无效

    def test_combat_reads_bars(self):
        st = self.read(self.render(slot=1, inrange=True, combat=True), 1.2)
        self.assertTrue(st.in_combat)
        self.assertEqual((st.player_hp, st.target_hp), (64, 33))   # 来自进度条
        self.assertTrue(st.hp_bar_ok)
        self.assertEqual(st.version, 7)
        self.assertEqual(st.hp_source, "bar")

    def test_facing_error_counter(self):
        msg = "你面朝错误的方向！"
        ev = [("UI_ERROR_MESSAGE", 50, msg)] * 3
        st = self.read(self.render(slot=1, combat=True, events=ev), 1.2)
        self.assertEqual(st.facing_count, 3)
        st = self.read(self.render(slot=1, combat=True, events=[("UI_ERROR_MESSAGE", 50, msg)] * 9))
        self.assertEqual(st.facing_count, 1)          # 0-7 循环
        st = self.read(self.render(slot=1, combat=True))
        self.assertEqual(st.facing_count, 0)

    def test_buff_blocks(self):
        st = self.read(self.render(slot=1))
        self.assertEqual((st.buff_thorns, st.buff_motw), ("unknown", "unknown"))   # 没有 aura API
        st = self.read(self.render(slot=1, auras="C_UnitAuras = { GetPlayerAuraBySpellID = function(id) "
                                   "if id == 9910 then return { expirationTime = 700 } end return nil end }"))
        self.assertEqual((st.buff_thorns, st.buff_motw), ("yes", "no"))
        st = self.read(self.render(slot=1, auras="C_UnitAuras = { GetPlayerAuraBySpellID = function(id) "
                                   "if id == 1126 then return { expirationTime = NOW + 30 } end return nil end }"))
        self.assertEqual((st.buff_thorns, st.buff_motw), ("no", "low"))
        # 按序号 + 名字（客户端语言）
        st = self.read(self.render(slot=1, auras="C_UnitAuras = { GetAuraDataByIndex = function(u, i) "
                                   "if i == 1 then return { name = '野性印记', expirationTime = 0 } end return nil end }"))
        self.assertEqual((st.buff_thorns, st.buff_motw), ("no", "yes"))
        st = self.read(self.render(slot=1, auras="C_UnitAuras = { GetPlayerAuraBySpellID = function(id) return nil end }; "
                                   "IsPlayerSpell = function(id) return id == 1126 end"))
        self.assertEqual((st.buff_thorns, st.buff_motw), ("unlearned", "no"))

    def test_game_menu_block(self):
        self.assertFalse(self.read(self.render(slot=1)).game_menu)
        self.assertTrue(self.read(self.render(slot=1, menu=True)).game_menu)

    def test_marker_state(self):
        # 没有 C_NamePlate（被拦/不存在）→ 洋红 error
        st = self.read(self.render(slot=1))
        self.assertEqual(st.marker_state, "error")
        st = self.read(self.render(slot=1, plate="none"))
        self.assertEqual(st.marker_state, "no_plate")
        st = self.read(self.render(slot=1, plate="ok"))
        self.assertEqual(st.marker_state, "shown")
        st = self.read(self.render(slot=1, plate="ok", target=False))
        self.assertEqual(st.marker_state, "none")

    def render(self, posx=0.4567, posy=0.7891, facing=1.2345, mapid=1423, target=True, slot=None, inrange=None,
               combat=False, events=(), plate=None, menu=False, auras=None):
        L = lupa.LuaRuntime()
        L.execute(STUBS)
        if auras:
            L.execute(auras)
        if menu:
            L.execute("GameMenuFrame = { IsShown = function() return true end }")
        if plate == "ok":
            L.execute("C_NamePlate = { GetNamePlateForUnit = function(u) return CreateFrame('Frame') end }")
        elif plate == "none":
            L.execute("C_NamePlate = { GetNamePlateForUnit = function(u) return nil end }")
        g = L.globals()
        g.POSX, g.POSY, g.FACING, g.MAPID = posx, posy, facing, mapid
        g.HASTARGET, g.TDEAD, g.COMBAT = target, False, combat
        g.ATTACKSLOT, g.INRANGE = slot, inrange
        with open(LUA, encoding="utf-8") as f:
            L.execute(f.read())
        frame = g.BotStatusFrame
        frame.script_OnEvent(frame, "PLAYER_ENTERING_WORLD")
        for ev in events:
            if ev[0] == "CLEU":
                g.CLEU = L.table(*ev[1])
                frame.script_OnEvent(frame, "COMBAT_LOG_EVENT_UNFILTERED")
            else:
                frame.script_OnEvent(frame, *ev)
        g.NOW = 103
        frame.script_OnUpdate(frame, 1.0)
        return self.paint(g)

    @staticmethod
    def paint(g):
        img = np.zeros((200, 400, 3), np.uint8)
        for t in g.TEXTURES.values():
            if not all(isinstance(v, (int, float)) for v in (t.px, t.py, t.w, t.h)) or t.rgb is None:
                continue   # 进度条背景（SetAllPoints）
            x, y = int(t.px), int(-t.py)
            r, gg, b = (int(round(t.rgb[i] * 255)) for i in (1, 2, 3))
            img[y:y + int(t.h), x:x + int(t.w)] = (b, gg, r)
        for bar in g.BARS.values():   # 进度条：按值画绿色宽度
            if isinstance(bar.value, (int, float)) and isinstance(bar.max, (int, float)) and bar.max:
                w = int(round(bar.value / bar.max * bar.w))
                img[int(-bar.py):int(-bar.py) + int(bar.h), int(bar.px):int(bar.px) + w] = (0, 255, 0)
        return img

    def test_lua_render_decodes_under_gamma(self):
        img = self.render()
        for gamma in (1.0, 1.2, 1.5):
            with self.subTest(gamma=gamma):
                st = AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(apply_gamma(img, gamma))
                self.assertTrue(st.ok, st.reason)
                self.assertAlmostEqual(st.pos[0], 45.67, places=2)
                self.assertAlmostEqual(st.pos[1], 78.91, places=2)
                self.assertAlmostEqual(st.facing, 1.2345, places=3)
                self.assertEqual(st.map_id, 1423)
                self.assertEqual((st.player_hp, st.target_hp, st.rage, st.mana), (64, 33, 12, 91))
                self.assertEqual(st.form, "bear")
                self.assertEqual(st.target_kind, "enemy")
                self.assertEqual(st.gamma_note == "", gamma == 1.0)

    def test_v09_combo_and_energy(self):
        st = AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(self.render(auras="function GetComboPoints(u, t) return 3 end"))
        self.assertTrue(st.ok, st.reason)
        self.assertEqual(st.combo, 3)
        self.assertAlmostEqual(st.energy, 85.7, places=1)     # 910/1000 → 6/7 档
        st = AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(self.render())
        self.assertIsNone(st.combo)                            # 读不到连击点 → None（走按次数轮转）

    def test_unavailable_location(self):
        img = self.render(posx=None, posy=None, facing=None, mapid=None)
        st = AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(img)
        self.assertTrue(st.ok)
        self.assertIsNone(st.pos)
        self.assertIsNone(st.facing)
        self.assertIsNone(st.map_id)


if __name__ == "__main__":
    unittest.main()

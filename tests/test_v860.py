"""v8.60（t231u）：大地图图层 —— WowGrinder 保存时写 BotStatus\\MapData.lua，插件 v0.17 在大地图上画
刷怪中心 + 半径圈、跑尸路线、修理路线 + 修理商；地图上有显示/隐藏按钮（BotStatusMapDB 记住）。"""
import copy
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bot import config as bcfg  # noqa: E402
from bot import mapdata  # noqa: E402
from bot.config import DEFAULT_CONFIG  # noqa: E402

try:
    import lupa
    from lupa import LuaRuntime
except ImportError:  # pragma: no cover
    lupa = None

ADDON = os.path.join(ROOT, "addon", "BotStatus")


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _cfg():
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["area"]["centers"] = [[40.0, 50.0, 1441], [45.5, 52.25, 1441]]
    c["area"]["radius"] = 3.0
    c["corpse"]["route"] = [[30.0, 60.0, 1441], [35.0, 55.0, 1441], [40.0, 50.0, 1441]]
    c["repair"]["route"] = [[40.0, 50.0, 1441], [50.0, 40.0, 1441], [55.0, 38.0, 1441]]
    c["repair"]["vendor_i"] = 2
    return c


def _addons_dir(tmp):
    d = os.path.join(tmp, "AddOns", "BotStatus")
    os.makedirs(d)
    open(os.path.join(d, "BotStatus.toc"), "w").close()
    return os.path.join(tmp, "AddOns"), d


class Writer(unittest.TestCase):
    def test_default_key(self):
        self.assertEqual(DEFAULT_CONFIG["addon"]["addons_dir"], "")

    @unittest.skipIf(lupa is None, "没有 lupa")
    def test_build_lua_parses(self):
        L = LuaRuntime(unpack_returned_tuples=True)
        L.execute(mapdata.build_lua(_cfg(), now=0))
        D = L.globals().BotStatusMapData
        self.assertEqual(D.version, 1)
        self.assertEqual(D.radius, 3.0)
        self.assertEqual(len(D.centers), 2)
        self.assertEqual((D.centers[2][1], D.centers[2][2], D.centers[2][3]), (45.5, 52.25, 1441))
        self.assertEqual(len(D.corpse), 3)
        self.assertEqual(len(D.repair), 3)
        self.assertEqual(D.vendor, 3)          # vendor_i=2（0 开始）→ Lua 第 3 个点

    @unittest.skipIf(lupa is None, "没有 lupa")
    def test_empty_and_bad_points(self):
        c = copy.deepcopy(DEFAULT_CONFIG)
        c["area"]["centers"] = [[1, 2], "坏", [3, 4, None]]
        c["repair"]["vendor_i"] = -1
        L = LuaRuntime(unpack_returned_tuples=True)
        L.execute(mapdata.build_lua(c))
        D = L.globals().BotStatusMapData
        self.assertEqual(len(D.centers), 2)
        self.assertEqual(D.centers[1][3], 0)   # 没有地图编号 → 0（插件按“当前地图”画）
        self.assertEqual(len(D.corpse), 0)
        self.assertEqual(D.vendor, 0)

    def test_write_atomic_and_find(self):
        with tempfile.TemporaryDirectory() as tmp:
            addons, bs = _addons_dir(tmp)
            c = _cfg()
            c["addon"]["addons_dir"] = addons
            p = mapdata.write_map_data(c)
            self.assertEqual(p, os.path.join(bs, "MapData.lua"))
            self.assertIn("BotStatusMapData", _rd(p))
            self.assertFalse(os.path.exists(p + ".tmp"))
            self.assertIn("中心 2 个", mapdata.status_msg())
            c["addon"]["addons_dir"] = bs            # 直接填 BotStatus 文件夹也行
            self.assertEqual(mapdata.write_map_data(c), p)

    def test_missing_dir_is_quiet(self):
        c = _cfg()
        c["addon"]["addons_dir"] = "/没有/这个/文件夹"
        self.assertIsNone(mapdata.write_map_data(c))
        self.assertIn("找不到 BotStatus", mapdata.status_msg())

    def test_save_config_writes_only_for_real_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            addons, bs = _addons_dir(tmp)
            c = _cfg()
            c["addon"]["addons_dir"] = addons
            real = os.path.join(tmp, "config.json")
            other = os.path.join(tmp, "other.json")
            with mock.patch.object(bcfg, "CONFIG_PATH", real), mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WOWGRINDER_NO_MAPDATA", None)
                bcfg.save_config(c, other)
                self.assertFalse(os.path.exists(os.path.join(bs, "MapData.lua")))
                bcfg.save_config(c, real)
                self.assertTrue(os.path.exists(os.path.join(bs, "MapData.lua")))
                self.assertTrue(os.path.exists(real))

    def test_save_config_survives_writer_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "config.json")
            with mock.patch.object(bcfg, "CONFIG_PATH", real), \
                    mock.patch.object(mapdata, "write_map_data", side_effect=RuntimeError("x")):
                bcfg.save_config(_cfg(), real)
            self.assertTrue(os.path.exists(real))


OVERLAY_STUBS = r"""
FRAMES = {}
HOOKS = {}
local function obj()
  local o = {}
  setmetatable(o, {__index = function(t, k) return function() end end})
  return o
end
local function region(kind)
  local r = obj()
  r.kind = kind
  r.shown = true
  r.kids = {}
  r.Show = function(self) self.shown = true end
  r.Hide = function(self) self.shown = false end
  r.SetText = function(self, t) self.text = t end
  r.SetPoint = function(self, a, rel, b, x, y) self.px, self.py = x, y end
  r.SetStartPoint = function(self, a, rel, x, y) self.x1, self.y1 = x, y end
  r.SetEndPoint = function(self, a, rel, x, y) self.x2, self.y2 = x, y end
  r.SetColorTexture = function(self, r_, g, b, a) self.rgb = {r_, g, b} end
  return r
end
function CreateFrame(kind, name, parent, tpl)
  local f = region(kind)
  f.scripts = {}
  f.SetScript = function(self, ev, fn) self.scripts[ev] = fn end
  f.HookScript = function(self, ev, fn) HOOKS[#HOOKS + 1] = ev end
  f.RegisterEvent = function() end
  f.GetFrameLevel = function() return 5 end
  f.GetWidth = function() return 1000 end
  f.GetHeight = function() return 500 end
  f.CreateTexture = function(self) local t = region("tex"); table.insert(self.kids, t); return t end
  f.CreateLine = function(self) local t = region("line"); table.insert(self.kids, t); return t end
  f.CreateFontString = function(self) local t = region("font"); table.insert(self.kids, t); return t end
  FRAMES[#FRAMES + 1] = f
  if name then _G[name] = f end
  return f
end
function hooksecurefunc(t, k, fn) HOOKS[#HOOKS + 1] = k end
SlashCmdList = {}
CURMAP = 1441
WorldMapFrame = CreateFrame("Frame")
WorldMapFrame.IsShown = function() return true end
WorldMapFrame.GetMapID = function() return CURMAP end
WorldMapFrame.OnMapChanged = function() end
WorldMapFrame.ScrollContainer = CreateFrame("Frame")
WorldMapFrame.ScrollContainer.Child = CreateFrame("Frame")
function CreateVector2D(x, y) return {x = x, y = y, GetXY = function(s) return s.x, s.y end} end
-- 假世界：1441（千针石林）在大陆 1414 上占 x 0.5-0.6、y 0.6-0.7；1415 不在同一大陆
C_Map = {
  GetWorldPosFromMapPos = function(m, v)
    if m == 1441 then return 1, {wx = 0.5 + v.x * 0.1, wy = 0.6 + v.y * 0.1} end
    if m == 1414 then return 1, {wx = v.x, wy = v.y} end
    return nil
  end,
  GetMapPosFromWorldPos = function(cont, w, dst)
    if dst == 1414 then return dst, CreateVector2D(w.wx, w.wy) end
    if dst == 1441 then return dst, CreateVector2D((w.wx - 0.5) * 10, (w.wy - 0.6) * 10) end
    return nil
  end,
}
"""


@unittest.skipIf(lupa is None, "没有 lupa")
class Overlay(unittest.TestCase):
    def load(self, data=True, db=None):
        L = LuaRuntime(unpack_returned_tuples=True)
        L.execute(OVERLAY_STUBS)
        g = L.globals()
        if db is not None:
            L.execute(db)
        if data:
            L.execute(mapdata.build_lua(_cfg(), now=0))
        else:
            L.execute(_rd(os.path.join(ADDON, "MapData.lua")))
        L.execute(_rd(os.path.join(ADDON, "MapOverlay.lua")))
        ev = g.FRAMES[len(g.FRAMES)]               # 最后建的是事件框
        ev.scripts["OnEvent"](ev, "ADDON_LOADED", "BotStatus")
        return L, g

    def shown(self, g, kind):
        M = g.BotStatusMapOverlay
        kids = M.frame.kids
        return [k for k in kids.values() if k.kind == kind and k.shown]

    def test_setup_hooks_and_button(self):
        L, g = self.load()
        M = g.BotStatusMapOverlay
        self.assertTrue(M.frame)
        self.assertEqual(g.BotStatusMapToggle.text, "WG 图层：开")
        hooks = list(g.HOOKS.values())
        self.assertIn("OnShow", hooks)
        self.assertIn("OnMapChanged", hooks)
        self.assertTrue(g.SlashCmdList["BOTSTATUSMAP"])

    def test_draw_same_map(self):
        L, g = self.load()
        g.BotStatusMapOverlay.Refresh()
        lines = self.shown(g, "line")
        # 2 个圈 × 48 段 + 跑尸 2 段 + 修理 2 段
        self.assertEqual(len(lines), 2 * 48 + 2 + 2)
        self.assertEqual(g.BotStatusMapOverlay.drawn, 2 + 3 + 3)
        fonts = sorted(f.text for f in self.shown(g, "font"))
        self.assertEqual(fonts, ["1", "2", "修"])
        dots = self.shown(g, "tex")
        self.assertEqual(len(dots), 2 + 3 + 3)
        # 中心 #1 (40,50) → 画布 1000x500 上的 (400, -250)
        self.assertTrue(any(abs(d.px - 400) < 1e-6 and abs(d.py + 250) < 1e-6 for d in dots))

    def test_radius_circle_size(self):
        L, g = self.load()
        g.BotStatusMapOverlay.Refresh()
        xs = [l.x1 for l in self.shown(g, "line")[:48]]
        # 半径 3（地图坐标）→ 1000 宽的画布上 ±30
        self.assertAlmostEqual(max(xs) - min(xs), 60, delta=0.5)

    def test_convert_to_parent_map(self):
        L, g = self.load()
        M = g.BotStatusMapOverlay
        x, y = M.Convert(0.4, 0.5, 1441, 1414)
        self.assertAlmostEqual(x, 0.54)
        self.assertAlmostEqual(y, 0.65)
        self.assertIsNone(M.Convert(0.4, 0.5, 1441, 1415))
        self.assertEqual(M.Convert(0.4, 0.5, 0, 1415), (0.4, 0.5))
        g.CURMAP = 1414
        M.Refresh()
        self.assertEqual(M.drawn, 8)
        g.CURMAP = 1415                              # 换算不了 → 什么都不画
        M.Refresh()
        self.assertEqual(M.drawn, 0)
        self.assertEqual(len(self.shown(g, "line")), 0)

    def test_toggle_hides_and_remembers(self):
        L, g = self.load()
        M = g.BotStatusMapOverlay
        M.Refresh()
        M.Toggle()
        self.assertTrue(g.BotStatusMapDB.hidden)
        self.assertEqual(g.BotStatusMapToggle.text, "WG 图层：关")
        self.assertEqual(len(self.shown(g, "line")) + len(self.shown(g, "tex")), 0)
        M.Toggle()
        self.assertFalse(g.BotStatusMapDB.hidden)
        self.assertGreater(len(self.shown(g, "line")), 0)

    def test_saved_hidden_state(self):
        L, g = self.load(db="BotStatusMapDB = {hidden = true}")
        self.assertEqual(g.BotStatusMapToggle.text, "WG 图层：关")
        g.BotStatusMapOverlay.Refresh()
        self.assertEqual(g.BotStatusMapOverlay.drawn, 0)

    def test_no_data_default_file(self):
        L, g = self.load(data=False)
        self.assertIsNone(g.BotStatusMapData)
        g.BotStatusMapOverlay.Refresh()                # 不报错，什么都不画
        self.assertEqual(g.BotStatusMapOverlay.drawn, 0)

    def test_no_worldmap_no_error(self):
        L = LuaRuntime(unpack_returned_tuples=True)
        L.execute(OVERLAY_STUBS)
        L.execute("WorldMapFrame = nil")
        L.execute(_rd(os.path.join(ADDON, "MapOverlay.lua")))
        g = L.globals()
        ev = g.FRAMES[len(g.FRAMES)]
        ev.scripts["OnEvent"](ev, "PLAYER_LOGIN")
        self.assertFalse(g.BotStatusMapOverlay.frame)
        g.BotStatusMapOverlay.Refresh()


class Strings(unittest.TestCase):
    def test_toc(self):
        toc = _rd(os.path.join(ADDON, "BotStatus.toc"))
        self.assertRegex(toc, r"## Version: 0\.(1[7-9]|[2-9]\d)\.\d")
        self.assertIn("## SavedVariables: BotStatusMapDB", toc)
        files = [l.strip() for l in toc.splitlines() if l.strip() and not l.startswith("#")]
        self.assertEqual(files[:3], ["MapData.lua", "BotStatus.lua", "MapOverlay.lua"])
        for f in files:
            self.assertTrue(os.path.isfile(os.path.join(ADDON, f)), f)

    def test_overlay_no_global_taint(self):
        src = _rd(os.path.join(ADDON, "MapOverlay.lua"))
        self.assertNotRegex(src, r"(?m)^\s*SlashCmdList\s*=")
        self.assertNotIn("SetCVar", src)
        self.assertNotIn("Minimap", src)

    def test_gui_and_readme(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertRegex(app, r"熊德自动打怪 v8\.(6\d|[7-9]\d)")
        self.assertIn('"addons_dir"', app)
        self.assertIn("status_msg", app)
        self.assertTrue(re.search(r"v8\.60.*大地图", _rd(os.path.join(ROOT, "README_中文.md"))))
        self.assertIn("v0.17", _rd(os.path.join(ADDON, "BotStatus.lua")))


if __name__ == "__main__":
    unittest.main()

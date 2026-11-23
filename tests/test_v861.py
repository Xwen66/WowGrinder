"""v8.61（t232u“地图上并没有任何显示”）：插件 v0.18
- 根因：图层固定放在画布层级 +500，被区域底图/图钉框架盖住 → 现在每次画、地图开着每 0.5 秒都抬到画布上最高子/孙框架 +1
- 线宽/点大小按 GetCanvasScale 换算；地图开着时自动检查重画；出错打印到聊天框
- /wgmap 打印诊断（地图编号、数据、画了几个点、层级）；/wgmap on|off 开关"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot import mapdata  # noqa: E402

try:
    from lupa import LuaRuntime
    import test_v860 as t860
except ImportError:  # pragma: no cover
    LuaRuntime = None

ADDON = os.path.join(ROOT, "addon", "BotStatus")

EXTRA = r"""
PRINTS = {}
function print(s) PRINTS[#PRINTS + 1] = tostring(s) end
SCALE = 0.5
WorldMapFrame.GetCanvasScale = function() return SCALE end
-- 画布上已有一个底图框架，层级 800（比旧的固定 +500 高）
local canvas = WorldMapFrame.ScrollContainer.Child
local art = CreateFrame("Frame")
art.GetFrameLevel = function() return 800 end
local pin = CreateFrame("Frame")
pin.GetFrameLevel = function() return 900 end
art.GetChildren = function() return pin end
canvas.GetChildren = function() return art, BotStatusMapOverlayFrame end
"""


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


@unittest.skipIf(LuaRuntime is None, "没有 lupa")
class Overlay(unittest.TestCase):
    def load(self, data=True):
        L = LuaRuntime(unpack_returned_tuples=True)
        L.execute(t860.OVERLAY_STUBS)
        L.execute(EXTRA)
        L.execute("""
          local mk = CreateFrame
          function CreateFrame(kind, name, parent, tpl)
            local f = mk(kind, name, parent, tpl)
            local lv = 5
            f.SetFrameLevel = function(self, v) lv = v end
            f.GetFrameLevel = function(self) return lv end
            return f
          end""")
        L.execute(mapdata.build_lua(t860._cfg(), now=0) if data else _rd(os.path.join(ADDON, "MapData.lua")))
        L.execute(_rd(os.path.join(ADDON, "MapOverlay.lua")))
        g = L.globals()
        ev = g.FRAMES[len(g.FRAMES)]
        ev.scripts["OnEvent"](ev, "ADDON_LOADED", "BotStatus")
        ev.scripts["OnEvent"](ev, "PLAYER_LOGIN")
        return L, g

    def prints(self, g):
        return "\n".join(g.PRINTS[i] for i in range(1, len(g.PRINTS) + 1))

    def test_raised_above_map_frames(self):
        L, g = self.load()
        M = g.BotStatusMapOverlay
        M.Refresh()
        self.assertEqual(M.frame.GetFrameLevel(M.frame), 901)   # 孙框架 900 + 1
        self.assertEqual(M.drawn, 8)

    def test_thickness_follows_scale(self):
        L, g = self.load()
        M = g.BotStatusMapOverlay
        lines = []
        L.execute("""
          local mk = BotStatusMapOverlay.frame.CreateLine
          BotStatusMapOverlay.frame.CreateLine = function(self, ...)
            local l = mk(self, ...)
            l.SetThickness = function(s, t) s.th = t end
            return l
          end""")
        M.Refresh()
        kid = [k for k in M.frame.kids.values() if k.kind == "line"][0]
        self.assertAlmostEqual(kid.th, 4.0)            # 2 / 0.5

    def test_onupdate_redraws(self):
        L, g = self.load()
        M = g.BotStatusMapOverlay
        M.drawn = 0
        M.frame.scripts["OnUpdate"](M.frame, 0.6)
        self.assertEqual(M.drawn, 8)

    def test_login_message_and_status(self):
        L, g = self.load()
        out = self.prints(g)
        self.assertIn("中心 2 个、跑尸 3 点、修理 3 点", out)
        g.SlashCmdList["BOTSTATUSMAP"]("")
        out = self.prints(g)
        self.assertIn("显示的地图 1441", out)
        self.assertIn("上次画了 8 个点", out)
        self.assertIn("初始化：成功", out)
        self.assertIn("地图最高 900", out)

    def test_slash_on_off(self):
        L, g = self.load()
        g.SlashCmdList["BOTSTATUSMAP"]("off")
        self.assertTrue(g.BotStatusMapDB.hidden)
        self.assertEqual(g.BotStatusMapOverlay.drawn, 0)
        self.assertIn("图层已关", self.prints(g))
        g.SlashCmdList["BOTSTATUSMAP"](" ON ")
        self.assertFalse(g.BotStatusMapDB.hidden)
        self.assertEqual(g.BotStatusMapOverlay.drawn, 8)

    def test_no_data_status(self):
        L, g = self.load(data=False)
        g.SlashCmdList["BOTSTATUSMAP"]("")
        out = self.prints(g)
        self.assertIn("没有数据", out)
        self.assertIn("MapData.lua 是空的", out)

    def test_draw_error_is_printed_once(self):
        L, g = self.load()
        L.execute("WorldMapFrame.GetMapID = function() error('坏了') end")
        M = g.BotStatusMapOverlay
        M.Refresh()
        M.Refresh()
        out = self.prints(g)
        self.assertEqual(out.count("大地图图层出错"), 1)
        self.assertIn("坏了", M.err)

    def test_setup_error_printed(self):
        L = LuaRuntime(unpack_returned_tuples=True)
        L.execute(t860.OVERLAY_STUBS)
        L.execute(EXTRA)
        L.execute("WorldMapFrame.GetCanvas = function() error('没有画布') end")
        L.execute(_rd(os.path.join(ADDON, "MapOverlay.lua")))
        g = L.globals()
        ev = g.FRAMES[len(g.FRAMES)]
        ev.scripts["OnEvent"](ev, "PLAYER_LOGIN")
        self.assertIn("初始化失败", "\n".join(g.PRINTS.values()))


class Strings(unittest.TestCase):
    def test_versions(self):
        self.assertRegex(_rd(os.path.join(ADDON, "BotStatus.toc")), r"## Version: 0\.(1[89]|[2-9]\d)\.0")
        self.assertIn("v0.18", _rd(os.path.join(ADDON, "BotStatus.lua")))
        src = _rd(os.path.join(ADDON, "MapOverlay.lua"))
        self.assertNotIn("+ 500", src)
        self.assertIn("GetCanvasScale", src)
        self.assertRegex(_rd(os.path.join(ROOT, "gui", "app.py")), r"熊德自动打怪 v8\.(6[1-9]|[7-9]\d)")
        self.assertIn("v8.61", _rd(os.path.join(ROOT, "README_中文.md")))


if __name__ == "__main__":
    unittest.main()

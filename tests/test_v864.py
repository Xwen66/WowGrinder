"""v8.64（t243u“把中心点范围在小地图上也同时显示”）：插件 v0.19 MinimapOverlay.lua
小地图上画 刷怪中心（黄点 + 编号）+ 半径圈；换算同 HereBeDragons-Pins（世界坐标差 ÷ 视野半径 × 小地图半宽，
旋转小地图、方形小地图、室内/室外缩放表）；圈到边上截断；和大地图共用“WG 图层”开关 / /wgmap。"""
import math
import os
import re
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

# 假世界：1441 上 x 每 1% = 40 码（往东 → “西向坐标”变小），y 每 1% = 26 码（往南 → 北向坐标变小）；大陆 1
MINI = r"""
PRINTS = {}
function print(s) PRINTS[#PRINTS + 1] = tostring(s) end
local old = C_Map.GetWorldPosFromMapPos
C_Map.GetWorldPosFromMapPos = function(m, v)
  local c, w = old(m, v)
  if not c then return nil end
  if m == 1441 then
    local north, west = 5000 - v.y * 2600, 10000 - v.x * 4000
    w.GetXY = function() return north, west end
  end
  return c, w
end
PX, PY = 0.40, 0.50
C_Map.GetBestMapForUnit = function() return 1441 end
C_Map.GetPlayerMapPosition = function(m, u)
  if PX == nil then return nil end
  return CreateVector2D(PX, PY)
end
VIEW = 200
C_Minimap = { GetViewRadius = function() return VIEW end }
CVARS = { rotateMinimap = "0", minimapZoom = "0" }
function GetCVar(k) return CVARS[k] end
FACING = 0
function GetPlayerFacing() return FACING end
Minimap = CreateFrame("Frame", "Minimap")
Minimap.GetWidth = function() return 140 end
Minimap.GetHeight = function() return 140 end
ZOOM = 0
Minimap.GetZoom = function() return ZOOM end
"""


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


@unittest.skipIf(LuaRuntime is None, "没有 lupa")
class Mini(unittest.TestCase):
    def load(self, extra=""):
        L = LuaRuntime(unpack_returned_tuples=True)
        L.execute(t860.OVERLAY_STUBS)
        L.execute(MINI)
        L.execute(extra)
        L.execute(mapdata.build_lua(t860._cfg(), now=0))
        g = L.globals()
        L.execute(_rd(os.path.join(ADDON, "MapOverlay.lua")))
        ev1 = g.FRAMES[len(g.FRAMES)]
        L.execute(_rd(os.path.join(ADDON, "MinimapOverlay.lua")))
        ev2 = g.FRAMES[len(g.FRAMES)]
        ev1.scripts["OnEvent"](ev1, "ADDON_LOADED", "BotStatus")
        ev1.scripts["OnEvent"](ev1, "PLAYER_LOGIN")
        ev2.scripts["OnEvent"](ev2, "PLAYER_LOGIN")
        return L, g, g.BotStatusMinimapOverlay

    def shown(self, MM, kind):
        return [k for k in MM.frame.kids.values() if k.kind == kind and k.shown]

    def test_setup_and_draw_at_center1(self):
        L, g, MM = self.load()
        self.assertTrue(MM.frame)
        self.assertEqual(MM.R, 200)
        self.assertEqual(MM.drawn, 2)                      # #1 整圈 + #2 圈的一部分
        dots = self.shown(MM, "tex")
        self.assertEqual(len(dots), 1)
        self.assertAlmostEqual(dots[0].px, 0, places=6)
        self.assertAlmostEqual(dots[0].py, 0, places=6)
        self.assertEqual([f.text for f in self.shown(MM, "font")], ["1"])
        lines = self.shown(MM, "line")
        self.assertGreater(len(lines), 64)
        for l in lines:                                    # 都在小地图里（截断到 98%）
            for x, y in ((l.x1, l.y1), (l.x2, l.y2)):
                self.assertLessEqual(math.hypot(x / 70, y / 70), 0.98 + 1e-6)

    def test_ring_scale_yards(self):
        # #1 圈：半径 3% → 东西 120 码、南北 78 码；视野 200 码、半宽 70 → 42 / 27.3 像素
        L, g, MM = self.load()
        lines = self.shown(MM, "line")[:64]
        xs = [l.x1 for l in lines]
        ys = [l.y1 for l in lines]
        self.assertAlmostEqual(max(xs), 42, delta=0.01)
        self.assertAlmostEqual(max(ys), 27.3, delta=0.2)

    def test_direction_east_south(self):
        L, g, MM = self.load()
        L.execute("PX, PY = 0.44, 0.52")
        MM.Refresh()
        fonts = {f.text: f for f in self.shown(MM, "font")}
        self.assertIn("2", fonts)
        f = fonts["2"]
        self.assertAlmostEqual(f.px, 1.5 * 40 / 200 * 70, places=4)          # 东 → 右
        self.assertAlmostEqual(f.py, -0.25 * 26 / 200 * 70 + 3, places=4)   # 南 → 下（字在点上方 3 像素）

    def test_rotate_minimap(self):
        L, g, MM = self.load()
        L.execute("PX, PY = 0.475, 0.5225")           # #2 在正西 80 码
        MM.Refresh()
        d = self.shown(MM, "tex")[0]
        self.assertAlmostEqual(d.px, -80 / 200 * 70, places=4)               # 不旋转：左
        L.execute("CVARS.rotateMinimap = '1'; FACING = math.pi / 2")          # 面朝西
        MM.Refresh()
        d = self.shown(MM, "tex")[0]
        self.assertAlmostEqual(d.px, 0, places=4)
        self.assertAlmostEqual(d.py, 80 / 200 * 70, places=4)                # 旋转：面朝的方向在上面
        self.assertTrue(MM.rot)
        L.execute("GetPlayerFacing = function() return nil end")
        MM.Refresh()
        self.assertEqual(MM.drawn, 0)
        self.assertIn("朝向", MM.why)

    def test_square_shape(self):
        L, g, MM = self.load()
        sq = L.eval("{false, false, false, false}")
        self.assertTrue(MM.Inside(0.9, 0.9, 0.98, sq))
        self.assertFalse(MM.Inside(0.9, 0.9, 0.98, None))
        L.execute("function GetMinimapShape() return 'SQUARE' end")
        MM.Refresh()
        self.assertEqual(MM.shapeName, "SQUARE")
        lines = self.shown(MM, "line")
        self.assertTrue(any(math.hypot(l.x2 / 70, l.y2 / 70) > 0.99 for l in lines)
                        or all(max(abs(l.x2), abs(l.y2)) <= 0.98 * 70 + 1e-6 for l in lines))

    def test_zoom_table_fallback(self):
        L, g, MM = self.load("C_Minimap = nil; ZOOM = 2; CVARS.minimapZoom = '2'")
        self.assertAlmostEqual(MM.R, (333 + 1 / 3) / 2, places=4)
        self.assertEqual(MM.rsrc, "室外表")
        L.execute("CVARS.minimapZoom = '0'")
        MM.Refresh()
        self.assertAlmostEqual(MM.R, 90, places=4)
        self.assertEqual(MM.rsrc, "室内表")

    def test_shared_toggle(self):
        L, g, MM = self.load()
        g.SlashCmdList["BOTSTATUSMAP"]("off")
        self.assertEqual(MM.drawn, 0)
        self.assertEqual(self.shown(MM, "line"), [])
        self.assertEqual(self.shown(MM, "tex"), [])
        g.BotStatusMapOverlay.Toggle()                     # 大地图按钮
        self.assertEqual(MM.drawn, 2)
        out = "\n".join(g.PRINTS.values())
        self.assertIn("小地图：画了 0 个中心", out)

    def test_status_line(self):
        L, g, MM = self.load()
        g.SlashCmdList["BOTSTATUSMAP"]("")
        out = "\n".join(g.PRINTS.values())
        self.assertRegex(out, r"插件 v0\.(19|[2-9]\d)")
        self.assertRegex(out, r"小地图：画了 2 个中心（线 \d+ 段）；视野半径 200 码（C_Minimap")

    def test_no_position_no_draw(self):
        L, g, MM = self.load("PX = nil; UnitPosition = function() return nil end")
        self.assertEqual(MM.drawn, 0)
        self.assertIn("读不到你的坐标", MM.why)

    def test_onupdate_follows_movement(self):
        L, g, MM = self.load()
        MM.frame.scripts["OnUpdate"](MM.frame, 0.06)
        L.execute("PX, PY = 0.44, 0.52")
        MM.frame.scripts["OnUpdate"](MM.frame, 0.06)
        self.assertIn("2", [f.text for f in self.shown(MM, "font")])

    def test_far_away_nothing(self):
        L, g, MM = self.load("PX, PY = 0.9, 0.1")
        self.assertEqual(MM.drawn, 0)
        self.assertEqual(self.shown(MM, "line"), [])

    def test_world_map_unchanged(self):
        L, g, MM = self.load()
        g.BotStatusMapOverlay.Refresh()
        self.assertEqual(g.BotStatusMapOverlay.drawn, 8)

    def test_draw_error_printed_once(self):
        L, g, MM = self.load()
        L.execute("Minimap.GetWidth = function() error('坏了') end")
        MM.Refresh()
        MM.Refresh()
        self.assertEqual("\n".join(g.PRINTS.values()).count("小地图图层出错"), 1)


class Strings(unittest.TestCase):
    def test_files(self):
        toc = _rd(os.path.join(ADDON, "BotStatus.toc"))
        self.assertRegex(toc, r"## Version: 0\.(19|[2-9]\d)\.0")
        files = [l.strip() for l in toc.splitlines() if l.strip() and not l.startswith("#")]
        self.assertEqual(files, ["MapData.lua", "BotStatus.lua", "MapOverlay.lua", "MinimapOverlay.lua"])
        src = _rd(os.path.join(ADDON, "MinimapOverlay.lua"))
        self.assertNotIn("SetCVar", src)
        self.assertNotRegex(src, r"(?m)^\s*SlashCmdList\s*=")
        self.assertIn("issecretvalue", src)
        self.assertIn("v0.19", _rd(os.path.join(ADDON, "BotStatus.lua")))
        self.assertRegex(_rd(os.path.join(ROOT, "gui", "app.py")), r"熊德自动打怪 v8\.(6[4-9]|[7-9]\d)")
        self.assertIn("v8.64", _rd(os.path.join(ROOT, "README_中文.md")))


if __name__ == "__main__":
    unittest.main()

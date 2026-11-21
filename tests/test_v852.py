"""v8.52（t212 卡片“改成插件自动报告界面位置，不动就拉黑默认关掉”）：
插件 v0.15 把看得见的界面框位置画成色块（D 行 = 个数 + gen，E1-E6 行 = 每页 6 个框），bot 攒齐一整组后红名找怪跳过这些框；
没收到插件数据 → 手动排除区；界面隐藏（Alt+Z）→ 不排除。red_walk_static_s 默认 0（已有配置改一次）。"""
import copy
import os
import re
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.addon_reader import AddonReader, paint_test_frame  # noqa: E402
from bot.config import DEFAULT_CONFIG, _merge, _migrate  # noqa: E402
from bot.detector import Detector  # noqa: E402
from test_v828 import TestRedNames  # noqa: E402
from test_v850 import Search  # noqa: E402

try:
    import lupa
    import test_addon_lua as tal
except ImportError:
    lupa = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 2160, 1350


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def frame(ui, size=(H, W)):
    return paint_test_frame({}, size=size, eat={}, ui=ui)


RECTS = [(100, 900, 400, 200), (1700, 100, 400, 700), (900, 1150, 500, 150), (1000, 130, 160, 30),
         (1800, 20, 300, 250), (20, 600, 300, 200), (1300, 950, 250, 120), (1500, 1100, 200, 60)]


def pages(rects, gen=3, shown=True):
    n = len(rects)
    out = []
    for p in range(max(1, (n + 5) // 6)):
        rows = [(i,) + tuple(rects[i]) for i in range(p * 6, min(n, p * 6 + 6))]
        out.append({"shown": shown, "n": n, "gen": gen, "rows": rows})
    return out


class Defaults(unittest.TestCase):
    def test_defaults(self):
        n = DEFAULT_CONFIG["nameplate"]
        self.assertTrue(n["use_addon_ui"])
        self.assertEqual(n["ui_margin_px"], 8)
        self.assertEqual(n["red_walk_static_s"], 0.0)
        self.assertEqual(DEFAULT_CONFIG["addon"]["ui_stale_s"], 5.0)

    def test_migrate_red_walk_once(self):
        loaded = {"nameplate": {"red_walk_static_s": 2.0}, "meta": {"migrations": ["red_ui_v850", "red_ui_v851"]}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(cfg["nameplate"]["red_walk_static_s"], 0.0)
        self.assertIn("red_walk_v852", cfg["meta"]["migrations"])
        loaded = {"nameplate": {"red_walk_static_s": 2.0},
                  "meta": {"migrations": ["red_ui_v850", "red_ui_v851", "red_walk_v852"]}}   # 自己又打开 → 不再改
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(cfg["nameplate"]["red_walk_static_s"], 2.0)


class Reader(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.r = AddonReader(copy.deepcopy(DEFAULT_CONFIG), clock=self.clock)

    def feed(self, ui):
        self.clock.t += 0.1
        return self.r.read(frame(ui, size=(200, 500)))

    def test_one_page_decodes(self):
        st = self.feed(pages(RECTS[:3])[0])
        self.assertTrue(st.ok, st.reason)
        self.assertTrue(st.uiinfo)
        self.assertTrue(st.ui_shown)
        self.assertEqual((st.ui_n, st.ui_gen), (3, 3))
        self.assertEqual(st.ui_rows, [(i,) + RECTS[i] for i in range(3)])
        self.assertEqual(self.r.ui_rects(), RECTS[:3])

    def test_needs_all_pages(self):
        p = pages(RECTS)                                          # 8 个框 = 2 页
        self.feed(p[0])
        self.assertIsNone(self.r.ui_rects())                      # 还没收齐过 → 手动排除区
        self.feed(p[0])
        self.assertIsNone(self.r.ui_rects())
        self.feed(p[1])
        self.assertEqual(self.r.ui_rects(), RECTS)

    def test_new_gen_merges_until_complete(self):
        for pg in pages(RECTS):
            self.feed(pg)
        new = RECTS[:7] + [(600, 600, 50, 50), (700, 100, 40, 40)]
        p2 = pages(new, gen=4)
        self.feed(p2[1])                                          # 新的一组只收到第 2 页
        got = self.r.ui_rects()
        for r in RECTS + [(600, 600, 50, 50)]:
            self.assertIn(r, got)                                 # 旧的一整组 + 新组已经收到的
        self.feed(p2[0])
        self.assertEqual(self.r.ui_rects(), new)

    def test_wrong_row_gen_ignored(self):
        pg = pages(RECTS[:2])[0]
        pg["row_gen"] = 9                                         # 撕裂：行的 gen 和 D 行对不上
        st = self.feed(pg)
        self.assertEqual(st.ui_rows, [])
        self.assertIsNone(self.r.ui_rects())

    def test_hidden_ui(self):
        st = self.feed({"shown": False, "n": 0, "gen": 5, "rows": []})
        self.assertFalse(st.ui_shown)
        self.assertEqual(self.r.ui_rects(), [])                   # 界面隐藏 → 不排除
        st = self.feed(pages(RECTS, gen=6)[0])                    # 界面回来了，还没收齐 → 先手动
        self.assertIsNone(self.r.ui_rects())
        self.feed(pages(RECTS, gen=6)[1])
        self.assertEqual(self.r.ui_rects(), RECTS)

    def test_stale_and_old_addon(self):
        for pg in pages(RECTS):
            self.feed(pg)
        self.clock.t += 6
        self.assertIsNone(self.r.ui_rects())                      # 5 秒没读到 → 手动
        r2 = AddonReader(copy.deepcopy(DEFAULT_CONFIG), clock=self.clock)
        st = r2.read(paint_test_frame({}, size=(200, 500), eat={}))   # v0.14 插件：没有块 42
        self.assertTrue(st.ok)
        self.assertFalse(st.uiinfo)
        self.assertIsNone(r2.ui_rects())

    def test_old_frame_layout_still_reads(self):
        st = self.r.read(paint_test_frame({}, size=(200, 400), eat={}))
        self.assertTrue(st.ok, st.reason)
        self.assertFalse(st.uiinfo)


class RedNames(unittest.TestCase):
    """2160×1350 整屏：红名在插件界面框里 → 不找；在旧手动排除区但插件说那里没界面 → 找得到。"""

    def setUp(self):
        self.clock = Clock()
        self.det = Detector(copy.deepcopy(DEFAULT_CONFIG))
        self.det.addon.clock = self.clock

    def img(self, ui, names):
        im = frame(ui)
        for x, y in names:
            TestRedNames.text(None, im, x - 30, y - 6)
        return im

    def feed_all(self, rects, names, gen=3, shown=True):
        im = None
        for pg in pages(rects, gen, shown) if rects or not shown else [{"shown": shown, "n": 0, "gen": gen, "rows": []}]:
            self.clock.t += 0.1
            im = self.img(pg, names)
            self.det.addon.read(im)
        return im

    def centers(self, im):
        return [(int(n["x"] + n["w"] / 2), int(n["y"] + n["h"] / 2)) for n in self.det.red_names(im)]

    def test_addon_rects_excluded(self):
        names = [(300, 1000), (1210, 1108), (1000, 400)]          # 第 1 个在界面框 0 里；第 2 个在 v8.51 手动排除区
        im = self.feed_all(RECTS, names)
        self.assertEqual(self.det.ui_exclude()[0], "addon")
        got = self.centers(im)
        self.assertEqual(len(got), 2, got)
        self.assertFalse(any(abs(x - 300) < 40 for x, _ in got))

    def test_margin(self):
        im = self.feed_all([(300, 500, 200, 40)], [(400, 546)])    # 名字在框下边外面（字 540-552，中心在 8 像素边里）
        self.assertEqual(self.centers(im), [])
        self.det.cfg["nameplate"]["ui_margin_px"] = 0
        self.assertEqual(len(self.centers(im)), 1)

    def test_manual_when_no_addon_data(self):
        im = self.img(None, [(1210, 1108), (1000, 400)])
        self.det.addon.read(im)
        self.assertEqual(self.det.ui_exclude(), ("manual", None))
        self.assertEqual(len(self.centers(im)), 1)               # 手动排除区挡住 (1210,1108)

    def test_toggle_off_uses_manual(self):
        im = self.feed_all(RECTS, [(1210, 1108), (1000, 400)])
        self.det.cfg["nameplate"]["use_addon_ui"] = False
        self.assertEqual(self.det.ui_exclude()[0], "manual")
        self.assertEqual(len(self.centers(im)), 1)

    def test_hidden_ui_whole_screen(self):
        names = [(300, 1000), (1210, 1108), (1000, 400), (1900, 500), (1000, 1250)]
        im = self.feed_all([], names, shown=False)
        self.assertEqual(self.det.ui_exclude(), ("hidden", []))
        self.assertEqual(len(self.centers(im)), 5)               # 底部动作条那条也不挡了


class DataRowsNotMarkers(unittest.TestCase):
    def test_no_marker_in_data_rows(self):
        from bot.marker import find_enemy_markers
        det = Detector(copy.deepcopy(DEFAULT_CONFIG))
        for gen in range(0, 64, 7):
            rects = [((37 * i * gen) % 2000, (53 * i + gen) % 1300, 100 + i, 40 + gen) for i in range(6)]
            im = frame(pages(rects, gen=gen)[0])
            self.assertIsNone(det.find_target_marker(im))
            self.assertEqual(find_enemy_markers(im, exclude=det._status_exclude()), [])
        x, y, w, h = det._status_exclude()
        self.assertGreaterEqual(y + h, 130)
        self.assertGreaterEqual(x + w, 430)


class Log(Search):
    def test_src_change_logged_once(self):
        for src in ("manual", "addon", "addon", "hidden"):
            o = self.o()
            o.ui_src, o.ui_n = src, 12
            self.step(o)
        self.assertEqual(sum("🖼️" in m for m in self.logs), 3)
        self.assertTrue(any("插件 v0.15 报告的界面框（12 个）" in m for m in self.logs))
        self.assertTrue(any("Alt+Z" in m for m in self.logs))


LUA_UI = r"""
local function region(vis) return { IsVisible = function() return vis end, GetAlpha = function() return 1 end,
  GetWidth = function() return 10 end, GetHeight = function() return 10 end } end
function mkframe(name, l, b, w, h, opts)
  opts = opts or {}
  local f = {}
  f.GetName = function() return name end
  f.IsVisible = function() return opts.hidden ~= true and UI_SHOWN end
  f.GetEffectiveAlpha = function() return opts.alpha or 1 end
  f.GetEffectiveScale = function() return 768 / 1080 end
  f.GetRect = function() return l, b, w, h end
  f.GetRegions = function() if opts.empty then return end return region(true) end
  f.GetChildren = function() return end
  return f
end
UI_SHOWN = true
local kids = {
  mkframe("PlayerFrame", 20, 1080 - 900 - 100, 300, 100),
  mkframe("MinimapCluster", 1700, 1080 - 10 - 200, 210, 200),
  mkframe("Hidden", 500, 500, 100, 100, { hidden = true }),
  mkframe("Invisible", 600, 500, 100, 100, { alpha = 0 }),
  mkframe("EmptyHolder", 700, 500, 100, 100, { empty = true }),
  mkframe("Fullscreen", 0, 0, 1920, 1080),
  mkframe("NamePlate1", 800, 500, 100, 20),
}
for i = 1, 10 do kids[#kids + 1] = mkframe("Bar" .. i, 100 * i, 0, 90, 40) end
UIParent = { IsVisible = function() return UI_SHOWN end, GetChildren = function() return table.unpack(kids) end }
UIErrorsFrame = mkframe("UIErrorsFrame", 704, 1080 - 122 - 60, 512, 60)
UIErrorsFrame.GetCenter = function() return 960, 1080 - 152 end
UIErrorsFrame.GetTop = function() return 1080 - 122 end
UIErrorsFrame.GetWidth = function() return 512 end
UIErrorsFrame.GetHeight = function() return 60 end
UIErrorsFrame.GetFont = function() return "f", 16, "" end
"""


@unittest.skipIf(lupa is None, "没有 lupa")
class Lua(unittest.TestCase):
    """真的跑 BotStatus.lua v0.15：翻几页，Python 收齐界面框。"""

    def run_lua(self, shown=True, error=False, updates=6):
        L = lupa.LuaRuntime()
        L.execute(tal.STUBS)
        L.execute(LUA_UI)
        g = L.globals()
        g.UI_SHOWN = shown
        g.POSX, g.POSY, g.FACING, g.MAPID, g.HASTARGET, g.TDEAD, g.COMBAT = 0.5, 0.5, 1.0, 1, False, False, False
        with open(tal.LUA, encoding="utf-8") as f:
            L.execute(f.read())
        fr = g.BotStatusFrame
        fr.script_OnEvent(fr, "PLAYER_ENTERING_WORLD")
        if error:
            fr.script_OnEvent(fr, "UI_ERROR_MESSAGE", 50, "能量值不足")
        clock = Clock()
        r = AddonReader(copy.deepcopy(DEFAULT_CONFIG), clock=clock)
        st = None
        for _ in range(updates):
            g.NOW = g.NOW + 0.06
            fr.script_OnUpdate(fr, 1.0)
            clock.t += 0.1
            st = r.read(self.paint(g))
        return st, r

    @staticmethod
    def paint(g):
        img = np.zeros((200, 500, 3), np.uint8)
        for t in g.TEXTURES.values():
            if not all(isinstance(v, (int, float)) for v in (t.px, t.py, t.w, t.h)) or t.rgb is None:
                continue
            x, y = int(t.px), int(-t.py)
            r, gg, b = (int(round(t.rgb[i] * 255)) for i in (1, 2, 3))
            img[y:y + int(t.h), x:x + int(t.w)] = (b, gg, r)
        return img

    def test_rects(self):
        st, r = self.run_lua()
        self.assertTrue(st.ok, st.reason)
        self.assertTrue(st.uiinfo)
        self.assertTrue(st.ui_shown)
        self.assertEqual(st.ui_n, 12)                             # 头像 + 小地图 + 10 条动作条；隐藏/透明/空/全屏/姓名板不算
        rects = r.ui_rects()
        self.assertIsNotNone(rects)
        self.assertIn((20, 900, 300, 100), rects)                 # GetRect × 有效缩放 × 1080/768 = 物理像素，y 从上往下
        self.assertIn((1700, 10, 210, 200), rects)
        self.assertIn((100, 1040, 90, 40), rects)

    def test_error_text_rect(self):
        st, r = self.run_lua(error=True)
        self.assertEqual(st.ui_n, 13)
        self.assertTrue(any(x == 704 and y == 122 and w == 512 for x, y, w, h in r.ui_rects()))   # 量不出字宽 → 整条宽

    def test_hidden(self):
        st, r = self.run_lua(shown=False)
        self.assertFalse(st.ui_shown)
        self.assertEqual(st.ui_n, 0)
        self.assertEqual(r.ui_rects(), [])


class Strings(unittest.TestCase):
    def test_files(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertIn("熊德自动打怪 v8.52", app)
        self.assertIn('"use_addon_ui"', app)
        self.assertIn('"ui_margin_px"', app)
        rd = _rd(os.path.join(ROOT, "README_中文.md"))
        self.assertTrue(re.search(r"v8\.52.*界面", rd))
        self.assertIn("v0.15", rd)
        lua = _rd(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.lua"))
        self.assertIn("local NUM_BLOCKS = 43", lua)
        self.assertIn("BotStatus v0.15", lua)
        toc = _rd(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.toc"))
        self.assertIn("## Version: 0.15.0", toc)
        self.assertIn("v8.52", _rd(os.path.join(ROOT, "bot", "runner.py")))


if __name__ == "__main__":
    unittest.main()

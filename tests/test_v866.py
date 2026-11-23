"""v8.66（t249u，Simon 选了 1/2/3 + 去掉插件改姓名板距离）：远处“红名”误判。

1. 固定界面遮罩：转身前后总在屏幕同一格的“红名”= 界面，永久不看（存 red_ui_mask.json）；转身/走路判定的永久界面位置也存。
2. 更严格的名字判断（走之前就筛）：纯红字、一行字的高宽、有笔画。实拍 11-22 18:34（千针石林）：红土/红岩/报错字都筛掉，真名字都留下。
3. 范围外的红名不走：朝那个方向离范围边（所有同地图中心的半径都算）不到 far_min_room_s 秒路 → 不走。
4. 插件 v0.20：不再改 nameplateMaxDistance（只读）。
"""
import copy
import json
import math
import os
import re
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG, _merge, _migrate  # noqa: E402
from bot.marker import find_red_names  # noqa: E402
from bot.redmask import RedUIMask  # noqa: E402
from test_v840 import TabBase, tobs  # noqa: E402

try:
    import cv2
except Exception:  # noqa: BLE001
    cv2 = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tests", "data")
V851 = [[0.80, 0.0, 0.20, 0.25], [0.86, 0.0, 0.14, 1.0], [0.74, 0.68, 0.26, 0.32], [0.0, 0.70, 0.20, 0.30],
        [0.79, 0.10, 0.21, 0.75], [0.0, 0.62, 0.36, 0.23], [0.66, 0.64, 0.14, 0.14],
        [0.51, 0.79, 0.10, 0.06], [0.47, 0.102, 0.06, 0.016]]
STRICT = {"min_purity": 90, "min_mean_r": 150, "min_strokes": 1.0}


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def mig(loaded):
    return _migrate(_merge(DEFAULT_CONFIG, copy.deepcopy(loaded)), loaded)


def near(names, x, y, tol=15):
    return [n for n in names if abs(n["x"] - x) < tol and abs(n["y"] - y) < tol]


# ---------------------------------------------------------------- 配置
class Config(unittest.TestCase):
    def test_defaults(self):
        n = DEFAULT_CONFIG["nameplate"]
        self.assertTrue(n["red_learn_mask"] and n["red_strict"] and n["red_in_area_only"])
        self.assertEqual((n["red_min_purity"], n["red_min_mean_r"], n["red_min_strokes"]), (90, 150, 1.0))
        self.assertEqual((n["red_mask_turn_deg"], n["red_mask_frac"], n["red_mask_min_hits"]), (15.0, 0.4, 5))
        self.assertEqual(n["far_min_room_s"], 3.0)
        self.assertEqual(n["red_ui_exclude"][:9], V851)
        self.assertEqual(n["red_ui_exclude"][9], [0.60, 0.0, 0.20, 0.06])

    def test_migration_adds_buff_row_once(self):
        cfg = mig({"nameplate": {"red_ui_exclude": copy.deepcopy(V851)}, "meta": {"migrations": []}})
        self.assertEqual(len(cfg["nameplate"]["red_ui_exclude"]), 10)
        self.assertIn("red_ui_v866", cfg["meta"]["migrations"])
        cfg["nameplate"]["red_ui_exclude"] = copy.deepcopy(V851)          # 自己删回去 → 不再加
        self.assertEqual(len(mig(cfg)["nameplate"]["red_ui_exclude"]), 9)

    def test_migration_keeps_custom(self):
        custom = [[0.1, 0.1, 0.1, 0.1]]
        cfg = mig({"nameplate": {"red_ui_exclude": custom}, "meta": {"migrations": ["red_ui_v850", "red_ui_v851"]}})
        self.assertEqual(cfg["nameplate"]["red_ui_exclude"], custom)

    def test_simon_like_config_keeps_values(self):
        cfg = mig({"nameplate": {"red_ui_exclude": copy.deepcopy(V851), "far_walk_max_s": 10, "far_lost_s": 1.5},
                   "meta": {"migrations": ["red_ui_v850", "red_ui_v851", "scan_v865"]}})
        self.assertEqual((cfg["nameplate"]["far_walk_max_s"], cfg["nameplate"]["far_lost_s"]), (10, 1.5))
        self.assertTrue(cfg["nameplate"]["red_strict"])


# ---------------------------------------------------------------- 2. 严格的名字判断（合成图）
class StrictSynthetic(unittest.TestCase):
    def frame(self):
        img = np.full((1350, 2160, 3), (90, 120, 140), np.uint8)           # 土黄地面（BGR，不算红）
        return img

    def text(self, img, x, y, color=(30, 30, 230), n=4):
        """画 n 个“字”：每个字 18x18，竖笔画 + 横笔画（像汉字）。"""
        for i in range(n):
            x0 = x + i * 22
            for dx in (2, 8, 14):
                img[y:y + 18, x0 + dx:x0 + dx + 2] = color
            for dy in (3, 9, 15):
                img[y + dy:y + dy + 2, x0:x0 + 18] = color

    def test_text_kept_bar_and_dull_red_dropped(self):
        img = self.frame()
        self.text(img, 600, 400)                                         # 纯红字 → 留
        img[500:512, 900:1100] = (30, 30, 230)                           # 红血条（实心）→ 不要
        self.text(img, 1300, 420, color=(60, 70, 130))                   # 暗红（红土那种）→ 不要
        img[600:640, 400:520] = (40, 40, 220)                            # 大红块（太高）→ 不要
        names = find_red_names(img, strict=STRICT)
        self.assertEqual(len(names), 1, names)
        self.assertEqual(len(near(names, 600, 400, 20)), 1)
        self.assertGreaterEqual(names[0]["pur"], 150)
        self.assertGreaterEqual(names[0]["strokes"], 1.0)
        loose = find_red_names(img)                                       # 旧做法：暗红字也算
        self.assertGreater(len(loose), 1)

    def test_thin_line_dropped(self):
        img = self.frame()
        img[700:706, 500:700] = (30, 30, 230)                            # 高 6 的细横条（选中血条那种）
        for k in range(0, 200, 6):
            img[700:706, 500 + k:502 + k] = (90, 120, 140)
        self.assertEqual(find_red_names(img, strict=STRICT), [])

    def test_scale_with_frame_height(self):
        img = np.full((675, 1080, 3), (90, 120, 140), np.uint8)           # 一半大小的画面：字也一半大
        for i in range(4):
            x0 = 300 + i * 11
            for dx in (1, 4, 7):
                img[200:209, x0 + dx:x0 + dx + 1] = (30, 30, 230)
            for dy in (1, 4, 7):
                img[200 + dy:201 + dy, x0:x0 + 9] = (30, 30, 230)
        self.assertEqual(len(find_red_names(img, min_px=10, strict=STRICT)), 1)


# ---------------------------------------------------------------- 2. 实拍（11-22 18:34，2160×1350，千针石林）
@unittest.skipIf(cv2 is None, "需要 cv2")
class StrictReal(unittest.TestCase):
    def load(self, name):
        img = cv2.imread(os.path.join(DATA, name))
        self.assertIsNotNone(img)
        return img

    def det(self, strict=True):
        from bot.detector import Detector
        cfg = mig({"nameplate": {"red_ui_exclude": copy.deepcopy(V851)}, "meta": {"migrations": []}})
        cfg["nameplate"]["red_strict"] = strict
        return Detector(cfg)

    def test_real_names_kept_terrain_dropped(self):
        img = self.load("far_names_2160.jpg")                            # 3 个“盐石蜥蜴”红名 + 红土
        names = find_red_names(img, strict=STRICT)
        for x, y in ((319, 413), (1588, 411), (538, 428)):
            self.assertEqual(len(near(names, x, y)), 1, (x, y, names))
        d = self.det()
        got = d.red_names(img, d.marker_plates(img))
        self.assertEqual(len(got), 3, got)                               # buff 图标被新排除区挡掉

    def test_red_terrain_all_dropped(self):
        img = self.load("red_terrain_2160.jpg")                          # 红土/红岩：旧做法 15 个“红名”
        old = self.det(False)
        self.assertGreaterEqual(len(old.red_names(img, old.marker_plates(img))), 10)
        new = self.det()
        self.assertEqual(new.red_names(img, new.marker_plates(img)), [])

    def test_error_text_dropped_name_kept(self):
        img = self.load("far_name_errtext_2160.jpg")                     # 顶部红字报错“你没有被允许搜索这个尸体” + 远处红名
        d = self.det()
        got = d.red_names(img, d.marker_plates(img))
        self.assertEqual(len(got), 1, got)
        self.assertEqual(len(near(got, 994, 369)), 1)
        loose = find_red_names(img)
        self.assertTrue(any(100 <= n["y"] <= 200 for n in loose))       # 旧做法会把报错字当红名

    def test_toggle_off_is_old_behaviour(self):
        img = self.load("red_terrain_2160.jpg")
        d = self.det(False)
        self.assertGreater(len(d.red_names(img, d.marker_plates(img))), 0)


# ---------------------------------------------------------------- 1. 固定界面遮罩
class Mask(unittest.TestCase):
    UI = {"x": 1652, "y": 20, "w": 48, "h": 24}

    def feed(self, m, n=12, step=20.0, moving=True):
        fac = 0.0
        new = []
        for i in range(n):
            names = [dict(self.UI)]
            if moving:
                names.append({"x": (300 + i * 380) % 2000, "y": 400, "w": 90, "h": 20})   # 真怪：转身就挪
            new += m.observe(names, math.radians(fac), (2160, 1350), turn_deg=15)
            fac += step
        return new

    def test_static_learned_moving_not(self):
        m = RedUIMask()
        new = self.feed(m)
        self.assertTrue(new)
        self.assertTrue(m.is_ui(1676, 32))
        for i in range(12):
            self.assertFalse(m.is_ui((300 + i * 380) % 2000 + 45, 410))

    def test_no_turn_no_learning(self):
        m = RedUIMask()
        self.assertEqual(self.feed(m, n=30, step=0.0), [])               # 一直不转（直走）：不取样
        self.assertFalse(m.is_ui(1676, 32))
        m2 = RedUIMask()
        self.assertEqual(self.feed(m2, n=4, step=3.0), [])               # 累计转不到 15°：还没取样
        self.assertFalse(m.is_ui(1676, 32))

    def test_needs_min_hits(self):
        m = RedUIMask()
        self.feed(m, n=4)                                                 # 取样 3 次 < 5
        self.assertFalse(m.is_ui(1676, 32))

    def test_save_load_and_size_change(self):
        m = RedUIMask()
        self.feed(m)
        m.add_point(500, 600, 40)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "red_ui_mask.json")
            m.path = p
            self.assertTrue(m.save())
            self.assertFalse(m.save())                                    # 没变就不写
            m2 = RedUIMask()
            msg = m2.attach(p)
            self.assertIn("格", msg)
            self.assertTrue(m2.is_ui(1676, 32) and m2.is_ui(510, 610))
            self.assertFalse(m2.is_ui(1676, 32, (1920, 1080)))           # 画面大小不同不算
            m2.observe([], 0.0, (1920, 1080))                             # 换了画面大小 → 作废重学
            self.assertFalse(m2.is_ui(1676, 32))
            with open(p, "w", encoding="utf-8") as f:
                f.write("{bad json")
            self.assertIn("读不了", RedUIMask().attach(p))
        self.assertIn("从头学", RedUIMask().attach(os.path.join(tempfile.gettempdir(), "nope_v866.json")))

    def test_clear(self):
        m = RedUIMask()
        self.feed(m)
        m.clear()
        self.assertFalse(m.is_ui(1676, 32))


class GrinderBase(TabBase):
    def setUp(self):
        super().setUp()
        self.cfg["area"].update(enabled=True, radius=1.0, auto_switch_center=True)
        self.g.home = (50.0, 50.0, 0)
        self.g.centers = []
        self.cfg["nameplate"].update(far_names=True, red_in_area_only=True, far_min_room_s=3.0, red_learn_mask=True)

    def o(self, names, pos=(50.0, 50.0), facing=0.0, combat=False):
        o = tobs(pos=pos, facing=facing, combat=combat)
        o.red_names = names
        self.g.last_obs = o
        return o


# ---------------------------------------------------------------- 1. 遮罩接进状态机
class MaskInGrinder(GrinderBase):
    def test_static_red_learned_while_turning_then_ignored(self):
        ui = {"x": 1500, "y": 30, "w": 48, "h": 24}
        for i in range(10):
            self.g._red_mask_tick(self.o([ui], facing=math.radians(20 * i)), self.clock.t)
            self.clock.t += 0.5
        self.assertTrue(any("界面遮罩" in m for m in self.logs))
        o = self.o([ui])
        self.assertEqual(self.g._far_targets(o, self.clock.t), [])
        self.cfg["nameplate"]["red_learn_mask"] = False                  # 关掉 → 不用遮罩
        self.assertEqual(len(self.g._far_targets(o, self.clock.t)), 1)

    def test_no_learning_in_combat(self):
        ui = {"x": 1500, "y": 30, "w": 48, "h": 24}
        for i in range(10):
            self.g._red_mask_tick(self.o([ui], facing=math.radians(20 * i), combat=True), self.clock.t)
        self.assertFalse(self.g.redmask.learned)

    def test_permanent_ui_point_goes_into_mask(self):
        self.o([])
        self.cfg["nameplate"]["red_turn_hits"] = 1
        self.g._mark_ui(700, 300, self.clock.t, "turn", "人转了 14°")
        self.assertTrue(self.g.redmask.is_ui(705, 305))
        self.assertTrue(any("以后也不看（记进界面遮罩）" in m for m in self.logs))
        self.assertTrue(self.g.redmask.dirty)


# ---------------------------------------------------------------- 3. 范围外的红名不走
class Room(GrinderBase):
    NAME = {"x": 915, "y": 400, "w": 90, "h": 20}                        # 1920 宽画面的正中间 = 正前方

    def test_center_kept(self):
        o = self.o([self.NAME], pos=(50.0, 50.0), facing=0.0)            # 在中心朝北：离边 1.0 / 0.17 ≈ 5.9 秒
        self.assertAlmostEqual(self.g._far_room_s(o, 960, 410), 1.0 / 0.17, delta=0.3)
        self.assertEqual(len(self.g._far_targets(o, self.clock.t)), 1)

    def test_near_edge_facing_out_skipped(self):
        o = self.o([self.NAME], pos=(50.0, 49.2), facing=0.0)            # 北边 0.8，朝北：只剩 0.2 → 1.2 秒
        self.assertLess(self.g._far_room_s(o, 960, 410), 3.0)
        self.assertEqual(self.g._far_targets(o, self.clock.t), [])
        self.assertTrue(any("在范围外的方向" in m and "不朝它走" in m for m in self.logs))
        self.assertFalse(self.g._far_begin(o, self.clock.t))

    def test_near_edge_facing_in_kept(self):
        o = self.o([self.NAME], pos=(50.0, 49.2), facing=math.pi)        # 朝南（往中心）
        self.assertEqual(len(self.g._far_targets(o, self.clock.t)), 1)

    def test_side_of_screen_uses_bearing(self):
        # 北边 0.8，朝东（3π/2）；名字在屏幕最左边（偏左约 45°）→ 方向东北，离边不远
        o = self.o([{"x": 10, "y": 400, "w": 90, "h": 20}], pos=(50.0, 49.2), facing=3 * math.pi / 2)
        r_left = self.g._far_room_s(o, 55, 410)
        r_mid = self.g._far_room_s(o, 960, 410)
        self.assertLess(r_left, r_mid)

    def test_other_center_extends_room(self):
        self.g.centers = [(50.0, 50.0, 0), (50.0, 48.4, 0)]              # 北边紧挨着另一个中心 → 自动切换，不算出范围
        o = self.o([self.NAME], pos=(50.0, 49.2), facing=0.0)
        self.assertGreater(self.g._far_room_s(o, 960, 410), 3.0)
        self.cfg["area"]["auto_switch_center"] = False
        self.assertLess(self.g._far_room_s(o, 960, 410), 3.0)

    def test_toggle_off_or_no_area(self):
        o = self.o([self.NAME], pos=(50.0, 49.2), facing=0.0)
        self.cfg["nameplate"]["red_in_area_only"] = False
        self.assertIsNone(self.g._far_room_s(o, 960, 410))
        self.assertEqual(len(self.g._far_targets(o, self.clock.t)), 1)
        self.cfg["nameplate"]["red_in_area_only"] = True
        self.cfg["area"]["enabled"] = False
        self.assertIsNone(self.g._far_room_s(o, 960, 410))
        self.cfg["area"]["enabled"] = True
        o.facing = None
        self.assertIsNone(self.g._far_room_s(o, 960, 410))

    def test_far_plate_markers_not_filtered(self):
        # 只筛红名；屏幕最上面一条的插件标记（远处姓名板）照旧
        o = self.o([], pos=(50.0, 49.2), facing=0.0)
        self.assertEqual(self.g._far_targets(o, self.clock.t), [])

    def test_walk_speed_learned(self):
        self.g.walking = True
        pos = [50.0, 50.0]
        for i in range(12):
            o = self.o([], pos=tuple(pos))
            self.g._speed_tick(o, self.clock.t)
            self.clock.t += 1.0
            pos[1] -= 0.25                                               # 0.25 / 秒
        self.assertAlmostEqual(self.g._walk_speed(), 0.25, delta=0.03)
        self.g.walking = False
        self.g._speed_tick(self.o([]), self.clock.t)
        self.assertIsNone(self.g._spd)


# ---------------------------------------------------------------- 4. 插件 v0.20 / 界面 / README / runner
class Strings(unittest.TestCase):
    def test_addon_no_cvar_writes(self):
        lua = _rd(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.lua"))
        self.assertNotIn("SetCVar", lua)
        self.assertNotIn("ApplyNameplateDistance", lua)
        self.assertIn("ReadNameplateDistance", lua)
        self.assertIn('GetCVar("nameplateMaxDistance")', lua)
        for f in ("MapOverlay.lua", "MinimapOverlay.lua"):
            self.assertNotIn("SetCVar", _rd(os.path.join(ROOT, "addon", "BotStatus", f)))
        toc = _rd(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.toc"))
        self.assertRegex(toc, r"## Version: 0\.(2\d|[3-9]\d)\.0")
        self.assertIn("v0.20", lua)

    def test_gui_readme_runner(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertRegex(app, r"熊德自动打怪 v8\.(6[6-9]|[7-9]\d)")
        for k in ("red_learn_mask", "red_strict", "red_in_area_only", "far_min_room_s", "clear_red_mask"):
            self.assertIn(k, app)
        rd = _rd(os.path.join(ROOT, "README_中文.md"))
        self.assertIn("v8.66", rd)
        run = _rd(os.path.join(ROOT, "bot", "runner.py"))
        self.assertIn("red_ui_mask.json", run)
        self.assertIn("g.redmask.save()", run)
        self.assertIn("red_ui_mask.json", _rd(os.path.join(ROOT, ".gitignore")) if os.path.exists(
            os.path.join(ROOT, ".gitignore")) else "red_ui_mask.json")


if __name__ == "__main__":
    unittest.main()

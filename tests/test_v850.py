"""v8.50（t206u/t207u）找怪顺序：看到能打的标记 → 马上右键点；没有 → 朝红名走，一出标记马上点；都没有才按 Tab。
红名走路时不按 Tab；Simon 的界面（左下头像框、右边任务追踪、目标框）不当红名；朝红名转了 2 次它不动 → 是界面，拉黑。
Tab 选中远处的怪：看得到它的姓名板/标记 → 直接右键点，不再按 W 走过去。"""
import copy
import os
import re
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG, _merge, _migrate  # noqa: E402
from bot.detector import Detector  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import plate  # noqa: E402
from test_v828 import TestRedNames  # noqa: E402
from test_v840 import TabBase, mplate, tobs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD_UI = [[0.80, 0.0, 0.20, 0.25], [0.86, 0.0, 0.14, 1.0], [0.74, 0.68, 0.26, 0.32], [0.0, 0.70, 0.20, 0.30]]


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class Defaults(unittest.TestCase):
    def test_defaults(self):
        n = DEFAULT_CONFIG["nameplate"]
        self.assertTrue(n["plates_first"])
        self.assertEqual((n["far_click_frames"], n["red_static_turns"]), (1, 2))
        self.assertFalse(DEFAULT_CONFIG["tab_search"]["far_walk_tab"])
        self.assertTrue(DEFAULT_CONFIG["tab_search"]["while_moving"])        # 巡逻/回中心照旧
        for r in ([0.79, 0.10, 0.21, 0.75], [0.0, 0.62, 0.36, 0.23], [0.66, 0.64, 0.14, 0.14]):
            self.assertIn(r, n["red_ui_exclude"])

    def test_migrate_old_default_exclude(self):
        loaded = {"nameplate": {"red_ui_exclude": copy.deepcopy(OLD_UI), "patrol_frames": 2}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(cfg["nameplate"]["red_ui_exclude"], DEFAULT_CONFIG["nameplate"]["red_ui_exclude"])
        self.assertEqual(cfg["nameplate"]["patrol_frames"], 2)
        self.assertIn("red_ui_v850", cfg["meta"]["migrations"])
        mine = [[0.1, 0.1, 0.1, 0.1]]                                          # 自己改过的 → 不动
        loaded = {"nameplate": {"red_ui_exclude": mine}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(cfg["nameplate"]["red_ui_exclude"], mine)


class SimonUI(unittest.TestCase):
    """Simon 现在的游戏窗口 2160×1350（截图 960×600 × 2.25）。"""
    W, H = 2160, 1350

    def found(self, x, y):
        img = np.full((self.H, self.W, 3), 60, np.uint8)
        TestRedNames.text(None, img, x, y)
        return Detector(copy.deepcopy(DEFAULT_CONFIG)).red_names(img)

    def test_ui_spots_excluded(self):
        for x, y, why in ((508, 996, "左下头像框红图标"), (559, 1034, "头像框"), (1808, 408, "任务追踪"),
                          (1769, 763, "任务追踪"), (1560, 958, "目标框"), (1692, 922, "目标框")):
            with self.subTest(why=why):
                self.assertEqual(self.found(x - 20, y - 6), [], why)

    def test_real_far_names_kept(self):
        for x, y in ((243, 166), (1300, 200), (900, 300), (1500, 700)):
            with self.subTest(xy=(x, y)):
                self.assertEqual(len(self.found(x - 20, y - 6)), 1)


class Search(TabBase):
    def setUp(self):
        super().setUp()
        n = self.cfg["nameplate"]
        n.update(plates_first=True, far_click_frames=1, red_static_turns=2, far_names=True)
        self.cfg["tab_search"]["far_walk_tab"] = False

    def o(self, names=(), moving=False, **kw):
        x = tobs(**kw)
        x.red_names = list(names)
        x.moving = moving
        return x

    def test_plate_visible_click_no_tab(self):
        self.step(self.o(plates=[mplate(900, 600)]))
        self.assertEqual(self.tab_count(), 0)
        self.assertTrue(any(c[1] == "right" for c in self.clicks()))
        self.assertTrue(any("右键点插件标记选怪" in m for m in self.logs))

    def test_red_name_walk_before_tab(self):
        name = {"x": 1300, "y": 150, "w": 60, "h": 14, "px": 200}
        self.step(self.o(names=[name]))
        self.assertIsNotNone(self.g.far)
        self.assertTrue(self.g.walking)
        self.assertEqual(self.tab_count(), 0)
        for i in range(8):                                        # 走 2.4 秒：不按 Tab
            self.step(self.o(names=[dict(name, x=1300 - 40 * i)], pos=(50.0 + 0.05 * (i + 1), 50.0)))
        self.assertEqual(self.tab_count(), 0)
        self.assertIsNotNone(self.g.far)

    def test_tab_only_when_nothing_on_screen(self):
        self.step(self.o())
        self.assertEqual(self.tab_count(), 1)

    def test_plate_appears_click_immediately(self):
        name = {"x": 1000, "y": 150, "w": 60, "h": 14, "px": 200}
        self.step(self.o(names=[name]))
        self.step(self.o(names=[name], pos=(50.05, 50.0)))
        self.step(self.o(pos=(50.1, 50.0), plates=[mplate(1000, 500)]))     # 出标记第 1 帧 → 停
        self.assertIsNone(self.g.far)
        self.assertFalse(self.g.walking)
        self.assertTrue(any("马上停下右键点" in m for m in self.logs))
        self.step(self.o(pos=(50.1, 50.0), plates=[mplate(1000, 500)]), dt=0.4)
        self.step(self.o(pos=(50.1, 50.0), plates=[mplate(1000, 500)]), dt=0.4)
        self.assertTrue(any(c[1] == "right" for c in self.clicks()))
        self.assertEqual(self.tab_count(), 0)

    def test_static_red_name_is_ui(self):
        name = {"x": 400, "y": 700, "w": 40, "h": 12, "px": 120}      # 偏左很多，每次都要转
        self.step(self.o(names=[name]))
        self.assertIsNotNone(self.g.far)
        for i in range(6):
            self.step(self.o(names=[name], pos=(50.0 + 0.05 * (i + 1), 50.0)), dt=0.6)
            if self.g.far is None:
                break
        self.assertIsNone(self.g.far)
        self.assertTrue(any("一动不动" in m and "界面" in m for m in self.logs))
        self.assertEqual(len(self.g.red_ui_bad), 1)
        self.assertEqual(self.g._far_targets(self.o(names=[name]), self.clock.t), [])
        n_turns = sum(1 for k in self.keys() if k in ("left", "right"))
        self.step(self.o(names=[name], pos=(50.4, 50.0)))             # 不再朝它走
        self.assertIsNone(self.g.far)
        self.assertLessEqual(sum(1 for k in self.keys() if k in ("left", "right")), n_turns + 1)

    def test_moving_red_name_not_ui(self):
        self.step(self.o(names=[{"x": 400, "y": 700, "w": 40, "h": 12, "px": 120}]))
        for i in range(5):
            nm = {"x": 400 + 60 * ((i + 1) % 2), "y": 700, "w": 40, "h": 12, "px": 120}   # 转了身位置在变
            self.step(self.o(names=[nm], pos=(50.0 + 0.05 * (i + 1), 50.0)), dt=0.6)
        self.assertEqual(self.g.red_ui_bad, [])

    def test_tab_target_with_plate_right_click_not_walk(self):
        self.cfg["tab_search"]["enabled"] = True
        self.step(self.o())                                        # 屏幕上什么都没有 → Tab
        sel = plate(1100, 300, kind="selected")
        self.step(self.o(thp=100, diff=1, rng="far", plates=[sel]))   # 选中合格的怪，它的姓名板在屏幕上
        self.step(self.o(thp=100, diff=1, rng="far", plates=[sel]))
        self.assertIsNone(self.g.wf)
        self.assertEqual(self.g.state, State.APPROACHING)
        self.assertTrue(any(c[1] == "right" for c in self.clicks()))
        self.assertFalse(any("先朝它走过去" in m for m in self.logs))

    def test_tab_target_offscreen_walk_then_click_when_plate_shows(self):
        self.cfg["nameplate"]["skip_top_pct"] = 0
        self.g.turn_toward_enabled = False
        self.step(self.o())
        self.step(self.o(thp=100, diff=1, rng="far"))              # 选中了，但看不到它的姓名板
        self.step(self.o(thp=100, diff=1, rng="far"))
        if self.g.wf is None:
            self.skipTest("这个场景下先转身找它（_turn_toward），不走 W")
        self.assertTrue(self.g.walking)
        n0 = len(self.clicks())
        sel = plate(1000, 400, kind="selected")
        self.clock.t += 3.5
        self.step(self.o(thp=100, diff=1, rng="far", plates=[sel], pos=(50.2, 50.0)))
        self.assertIsNone(self.g.wf)
        self.assertGreater(len(self.clicks()), n0)
        self.assertTrue(any("马上右键点它" in m for m in self.logs))


class Strings(unittest.TestCase):
    def test_files(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertIn("熊德自动打怪 v8.50", app)
        self.assertIn('"plates_first"', app)
        rd = _rd(os.path.join(ROOT, "README_中文.md"))
        self.assertIn("v8.50", rd)
        self.assertTrue(re.search(r"v8\.50.*红名", rd))


if __name__ == "__main__":
    unittest.main()

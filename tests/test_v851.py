"""v8.51（t211u “做第 3 步”）假红名：界面排除区加技能栏上方 + 顶部红字报错两块（v8.50 默认的配置自动加上）；
朝红名直走时人在走、“红名”在屏幕上不动 → 是界面（第 1 次暂时不看，同一位置 2 次 → 这次运行都不看）。"""
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
from test_v828 import TestRedNames  # noqa: E402
from test_v850 import Search  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V850 = [[0.80, 0.0, 0.20, 0.25], [0.86, 0.0, 0.14, 1.0], [0.74, 0.68, 0.26, 0.32], [0.0, 0.70, 0.20, 0.30],
        [0.79, 0.10, 0.21, 0.75], [0.0, 0.62, 0.36, 0.23], [0.66, 0.64, 0.14, 0.14]]
NEW = ([0.51, 0.79, 0.10, 0.06], [0.47, 0.102, 0.06, 0.016])


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class Defaults(unittest.TestCase):
    def test_defaults(self):
        n = DEFAULT_CONFIG["nameplate"]
        self.assertEqual(n["red_ui_exclude"], V850 + [list(r) for r in NEW])
        self.assertEqual((n["red_walk_static_s"], n["red_walk_static_px"], n["red_walk_min_move"],
                          n["red_walk_bad_s"], n["red_walk_hits"]), (0.0, 5, 0.15, 120.0, 2))   # v8.52 起默认关

    def test_migrate_v850_default(self):
        loaded = {"nameplate": {"red_ui_exclude": copy.deepcopy(V850), "patrol_frames": 2},
                  "meta": {"migrations": ["red_ui_v850"]}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(cfg["nameplate"]["red_ui_exclude"], DEFAULT_CONFIG["nameplate"]["red_ui_exclude"])
        self.assertEqual(cfg["nameplate"]["patrol_frames"], 2)
        self.assertIn("red_ui_v851", cfg["meta"]["migrations"])

    def test_migrate_older_default_goes_straight_to_new(self):
        old = V850[:4]
        loaded = {"nameplate": {"red_ui_exclude": copy.deepcopy(old)}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(len(cfg["nameplate"]["red_ui_exclude"]), 9)

    def test_custom_untouched_and_once(self):
        mine = V850 + [[0.1, 0.1, 0.1, 0.1]]
        loaded = {"nameplate": {"red_ui_exclude": copy.deepcopy(mine)}, "meta": {"migrations": ["red_ui_v850"]}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(cfg["nameplate"]["red_ui_exclude"], mine)
        loaded = {"nameplate": {"red_ui_exclude": copy.deepcopy(V850)},
                  "meta": {"migrations": ["red_ui_v850", "red_ui_v851"]}}             # 已迁移过、自己删回去 → 不再加
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(cfg["nameplate"]["red_ui_exclude"], V850)


class SimonUI(unittest.TestCase):
    W, H = 2160, 1350

    def found(self, cx, cy, n=5):
        img = np.full((self.H, self.W, 3), 60, np.uint8)
        TestRedNames.text(None, img, int(cx - (n * 14 - 5) / 2), int(cy - 6), n=n)
        return Detector(copy.deepcopy(DEFAULT_CONFIG)).red_names(img)

    def test_ui_spots_excluded(self):
        self.assertEqual(self.found(1210, 1108), [], "技能栏上方")
        self.assertEqual(self.found(1080, 148), [], "顶部红字报错（5 个字）")
        self.assertEqual(self.found(1080, 148, n=7), [], "顶部红字报错（7 个字，两头露出的碎片不成行）")

    def test_real_far_names_kept(self):
        for x, y in ((1086, 120), (1080, 185), (904, 148), (1250, 148), (1080, 1000), (900, 1108), (1400, 1108)):
            with self.subTest(xy=(x, y)):
                self.assertEqual(len(self.found(x, y)), 1)


class WalkStatic(Search):
    NAME = {"x": 1000, "y": 894, "w": 60, "h": 12, "px": 200}      # 中心 (1030,900)，1920 宽上偏 +2°，不用转身

    def setUp(self):
        super().setUp()
        d = DEFAULT_CONFIG["nameplate"]
        self.cfg["nameplate"].update({k: d[k] for k in ("red_walk_static_px", "red_walk_min_move",
                                                         "red_walk_bad_s", "red_walk_hits")})
        self.cfg["nameplate"]["red_walk_static_s"] = 2.0              # v8.52 起默认关，这里打开测

    def walk(self, name, steps=8, dpos=0.05, dxy=0, dt=0.3):
        for i in range(steps):
            self.step(self.o(names=[dict(name, x=name["x"] + dxy * (i + 1))], pos=(50.0 + dpos * (i + 1), 50.0)), dt=dt)
            if self.g.far is None:
                break

    def test_straight_static_is_ui_temp_then_permanent(self):
        self.step(self.o(names=[self.NAME]))
        self.assertIsNotNone(self.g.far)
        self.assertFalse(any(k in ("left", "right") for k in self.keys()))
        self.walk(self.NAME)
        self.assertIsNone(self.g.far)
        self.assertTrue(any("直走了" in m and "界面上的红色" in m and "第 1 次" in m for m in self.logs))
        self.assertEqual(self.g.red_ui_bad, [])
        self.assertEqual(len(self.g.red_ui_tmp), 1)
        self.assertEqual(self.g._far_targets(self.o(names=[self.NAME]), self.clock.t), [])
        self.clock.t += 121                                       # 过了 120 秒 → 又会朝它走，第 2 次 → 永久
        self.assertEqual(len(self.g._far_targets(self.o(names=[self.NAME]), self.clock.t)), 1)
        self.g.far_skip_until = -1e9
        self.step(self.o(names=[self.NAME], pos=(50.0, 50.0)))
        self.assertIsNotNone(self.g.far)
        for i in range(8):
            self.step(self.o(names=[self.NAME], pos=(50.0 - 0.05 * (i + 1), 50.0)), dt=0.3)
            if self.g.far is None:
                break
        self.assertIsNone(self.g.far)
        self.assertEqual(len(self.g.red_ui_bad), 1)
        self.assertTrue(any("第 2 次" in m and "这次运行不再看" in m for m in self.logs))
        self.clock.t += 1000
        self.assertEqual(self.g._far_targets(self.o(names=[self.NAME]), self.clock.t), [])

    def test_hits_1_permanent_immediately(self):
        self.cfg["nameplate"]["red_walk_hits"] = 1
        self.step(self.o(names=[self.NAME]))
        self.walk(self.NAME)
        self.assertEqual(len(self.g.red_ui_bad), 1)

    def test_real_name_drifting_not_ui(self):
        self.step(self.o(names=[self.NAME]))
        self.walk(self.NAME, dxy=3)                               # 走近时名字慢慢挪（0.9 秒挪 9 像素）
        self.assertEqual(self.g.red_ui_bad, [])
        self.assertEqual(self.g.red_ui_tmp, [])
        self.assertFalse(any("直走了" in m for m in self.logs))

    def test_not_moving_not_ui(self):
        self.step(self.o(names=[self.NAME]))
        self.walk(self.NAME, dpos=0.01)                           # 人走得很慢（几乎卡住）→ 不算
        self.assertEqual(self.g.red_ui_tmp, [])
        self.assertFalse(any("直走了" in m for m in self.logs))

    def test_too_short_not_ui(self):
        self.step(self.o(names=[self.NAME]))
        self.walk(self.NAME, steps=5)                             # 只走了 1.5 秒
        self.assertEqual(self.g.red_ui_tmp, [])
        self.assertIsNotNone(self.g.far)

    def test_disabled(self):
        self.cfg["nameplate"]["red_walk_static_s"] = 0
        self.step(self.o(names=[self.NAME]))
        self.walk(self.NAME)
        self.assertEqual(self.g.red_ui_tmp, [])
        self.assertFalse(any("直走了" in m for m in self.logs))


class Strings(unittest.TestCase):
    def test_files(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertTrue(re.search(r"熊德自动打怪 v8\.5[1-9]", app))
        for k in ("red_walk_static_s", "red_walk_static_px", "red_walk_min_move", "red_walk_bad_s", "red_walk_hits"):
            self.assertIn(f'"{k}"', app)
        rd = _rd(os.path.join(ROOT, "README_中文.md"))
        self.assertTrue(re.search(r"v8\.51.*红名", rd))
        self.assertIn("v8.51", _rd(os.path.join(ROOT, "bot", "runner.py")))


if __name__ == "__main__":
    unittest.main()

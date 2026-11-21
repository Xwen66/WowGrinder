"""v8.34（t165u）：远处红名在整个游戏窗口里找（不再用旧的姓名板区域裁剪），排除界面区域；
走过去没结果后的暂停 far_skip_s 20 → 5 秒（旧配置还是 20 的自动改）。"""
import copy
import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config as bcfg  # noqa: E402
from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.detector import Detector  # noqa: E402
from test_v828 import TestRedNames  # noqa: E402

W, H = 2622, 1206
text = TestRedNames.text


def det(region=(156, 93, 1405, 770)):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["regions"]["nameplates"] = list(region)                 # 旧的裁剪（Simon 的配置）
    return Detector(cfg)


def frame():
    return np.full((H, W, 3), 60, np.uint8)


def centers(names):
    return [(n["x"] + n["w"] / 2, n["y"] + n["h"] / 2) for n in names]


class TestFullWindow(unittest.TestCase):
    def found(self, x, y):
        img = frame()
        text(None, img, x, y)
        return det().red_names(img)

    def test_right_side_found(self):                            # 以前 x > 1561 都不找
        names = self.found(1900, 400)
        self.assertEqual(len(names), 1)
        self.assertTrue(1890 <= names[0]["x"] <= 1910)

    def test_left_and_upper_found(self):
        self.assertEqual(len(self.found(400, 300)), 1)
        self.assertEqual(len(self.found(1200, 60)), 1)            # 旧区域 y < 93 不找

    def test_below_old_region_found(self):
        self.assertEqual(len(self.found(1000, 900)), 1)           # 旧区域 y > 863 不找（v8.50：x 700 现在是左下头像框排除区）

    def test_old_region_ignored_even_if_tiny(self):
        img = frame()
        text(None, img, 1900, 400)
        self.assertEqual(len(det((0, 0, 50, 50)).red_names(img)), 1)

    def test_ui_areas_excluded(self):
        for x, y, why in ((60, 40, "左上头像"), (2200, 100, "右上小地图"), (2300, 600, "右边任务追踪"),
                          (2000, 1000, "右下面板"), (100, 950, "左下聊天"), (1250, 600, "自己角色"),
                          (1200, 1100, "动作条"), (30, 10, "插件色块")):
            with self.subTest(why=why):
                self.assertEqual(self.found(x, y), [], why)

    def test_exclude_configurable(self):
        img = frame()
        text(None, img, 2300, 600)
        d = det()
        d.cfg["nameplate"]["red_ui_exclude"] = []
        self.assertEqual(len(d.red_names(img)), 1)

    def test_scales_with_window(self):                          # 1920x1080 也是按比例排除
        img = np.full((1080, 1920, 3), 60, np.uint8)
        text(None, img, 1800, 100)
        self.assertEqual(det().red_names(img), [])
        img = np.full((1080, 1920, 3), 60, np.uint8)
        text(None, img, 1400, 300)
        self.assertEqual(len(det().red_names(img)), 1)

    def test_name_next_to_marker_still_skipped(self):
        img = frame()
        text(None, img, 1900, 400)
        plate = {"x": 1920, "y": 420, "w": 20, "h": 12}
        self.assertEqual(det().red_names(img, [plate]), [])


class TestFarSkip(unittest.TestCase):
    def load(self, data):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return bcfg.load_config(p)

    def test_default_5(self):
        self.assertEqual(DEFAULT_CONFIG["nameplate"]["far_skip_s"], 5.0)

    def test_old_default_migrated(self):
        cfg = self.load({"nameplate": {"far_skip_s": 20.0}})
        self.assertEqual(cfg["nameplate"]["far_skip_s"], 5.0)
        self.assertIn("far_skip_v834", cfg["meta"]["migrations"])

    def test_custom_value_kept(self):
        self.assertEqual(self.load({"nameplate": {"far_skip_s": 12.0}})["nameplate"]["far_skip_s"], 12.0)

    def test_migrates_only_once(self):
        cfg = self.load({"nameplate": {"far_skip_s": 20.0}, "meta": {"migrations": ["far_skip_v834"]}})
        self.assertEqual(cfg["nameplate"]["far_skip_s"], 20.0)

    def test_state_machine_uses_it(self):
        from test_state_machine import Base
        b = Base("run")
        b.setUp()
        b.g.clock = lambda: 100.0
        b.g._stop_walk = lambda: None
        b.g._far_end("测试")
        self.assertAlmostEqual(b.g.far_skip_until, 105.0)


if __name__ == "__main__":
    unittest.main()

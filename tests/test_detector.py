import os
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.detector import (Detector, bar_percent, color_ratio, match_best,  # noqa: E402
                          suggest_hsv_range)

GREEN_LO, GREEN_HI = DEFAULT_CONFIG["colors"]["player_hp_lo"], DEFAULT_CONFIG["colors"]["player_hp_hi"]


def make_bar(pct, w=200, h=16, text=False):
    img = np.full((h, w, 3), (30, 30, 30), np.uint8)  # 深灰背景（空血部分）
    fill = int(round(w * pct / 100))
    img[:, :fill] = (0, 200, 0)  # BGR 绿
    if text:  # 血条中间的白色数字
        cv2.putText(img, "1234/5678", (w // 2 - 40, h - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    return img


class TestBar(unittest.TestCase):
    def test_percentages(self):
        for pct in (0, 10, 37, 50, 99, 100):
            got = bar_percent(make_bar(pct), GREEN_LO, GREEN_HI)
            self.assertAlmostEqual(got, pct, delta=1.0, msg=f"pct={pct}")

    def test_text_overlay_rightmost(self):
        got = bar_percent(make_bar(80, text=True), GREEN_LO, GREEN_HI, mode="rightmost")
        self.assertAlmostEqual(got, 80, delta=1.0)

    def test_count_mode(self):
        self.assertAlmostEqual(bar_percent(make_bar(60), GREEN_LO, GREEN_HI, mode="count"), 60, delta=1.0)

    def test_red_hue_wrap(self):
        img = np.zeros((10, 100, 3), np.uint8)
        img[:, :30] = (0, 0, 220)  # 红
        self.assertAlmostEqual(bar_percent(img, [170, 100, 100], [10, 255, 255]), 30, delta=1)
        self.assertAlmostEqual(color_ratio(img, [170, 100, 100], [10, 255, 255]), 0.3, delta=0.01)

    def test_suggest_range(self):
        lo, hi = suggest_hsv_range(make_bar(100))
        self.assertAlmostEqual(bar_percent(make_bar(45), lo, hi), 45, delta=1)

    def test_empty(self):
        self.assertEqual(bar_percent(None, GREEN_LO, GREEN_HI), 0.0)


class TestTemplate(unittest.TestCase):
    def test_match(self):
        rng = np.random.default_rng(0)
        hay = rng.integers(0, 255, (100, 300, 3), dtype=np.uint8)
        tpl = hay[40:72, 150:182].copy()
        score, loc = match_best(hay, tpl)
        self.assertGreater(score, 0.99)
        self.assertEqual(loc, (150, 40))

    def test_observe_frame(self):
        cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_CONFIG.items()}
        cfg["regions"] = dict(DEFAULT_CONFIG["regions"], player_hp=[100, 50, 200, 16], target_hp=[400, 50, 200, 16])
        cfg["detect"] = dict(DEFAULT_CONFIG["detect"], combat_mode="none")
        cfg["rotation"] = dict(DEFAULT_CONFIG["rotation"], mode="timed")
        frame = np.zeros((300, 800, 3), np.uint8)
        frame[50:66, 100:300] = make_bar(70)
        frame[50:66, 400:600] = make_bar(25)
        obs = Detector(cfg).observe(frame)
        self.assertAlmostEqual(obs.player_hp, 70, delta=1)
        self.assertAlmostEqual(obs.target_hp, 25, delta=1)
        self.assertTrue(obs.target_exists)
        self.assertIsNone(obs.in_combat)
        self.assertEqual(len(obs.ready_skills), 4)


if __name__ == "__main__":
    unittest.main()

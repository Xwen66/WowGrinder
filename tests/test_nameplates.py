import copy
import os
import sys
import time
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.detector import detect_nameplates, iou, nearest_to, nms  # noqa: E402


def plate_img(text="Wolf", selected=False, scale=1.0):
    """仿正式服姓名板：名字 + 红色血条；选中的更大、更亮、带白色描边。"""
    w, h = 130, 30
    img = np.full((h, w, 3), (25, 30, 35), np.uint8)
    cv2.putText(img, text, (30, 11), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (220, 220, 220), 1, cv2.LINE_AA)
    cv2.rectangle(img, (5, 16), (124, 25), (10, 10, 10), -1)
    cv2.rectangle(img, (6, 17), (110, 24), (20, 20, 200), -1)  # 红色血条（未满）
    if selected:
        img = cv2.convertScaleAbs(img, alpha=1.5, beta=40)
        cv2.rectangle(img, (4, 15), (125, 26), (255, 255, 255), 2)
        img = cv2.resize(img, None, fx=1.2, fy=1.2, interpolation=cv2.INTER_LINEAR)
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return img


def scene(placements, size=(640, 1600)):
    rng = np.random.default_rng(1)
    bg = cv2.GaussianBlur(rng.integers(40, 140, (size[0] // 8, size[1] // 8, 3), dtype=np.uint8), (5, 5), 0)
    img = cv2.resize(bg, (size[1], size[0]), interpolation=cv2.INTER_CUBIC)
    for p, (x, y) in placements:
        h, w = p.shape[:2]
        img[y:y + h, x:x + w] = p
    return img


class TestNMS(unittest.TestCase):
    def test_iou(self):
        a = {"x": 0, "y": 0, "w": 10, "h": 10}
        self.assertAlmostEqual(iou(a, a), 1.0)
        self.assertAlmostEqual(iou(a, {"x": 5, "y": 0, "w": 10, "h": 10}), 50 / 150)
        self.assertEqual(iou(a, {"x": 20, "y": 20, "w": 5, "h": 5}), 0.0)

    def test_nms(self):
        boxes = [{"x": 0, "y": 0, "w": 10, "h": 10, "adj": 0.9},
                 {"x": 1, "y": 1, "w": 10, "h": 10, "adj": 0.95},
                 {"x": 50, "y": 50, "w": 10, "h": 10, "adj": 0.8}]
        kept = nms(boxes, 0.3)
        self.assertEqual([(b["x"], b["adj"]) for b in kept], [(1, 0.95), (50, 0.8)])

    def test_nearest(self):
        pts = [{"x": 0, "y": 0, "w": 10, "h": 10}, {"x": 940, "y": 500, "w": 40, "h": 10}]
        self.assertEqual(nearest_to(pts, 960, 540)["x"], 940)
        self.assertIsNone(nearest_to([], 0, 0))


class TestNameplateDetect(unittest.TestCase):
    def setUp(self):
        self.ncfg = copy.deepcopy(DEFAULT_CONFIG["nameplate"])
        self.tpls = {"selected": [plate_img(selected=True)], "unselected": [plate_img()]}

    def _run(self, placements, **over):
        self.ncfg.update(over)
        return detect_nameplates(scene(placements), self.tpls, self.ncfg)

    def test_selected_vs_unselected(self):
        res = self._run([(plate_img("Wolf"), (200, 100)),
                         (plate_img("Boar", scale=0.85), (1200, 300)),   # 远处更小
                         (plate_img("Bear", selected=True), (700, 220))])
        sel = [r for r in res if r["kind"] == "selected"]
        un = [r for r in res if r["kind"] == "unselected"]
        self.assertEqual(len(sel), 1, res)
        self.assertEqual(len(un), 2, res)
        self.assertLess(abs(sel[0]["x"] - 700) + abs(sel[0]["y"] - 220), 12)
        xs = sorted(r["x"] for r in un)
        self.assertLess(abs(xs[0] - 200), 10)
        self.assertLess(abs(xs[1] - 1200), 10)

    def test_no_selected_when_none_on_screen(self):
        res = self._run([(plate_img("Wolf"), (300, 300)), (plate_img("Boar"), (900, 120))])
        self.assertEqual([r["kind"] for r in res], ["unselected", "unselected"])

    def test_only_one_selected(self):
        res = self._run([(plate_img(selected=True), (100, 100)), (plate_img(selected=True), (900, 400))])
        self.assertEqual(sum(r["kind"] == "selected" for r in res), 1)

    def test_empty_scene(self):
        self.assertEqual(self._run([]), [])

    def test_gray_and_no_downscale(self):
        res = self._run([(plate_img(), (200, 100)), (plate_img(selected=True), (700, 220))],
                        color_match=False, downscale=1.0)
        self.assertEqual(sorted(r["kind"] for r in res), ["selected", "unselected"])

    def test_speed(self):
        img = scene([(plate_img(), (200, 100))])
        t = time.perf_counter()
        detect_nameplates(img, self.tpls, self.ncfg)
        ms = (time.perf_counter() - t) * 1000
        print(f"\n  姓名板识别 1600x640 区域、2 模板 x 5 尺度：{ms:.0f} ms")
        self.assertLess(ms, 1500)


if __name__ == "__main__":
    unittest.main()

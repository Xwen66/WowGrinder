"""v8.45（t182u）：认出“已与服务器断开连接”弹窗（黑底 + 棕金边框 + 红按钮），断线时照样按 Enter。
v8.44 只认 logo/进入按钮/选角色顶栏 → 02:44 断线后近 6 小时一次 Enter 都没按。"""
import copy
import os
import re
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.glue import GlueDetector, find_dialog  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tests", "data")


def img(name, size=None):
    im = cv2.imread(os.path.join(DATA, name))
    assert im is not None, name
    return cv2.resize(im, size) if size else im


def dialog_frame(scale=2.25, bg=None, size=(2160, 1350)):
    """真实的暴雪弹窗（游戏截图里抠出来的）贴到屏幕中间偏上，模拟“已与服务器断开连接”。"""
    W, H = size
    fr = np.zeros((H, W, 3), np.uint8) if bg is None else cv2.resize(bg, (W, H))
    c = cv2.resize(img("dialog_popup_real.png"), None, fx=scale * H / 1350, fy=scale * H / 1350)
    h, w = c.shape[:2]
    y0, x0 = (H - h) // 2 - H // 13, (W - w) // 2
    fr[y0:y0 + h, x0:x0 + w] = c
    return fr


def screen_obs(glue=False, **kw):
    o = obs(**kw)
    o.source = "screen"
    o.glue, o.glue_what = glue, ("屏幕中间的弹窗" if glue else "")
    return o


class TestFindDialog(unittest.TestCase):
    def test_real_dialog_black_background_scales(self):
        for s in (1.8, 2.25, 3, 4, 5, 6):
            self.assertTrue(find_dialog(dialog_frame(s)), s)

    def test_dialog_over_login_like_background(self):
        bg = img("glue_charselect_960.jpg")
        for s in (2.25, 3.5):
            self.assertTrue(find_dialog(dialog_frame(s, bg)), s)

    def test_resolutions(self):
        for size in ((1920, 1200), (1280, 800), (2560, 1440)):
            self.assertTrue(find_dialog(dialog_frame(2.25, size=size)), size)

    def test_plain_world_no_dialog(self):
        for n in ("world_960.jpg", "glue_charselect_960.jpg"):
            self.assertEqual(find_dialog(img(n)), [], n)
            self.assertEqual(find_dialog(img(n, (2160, 1350))), [], n)

    def test_noise_and_red_noise(self):
        rng = np.random.default_rng(3)
        self.assertEqual(find_dialog(rng.integers(0, 255, (1350, 2160, 3), dtype=np.uint8)), [])
        fr = np.zeros((1350, 2160, 3), np.uint8)
        fr[600:640, 900:1100] = (0, 0, 200)      # 光一个红块，没有黑框+边框
        fr[:600] = (90, 120, 60)
        self.assertEqual(find_dialog(fr), [])
        self.assertEqual(find_dialog(None), [])

    def test_ingame_dialogs_found_but_gated(self):
        # 游戏里的“释放灵魂”弹窗、Esc 菜单 —— 长得一样，find_dialog 找得到，但插件读得到时不算
        g = GlueDetector()
        for n in ("world_release_popup_960.jpg", "world_menu_960.jpg"):
            self.assertTrue(find_dialog(img(n)), n)
            ok, _, sc = g.check(img(n, (2160, 1350)), dialog_ok=False)
            self.assertFalse(ok, (n, sc))
            self.assertGreater(sc["dialog"], 0)
        ok, what, _ = g.check(dialog_frame(), dialog_ok=True)
        self.assertTrue(ok)
        self.assertIn("弹窗", what)


class TestDetectorGate(unittest.TestCase):
    def det(self):
        from bot.detector import Detector
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["reconnect"]["check_every_s"] = 0
        return Detector(cfg)

    def test_disconnect_dialog_screen_mode(self):
        o = self.det().observe(dialog_frame(), want_nameplates=False)
        self.assertNotEqual(o.source, "addon")
        self.assertTrue(o.glue)
        self.assertIn("弹窗", o.glue_what)
        self.assertIn("弹窗 ", o.glue_info)

    def test_gate_uses_source(self):
        d = self.det()
        from bot.detector import Observation
        o = Observation()
        o.source = "addon"
        d._check_glue(o, dialog_frame())
        self.assertFalse(o.glue)                  # 插件读得到 = 游戏里的弹窗，不按
        o2 = Observation()
        o2.source = "screen"
        d._check_glue(o2, dialog_frame())
        self.assertTrue(o2.glue)

    def test_cache_not_shared_between_gates(self):
        from bot.detector import Detector, Observation
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["reconnect"]["check_every_s"] = 60
        d = Detector(cfg)
        o = Observation()
        o.source = "addon"
        d._check_glue(o, dialog_frame())
        o2 = Observation()
        o2.source = "screen"
        d._check_glue(o2, dialog_frame())
        self.assertFalse(o.glue)
        self.assertTrue(o2.glue)


class TestReconnectV845(Base):
    def enters(self):
        return [k for k in self.keys() if k == "enter"]

    def start(self):
        self.g.tick(obs(hp=100))
        self.inp.history.clear()

    def test_dialog_then_unrecognised_login_keeps_pressing(self):
        """02:44 那种：弹窗 → 按 Enter 关掉 → 登录界面没认出来 → 30 秒后照样每 10 秒按 → 进游戏。"""
        self.start()
        for _ in range(6):
            self.clock.t += 0.5
            self.g.tick(screen_obs(glue=True, hp=0))
        self.assertEqual(len(self.enters()), 1)
        self.assertTrue(any("🔌 认出断线" in m and "弹窗" in m for m in self.logs))
        for _ in range(58):                    # 29 秒：先等着
            self.clock.t += 0.5
            self.g.tick(screen_obs(hp=0))
        self.assertEqual(len(self.enters()), 1)
        for _ in range(43):                    # 再 21.5 秒：第 30、40、50 秒按（v8.46：多半秒，第 50 秒正好卡边界，浮点误差会偶尔差一次）
            self.clock.t += 0.5
            self.g.tick(screen_obs(hp=0))
        self.assertEqual(len(self.enters()), 4)
        self.assertEqual(set(self.keys()), {"enter"})
        self.assertFalse(any("死亡" in m for m in self.logs))
        for _ in range(8):
            self.clock.t += 0.5
            self.g.tick(obs(hp=100))
        self.assertFalse(self.g.rc_active)
        self.assertTrue(any("✅ 重连成功" in m for m in self.logs))

    def test_no_blind_enter_right_after_world_flicker(self):
        # 刚进游戏插件闪一下读不到 → 不能按 Enter（会打开聊天框）
        self.start()
        for _ in range(6):
            self.clock.t += 0.5
            self.g.tick(screen_obs(glue=True, hp=0))
        n = len(self.enters())
        for i in range(40):
            self.clock.t += 0.5
            self.g.tick(obs(hp=100) if i % 4 else screen_obs(hp=100))
        self.assertEqual(len(self.enters()), n)

    def test_not_active_never_blind_presses(self):
        self.start()
        for _ in range(400):                   # 插件读不到 200 秒，又没认出界面 → 不按（v8.44 原样）
            self.clock.t += 0.5
            self.g.tick(screen_obs(hp=100))
        self.assertEqual(self.enters(), [])

    def test_addon_lost_snapshot_event(self):
        self.start()
        for _ in range(62):
            self.clock.t += 0.5
            self.g.tick(screen_obs(hp=100))
        kinds = [k for k, _ in self.g.events]
        self.assertEqual(kinds.count("addon_lost"), 1)
        for _ in range(100):
            self.clock.t += 0.5
            self.g.tick(screen_obs(hp=100))
        self.assertEqual([k for k, _ in self.g.events].count("addon_lost"), 1)   # 10 分钟一次


class TestStrings(unittest.TestCase):
    def test_versions(self):
        with open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8") as f:
            self.assertTrue(re.search(r"熊德自动打怪 v8\.(4\d|[5-9]\d)", f.read()))      # v8.46 起标题版本在新测试里查
        with open(os.path.join(ROOT, "README_中文.md"), encoding="utf-8") as f:
            rd = f.read()
        self.assertIn("v8.45 认出“已与服务器断开连接”弹窗", rd)
        self.assertLess(rd.index("v8.45 认出"), rd.index("v8.44 断线自动重连"))
        self.assertEqual(DEFAULT_CONFIG["reconnect"]["blind_after_s"], 30.0)


if __name__ == "__main__":
    unittest.main()

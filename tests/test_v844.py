"""v8.44（t181u）：断线自动重连 —— 截图认出断线弹窗/登录/选角色界面 → 每 10 秒按一次 Enter，直到回到游戏。"""
import copy
import os
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.glue import GlueDetector  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402
from test_v831 import dobs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tests", "data")


def img(name, size=None):
    im = cv2.imread(os.path.join(DATA, name))
    assert im is not None, name
    return cv2.resize(im, size) if size else im


def gobs(**kw):
    o = obs(**kw)
    o.glue, o.glue_what = True, "左上角魔兽世界 logo"
    return o


class TestGlueDetector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = GlueDetector()

    def test_templates_shipped(self):
        for n in ("logo", "enter_world", "top_bar"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "images", "glue", n + ".png")))

    def test_real_char_select_all_sizes(self):
        # 真的选角色界面（bot 自己 00:41:56 存的 960 宽截图）
        for size in (None, (1280, 800), (1920, 1200), (2160, 1350)):
            ok, what, sc = self.g.check(img("glue_charselect_960.jpg", size))
            self.assertTrue(ok, (size, sc))
            self.assertIn("logo", what)

    def test_logo_alone_on_dark_frame(self):
        # 登录界面/断线弹窗：只假设左上角 logo 还在，别的都不在
        frame = np.full((1350, 2160, 3), 20, np.uint8)
        logo = cv2.imread(os.path.join(ROOT, "images", "glue", "logo.png"))
        frame[6:6 + logo.shape[0], 86:86 + logo.shape[1]] = logo
        ok, what, _ = self.g.check(frame)
        self.assertTrue(ok)
        self.assertIn("logo", what)
        ok2, _, _ = self.g.check(cv2.resize(frame, (1920, 1200)))
        self.assertTrue(ok2)

    def test_in_world_negative(self):
        for n in ("world_960.jpg", "world_menu_960.jpg"):
            for size in (None, (2160, 1350)):
                ok, _, sc = self.g.check(img(n, size), dialog_ok=False)   # v8.45：插件读得到时弹窗不算
                self.assertFalse(ok, (n, size, sc))

    def test_plain_and_noise_negative(self):
        rng = np.random.default_rng(1)
        for frame in (np.zeros((1350, 2160, 3), np.uint8), np.full((1200, 1920, 3), 128, np.uint8),
                      rng.integers(0, 255, (1350, 2160, 3), dtype=np.uint8)):
            self.assertFalse(self.g.check(frame)[0])


class TestDetectorIntegration(unittest.TestCase):
    def det(self, **rc):
        from bot.detector import Detector
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["reconnect"]["check_every_s"] = 0
        cfg["reconnect"].update(rc)
        return Detector(cfg)

    def test_glue_flag_even_if_addon_misread(self):
        # 选角色界面左上角会被插件读取器误读成“插件读得到”→ 不管 source 都要认
        o = self.det().observe(img("glue_charselect_960.jpg", (2160, 1350)), want_nameplates=False)
        self.assertTrue(o.glue)

    def test_world_no_glue(self):
        o = self.det().observe(img("world_960.jpg", (2160, 1350)), want_nameplates=False)
        self.assertFalse(o.glue)

    def test_disabled_skips(self):
        o = self.det(enabled=False).observe(img("glue_charselect_960.jpg", (2160, 1350)), want_nameplates=False)
        self.assertFalse(o.glue)

    def test_throttled(self):
        d = self.det(check_every_s=60)
        o1 = d.observe(img("glue_charselect_960.jpg", (2160, 1350)), want_nameplates=False)
        o2 = d.observe(img("world_960.jpg", (2160, 1350)), want_nameplates=False)
        self.assertTrue(o1.glue and o2.glue)     # 60 秒内用上次的结果


class TestReconnectTick(Base):
    def enters(self):
        return [k for k in self.keys() if k == "enter"]

    def start(self):
        self.g.tick(obs(hp=100))
        self.inp.history.clear()

    def test_confirm_then_enter_every_10s(self):
        self.start()
        for _ in range(3):                 # 第一次认出后才 1 秒 < confirm 2 秒：不按
            self.clock.t += 0.5
            self.g.tick(gobs(hp=0))
        self.assertEqual(self.enters(), [])
        self.clock.t += 1.1
        self.g.tick(gobs(hp=0))
        self.assertEqual(self.enters(), ["enter"])
        self.assertEqual(self.keys(), ["enter"])          # 只按 Enter，别的不按
        self.assertTrue(any("认出断线/登录/选角色界面" in m for m in self.logs))
        self.assertTrue(any("🔌 断线 → 按 Enter 重连（第 1 次）" in m for m in self.logs))
        for _ in range(39):                # 再 19.5 秒 → 再按 1 次（第 10 秒），第 20 秒还差一点
            self.clock.t += 0.5
            self.g.tick(gobs(hp=0))
        self.assertEqual(len(self.enters()), 2)
        self.clock.t += 0.6
        self.g.tick(gobs(hp=0))
        self.assertEqual(len(self.enters()), 3)
        self.assertTrue(any("第 3 次" in m for m in self.logs))
        self.assertEqual(self.g.status_label(), "🔌 断线重连中（按了 3 次 Enter）")

    def test_no_give_up(self):
        self.start()
        for _ in range(2000):              # 1000 秒
            self.clock.t += 0.5
            self.g.tick(gobs(hp=0))
        self.assertGreaterEqual(len(self.enters()), 99)
        self.assertFalse(self.g.stopped)

    def test_no_death_logic_on_glue(self):
        self.start()
        for _ in range(10):
            self.clock.t += 0.5
            self.g.tick(gobs(hp=0, combat=False))
        self.assertFalse(any("死亡" in m for m in self.logs))
        self.assertEqual(self.g.deaths, 0)

    def test_no_enter_in_world(self):
        self.start()
        for _ in range(60):
            self.clock.t += 0.5
            self.g.tick(obs(hp=100))
            self.g.tick(dobs(dead=True, hp=0))
        self.assertEqual(self.enters(), [])

    def test_short_glue_blip_no_enter(self):
        self.start()
        for i in range(20):
            self.clock.t += 0.5
            self.g.tick(gobs(hp=0) if i % 3 == 0 else obs(hp=100))
        self.assertEqual(self.enters(), [])
        self.assertFalse(self.g.rc_active)

    def test_loading_wait_then_success(self):
        self.start()
        for _ in range(6):
            self.clock.t += 0.5
            self.g.tick(gobs(hp=0))
        n = len(self.enters())
        self.assertEqual(n, 1)
        # 读条：既不是登录界面也没有插件 → 等着，不按键
        lost = obs(hp=100)
        lost.source = "screen"
        for _ in range(50):                # v8.45：30 秒后才会照样按，这里 25 秒
            self.clock.t += 0.5
            self.g.tick(lost)
        self.assertEqual(len(self.enters()), 1)
        self.assertEqual(self.keys(), ["enter"])
        self.assertEqual(sum("重连中" in m and "等着" in m for m in self.logs), 1)
        # 插件读得到，但不到 3 秒 → 还不算
        self.clock.t += 0.5
        self.g.tick(obs(hp=100))
        self.clock.t += 2.0
        self.g.tick(obs(hp=100))
        self.assertTrue(self.g.rc_active)
        self.assertEqual(self.keys(), ["enter"])
        self.clock.t += 1.1
        self.g.tick(obs(hp=100))
        self.assertFalse(self.g.rc_active)
        self.assertTrue(any("✅ 重连成功" in m and "继续打怪" in m for m in self.logs))
        self.assertEqual(self.g.state, State.SEARCHING)

    def test_addon_misread_at_char_select_still_presses(self):
        # 选角色界面：source=addon（误读）但截图认出 → 照样按
        self.start()
        for _ in range(25):
            self.clock.t += 0.5
            self.g.tick(gobs(hp=0, combat=False))
        self.assertEqual(len(self.enters()), 2)

    def test_disabled(self):
        self.cfg["reconnect"] = {"enabled": False}
        self.start()
        for _ in range(30):
            self.clock.t += 0.5
            self.g.tick(gobs(hp=100))
        self.assertEqual(self.enters(), [])

    def test_interval_configurable(self):
        self.cfg.setdefault("reconnect", {})["interval_s"] = 5
        self.start()
        for _ in range(25):                # 12.5 秒：第 2、7、12 秒各按一次
            self.clock.t += 0.5
            self.g.tick(gobs(hp=0))
        self.assertEqual(len(self.enters()), 3)


class TestStrings(unittest.TestCase):
    def test_config(self):
        rc = DEFAULT_CONFIG["reconnect"]
        self.assertTrue(rc["enabled"])
        self.assertEqual(rc["interval_s"], 10.0)
        self.assertEqual(rc["key"], "enter")

    def test_gui_readme(self):
        with open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("v8.44", src)
        self.assertIn("断线自动重连（v8.44）", src)
        self.assertIn("重连按 Enter 间隔(秒)", src)
        with open(os.path.join(ROOT, "README_中文.md"), encoding="utf-8") as f:
            rd = f.read()
        self.assertIn("v8.44 断线自动重连", rd)
        self.assertLess(rd.index("v8.44 断线自动重连"), rd.index("v8.43 去掉自动停止"))


if __name__ == "__main__":
    unittest.main()

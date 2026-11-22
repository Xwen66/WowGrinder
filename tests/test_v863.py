"""v8.63（t236u）：认出这个客户端真实的断线画面（登录界面 + 灰色弹窗“已从服务器断开。(WOW51900319)”+“确定”）→ 每 10 秒按 Enter。
11/22 14:59 断线，v8.62 只报 addon_lost（logo 0.33 / 进入按钮 0.28 / 顶栏 0.59 / 弹窗 0），一次 Enter 都没按。"""
import os
import re
import sys
import unittest

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.glue import GlueDetector, LOGIN_TEMPLATES, score_text, yellow  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tests", "data")
WORLD = ("world_960.jpg", "world_menu_960.jpg", "world_release_popup_960.jpg", "world_heal_960.jpg",
         "world_bagfull_960.jpg", "world_death_960.jpg", "world_repair_960.jpg")


def img(name, size=None):
    im = cv2.imread(os.path.join(DATA, name))
    assert im is not None, name
    return cv2.resize(im, size) if size else im


class TestDisconnectScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = GlueDetector()

    def test_templates_present(self):
        for name, fn, *_ in LOGIN_TEMPLATES:
            self.assertTrue(os.path.exists(os.path.join(ROOT, "images", "glue", fn)), fn)
            self.assertIn(name, self.g.login)

    def test_real_screenshot_full_res(self):
        ok, what, sc = self.g.check(img("disconnect_login_2160.jpg"), dialog_ok=True)
        self.assertTrue(ok)
        for n in ("dc_text", "login_menu", "login_logo"):
            self.assertGreater(sc[n], 0.9, n)
        self.assertIn("已从服务器断开", what)

    def test_real_screenshot_960_bot_shot(self):
        ok, what, sc = self.g.check(img("disconnect_login_960.jpg"), dialog_ok=True)
        self.assertTrue(ok, score_text(sc))
        self.assertGreaterEqual(sum(sc[n] >= self.g.thr[n] for n in ("dc_text", "login_menu", "login_logo")), 3)

    def test_other_resolutions(self):
        for size in ((1920, 1200), (1680, 1050), (2560, 1600)):
            ok, _, sc = self.g.check(img("disconnect_login_2160.jpg", size), dialog_ok=True)
            self.assertTrue(ok, (size, score_text(sc)))

    def test_counts_even_if_addon_misread(self):
        # 至少 2 个新模板同时认出 → 就算插件“读到”（误读）也算
        ok, _, _ = self.g.check(img("disconnect_login_2160.jpg"), dialog_ok=False)
        self.assertTrue(ok)

    def test_world_screens_no_login_hits(self):
        for f in WORLD:
            sc = self.g.scores(img(f))
            for n in ("dc_text", "login_menu", "login_logo"):
                self.assertLess(sc[n], self.g.thr[n] - 0.1, (f, n, score_text(sc)))
            ok, _, _ = self.g.check(img(f), dialog_ok=False)
            self.assertFalse(ok, f)

    def test_single_login_hit_gated_by_addon(self):
        g = GlueDetector()
        fr = img("disconnect_login_2160.jpg")
        sc = g.scores(fr)
        # 人为只让一个模板过线：插件读得到 → 不算；读不到 → 算
        g.thr.update({"dc_text": 2, "login_menu": 2, "logo": 2, "enter_world": 2, "top_bar": 2})
        self.assertFalse(g.check(fr, dialog_ok=False)[0])
        self.assertTrue(g.check(fr, dialog_ok=True)[0])
        self.assertGreater(sc["login_logo"], 0.9)

    def test_yellow_channel(self):
        import numpy as np
        px = np.array([[[0, 210, 255], [128, 128, 128], [40, 40, 200]]], np.uint8)   # 黄 / 灰 / 红 (BGR)
        y = yellow(px)[0]
        self.assertGreater(y[0], 150)
        self.assertEqual(y[1], 0)
        self.assertEqual(y[2], 0)


def dc_obs(glue=True, **kw):
    o = obs(**kw)
    o.source = "screen"
    o.glue = glue
    o.glue_what = "弹窗黄字“已从服务器断开”、登录界面右下角菜单按钮、登录界面 logo" if glue else ""
    o.glue_info = "断开黄字 0.83 / 登录菜单 0.85 / 登录logo 0.98"
    return o


class TestReconnectV863(Base):
    def enters(self):
        return [k for k in self.keys() if k == "enter"]

    def test_press_every_10s_with_log_and_shot(self):
        self.g.tick(obs(hp=100))
        self.inp.history.clear()
        self.g.events.clear()
        for _ in range(int(35 / 0.5)):
            self.clock.t += 0.5
            self.g.tick(dc_obs(hp=0))
        n = len(self.enters())
        self.assertIn(n, (3, 4))                       # 第 2、12、22、32 秒
        self.assertEqual(set(self.keys()), {"enter"})
        press_logs = [m for m in self.logs if "按 Enter 重连" in m]
        self.assertEqual(len(press_logs), n)
        self.assertTrue(all("已从服务器断开" in m for m in press_logs))
        shots = [e for e in self.g.events if e[0] == "reconnect_press"]
        self.assertEqual(len(shots), n)
        self.assertIn("登录logo", shots[0][1])
        self.assertFalse(any("死亡" in m for m in self.logs))
        for _ in range(8):
            self.clock.t += 0.5
            self.g.tick(obs(hp=100))
        self.assertFalse(self.g.rc_active)
        self.assertTrue(any("✅ 重连成功" in m for m in self.logs))

    def test_versions(self):
        src = open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8").read()
        self.assertRegex(src, r"v8\.6[3-9]")
        self.assertIn("v8.63", open(os.path.join(ROOT, "README_中文.md"), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()

"""v8.53（t217u）：退回 v8.52（插件报告界面框），改用“跟不跟着动”判断红名是不是界面：
随时盯着屏幕上的红名；人转身 ≥ 10° 它还在原地 → 界面（第一次就永久）；人走了 ≥ 0.15、2 秒内不动 → 界面（120 秒，第 2 次永久）。"""
import copy
import math
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG, _merge, _migrate  # noqa: E402
from bot.detector import Detector  # noqa: E402
from test_v840 import TabBase, tobs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYS = ("red_turn_static_deg", "red_turn_static_px", "red_turn_hits", "red_walk_static_s", "red_walk_static_px",
        "red_walk_min_move", "red_walk_bad_s", "red_walk_hits")


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def nm(cx, cy):
    return {"x": cx - 30, "y": cy - 6, "w": 60, "h": 12, "px": 200}


class Defaults(unittest.TestCase):
    def test_defaults(self):
        n = DEFAULT_CONFIG["nameplate"]
        self.assertEqual((n["red_turn_static_deg"], n["red_turn_static_px"], n["red_turn_hits"]), (10.0, 8, 1))
        self.assertEqual((n["red_walk_static_s"], n["red_walk_static_px"], n["red_walk_min_move"],
                          n["red_walk_bad_s"], n["red_walk_hits"]), (2.0, 5, 0.15, 120.0, 2))
        self.assertNotIn("use_addon_ui", n)
        self.assertNotIn("ui_margin_px", n)
        self.assertEqual(len(n["red_ui_exclude"]), 10)             # 手动排除区：v8.51 的 9 块 + v8.66 右上 buff 一排

    def test_migrate_back_from_v852_once(self):
        loaded = {"nameplate": {"red_walk_static_s": 0.0, "use_addon_ui": True, "ui_margin_px": 8},
                  "addon": {"ui_stale_s": 5.0},
                  "meta": {"migrations": ["red_ui_v850", "red_ui_v851", "red_walk_v852"]}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(cfg["nameplate"]["red_walk_static_s"], 2.0)
        self.assertNotIn("use_addon_ui", cfg["nameplate"])
        self.assertNotIn("ui_margin_px", cfg["nameplate"])
        self.assertNotIn("ui_stale_s", cfg["addon"])
        self.assertIn("red_walk_v853", cfg["meta"]["migrations"])
        loaded = {"nameplate": {"red_walk_static_s": 0.0},
                  "meta": {"migrations": ["red_ui_v850", "red_ui_v851", "red_walk_v852", "red_walk_v853"]}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)
        self.assertEqual(cfg["nameplate"]["red_walk_static_s"], 0.0)   # 之后自己关掉 → 不再改


class Reverted(unittest.TestCase):
    def test_addon_back_to_v014(self):
        lua = _rd(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.lua"))
        self.assertNotIn("ui_rects", lua)                  # v8.52 的界面框没了（v0.15 起插件是 v8.55 修理用的新版本）
        self.assertNotIn("UIRect", lua)

    def test_bot_side_removed(self):
        det = Detector(copy.deepcopy(DEFAULT_CONFIG))
        self.assertFalse(hasattr(det, "ui_exclude"))
        self.assertFalse(hasattr(det.addon, "ui_rects"))
        self.assertEqual(det._status_exclude(), (0, 0, 430, 60))     # 左上角跳过区回到 v8.51 大小
        for f in ("bot/detector.py", "bot/addon_reader.py", "gui/app.py"):      # config.py 里只剩迁移时删掉它
            self.assertNotIn("use_addon_ui", _rd(os.path.join(ROOT, f)))


class Track(TabBase):
    def setUp(self):
        super().setUp()
        n = self.cfg["nameplate"]
        n.update(plates_first=True, far_click_frames=1, red_static_turns=2, far_names=True)
        self.cfg["tab_search"]["far_walk_tab"] = False
        d = DEFAULT_CONFIG["nameplate"]
        self.cfg["nameplate"].update({k: d[k] for k in KEYS})
        self.cfg["nameplate"]["far_names"] = False                  # 只看判定，不朝红名走

    def o(self, names=(), moving=False, **kw):
        x = tobs(**kw)
        x.red_names = list(names)
        x.moving = moving
        return x

    def seq(self, frames, dt=0.3):
        for names, facing, pos in frames:
            self.step(self.o(names=names, facing=facing, pos=pos), dt=dt)

    def test_turn_static_is_ui_permanent_first_time(self):
        p = (50.0, 50.0)
        self.seq([([nm(1210, 1000)], 0.0, p), ([nm(1210, 1000)], 0.1, p), ([nm(1211, 1001)], 0.25, p)])
        self.assertEqual(len(self.g.red_ui_bad), 1)
        self.assertTrue(any("人转了 14°" in m and "界面上的红色" in m and "这次运行不再看" in m for m in self.logs))
        self.assertTrue(self.g._red_ui(1210, 1000))

    def test_turn_real_name_moves(self):
        p = (50.0, 50.0)
        self.seq([([nm(1000, 300)], 0.0, p), ([nm(760, 300)], 0.175, p), ([nm(520, 300)], 0.35, p)])
        self.assertEqual(self.g.red_ui_bad, [])
        self.assertFalse(any("🚫" in m for m in self.logs))

    def test_small_turn_not_enough(self):
        p = (50.0, 50.0)
        self.seq([([nm(1210, 1000)], 0.0, p), ([nm(1210, 1000)], 0.05, p), ([nm(1210, 1000)], 0.1, p)])   # 5.7°
        self.assertEqual(self.g.red_ui_bad, [])

    def test_facing_wraps(self):
        p = (50.0, 50.0)
        self.seq([([nm(1210, 1000)], 2 * math.pi - 0.1, p), ([nm(1210, 1000)], 0.1, p)])   # 跨过 0：转了 11.5°
        self.assertEqual(len(self.g.red_ui_bad), 1)

    def test_walk_static_temp_then_permanent(self):
        fr = [([nm(1210, 1000)], 0.0, (50.0 + 0.03 * i, 50.0)) for i in range(9)]
        self.seq(fr)
        self.assertEqual(self.g.red_ui_bad, [])
        self.assertEqual(len(self.g.red_ui_tmp), 1)
        self.assertTrue(any("人走了" in m and "第 1 次" in m and "120 秒" in m for m in self.logs))
        self.clock.t += 121
        self.seq([([nm(1210, 1000)], 0.0, (52.0 + 0.03 * i, 50.0)) for i in range(9)])
        self.assertEqual(len(self.g.red_ui_bad), 1)

    def test_chasing_mob_drifts_not_ui(self):
        fr = [([nm(1000 + 4 * i, 900 - 3 * i)], 0.0, (50.0 + 0.03 * i, 50.0)) for i in range(12)]   # 跟着走、慢慢挪
        self.seq(fr)
        self.assertEqual(self.g.red_ui_tmp, [])
        self.assertEqual(self.g.red_ui_bad, [])

    def test_standing_still_not_ui(self):
        self.seq([([nm(1210, 1000)], 0.0, (50.0, 50.0)) for _ in range(12)])     # 人不动不转 → 判断不了
        self.assertEqual((self.g.red_ui_tmp, self.g.red_ui_bad), ([], []))

    def test_gap_resets_track(self):
        p = (50.0, 50.0)
        self.seq([([nm(1210, 1000)], 0.0, p)])
        self.seq([([], 0.0, p)] * 5)                                # 1.5 秒看不到 → 重新开始
        self.seq([([nm(1210, 1000)], 0.3, p), ([nm(1210, 1000)], 0.3, p)])
        self.assertEqual(self.g.red_ui_bad, [])

    def test_combat_ignored(self):
        p = (50.0, 50.0)
        for f in (0.0, 0.2, 0.4):
            self.step(self.o(names=[nm(1210, 1000)], facing=f, pos=p, combat=True))
        self.assertEqual(self.g.red_ui_bad, [])

    def test_two_names_only_static_marked(self):
        p = (50.0, 50.0)
        self.seq([([nm(1210, 1000), nm(500, 300)], 0.0, p), ([nm(1210, 1000), nm(260, 300)], 0.2, p)])
        self.assertEqual(len(self.g.red_ui_bad), 1)
        self.assertTrue(self.g._red_ui(1210, 1000))
        self.assertFalse(self.g._red_ui(260, 300))

    def test_disabled(self):
        self.cfg["nameplate"].update(red_turn_static_deg=0, red_walk_static_s=0)
        self.seq([([nm(1210, 1000)], 0.03 * i, (50.0 + 0.03 * i, 50.0)) for i in range(12)])
        self.assertEqual((self.g.red_ui_tmp, self.g.red_ui_bad), ([], []))

    def test_turn_during_far_walk_ends_walk(self):
        self.cfg["nameplate"]["far_names"] = True
        self.step(self.o(names=[nm(1000, 900)], facing=0.0))
        self.assertIsNotNone(self.g.far)
        self.step(self.o(names=[nm(1000, 900)], facing=0.3, pos=(50.05, 50.0)))
        self.assertIsNone(self.g.far)
        self.assertTrue(any("人转了" in m for m in self.logs))
        self.assertTrue(any("那个“红名”是界面" in m for m in self.logs))


class Strings(unittest.TestCase):
    def test_files(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertRegex(app, r"熊德自动打怪 v8\.(5[3-9]|[6-9]\d)")
        for k in ("red_turn_static_deg", "red_turn_static_px", "red_turn_hits"):
            self.assertIn(f'"{k}"', app)
        rd = _rd(os.path.join(ROOT, "README_中文.md"))
        self.assertTrue(re.search(r"v8\.53.*界面", rd))
        self.assertIn("v8.53", _rd(os.path.join(ROOT, "bot", "runner.py")))


if __name__ == "__main__":
    unittest.main()

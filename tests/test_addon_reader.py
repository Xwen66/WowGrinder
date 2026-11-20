import copy
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.addon_reader import (AddonReader, FIELDS, apply_gamma, classify, decode_bits,  # noqa: E402
                              encode_bits, paint_test_frame)
from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.detector import Detector  # noqa: E402


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


# 战斗中：百分比是机密值 → 二进制无效，靠进度条
FULL = {2: "green", 3: "green", 4: "red", 6: "black", 7: "green", 8: "red", 9: "red", 10: "black",
        12: "black", 13: "green", 14: "green", "bars": (100, 40, 60, 80)}

# 各种“游戏画面设置”：伽马、亮度偏移
DISTORTIONS = [(1.0, 0), (1.2, 0), (1.5, 0), (0.8, 0), (1.2, 20), (1.0, -20), (2.0, 10)]


class TestBits(unittest.TestCase):
    def test_roundtrip(self):
        for nbits in (7, 16):
            for v in (0, 1, 37, 100, 2 ** nbits - 1):
                self.assertEqual(decode_bits(encode_bits(v, nbits), nbits), v)

    def test_invalid_and_corrupt(self):
        self.assertIsNone(decode_bits(encode_bits(None, 16), 16))
        bits = encode_bits(12345, 16)
        for i in range(1, 17):   # 任意翻转一位数据 → 校验失败
            b = list(bits)
            b[i] ^= 1
            self.assertIsNone(decode_bits(b, 16), i)

    def test_classify_threshold(self):
        self.assertEqual(classify((255, 144, 0)), "yellow")   # 旧版橙色在伽马 1.2 下 → 不再使用
        self.assertEqual(classify((230, 20, 30)), "red")
        self.assertEqual(classify((140, 140, 140)), "white")


class TestAddonReader(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(DEFAULT_CONFIG)
        self.clock = Clock()
        self.r = AddonReader(self.cfg, clock=self.clock)

    def check_full(self, st, hp=100, thp=40, rage=60, mana=80):
        self.assertTrue(st.ok, st.reason)
        self.assertAlmostEqual(st.player_hp, hp, delta=1)
        self.assertFalse(st.player_dead)
        self.assertTrue(st.in_combat)
        self.assertEqual(st.target_kind, "enemy")
        self.assertAlmostEqual(st.target_hp, thp, delta=1)
        self.assertEqual(st.range, "melee")
        self.assertEqual(st.error, "facing")
        self.assertEqual(st.form, "bear")
        self.assertFalse(st.casting)
        self.assertAlmostEqual(st.rage, rage, delta=1)
        self.assertTrue(st.aggro)
        self.assertTrue(st.autoattack)
        self.assertAlmostEqual(st.mana, mana, delta=1)

    def test_decode_combat_uses_bars(self):
        self.check_full(self.r.read(paint_test_frame(FULL, beat=3)))

    def test_out_of_combat_binary_percent(self):
        f = paint_test_frame({2: "green", 9: "black", "bars": (0, 0, 0, 0)},
                             bin_pct={"hp": 73, "target_hp": None, "rage": 0, "mana": 55})
        st = self.r.read(f)
        self.assertEqual((st.player_hp, st.rage, st.mana), (73, 0, 55))
        self.assertEqual(st.form, "caster")

    def test_every_value_survives_gamma_and_brightness(self):
        for gamma, bright in DISTORTIONS:
            with self.subTest(gamma=gamma, bright=bright):
                r = AddonReader(self.cfg, clock=Clock())
                f = paint_test_frame(FULL, beat=7, pos=(45.67, 78.91), facing=5.5, map_id=1423,
                                     bin_pct={"hp": 64, "target_hp": 33, "rage": 12, "mana": 91})
                st = r.read(apply_gamma(f, gamma, bright))
                self.check_full(st)        # 战斗中：一律读进度条（二进制百分比被忽略）
                self.assertAlmostEqual(st.pos[0], 45.67, places=2)
                self.assertAlmostEqual(st.pos[1], 78.91, places=2)
                self.assertAlmostEqual(st.facing, 5.5, places=3)
                self.assertEqual(st.map_id, 1423)
                ooc = {**FULL, **{3: "black"}}
                st2 = AddonReader(self.cfg, clock=Clock()).read(apply_gamma(paint_test_frame(
                    ooc, beat=7, bin_pct={"hp": 64, "target_hp": 33, "rage": 12, "mana": 91}, since_damage=5),
                    gamma, bright))
                self.assertEqual((st2.player_hp, st2.target_hp, st2.rage, st2.mana), (64, 33, 12, 91))
                self.assertEqual(st2.since_damage, 5)
                default = gamma == 1.0 and bright == 0
                self.assertEqual(st.gamma_note == "", default, (st.calib, st.gamma_note))

    def test_v05_blocks(self):
        f = paint_test_frame({**FULL, **{16: "red", 17: "red", 18: "yellow", 19: "magenta", 20: "green"}},
                             since_damage=3)
        for gamma in (1.0, 1.2):
            st = AddonReader(self.cfg, clock=Clock()).read(apply_gamma(f, gamma))
            self.assertEqual(st.attack_range, "out")
            self.assertTrue(st.err_facing and st.err_far and st.err_los)
            self.assertTrue(st.attack_slot)
            self.assertEqual(st.since_damage, 3)
            self.assertTrue(st.hp_bar_ok)
        st = self.r.read(paint_test_frame({**FULL, **{16: "green"}}))
        self.assertEqual(st.attack_range, "in")
        self.assertFalse(st.err_facing or st.err_far or st.err_los)
        self.assertIsNone(st.since_damage)                 # 战斗日志不可用
        st = self.r.read(paint_test_frame({**FULL, **{16: "blue", 20: "blue"}}, since_damage=math.inf))
        self.assertEqual(st.attack_range, "no_slot")
        self.assertFalse(st.attack_slot)
        self.assertTrue(math.isinf(st.since_damage))

    def test_hp_bar_status(self):
        st = self.r.read(paint_test_frame({**FULL, **{21: "red"}}))
        self.assertFalse(st.hp_bar_ok)
        st = self.r.read(paint_test_frame({**FULL, **{21: "yellow"}}))
        self.assertTrue(st.hp_bar_ok)
        self.assertFalse(st.target_bar_ok)

    def test_old_v04_rejected(self):
        st = self.r.read(paint_test_frame(FULL, version=4))
        self.assertFalse(st.ok)
        self.assertIn("v0.4", st.reason)
        self.assertIn("v0.7", st.reason)

    def test_v05_v06_still_accepted_v07_default(self):
        self.assertTrue(self.r.read(paint_test_frame(FULL, version=5)).ok)
        st6 = self.r.read(paint_test_frame(FULL, version=6))
        self.assertTrue(st6.ok)
        self.assertEqual((st6.marker_state, st6.facing_count), ("unknown", None))   # 旧插件没有这两块
        st = self.r.read(paint_test_frame(FULL))
        self.assertTrue(st.ok)
        self.assertEqual(st.version, 7)

    def test_v07_marker_and_counter_blocks(self):
        for mk, name in (("green", "shown"), ("red", "no_plate"), ("magenta", "error"), ("black", "none")):
            self.assertEqual(self.r.read(paint_test_frame(FULL, marker=mk)).marker_state, name)
        for n in range(8):
            st = self.r.read(apply_gamma(paint_test_frame(FULL, facing_count=n), 1.2))
            self.assertEqual(st.facing_count, n)

    def test_hp_source(self):
        self.assertEqual(self.r.read(paint_test_frame(FULL)).hp_source, "bar")

    def test_aggro_block(self):
        self.assertTrue(self.r.read(paint_test_frame(FULL)).aggro)
        self.assertFalse(self.r.read(paint_test_frame({**FULL, 13: "black"})).aggro)

    def test_combat_ignores_binary_percent(self):
        f = paint_test_frame(FULL, bin_pct={"hp": 90})           # 防御：战斗中二进制万一有值也不用
        self.assertEqual(self.r.read(f).player_hp, 100)

    def test_simons_v03_pixels_are_rejected_not_misread(self):
        # 实机 v0.3 + 伽马 1.2：版本块不存在 → 明确报“版本不对”，回退截图识别
        f = paint_test_frame(FULL, beat=1, version=False)
        st = self.r.read(f)
        self.assertFalse(st.ok)
        self.assertIn("版本", st.reason)

    def test_dead_target_lootable_no_target(self):
        st = self.r.read(paint_test_frame({2: "green", 4: "white", 6: "green", 9: "black"}))
        self.assertEqual(st.target_kind, "dead")
        self.assertTrue(st.lootable)
        st = self.r.read(paint_test_frame({2: "green", 4: "yellow"}))
        self.assertEqual(st.target_kind, "tapped")
        st = self.r.read(paint_test_frame({2: "yellow"}))
        self.assertTrue(st.player_dead)                  # 灵魂
        st = self.r.read(paint_test_frame({2: "green"}))
        self.assertEqual(st.target_kind, "none")
        self.assertEqual(st.target_hp, 0)
        self.assertIsNone(st.error)

    def test_form_error_is_green(self):
        st = self.r.read(paint_test_frame({2: "green", 8: "green"}))
        self.assertEqual(st.error, "form")

    def test_heartbeat_stale(self):
        f = paint_test_frame(FULL, beat=5)
        self.assertTrue(self.r.read(f).ok)
        self.clock.t = 2.0
        self.assertTrue(self.r.read(f).ok)
        self.clock.t = 3.5
        st = self.r.read(f)
        self.assertFalse(st.ok)
        self.assertIn("心跳", st.reason)
        self.clock.t = 3.6
        self.assertTrue(self.r.read(paint_test_frame(FULL, beat=6)).ok)

    def test_missing_and_error(self):
        self.assertFalse(self.r.read(np.zeros((200, 400, 3), np.uint8)).ok)
        f = paint_test_frame(FULL)
        f[0:10, 0:10] = (0, 0, 255)  # BGR 纯红 = 插件报错
        st = self.r.read(f)
        self.assertFalse(st.ok)
        self.assertIn("报错", st.reason)

    def test_color_noise_tolerated(self):
        f = paint_test_frame(FULL, beat=1, pos=(10, 20), facing=1.0, map_id=7).astype(int)
        f = np.clip(f + np.random.default_rng(0).integers(-40, 41, f.shape), 0, 255).astype(np.uint8)
        st = self.r.read(f)
        self.check_full(st)
        self.assertAlmostEqual(st.pos[0], 10, places=2)

    def test_location_unavailable(self):
        st = self.r.read(paint_test_frame(FULL, beat=2))
        self.assertIsNone(st.pos)
        self.assertIsNone(st.facing)
        self.assertIsNone(st.map_id)

    def test_detector_prefers_addon_then_falls_back(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["regions"]["nameplates"] = [0, 0, 0, 0]
        det = Detector(cfg)
        frame = np.zeros((1080, 1920, 3), np.uint8)
        frame[:200, :400] = paint_test_frame(FULL, beat=9, pos=(10, 20), facing=1.0, map_id=7)
        o = det.observe(apply_gamma(frame, 1.2))
        self.assertEqual(o.source, "addon")
        self.assertEqual(o.target_kind, "enemy")
        self.assertTrue(o.in_combat)
        self.assertAlmostEqual(o.pos[0], 10, places=2)
        self.assertEqual(o.map_id, 7)
        self.assertIn("坐标 10.00,20.00", o.summary())
        self.assertIn("普通攻击距离", o.summary())
        self.assertIn("上次伤害", o.summary())
        self.assertIn("伽马", o.summary())
        o2 = det.observe(np.zeros((1080, 1920, 3), np.uint8))
        self.assertEqual(o2.source, "screen")
        self.assertIn("心跳", o2.addon_reason)
        cfg["addon"]["enabled"] = False
        self.assertEqual(Detector(cfg).observe(frame).source, "screen")

    def test_field_layout_fits(self):
        ends = {}
        for name, (row, start, nbits) in FIELDS.items():
            ends.setdefault(row, []).append((start, start + math.ceil((1 + nbits + 4) / 3)))
        for row, spans in ends.items():
            spans.sort()
            for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
                self.assertLessEqual(a1, b0)
        self.assertLessEqual(max(e for _, e in ends[1]), 23)  # 23=版本块 24=校准块


if __name__ == "__main__":
    unittest.main()


class TestV824ReadPosition(unittest.TestCase):
    """v8.24（t146u）：F7/F8/F9 共用读坐标，读坏一帧会重试，失败写原因。"""
    def setUp(self):
        self.cfg = copy.deepcopy(DEFAULT_CONFIG)

    def frames(self, *kinds):
        good = paint_test_frame(FULL, beat=3, pos=(57.88, 53.93), facing=1.0, map_id=1413)
        nil = paint_test_frame(FULL, beat=3, pos=None, facing=1.0, map_id=1413)
        r = AddonReader(self.cfg, clock=Clock())
        x0, y0, bs = r._geom()
        from bot.addon_reader import BIN_TOP
        bad = good.copy()
        cx, cy = x0 + 2 * bs + bs // 2, y0 + BIN_TOP + bs // 2       # 坐标 x 的第 3 块：翻一个通道 → 校验不对
        bad[cy - 2:cy + 3, cx - 2:cx + 3, 2] = 255 - bad[cy, cx, 2]
        m = {"good": good, "bad": bad, "nil": nil}
        seq = [m[k] for k in kinds]
        it = iter(seq)
        return r, (lambda: next(it))

    def test_retry_after_bad_frame(self):
        r, grab = self.frames("bad", "bad", "good")
        st, why = r.read_position(grab, tries=6, sleep=lambda s: None)
        self.assertEqual(why, "")
        self.assertAlmostEqual(st.pos[0], 57.88, places=2)

    def test_reason_bad(self):
        r, grab = self.frames("bad", "bad", "bad")
        st, why = r.read_position(grab, tries=3, sleep=lambda s: None)
        self.assertIsNone(st.pos)
        self.assertIn("校验不对", why)

    def test_reason_nil(self):
        r, grab = self.frames("nil", "nil")
        st, why = r.read_position(grab, tries=2, sleep=lambda s: None)
        self.assertIn("没有坐标", why)


class TestV824HotkeysIdle(unittest.TestCase):
    """GUI 没在打怪时：F9 能读坐标，F7 也一定能（同一个读法）。不写真的 config.json。"""
    def test_f7_uses_same_read_as_f9(self):
        if not sys.platform.startswith("linux") or not os.environ.get("DISPLAY"):
            self.skipTest("只在测试机的虚拟屏幕上跑（不在玩家电脑上开第二个界面/全局热键）")
        try:
            import tkinter as tk
            root = tk.Tk()
            root.destroy()
        except Exception:
            self.skipTest("没有图形界面")
        import gui.app as A
        import bot.capture as C
        good = paint_test_frame(FULL, beat=3, pos=(57.88, 53.93), facing=1.0, map_id=1413)
        r = AddonReader(copy.deepcopy(DEFAULT_CONFIG), clock=Clock())
        x0, y0, bs = r._geom()
        from bot.addon_reader import BIN_TOP
        bad = good.copy()
        cx, cy = x0 + 2 * bs + bs // 2, y0 + BIN_TOP + bs // 2
        bad[cy - 2:cy + 3, cx - 2:cx + 3, 2] = 255 - bad[cy, cx, 2]
        seq = [good, bad, good]

        class Cap:
            def __init__(self, *a):
                pass

            def grab(self):
                return seq.pop(0) if len(seq) > 1 else seq[0]

            def close(self):
                pass

        saved = {}
        cfgs = copy.deepcopy(DEFAULT_CONFIG)
        cfgs["addon"]["stale_s"] = 1e9
        old = (C.ScreenCapture, A.load_config, A.save_config)
        C.ScreenCapture = Cap
        A.load_config = lambda *a: copy.deepcopy(cfgs)
        A.save_config = lambda c, *a: saved.update(c=copy.deepcopy(c))
        try:
            app = A.App()
            app.cfg = copy.deepcopy(cfgs)
            logs = []
            app.log = logs.append
            app._area_idle("check")
            app._area_idle("add")          # 第一帧读坏 → 重试读到
            app.destroy()
        finally:
            C.ScreenCapture, A.load_config, A.save_config = old
        self.assertTrue(any("57.88" in m for m in logs), logs)
        self.assertTrue(any("已添加中心" in m for m in logs), logs)
        self.assertEqual(saved["c"]["area"]["centers"][-1][:2], [57.88, 53.93])

"""v8.68（t264u/t265u）：点找怪标记前停稳不转视角 + 标记稳定；捡尸⇄打怪来回；刚放弃的怪不再点。"""
import copy
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_v828 import Click828, mplate  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


class V868(Click828):
    def setUp(self):
        super().setUp()
        d = DEFAULT_CONFIG["nameplate"]
        self.cfg["nameplate"].update({k: d[k] for k in ("still_click", "reject_s", "fight_first_keep_s")})
        self.pos, self.fac = (50.0, 50.0), 1.0

    def o(self, plates=(), names=(), moving=False, pos=None, fac=None, marker=None, **kw):
        x = super().o(plates=plates, names=names, moving=moving, **kw)
        x.pos = self.pos if pos is None else pos
        x.facing = self.fac if fac is None else fac
        x.marker = marker
        return x

    def tick(self, dt=0.25, **kw):
        self.clock.t += dt
        self.g.tick(self.o(**kw))

    def until_click(self, n=20, dt=0.25, **kw):
        for i in range(n):
            self.tick(dt=dt, **kw)
            if self.clicks_at():
                return i + 1
        return None


class TestDefaults(unittest.TestCase):
    def test_defaults(self):
        n = DEFAULT_CONFIG["nameplate"]
        self.assertTrue(n["still_click"])
        self.assertEqual((n["still_after_s"], n["aim_stable_px"], n["aim_max_wait_s"]), (0.8, 6, 1.0))
        self.assertEqual((n["reject_s"], n["reject_px"], n["fight_first_keep_s"]), (15.0, 90, 8.0))

    def test_gui_and_runner(self):
        with open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8") as f:
            gui = f.read()
        for k in ("still_click", "still_after_s", "aim_stable_px", "aim_max_wait_s", "reject_s", "fight_first_keep_s"):
            self.assertIn(f'("nameplate", "{k}"', gui)
        with open(os.path.join(ROOT, "bot", "runner.py"), encoding="utf-8") as f:
            self.assertIn("v8.68 点找怪标记", f.read())


class TestStill(V868):
    def test_waits_still_then_stable_then_clicks(self):
        self.tick(plates=[mplate(960, 500)])
        self.assertEqual(self.clicks_at(), [])
        t0 = self.clock.t
        n = self.until_click(plates=[mplate(960, 500)])
        self.assertIsNotNone(n)
        self.assertGreaterEqual(self.clock.t - t0, 0.8)
        self.assertEqual(self.clicks_at(), ["click(960,500)"])
        self.assertTrue(any("标记稳定" in m for m in self.logs))

    def test_turning_camera_blocks_click(self):
        for i in range(8):                                    # 一直在转：朝向每帧变 5°
            self.fac += math.radians(5)
            self.tick(plates=[mplate(960, 500)])
        self.assertEqual(self.clicks_at(), [])
        self.assertTrue(any("先停稳" in m for m in self.logs))
        t_stop = self.clock.t
        self.until_click(plates=[mplate(960, 500)])
        self.assertTrue(self.clicks_at())
        self.assertGreaterEqual(self.clock.t - t_stop, 0.8)

    def test_coords_changing_blocks_click(self):
        for i in range(8):
            self.pos = (50.0 + 0.01 * i, 50.0)
            self.tick(plates=[mplate(960, 500)])
        self.assertEqual(self.clicks_at(), [])

    def test_moving_mob_predicted_after_max_wait(self):
        self.until_click(n=4, plates=[mplate(960, 500)])        # 先停稳（不点：标记还没盯两帧）
        self.inp.history.clear()
        self.g.aim = None
        x = 700
        hit = None
        for i in range(10):
            x += 30                                           # 120 px/秒 一直在走
            self.tick(plates=[mplate(x, 500)])
            if self.clicks_at():
                hit = self.clicks_at()[0]
                break
        self.assertIsNotNone(hit)
        cx = int(hit[6:].split(",")[0])
        self.assertGreater(cx, x)                             # 点在它前面（预计位置）
        self.assertTrue(any("点预计位置" in m for m in self.logs))

    def test_lost_marker_picks_another_in_same_stop(self):
        self.until_click(n=4, plates=[mplate(960, 500), mplate(1400, 600)])
        self.inp.history.clear()
        self.g.aim = None
        self.g.last_rclick = -1e9
        self.tick(plates=[mplate(960, 500), mplate(1400, 600)])      # 盯中间那个
        self.tick(plates=[mplate(1400, 600)])                        # 它不见了 → 换另一个，不再走
        self.tick(plates=[mplate(1400, 600)])
        self.assertEqual(self.clicks_at(), ["click(1400,600)"])
        self.assertFalse(self.g.walking)

    def test_off_switch_old_behaviour(self):
        self.cfg["nameplate"]["still_click"] = False
        self.tick(plates=[mplate(960, 500)])
        self.assertEqual(self.clicks_at(), ["click(960,500)"])


class TestMissChain1413(V868):
    """14:13:34–40 回放：点空 → 右键点到地面角色自己走（点击移动）→ 点 s → 旧版 0.9 秒后又点 → 连空 3 次。
    新版：点 s 后要 坐标/朝向 不变满 0.8 秒、标记两帧稳定 才点下一下。"""
    def test_after_miss_settles_before_next(self):
        self.until_click(plates=[mplate(1351, 440)])
        self.assertEqual(len(self.clicks_at()), 1)
        self.tick(dt=0.35, plates=[mplate(1351, 440)])
        self.tick(dt=0.35, plates=[mplate(1351, 440)])               # 0.7 秒没选上 → 点空
        self.assertTrue(any("算点空" in m for m in self.logs))
        # 点到地面：角色自己往那里走，镜头在晃，标记一帧一个位置
        self.pos = (50.05, 50.0)
        self.tick(moving=True, plates=[mplate(1730, 538)])
        self.assertIn("s", self.keys())
        t_s = self.clock.t
        self.pos = (50.06, 50.0)
        self.tick(dt=0.3, plates=[mplate(1600, 400)])                # s 刚松开，还在挪
        seq = [mplate(1272, 311)] * 12
        for p in seq:
            self.tick(dt=0.3, plates=[p])
            if len(self.clicks_at()) > 1:
                break
        self.assertEqual(len(self.clicks_at()), 2)
        self.assertEqual(self.clicks_at()[-1], "click(1272,311)")
        self.assertGreaterEqual(self.clock.t - t_s, 0.8 + 0.3)        # 停稳 0.8 秒（从最后一次坐标变化算）+ 盯两帧


class TestFlipFlop1419(V868):
    """14:19:17–36 回放：有 1 具尸体没捡，看到怪 → 先打（右键点选上）→ 下一帧“要捡尸体，先清掉选中的活怪” → 再点 … 8 次。"""
    def _run(self):
        self.k["interact"] = self.k["loot"] = "`"
        self.g.pending_loot, self.g.pending_loot_t = 1, self.clock.t
        self.until_click(plates=[mplate(1027, 328)])
        self.assertEqual(len(self.clicks_at()), 1)
        self.tick(thp=100, plates=[], marker=(1027, 328))          # 选上了（活怪，没打我）
        self.tick(thp=100, plates=[], marker=(1027, 330))

    def test_no_clear_goes_to_fight(self):
        self._run()
        self.assertNotIn(self.k["clear_target"], self.keys())
        self.assertFalse(any("先清掉选中的活怪" in m for m in self.logs))
        self.assertEqual(self.g.state, State.APPROACHING)
        self.assertEqual(self.g.pending_loot, 1)                  # 尸体还记着，打完再捡

    def test_old_behaviour_flipflops(self):
        self.cfg["nameplate"]["fight_first_keep_s"] = 0
        self._run()
        self.assertTrue(any("先清掉选中的活怪" in m for m in self.logs))


class TestReject(V868):
    def test_rejected_mob_not_clicked_other_is(self):
        # 14:18:04 回放：Tab 选中的怪在追怪上限外 → 清目标；它的标记 (1220,260) 附近的 (1254,276) 不再点
        x = self.o(thp=100, marker=(1220, 260))
        self.g.last_obs = x
        self.g._clear_target(x)
        self.assertEqual(len(self.g.rejects), 1)
        for _ in range(8):
            self.tick(plates=[mplate(1254, 276)])
        self.assertEqual(self.clicks_at(), [])
        self.until_click(plates=[mplate(1254, 276), mplate(700, 450)])
        self.assertEqual(self.clicks_at(), ["click(700,450)"])

    def test_expires(self):
        x = self.o(thp=100, marker=(1220, 260))
        self.g._clear_target(x)
        self.clock.t += 16
        self.until_click(plates=[mplate(1254, 276)])
        self.assertEqual(self.clicks_at(), ["click(1254,276)"])

    def test_turn_compensation(self):
        x = self.o(thp=100, marker=(900, 400))
        self.g._clear_target(x)
        now = self.clock.t
        self.g.last_obs = self.o(fac=self.fac + math.radians(10))  # 左转 10° → 怪往右挪 ≈ 213 px
        self.assertTrue(self.g._is_rejected(1113, 400, now))
        self.assertFalse(self.g._is_rejected(900, 400, now))

    def test_dead_target_not_rejected(self):
        x = self.o(kind="dead", thp=0, marker=(900, 400))
        self.g._clear_target(x)
        self.assertEqual(self.g.rejects, [])


if __name__ == "__main__":
    unittest.main()

"""v8.57（t225u）：1. 修理路上（去修理商 + 走回来，两种模式）被怪打也不还手，接着走；血量 ≤ 40% 才还手。
2. 背包满了也走修理流程卖垃圾（插件 v0.16 卖掉所有灰色物品）；卖完 30 分钟内又满 → 不再回去，照旧只捡钱。
3. 修理路上用猎豹形态走。"""
import copy
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_v855 import RepBase, ro, TOWN, ZONE, ROOT, _rd, Lua  # noqa: E402

try:
    import lupa  # noqa: F401
except ImportError:  # pragma: no cover
    lupa = None


class Defaults(unittest.TestCase):
    def test_defaults(self):
        r = DEFAULT_CONFIG["repair"]
        self.assertTrue(r["ignore_combat"] and r["bag_trip"] and r["cat_travel"])
        self.assertEqual((r["fight_back_hp_pct"], r["bag_trip_cd_s"]), (40, 1800.0))


class WalkBase(RepBase):
    def setUp(self):
        super().setUp()
        self.cfg["repair"]["use_hearth"] = False

    def go(self, pos, mp=ZONE, **kw):
        self.t(ro(pos=pos, mp=mp, **kw))

    def start(self, **kw):
        for _ in range(8):
            self.go((42.0, 61.0), **kw)
        self.assertEqual(self.g.state, State.REPAIR)

    def to_vendor(self, **kw):
        self.go((40.0, 60.0), **kw)
        self.go((41.0, 60.0), **kw)
        self.go((80.0, 10.0), **kw)
        self.go((80.1, 9.5), **kw)
        self.go((70.0, 90.0), mp=TOWN, **kw)
        self.go((63.0, 75.0), mp=TOWN, **kw)
        self.assertEqual(self.g.rp["phase"], "vendor")


class IgnoreCombat(WalkBase):
    def test_keeps_walking_when_attacked(self):
        self.start()
        self.go((40.0, 60.0))
        self.go((40.5, 60.0), combat=True, thp=90, hp=80)
        self.assertFalse(self.g.rp["paused"])
        self.assertEqual(self.g.state, State.REPAIR)
        self.assertTrue(any("不理它，接着走" in m for m in self.logs))
        self.go((41.0, 60.0), combat=True, thp=90, hp=70)
        self.assertEqual(self.g.rp["wi"], 2)                 # 照样到点、往下走
        self.go((60.0, 30.0))
        self.assertTrue(any("脱战了" in m for m in self.logs))

    def test_low_hp_fights_back(self):
        self.start()
        self.go((40.0, 60.0))
        self.go((40.5, 60.0), combat=True, thp=90, hp=35)
        self.assertTrue(self.g.rp["paused"])
        self.assertNotEqual(self.g.state, State.REPAIR)        # 交给打怪/治疗
        self.assertTrue(any("先还手" in m for m in self.logs))
        self.g.state = State.SEARCHING                          # 打完、治疗完
        self.go((40.5, 60.0), hp=90)                          # 打完 → 接着走
        self.assertFalse(self.g.rp["paused"])

    def test_zero_line_never_fights(self):
        self.cfg["repair"]["fight_back_hp_pct"] = 0
        self.start()
        self.go((40.5, 60.0), combat=True, thp=90, hp=10)
        self.assertFalse(self.g.rp["paused"])

    def test_back_leg_also_ignores(self):
        self.start()
        self.to_vendor()
        self.go((63.0, 75.0), mp=TOWN, merchant=True)
        self.go((63.0, 75.0), mp=TOWN, merchant=True, dur=100)
        self.assertEqual(self.g.rp["phase"], "back")
        self.go((70.0, 90.0), mp=TOWN, dur=100, combat=True, thp=90, hp=90)
        self.assertFalse(self.g.rp["paused"])
        self.assertEqual(self.g.state, State.REPAIR)


class HearthIgnore(RepBase):
    def test_hearth_mode_to_vendor_ignores(self):
        self.start_cast()
        self.land()
        self.t(ro(pos=(60.2, 70.0), mp=TOWN, combat=True, thp=90, hp=90))
        self.assertFalse(self.g.rp["paused"])
        self.assertEqual(self.g.state, State.REPAIR)


class BagTrip(WalkBase):
    def test_bag_full_walks_and_sells(self):
        self.g.bag_full = True
        self.start(dur=80)
        self.assertTrue(any("🎒 背包满了" in m and "卖垃圾" in m for m in self.logs))
        self.to_vendor(dur=80)
        self.go((63.0, 75.0), mp=TOWN, dur=80)
        self.go((63.0, 75.0), mp=TOWN, dur=80, merchant=True)
        self.inp.history.clear()
        self.go((63.0, 75.0), mp=TOWN, dur=100, merchant=True)  # 还在等插件卖
        self.assertNotIn("esc", self.keys())
        for _ in range(7):
            self.go((63.0, 75.0), mp=TOWN, dur=100, merchant=True)
        self.assertIn("esc", self.keys())
        self.go((70.0, 90.0), mp=TOWN, dur=100)
        self.go((80.0, 10.0), dur=100)
        self.go((41.0, 60.0), dur=100)
        self.assertIsNone(self.g.rp)
        self.assertFalse(self.g.bag_full)
        self.assertTrue(any("卖完垃圾 → 恢复拾取" in m for m in self.logs))
        # 30 分钟内又满了 → 不再回去
        self.g.bag_full = True
        self.g.state = State.SEARCHING
        for _ in range(10):
            self.go((42.0, 61.0), dur=100)
        self.assertIsNone(self.g.rp)
        self.clock.t += 1800
        for _ in range(8):
            self.go((42.0, 61.0), dur=100)
        self.assertIsNotNone(self.g.rp)

    def test_non_repair_merchant_ok_for_bag(self):
        self.g.bag_full = True
        self.start(dur=80)
        self.to_vendor(dur=80)
        self.go((63.0, 75.0), mp=TOWN, dur=80)
        for _ in range(8):
            self.go((63.0, 75.0), mp=TOWN, dur=100, merchant=True, can_repair=False)
        self.assertFalse(self.g.stopped)
        self.assertEqual(self.g.rp["phase"], "back")

    def test_off(self):
        self.cfg["repair"]["bag_trip"] = False
        self.g.bag_full = True
        for _ in range(10):
            self.go((42.0, 61.0), dur=80)
        self.assertIsNone(self.g.rp)

    def test_missing_config_no_stop(self):
        self.k["vendor_target"] = ""
        self.g.bag_full = True
        for _ in range(10):
            self.go((42.0, 61.0), dur=80)
        self.assertIsNone(self.g.rp)
        self.assertFalse(self.g.stopped)
        self.assertTrue(any("背包满了，但修理路线/键没设置好" in m for m in self.logs))


class BagHearth(RepBase):
    def test_hearth_cd_falls_back(self):
        self.g.bag_full = True
        for _ in range(10):
            self.t(ro(dur=80, cd=30))
        self.assertIsNone(self.g.rp)
        self.assertFalse(self.g.stopped)
        self.assertNotIn("h", self.keys())
        self.assertTrue(any("先照旧只捡钱" in m for m in self.logs))

    def test_hearth_ready_casts(self):
        self.g.bag_full = True
        for _ in range(8):
            self.t(ro(dur=80))
        self.assertIn("h", self.keys())
        self.assertEqual(self.g.rp["why"], "bag")


class Cat(WalkBase):
    def setUp(self):
        super().setUp()
        self.k["cat_form"] = "c"

    def test_shifts_to_cat_on_the_way(self):
        self.start(form="caster")
        self.assertEqual(self.keys().count("c"), 1)
        self.go((40.0, 60.0), form="caster")
        self.go((40.5, 60.0), form="caster")                  # 3 秒内不连按
        self.assertEqual(self.keys().count("c"), 1)
        t0 = self.clock.t
        for _ in range(6):
            self.go((40.6, 60.0), form="caster")
        n = self.keys().count("c")
        self.assertGreaterEqual(n, 2)                           # 还不是猎豹 → 过 3 秒再按
        self.assertLessEqual(n, 1 + int((self.clock.t - t0) / 3.0) + 1)
        self.assertTrue(any("变猎豹" in m for m in self.logs))

    def test_no_press_when_cat_or_unknown(self):
        self.start(form="cat")
        self.inp.history.clear()
        self.go((40.0, 60.0), form="cat")
        self.go((40.2, 60.0), form="unknown")
        self.assertNotIn("c", self.keys())

    def test_off(self):
        self.cfg["repair"]["cat_travel"] = False
        self.start(form="caster")
        self.go((40.0, 60.0), form="caster")
        self.go((40.5, 60.0), form="caster")
        self.assertNotIn("c", self.keys())


@unittest.skipIf(lupa is None, "没有 lupa")
class LuaSellAll(unittest.TestCase):
    API = Lua.API.replace(
        """GetContainerNumSlots = function(b) if b == 0 then return 3 end return 0 end,""",
        """GetContainerNumSlots = function(b) if b <= 1 then return 16 end return 0 end,""").replace(
        """GetContainerItemInfo = function(b, s) if s == 2 then return { quality = 0, hasNoValue = false } end
                                               if s == 3 then return { quality = 2 } end return nil end,""",
        """GetContainerItemInfo = function(b, s) if s == 3 then return { quality = 2 } end
                                               if SOLDSET[b * 100 + s] then return nil end
                                               return { quality = 0, hasNoValue = false } end,""").replace(
        """UseContainerItem = function(b, s) SOLD[#SOLD + 1] = s end,""",
        """UseContainerItem = function(b, s) SOLD[#SOLD + 1] = s; SOLDSET[b * 100 + s] = true end,""") + """
      SOLDSET = {}
      PENDING = {}
      C_Timer = { After = function(d, f) PENDING[#PENDING + 1] = f end }
      function RUNPENDING() local i = 1 while PENDING[i] do PENDING[i](); i = i + 1 end return i - 1 end
    """
    run_addon = Lua.run_addon

    def test_sells_all_greys_in_batches(self):
        self.assertIn("SOLDSET", self.API)
        g, st = self.run_addon(events=["MERCHANT_SHOW"])
        self.assertEqual(len(list(g.SOLD.values())), 12)        # 第一批 12 个
        n = g.RUNPENDING()
        self.assertEqual(n, 2)                                   # 再两批
        self.assertEqual(len(list(g.SOLD.values())), 30)        # 32 格里 2 格绿色，30 个灰色全卖
        self.assertEqual(g.REPAIRED, 1)

    def test_stops_when_closed(self):
        g, st = self.run_addon(events=["MERCHANT_SHOW", "MERCHANT_CLOSED"])
        g.RUNPENDING()
        self.assertEqual(len(list(g.SOLD.values())), 12)


class Strings(unittest.TestCase):
    def test_files(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertRegex(app, r"熊德自动打怪 v8\.(5[7-9]|[6-9]\d)")
        for k in ("ignore_combat", "fight_back_hp_pct", "bag_trip", "cat_travel"):
            self.assertIn(f'"{k}"', app)
        self.assertTrue(re.search(r"v8\.57.*无视", _rd(os.path.join(ROOT, "README_中文.md"))))
        self.assertIn("v8.57", _rd(os.path.join(ROOT, "bot", "runner.py")))
        self.assertIn("## Version: 0.16.0", _rd(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.toc")))
        self.assertIn("C_Timer.After(0.3, SellBatch)", _rd(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.lua")))


if __name__ == "__main__":
    unittest.main()

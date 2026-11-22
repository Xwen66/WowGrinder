"""v8.55（t222u）：A. 炉石回城修理（插件 v0.15 报告 最低耐久 / 炉石冷却 / 商人窗口，打开商人自动全部修理）：
耐久低 → 炉石 → 照修理路线走到修理商（F5 记点、F4 标修理商）→ 选修理商 + 互动 → 修好 → 照路线走回刷怪区 → 回中心；
任何一步失败 → 停止 + 截图。B. 跳过的中心：到了任何一个中心就全部恢复。"""
import copy
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.addon_reader import AddonReader, paint_test_frame  # noqa: E402
from bot.config import DEFAULT_CONFIG, key_conflicts  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOWN, ZONE = 1453, 1429
GRIND = (40.0, 60.0)
# 路线：#1 炉石落点、#2 路上、#3 修理商（城里）、#4 出城前、#5 进野外后、#6 中心附近
ROUTE = [[60.0, 70.0, TOWN], [62.0, 72.0, TOWN], [63.0, 75.0, TOWN], [70.0, 90.0, TOWN],
         [80.0, 10.0, ZONE], [41.0, 60.0, ZONE]]


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def ro(pos=GRIND, mp=ZONE, dur=15.0, cd=0, hearth=True, merchant=False, can_repair=True, repinfo=True, **kw):
    kw.setdefault("form", "cat")
    kw.setdefault("mana", 80)
    o = obs(**kw)
    o.pos, o.map_id, o.facing = pos, mp, 0.0
    o.repinfo, o.durability, o.hearth_cd, o.has_hearth = repinfo, dur, (cd if hearth else None), hearth
    o.merchant_open, o.merchant_repair = merchant, can_repair
    return o


class Reader(unittest.TestCase):
    def read(self, img):
        return AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(img)

    def test_v015_blocks(self):
        st = self.read(paint_test_frame({4: "red"}, size=(200, 500),
                                        rep={"dur": 17, "cd": 42, "merchant": True, "can_repair": True}))
        self.assertTrue(st.ok, st.reason)
        self.assertTrue(st.repinfo)
        self.assertEqual((st.durability, st.hearth_cd, st.has_hearth), (17.0, 42, True))
        self.assertEqual((st.merchant_open, st.merchant_repair), (True, True))
        st = self.read(paint_test_frame({4: "red"}, size=(200, 500), rep={"dur": None, "hearth": False}))
        self.assertTrue(st.repinfo)
        self.assertIsNone(st.durability)
        self.assertIsNone(st.hearth_cd)
        self.assertFalse(st.has_hearth or st.merchant_open)

    def test_old_addon(self):
        st = self.read(paint_test_frame({4: "red"}, size=(200, 500), eat={"wf": "black"}))
        self.assertTrue(st.ok, st.reason)
        self.assertFalse(st.repinfo)
        self.assertIsNone(st.durability)


try:
    import lupa  # noqa: F401
    import numpy as np
    import test_addon_lua as tal
except ImportError:
    lupa = None


@unittest.skipIf(lupa is None, "没有 lupa")
class Lua(unittest.TestCase):
    """真的跑 BotStatus.lua v0.15。"""
    API = """
      DUR = { [1] = {50, 100}, [5] = {13, 80}, [16] = {90, 90} }
      function GetInventoryItemDurability(s) local d = DUR[s]; if d then return d[1], d[2] end return nil end
      function GetItemCount(id) return id == 6948 and HSCOUNT or 0 end
      function GetItemCooldown(id) return HSSTART, HSDUR, 1 end
      REPAIRED, SOLD = 0, {}
      function CanMerchantRepair() return true end
      function GetRepairAllCost() return 12345, true end
      function GetMoney() return MONEY end
      function RepairAllItems() REPAIRED = REPAIRED + 1 end
      C_Container = {
        GetContainerNumSlots = function(b) if b == 0 then return 3 end return 0 end,
        GetContainerItemInfo = function(b, s) if s == 2 then return { quality = 0, hasNoValue = false } end
                                               if s == 3 then return { quality = 2 } end return nil end,
        UseContainerItem = function(b, s) SOLD[#SOLD + 1] = s end,
      }
      function print() end
    """

    def run_addon(self, count=1, start=0, dur=0, money=99999, events=()):
        L = lupa.LuaRuntime()
        L.execute(tal.STUBS)
        L.execute(self.API)
        g = L.globals()
        g.POSX, g.POSY, g.FACING, g.MAPID = 0.4, 0.6, 1.0, 1429
        g.HASTARGET, g.TDEAD, g.COMBAT = False, False, False
        g.HSCOUNT, g.HSSTART, g.HSDUR, g.MONEY = count, start, dur, money
        with open(tal.LUA, encoding="utf-8") as f:
            L.execute(f.read())
        frame = g.BotStatusFrame
        frame.script_OnEvent(frame, "PLAYER_ENTERING_WORLD")
        for ev in events:
            frame.script_OnEvent(frame, ev)
        g.NOW = 103
        frame.script_OnUpdate(frame, 1.0)
        img = np.zeros((200, 500, 3), np.uint8)
        for t in g.TEXTURES.values():
            if not all(isinstance(v, (int, float)) for v in (t.px, t.py, t.w, t.h)) or t.rgb is None:
                continue
            x, y = int(t.px), int(-t.py)
            r, gg, b = (int(round(t.rgb[i] * 255)) for i in (1, 2, 3))
            img[y:y + int(t.h), x:x + int(t.w)] = (b, gg, r)
        return g, AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(img)

    def test_durability_and_hearth(self):
        g, st = self.run_addon()
        self.assertTrue(st.ok, st.reason)
        self.assertTrue(st.repinfo)
        self.assertEqual(st.durability, 16.0)              # 13/80 = 16.25%
        self.assertEqual((st.has_hearth, st.hearth_cd), (True, 0))
        g, st = self.run_addon(start=90, dur=3600)           # NOW=103 → 还剩 3587 秒 = 60 分钟
        self.assertEqual(st.hearth_cd, 60)
        g, st = self.run_addon(count=0)
        self.assertFalse(st.has_hearth)
        self.assertIsNone(st.hearth_cd)

    def test_merchant_repairs_and_sells_grey(self):
        g, st = self.run_addon(events=["MERCHANT_SHOW"])
        self.assertEqual(g.REPAIRED, 1)
        self.assertEqual(list(g.SOLD.values()), [2])        # 只卖灰色
        self.assertTrue(st.merchant_open and st.merchant_repair)
        g, st = self.run_addon(money=100, events=["MERCHANT_SHOW"])
        self.assertEqual(g.REPAIRED, 0)                     # 钱不够不修
        g, st = self.run_addon(events=["MERCHANT_SHOW", "MERCHANT_CLOSED"])
        self.assertFalse(st.merchant_open)


class Config(unittest.TestCase):
    def test_defaults(self):
        r, k = DEFAULT_CONFIG["repair"], DEFAULT_CONFIG["keys"]
        self.assertTrue(r["enabled"])
        self.assertEqual((r["durability_pct"], r["stop_pct"], r["vendor_i"], r["route"]), (20, 5, -1, []))
        self.assertEqual((k["hearth"], k["vendor_target"], k["hotkey_add_repair"], k["hotkey_repair_vendor"]),
                         ("", "", "f5", "f4"))

    def test_key_conflicts(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["keys"].update(hearth="t")                      # t = 野性印记
        self.assertTrue(any("炉石" in c for c in key_conflicts(cfg)))


class RepBase(Base):
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.cfg["eat"]["enabled"] = False
        self.k.update(hearth="h", vendor_target="n", interact="`")
        self.cfg["repair"].update(route=copy.deepcopy(ROUTE), vendor_i=2)
        self.cfg["area"].update(enabled=True, radius=1.5, centers=[[40.0, 60.0, ZONE], [45.0, 60.0, ZONE]])
        self.g.centers = [(40.0, 60.0, ZONE), (45.0, 60.0, ZONE)]
        self.g.home = self.g.centers[0]
        self.g.state = State.SEARCHING

    def t(self, o, dt=0.5):
        self.g.tick(o)
        self.clock.t += dt

    def start_cast(self):
        for _ in range(8):                                  # 3 秒确认
            self.t(ro())
        self.assertEqual(self.g.state, State.REPAIR)
        self.assertIn("h", self.keys())

    def land(self):
        self.t(ro(casting=True))
        self.clock.t += 10
        self.t(ro(casting=False))
        self.assertEqual(self.g.rp["phase"], "load")
        self.t(ro(pos=(60.05, 70.0), mp=TOWN, form="caster"))   # 落地：换地图
        self.assertEqual(self.g.rp["phase"], "to_vendor")

    def walk_to_vendor(self):
        for p in ROUTE[:3]:
            self.t(ro(pos=(p[0] + 0.05, p[1]), mp=TOWN, form="caster"))
        self.assertEqual(self.g.rp["phase"], "vendor")


class Flow(RepBase):
    def test_full_flow(self):
        self.start_cast()
        self.assertTrue(any("炉石回城修理" in m for m in self.logs))
        self.land()
        self.assertTrue(any("炉石到了" in m for m in self.logs))
        self.walk_to_vendor()
        self.inp.history.clear()
        self.t(ro(pos=(63.0, 75.0), mp=TOWN, form="caster"))
        self.assertEqual(self.keys()[:2], ["n", "`"])        # 选修理商 + 互动
        self.t(ro(pos=(63.0, 75.0), mp=TOWN, form="caster", merchant=True))
        self.assertEqual(self.g.rp["phase"], "repairing")
        self.inp.history.clear()
        self.t(ro(pos=(63.0, 75.0), mp=TOWN, form="caster", merchant=True, dur=100))
        self.assertIn("esc", self.keys())
        self.assertEqual(self.g.rp["phase"], "back")
        self.t(ro(pos=(70.0, 90.0), mp=TOWN, form="caster", dur=100))     # 出城前的点
        self.t(ro(pos=(70.2, 90.6), mp=TOWN, form="caster", dur=100))     # 下一个点在野外 → 往前走
        self.assertTrue(any("下一个点在地图 1429" in m for m in self.logs))
        self.t(ro(pos=(80.0, 10.0), mp=ZONE, form="caster", dur=100))     # 进野外
        self.assertTrue(self.g.rp is not None and self.g.rp["phase"] == "back")
        self.t(ro(pos=(41.0, 60.0), mp=ZONE, form="caster", dur=100))     # 中心附近
        self.assertIsNone(self.g.rp)
        self.assertEqual(self.g.state, State.RETURNING)
        self.assertEqual(self.g.ci, 0)
        self.assertTrue(any("回城修理完成" in m for m in self.logs))
        self.assertFalse(self.g.stopped)

    def test_attacked_on_way_back_fights_then_resumes(self):
        self.cfg["repair"]["ignore_combat"] = False         # v8.57：老行为（被打就还手）
        self.start_cast()
        self.land()
        self.walk_to_vendor()
        self.t(ro(pos=(63.0, 75.0), mp=TOWN, form="caster"))
        self.t(ro(pos=(63.0, 75.0), mp=TOWN, form="caster", merchant=True))
        self.t(ro(pos=(63.0, 75.0), mp=TOWN, form="caster", merchant=True, dur=100))
        self.t(ro(pos=(70.0, 90.0), mp=TOWN, form="caster", dur=100))
        self.t(ro(pos=(80.0, 10.0), mp=ZONE, combat=True, thp=90, dur=100))
        self.assertTrue(self.g.rp["paused"])
        self.assertTrue(any("修理路上被打了" in m for m in self.logs))
        self.g.state = State.SEARCHING
        self.t(ro(pos=(80.0, 10.0), mp=ZONE, dur=100))
        self.assertFalse(self.g.rp["paused"])
        self.assertTrue(any("接着走修理路线" in m for m in self.logs))

    def test_hit_while_casting_retries_later(self):
        self.start_cast()
        self.t(ro(casting=True))
        self.t(ro(combat=True, thp=90))
        self.assertIsNone(self.g.rp)
        self.assertEqual(self.g.rp_attempts, 1)
        self.assertTrue(any("读炉石时被打了" in m for m in self.logs))


class Guards(RepBase):
    def test_no_trigger_above_line_or_in_combat(self):
        for _ in range(10):
            self.t(ro(dur=25))
        self.assertIsNone(self.g.rp)
        for _ in range(10):
            self.t(ro(combat=True, thp=90))
        self.assertIsNone(self.g.rp)
        self.assertNotIn("h", self.keys())

    def test_cooldown_keeps_grinding_then_stops(self):
        for _ in range(10):
            self.t(ro(cd=40))
        self.assertIsNone(self.g.rp)
        self.assertNotIn("h", self.keys())
        self.assertTrue(any("炉石还要 40 分钟" in m for m in self.logs))
        self.assertFalse(self.g.stopped)
        for _ in range(10):
            self.t(ro(cd=40, dur=4))
        self.assertTrue(self.g.stopped)
        self.assertIn("炉石还在冷却", self.g.stop_reason)

    def test_not_configured(self):
        self.k["vendor_target"] = ""
        for _ in range(10):
            self.t(ro())
        self.assertIsNone(self.g.rp)
        self.assertTrue(any("没设置好" in m and "选修理商键" in m for m in self.logs))
        self.k["vendor_target"] = "n"
        self.cfg["repair"]["vendor_i"] = 5                   # 修理商后面没有回程点
        self.assertIn("修理商之后走回刷怪区的路线点", self.g._rp_missing())

    def test_old_addon_never_triggers(self):
        for _ in range(10):
            self.t(ro(repinfo=False))
        self.assertIsNone(self.g.rp)
        self.assertTrue(any("插件 v0.15" in m for m in self.logs))

    def test_no_cast_stops(self):
        for _ in range(30):
            self.t(ro(casting=False))
        self.assertTrue(self.g.stopped)
        self.assertIn("都没开始读条", self.g.stop_reason)
        self.assertEqual(self.keys().count("h"), 3)

    def test_wrong_landing_spot_stops(self):
        self.start_cast()
        self.t(ro(casting=True))
        self.clock.t += 10
        self.t(ro(casting=False))
        self.t(ro(pos=(10.0, 10.0), mp=TOWN, form="caster"))
        self.assertTrue(self.g.stopped)
        self.assertIn("位置不对", self.g.stop_reason)

    def test_wrong_map_stops(self):
        self.start_cast()
        self.t(ro(casting=True))
        self.clock.t += 10
        self.t(ro(casting=False))
        self.t(ro(pos=(60.0, 70.0), mp=9999, form="caster"))
        self.assertTrue(self.g.stopped)
        self.assertIn("没有当前地图", self.g.stop_reason)

    def test_merchant_never_opens_stops(self):
        self.start_cast()
        self.land()
        self.walk_to_vendor()
        for _ in range(40):
            self.t(ro(pos=(63.0, 75.0), mp=TOWN, form="caster"))
        self.assertTrue(self.g.stopped)
        self.assertIn("商人窗口都没开", self.g.stop_reason)
        self.assertEqual(self.keys().count("n"), 3)

    def test_not_repaired_stops(self):
        self.start_cast()
        self.land()
        self.walk_to_vendor()
        self.t(ro(pos=(63.0, 75.0), mp=TOWN, form="caster"))
        for _ in range(20):
            self.t(ro(pos=(63.0, 75.0), mp=TOWN, form="caster", merchant=True))
        self.assertTrue(self.g.stopped)
        self.assertIn("钱不够", self.g.stop_reason)

    def test_stuck_too_often_stops(self):
        self.start_cast()
        self.land()
        self.cfg["repair"]["stuck_max"] = 2
        from bot.navigation import bearing_to
        for _ in range(60):
            o = ro(pos=(60.05, 70.0), mp=TOWN, form="caster")
            o.facing = bearing_to((60.05, 70.0), (62.0, 72.0))
            self.t(o)
        self.assertTrue(self.g.stopped)
        self.assertIn("卡住", self.g.stop_reason)

    def test_died_cancels(self):
        self.start_cast()
        self.land()
        self.cfg["corpse"]["enabled"] = False
        self.t(ro(pos=(60.0, 70.0), mp=TOWN, dead=True, hp=0))
        self.assertIsNone(self.g.rp)


class Record(RepBase):
    def test_f5_f4(self):
        self.cfg["repair"].update(route=[], vendor_i=-1)
        got = []
        self.g.on_repair_route = lambda rs, vi: got.append((rs, vi))
        self.g.request_area("rroute")
        self.g._route_tick(ro(pos=(60.0, 70.0), mp=TOWN), self.clock.t)
        self.g.request_area("rvendor")
        self.g._route_tick(ro(pos=(63.0, 75.0), mp=TOWN), self.clock.t)
        self.g.request_area("rroute")
        self.g._route_tick(ro(pos=(41.0, 60.0), mp=ZONE), self.clock.t)
        self.assertEqual(self.cfg["repair"]["route"], [[60.0, 70.0, TOWN], [63.0, 75.0, TOWN], [41.0, 60.0, ZONE]])
        self.assertEqual(self.cfg["repair"]["vendor_i"], 1)
        self.assertEqual(got[-1][1], 1)
        self.assertTrue(any("炉石落点" in m for m in self.logs))
        self.assertTrue(any("已标记修理商" in m for m in self.logs))
        self.assertEqual(self.cfg["corpse"]["route"], [])     # 跑尸路线不动


class CenterReset(Base):
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["area"].update(enabled=True, center_skip_fails=2)
        self.g.centers = [(10.0, 10.0, 0), (20.0, 20.0, 0), (30.0, 30.0, 0)]
        self.g.ci, self.g.cdir, self.g.travel = 1, 1, True
        self.g.home = self.g.centers[1]

    def o(self, pos=(15.0, 15.0)):
        o = obs()
        o.pos, o.facing, o.map_id = pos, 0.0, 0
        return o

    def fail(self, i):
        self.g.ci, self.g.home, self.g.travel = i, self.g.centers[i], True
        self.g.patrol_skips = 0
        self.g._return_give_up(self.o(), self.clock.t, "的路上卡住了（跳 + 平移 2 次都没用）")
        self.clock.t += 1

    def test_any_arrival_restores(self):
        self.fail(1); self.fail(1)
        self.assertIn(1, self.g.center_skip)
        self.g.ci, self.g.home, self.g.travel = 2, self.g.centers[2], True
        self.g.state = State.RETURNING
        self.g._return(self.o((30.0, 30.0)), self.clock.t)
        self.assertEqual(self.g.center_skip, set())
        self.assertEqual(self.g.center_fails, {})
        self.assertTrue(any("到了中心 #3 → 之前跳过的中心 #2 恢复" in m for m in self.logs))
        self.g.ci, self.g.cdir = 2, -1
        self.assertEqual(self.g._next_center(self.o())[0], 1)        # #2 又能去了

    def test_auto_switch_restores(self):
        self.fail(1); self.fail(1)
        self.g._center_reached(0)
        self.assertEqual(self.g.center_skip, set())


class Strings(unittest.TestCase):
    def test_files(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertRegex(app, r"熊德自动打怪 v8\.(5[5-9]|[6-9]\d)")
        for k in ("hearth", "vendor_target", "hotkey_add_repair", "hotkey_repair_vendor", "durability_pct", "stop_pct"):
            self.assertIn(f'"{k}"', app)
        self.assertIn("v8.55", _rd(os.path.join(ROOT, "README_中文.md")))
        self.assertIn("v8.55", _rd(os.path.join(ROOT, "bot", "runner.py")))
        lua = _rd(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.lua"))
        self.assertIn("local NUM_BLOCKS = 44", lua)
        self.assertIn("RepairAllItems", lua)
        self.assertRegex(_rd(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.toc")), r"## Version: 0\.1[5-9]\.0")


if __name__ == "__main__":
    unittest.main()

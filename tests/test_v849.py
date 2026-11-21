"""v8.49：背包满只捡钱后不按 Esc；自动进食（插件 v0.14 报告“充分进食”buff 和是否正在进食）。"""
import copy
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.addon_reader import AddonReader, paint_test_frame  # noqa: E402
from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = (50.0, 50.0)


class Defaults(unittest.TestCase):
    def test_defaults(self):
        e = DEFAULT_CONFIG["eat"]
        self.assertTrue(e["enabled"])
        self.assertEqual(e["key"], "")
        self.assertEqual((e["min_s"], e["max_s"], e["retry_min"]), (11, 30, 30))
        self.assertNotIn("full_bag_close_key", DEFAULT_CONFIG["loot"])
        self.assertTrue(DEFAULT_CONFIG["loot"]["full_bag_money_only"])


class Reader(unittest.TestCase):
    def read(self, img):
        return AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(img)

    def test_v014_blocks(self):
        for wf, want in (("green", "yes"), ("yellow", "low"), ("black", "no"), ("magenta", "unknown")):
            st = self.read(paint_test_frame({4: "red"}, size=(200, 500), eat={"wf": wf, "eating": False}))
            self.assertTrue(st.ok, st.reason)
            self.assertTrue(st.eatinfo)
            self.assertEqual(st.well_fed, want)
            self.assertIs(st.eating, False)
        st = self.read(paint_test_frame({4: "red"}, size=(200, 500), eat={"wf": "black", "eating": True}))
        self.assertIs(st.eating, True)
        st = self.read(paint_test_frame({4: "red"}, size=(200, 500), eat={"wf": "black", "eating": False, "readable": False}))
        self.assertIsNone(st.eating)

    def test_old_addon_no_eatinfo(self):
        st = self.read(paint_test_frame({4: "red"}, tinfo={}))
        self.assertTrue(st.ok, st.reason)
        self.assertFalse(st.eatinfo)
        st = self.read(paint_test_frame({4: "red"}))
        self.assertFalse(st.eatinfo)


def o(pos=P, wf="no", eating=False, eatinfo=True, **kw):
    kw.setdefault("form", "cat")
    ob = obs(**kw)
    ob.pos = pos
    ob.eatinfo, ob.well_fed, ob.eating = eatinfo, wf, eating
    return ob


class Eat(Base):
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["cat"]["combat_form"] = "猎豹"
        self.cfg["keys"]["cat_form"] = "f2"
        self.cfg["keys"]["cancel_form"] = "f2"
        self.cfg["eat"]["key"] = "0"
        self.clock.t = 2000.0

    def ticks(self, n=1, dt=0.5, **kw):
        for _ in range(n):
            self.g.tick(o(**kw))
            self.clock.t += dt

    def eats(self):
        return self.keys().count("0")

    def test_full_flow_until_buff(self):
        self.ticks(form="cat")                                  # 开始 → 退出猎豹
        self.assertIsNotNone(self.g.eat)
        self.assertIn("f2", self.keys())
        self.ticks(form="caster")                               # 人形 → 按进食键
        self.assertEqual(self.eats(), 1)
        self.ticks(n=10, form="caster", eating=True)            # 原地吃，不再按、不动
        self.assertEqual(self.eats(), 1)
        self.assertFalse(any(k.startswith("click") for k in self.keys()))
        self.assertIn("进食", self.g.status_label())
        self.ticks(form="caster", eating=True, wf="yes")         # buff 出来了但才吃 5 秒 → 吃满 11 秒
        self.assertIsNotNone(self.g.eat)
        self.clock.t += 7
        n = self.keys().count("f2")
        self.ticks(form="caster", eating=True, wf="yes")         # 够 11 秒 + buff → 结束，变回猎豹
        self.assertIsNone(self.g.eat)
        self.assertEqual(self.keys().count("f2"), n + 1)
        self.assertTrue(any("充分进食" in m for m in self.logs))
        n = len(self.keys())
        self.ticks(n=3, form="cat", wf="yes")                    # 有 buff → 不再吃
        self.assertIsNone(self.g.eat)
        self.assertEqual(self.eats(), 1)

    def test_min_seconds(self):
        self.ticks(form="cat")
        self.ticks(form="caster")
        self.ticks(n=6, dt=1.0, form="caster", eating=True)    # 吃 6 秒后停了（还没 buff）→ 再按一次
        self.ticks(form="caster", eating=False)
        self.assertIsNotNone(self.g.eat)
        self.ticks(form="caster")
        self.assertEqual(self.eats(), 2)
        self.ticks(n=12, dt=1.0, form="caster", eating=True)   # 吃够 11 秒后停 → 结束
        self.ticks(form="caster", eating=False)
        self.assertIsNone(self.g.eat)
        self.assertEqual(self.eats(), 2)

    def test_out_of_food(self):
        self.ticks(form="cat")
        self.ticks(form="caster")
        self.assertEqual(self.eats(), 1)
        self.ticks(n=7, form="caster")                          # 3 秒还没在进食 → 没食物
        self.assertTrue(self.g.out_of_food)
        self.assertIsNone(self.g.eat)
        self.assertTrue(any("没有食物" in m for m in self.logs))
        self.assertIn("没食物", self.g.status_label())
        self.ticks(n=20, dt=5, form="cat")                      # 100 秒内不再试
        self.assertEqual(self.eats(), 1)
        self.clock.t += 30 * 60
        self.ticks(form="cat")
        self.ticks(form="caster")
        self.assertEqual(self.eats(), 2)                        # 30 分钟后再试

    def test_attacked_while_eating(self):
        self.ticks(form="cat")
        self.ticks(form="caster")
        self.ticks(n=3, form="caster", eating=True)
        self.ticks(form="caster", eating=True, combat=True)
        self.assertIsNone(self.g.eat)
        self.assertIn("f2", self.keys()[-3:])                  # 变回猎豹迎战

    def test_no_eat_cases(self):
        cases = [dict(wf="yes"), dict(wf="low"), dict(wf="unknown"), dict(eatinfo=False), dict(combat=True),
                 dict(thp=80, kind="enemy")]
        for kw in cases:
            self.setUp()
            self.ticks(n=3, **kw)
            self.assertIsNone(self.g.eat, kw)
            self.assertEqual(self.eats(), 0, kw)

    def test_switch_off_and_no_key(self):
        self.cfg["eat"]["enabled"] = False
        self.ticks(n=3)
        self.assertIsNone(self.g.eat)
        self.setUp()
        self.cfg["eat"]["key"] = ""
        self.ticks(n=3)
        self.assertIsNone(self.g.eat)
        self.assertEqual(self.eats(), 0)

    def test_quiet_after_kill(self):
        self.g.last_kill_t = self.clock.t - 1.0                 # 刚杀完 1 秒 → 先不吃（等 3 秒）
        self.g.tick(o())
        self.assertIsNone(self.g.eat)
        self.clock.t += 3
        self.g.tick(o())
        self.assertIsNotNone(self.g.eat)

    def test_state_still_searching(self):
        self.ticks(form="cat")
        self.assertEqual(self.g.state, State.SEARCHING)


try:
    import lupa  # noqa: F401
    import numpy as np
    import test_addon_lua as tal
except ImportError:
    lupa = None


@unittest.skipIf(lupa is None, "没有 lupa")
class Lua(unittest.TestCase):
    """真的跑 BotStatus.lua v0.14，看块 40/41。"""
    def render(self, auras=None):
        t = tal.TestAddonLua("test_buff_blocks")
        orig = tal.TestAddonLua.paint

        def wide(g):
            img = orig(g)
            out = np.zeros((200, 500, 3), np.uint8)
            out[:, :400] = img
            for tx in g.TEXTURES.values():      # 400 以外的块（40、41）
                if not all(isinstance(v, (int, float)) for v in (tx.px, tx.py, tx.w, tx.h)) or tx.rgb is None:
                    continue
                x, y = int(tx.px), int(-tx.py)
                if x >= 400:
                    r, gg, b = (int(round(tx.rgb[i] * 255)) for i in (1, 2, 3))
                    out[y:y + int(tx.h), x:x + int(tx.w)] = (b, gg, r)
            return out
        t.paint = wide
        return AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(t.render(slot=1, auras=auras))

    def test_blocks(self):
        st = self.render()
        self.assertTrue(st.ok, st.reason)
        self.assertTrue(st.eatinfo)
        self.assertEqual(st.well_fed, "unknown")              # 没有 aura API
        st = self.render("C_UnitAuras = { GetPlayerAuraBySpellID = function(id) "
                         "if id == 19705 then return { expirationTime = 900 } end return nil end }")
        self.assertEqual((st.well_fed, st.eating), ("yes", False))
        st = self.render("C_UnitAuras = { GetAuraDataByIndex = function(u, i) "
                         "if i == 1 then return { name = '进食', expirationTime = 0 } end return nil end }")
        self.assertEqual((st.well_fed, st.eating), ("no", True))
        st = self.render("C_UnitAuras = { GetAuraDataByIndex = function(u, i) "
                         "if i == 1 then return { name = '充分进食', expirationTime = 0 } end return nil end }")
        self.assertEqual((st.well_fed, st.eating), ("yes", False))


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class Strings(unittest.TestCase):
    def test_files(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertTrue(re.search(r"熊德自动打怪 v8\.(49|[5-9]\d)", app))
        self.assertIn('"eat"', app)
        rd = _rd(os.path.join(ROOT, "README_中文.md"))
        self.assertIn("v8.49", rd)
        self.assertIn("充分进食", rd)
        lua = _rd(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.lua"))
        self.assertIn("local NUM_BLOCKS = 42", lua)
        self.assertIn("BotStatus v0.14", lua)
        toc = _rd(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.toc"))
        self.assertTrue(re.search(r"## Version: 0\.14\.0", toc))
        sm = _rd(os.path.join(ROOT, "bot", "state_machine.py"))
        self.assertNotIn("_fb_close_tick", sm)


if __name__ == "__main__":
    unittest.main()

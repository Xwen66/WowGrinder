"""v8.67（t252u，Simon 选 1–4）：战斗治疗满血还连放治疗之触。
根因（11/22 21:06 日志）：被打推迟读条，治疗之触要 6.3–6.5 秒；以前 读条时间+2=5 秒就当读完 → 看到治疗落地前的旧血量 → 又按
→ 游戏排队，上一个加满血后再读一个。修：1 读条中继续等（上限 10 秒）；2 读完等 0.5 秒血条更新；3 在读条时绝不按；4 血量涨了才连放。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class V867(Base):
    MODE = "stationary_tab"

    def setUp(self):
        super().setUp()
        h, d = self.cfg["heal"], DEFAULT_CONFIG["heal"]
        h["ht_settle_s"] = d["ht_settle_s"]                 # Base 给旧测试设成 0；这里用新默认
        h["heal_to_pct"], h["heal_chain_max"] = 90, 6       # Simon 的值（不改）
        self.cfg["buffs"]["enabled"] = False
        self.ht = self.k["healing_touch"]

    def t(self, dt=0.25, **kw):
        self.clock.t += dt
        kw.setdefault("thp", 60)
        kw.setdefault("combat", True)
        kw.setdefault("mana", 80)
        self.g.tick(obs(**kw))

    def start_heal(self, hp=23):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        self.g.tick(obs(hp=hp, thp=60, combat=True, form="bear", mana=80))
        self.assertEqual(self.g.state, State.HEALING)
        self.t(hp=hp, form="caster", casting=False)          # 已退形态 → 按治疗之触
        self.assertEqual(self.keys().count(self.ht), 1)


class Defaults(unittest.TestCase):
    def test_new_keys_and_unchanged_lines(self):
        h = DEFAULT_CONFIG["heal"]
        self.assertEqual((h["ht_max_wait_s"], h["ht_settle_s"], h["heal_chain_min_rise_pct"]), (10.0, 0.5, 1.0))
        self.assertEqual((h["heal_to_pct"], h["heal_chain_max"], h["cast_s"]), (90, 6, 3.0))   # 没改

    def test_title_readme(self):
        self.assertRegex(_rd(os.path.join(ROOT, "gui", "app.py")), r"熊德自动打怪 v8\.(6[7-9]|[7-9]\d)")
        self.assertIn("v8.67", _rd(os.path.join(ROOT, "README_中文.md")))


# ---------------------------------------------------------------- 1. 读条中继续等
class KeepWaiting(V867):
    def test_no_end_at_5s_while_casting(self):
        self.start_heal()
        self.t(hp=23, form="caster", casting=True)
        for _ in range(26):                                   # 6.5 秒还在读
            self.t(hp=22, form="caster", casting=True)
        self.assertEqual(self.g.state, State.HEALING)
        self.assertEqual(self.g.heal_phase, "wait")
        self.assertFalse(any("读条超过" in m for m in self.logs))
        self.assertEqual(self.keys().count(self.ht), 1)

    def test_hard_cap(self):
        self.start_heal()
        self.t(hp=23, form="caster", casting=True)
        for _ in range(45):
            self.t(hp=22, form="caster", casting=True)
        self.assertTrue(any("读条超过 10 秒还在读" in m for m in self.logs))
        self.assertIsNotNone(self.g.heal_inflight)
        self.assertEqual(self.keys().count(self.ht), 1)       # 到上限也不在读条中再按


# ---------------------------------------------------------------- 2. 读完等 0.5 秒再看血
class Settle(V867):
    def test_waits_for_hp_update(self):
        self.start_heal()
        self.t(hp=23, form="caster", casting=True)
        for _ in range(16):
            self.t(hp=22, form="caster", casting=True)
        self.t(dt=0.1, hp=22, form="caster", casting=False)  # 读条刚结束，血条还没更新
        self.assertEqual(self.g.heal_phase, "wait")
        self.t(dt=0.2, hp=22, form="caster", casting=False)
        self.assertEqual(self.g.heal_phase, "wait")
        self.t(dt=0.3, hp=95, form="caster", casting=False)  # 0.5 秒后：治疗落地 95%
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertEqual(self.keys().count(self.ht), 1)
        self.assertTrue(any("→ 95%" in m for m in self.logs))


# ---------------------------------------------------------------- 3. 在读条时绝不按
class NeverPressWhileCasting(V867):
    def test_heal_starts_while_casting_waits(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        self.g.tick(obs(hp=30, thp=60, combat=True, form="caster", casting=True, mana=80))
        self.assertEqual(self.g.state, State.HEALING)
        for _ in range(20):
            self.t(hp=30, form="caster", casting=True)
        self.assertNotIn(self.ht, self.keys())
        self.assertTrue(any("先不按治疗之触" in m for m in self.logs))
        self.t(hp=30, form="caster", casting=False)
        self.t(dt=0.2, hp=30, form="caster", casting=False)
        self.assertNotIn(self.ht, self.keys())                # 还在等血条更新
        self.t(dt=0.4, hp=96, form="caster", casting=False)   # 上一个读条落地 96% → 不放
        self.assertNotIn(self.ht, self.keys())
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertTrue(any("上一个读条落地后" in m for m in self.logs))

    def test_after_busy_low_hp_presses(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        self.g.tick(obs(hp=30, thp=60, combat=True, form="caster", casting=True, mana=80))
        self.t(hp=30, form="caster", casting=True)
        self.t(hp=29, form="caster", casting=False)
        self.t(dt=0.6, hp=29, form="caster", casting=False)
        self.assertEqual(self.keys().count(self.ht), 1)
        self.assertEqual(self.g.state, State.HEALING)

    def test_busy_cap(self):
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        self.g.tick(obs(hp=30, thp=60, combat=True, form="caster", casting=True, mana=80))
        for _ in range(45):
            self.t(hp=30, form="caster", casting=True)
        self.assertNotIn(self.ht, self.keys())
        self.assertNotEqual(self.g.state, State.HEALING)

    def test_post_heal_waits_for_cast(self):
        self.cfg["heal"]["post_enabled"] = True
        self.g.tick(obs(hp=40, form="caster", casting=True, mana=80))
        self.g._start_post_heal(obs(hp=40, form="caster", casting=True, mana=80), "测试")
        for _ in range(6):
            self.t(hp=40, combat=False, thp=0, form="caster", casting=True)
        self.assertNotIn(self.ht, self.keys())
        self.t(hp=40, combat=False, thp=0, form="caster", casting=False)
        self.t(dt=0.6, hp=90, combat=False, thp=0, form="caster", casting=False)    # 落地 90% ≥ 战后线 → 不放
        self.assertNotIn(self.ht, self.keys())
        self.assertTrue(any("战后治疗" in m and "上一个读条落地后" in m for m in self.logs))

    def test_post_heal_waits_then_presses_when_low(self):
        self.cfg["heal"]["post_enabled"] = True
        self.g.tick(obs(hp=40, form="caster", casting=True, mana=80))
        self.g._start_post_heal(obs(hp=40, form="caster", casting=True, mana=80), "测试")
        self.t(hp=40, combat=False, thp=0, form="caster", casting=True)
        self.t(hp=40, combat=False, thp=0, form="caster", casting=False)
        self.assertNotIn(self.ht, self.keys())
        self.t(dt=0.6, hp=41, combat=False, thp=0, form="caster", casting=False)
        self.assertEqual(self.keys().count(self.ht), 1)

    def test_rest_and_corpse_guards_in_code(self):
        sm = _rd(os.path.join(ROOT, "bot", "state_machine.py"))
        self.assertIn('self.rest_next_cast = now + float(h.get("ht_settle_s", 0.5))', sm)
        cp = _rd(os.path.join(ROOT, "bot", "corpse.py"))
        i = cp.index('self.inp.press(key)\n            self.log(f"🩹 复活后血量')
        self.assertIn("if obs.casting:\n            return True", cp[:i][-900:])


# ---------------------------------------------------------------- 4. 血量涨了才连放
class ChainNeedsRise(V867):
    def cast(self, hp_before, hp_after):
        self.t(hp=hp_before, form="caster", casting=True)
        for _ in range(12):
            self.t(hp=hp_before, form="caster", casting=True)
        self.t(hp=hp_after, form="caster", casting=False)
        self.t(dt=0.6, hp=hp_after, form="caster", casting=False)

    def test_no_chain_when_hp_not_rising(self):
        self.start_heal(hp=30)
        self.cast(30, 28)
        self.assertNotEqual(self.g.state, State.HEALING)
        self.assertEqual(self.keys().count(self.ht), 1)
        self.assertTrue(any("血量没涨" in m for m in self.logs))

    def test_chain_when_hp_rose(self):
        self.start_heal(hp=30)
        self.cast(30, 60)
        self.assertEqual(self.g.state, State.HEALING)
        self.t(hp=60, form="caster", casting=False)
        self.assertEqual(self.keys().count(self.ht), 2)


# ---------------------------------------------------------------- 回放 11/22 21:06 那场
class Replay2106(V867):
    """小模拟：被打推迟读条 → 一个治疗之触 6.4 秒才落地，+47% 血；一直挨打每 0.25 秒 −0.5%（21:06 实测 2–4%/秒），38% 开始。
    在读条最后 1 秒内按会排队（日志里落地前 0.2–0.7 秒按的都排上了），读条中更早按无效。以前的代码：每 5 秒按一次，6 个连放、4 个开读时 ≥ 97%。"""

    def run_fight(self, secs=40.0):
        g = {"hp": 38.0, "form": "bear", "cast_end": None, "queued": False, "starts": [], "mana": 100.0}
        self.g.tick(obs(thp=100, arange="in"))
        self.inp.history.clear()
        seen = 0
        pressed_while_casting = 0
        t_end = self.clock.t + secs
        while self.clock.t < t_end:
            now = self.clock.t
            casting = g["cast_end"] is not None
            o = obs(hp=round(g["hp"]), thp=60, combat=True, form=g["form"], casting=casting if g["form"] == "caster" else False,
                    mana=round(g["mana"]), arange="in", rng="melee", aggro=True)
            self.g.tick(o)
            for key in self.keys()[seen:]:
                if key == self.k["cancel_form"] and key == self.k["bear_form"]:     # 同一个键：切换
                    g["form"] = "caster" if g["form"] == "bear" else "bear"
                elif key == self.k["cancel_form"]:
                    g["form"] = "caster"
                elif key == self.k["bear_form"]:
                    g["form"] = "bear"
                elif key == self.ht and g["form"] == "caster":
                    if g["cast_end"] is None:
                        g["cast_end"] = now + 6.4
                        g["starts"].append(g["hp"])
                        g["mana"] -= 9
                    else:
                        pressed_while_casting += 1
                        if g["cast_end"] - now <= 1.0:
                            g["queued"] = True
            seen = len(self.keys())
            self.clock.t += 0.25
            g["hp"] = max(1.0, g["hp"] - 0.5)
            if g["cast_end"] is not None and self.clock.t >= g["cast_end"]:
                g["hp"] = min(100.0, g["hp"] + 47)
                g["cast_end"] = None
                if g["queued"]:
                    g["queued"] = False
                    g["cast_end"] = self.clock.t + 6.4
                    g["starts"].append(g["hp"])
                    g["mana"] -= 9
        return g, pressed_while_casting

    def test_no_heal_starts_near_full(self):
        g, pwc = self.run_fight()
        self.assertEqual(pwc, 0)                                   # 3. 读条中一次都没按
        self.assertTrue(g["starts"])
        self.assertLessEqual(len(g["starts"]), 3)                   # 38% → 两三个就够了（以前 6 个）
        self.assertTrue(all(s < 90 for s in g["starts"]), g["starts"])
        self.assertFalse(any("读条超过" in m for m in self.logs))   # 6.4 秒的读条不再“先结束”


if __name__ == "__main__":
    unittest.main()

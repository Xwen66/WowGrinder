"""v8.54（t219u/t220u）：① 战后回春术整个删掉（只在血量 < 生命之触线时生命之触）；② 停着找怪连续 3 次 Tab 没选到 → 暂停 Tab 30 秒，
找到怪马上恢复；③ 同一个中心连续 2 次去不了（卡住/超时，中间没到过）→ 这次运行跳过它，至少留 1 个。"""
import copy
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG, _merge, _migrate  # noqa: E402
from bot.tabsearch import TS_DEFAULTS  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402
from test_v840 import TabBase, tobs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class Config(unittest.TestCase):
    def test_defaults(self):
        h = DEFAULT_CONFIG["heal"]
        for k in ("post_rejuv_enabled", "post_rejuv_below_pct", "rejuv_cost_pct", "rejuv_no_repeat_s"):
            self.assertNotIn(k, h)                                      # 战后回春术删掉了
        self.assertNotIn("rejuvenation", DEFAULT_CONFIG["keys"])
        self.assertTrue(h["post_enabled"])
        t = DEFAULT_CONFIG["tab_search"]
        self.assertEqual((t["miss_limit"], t["miss_backoff_s"]), (3, 30.0))
        self.assertEqual((TS_DEFAULTS["miss_limit"], TS_DEFAULTS["miss_backoff_s"]), (3, 30.0))
        self.assertEqual(DEFAULT_CONFIG["area"]["center_skip_fails"], 2)

    def test_migration_removes_keys(self):
        loaded = {"heal": {"post_rejuv_enabled": True, "post_rejuv_below_pct": 85, "rejuv_cost_pct": 14,
                           "rejuv_no_repeat_s": 12, "post_ht_below_pct": 65},
                  "keys": {"rejuvenation": "g", "healing_touch": "e"},
                  "meta": {"migrations": ["far_skip_v834", "red_ui_v850", "keys_v827"]}}
        cfg = _migrate(_merge(DEFAULT_CONFIG, copy.deepcopy(loaded)), loaded)
        for k in ("post_rejuv_enabled", "post_rejuv_below_pct", "rejuv_cost_pct", "rejuv_no_repeat_s"):
            self.assertNotIn(k, cfg["heal"])
        self.assertNotIn("rejuvenation", cfg["keys"])
        self.assertEqual(cfg["heal"]["post_ht_below_pct"], 65)          # 生命之触线不动
        self.assertEqual(cfg["keys"]["healing_touch"], "e")
        self.assertIn("post_rejuv_v854", cfg["meta"]["migrations"])
        self.assertEqual(cfg["meta"]["migrations"].count("post_rejuv_v854"), 1)
        cfg2 = _migrate(_merge(DEFAULT_CONFIG, copy.deepcopy(cfg)), cfg)
        self.assertEqual(cfg2["meta"]["migrations"].count("post_rejuv_v854"), 1)

    def test_no_rejuv_code_left(self):
        for f in ("bot/state_machine.py", "bot/runner.py", "gui/app.py"):
            src = _rd(os.path.join(ROOT, f))
            self.assertNotIn("post_rejuv", src, f)
            self.assertNotIn('"rejuvenation"', src, f)
            self.assertNotIn("rejuv_no_repeat", src, f)


class TabBackoff(TabBase):
    def setUp(self):
        super().setUp()
        self.cfg["tab_search"].update(miss_limit=3, miss_backoff_s=30.0)

    def run_for(self, secs, o=None):
        end = self.clock.t + secs
        while self.clock.t < end:
            self.step(o or tobs(), 0.3)

    def test_backoff_after_3_misses(self):
        self.run_for(20)
        self.assertEqual(sum("Tab 没选到怪（" in m for m in self.logs), 3)
        self.assertTrue(any("Tab 连续 3 次没选到怪 → 30 秒内停着找怪不按 Tab" in m for m in self.logs))
        n = self.tab_count()
        self.run_for(10)
        self.assertEqual(self.tab_count(), n)                           # 暂停中不按
        self.run_for(25)
        self.assertGreater(self.tab_count(), n)                         # 30 秒后恢复

    def test_combat_resets(self):
        self.run_for(20)
        self.assertTrue(self.g.ts_backoff)
        self.step(tobs(combat=True, thp=100, rng="melee"))
        self.assertFalse(self.g.ts_backoff)
        self.assertEqual(self.g.ts_miss, 0)
        self.assertTrue(any("找到怪了（进入战斗） → Tab 恢复" in m for m in self.logs))

    def test_found_resets_count(self):
        self.g.ts_miss = 2
        self.g._ts_found()
        self.assertEqual(self.g.ts_miss, 0)
        self.assertFalse(any("Tab 恢复" in m for m in self.logs))       # 没在暂停 → 不刷日志

    def test_zero_disables(self):
        self.cfg["tab_search"]["miss_limit"] = 0
        self.run_for(40)
        self.assertFalse(any("Tab 连续" in m for m in self.logs))
        self.assertGreaterEqual(sum("Tab 没选到怪（" in m for m in self.logs), 5)


class CenterSkip(Base):
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["area"].update(enabled=True, center_skip_fails=2)
        self.g.centers = [(10.0, 10.0, 0), (20.0, 20.0, 0), (30.0, 30.0, 0)]
        self.g.ci, self.g.cdir = 1, 1
        self.g.home = self.g.centers[1]
        self.g.travel = True

    def o(self):
        o = obs()
        o.pos, o.facing, o.map_id = (15.0, 15.0), 0.0, 0
        return o

    def fail(self, i):
        self.g.ci, self.g.home, self.g.travel = i, self.g.centers[i], True
        self.g.patrol_skips = 0
        self.g._return_give_up(self.o(), self.clock.t, "的路上卡住了（跳 + 平移 2 次都没用）")
        self.clock.t += 1

    def test_twice_in_a_row_skips(self):
        self.fail(1)
        self.assertNotIn(1, self.g.center_skip)
        self.fail(1)
        self.assertIn(1, self.g.center_skip)
        self.assertTrue(any("中心 #2 已经连续 2 次去不了" in m and "这次运行跳过它" in m for m in self.logs))
        self.g.ci, self.g.cdir = 0, 1
        self.assertEqual(self.g._next_center(self.o())[0], 2)          # 1→3，跳过 #2
        self.g.ci, self.g.cdir = 2, 1
        self.assertEqual(self.g._next_center(self.o())[0], 0)

    def test_arrival_resets(self):
        self.fail(1)
        self.g.ci, self.g.home, self.g.travel = 1, self.g.centers[1], True
        self.g.state = self.g.state.__class__.RETURNING
        o = self.o()
        o.pos = (20.0, 20.0)
        self.g._return(o, self.clock.t)
        self.assertTrue(any("到达中心 #2" in m for m in self.logs))
        self.fail(1)
        self.assertNotIn(1, self.g.center_skip)                          # 中间到过 → 重新数

    def test_keeps_last_center(self):
        self.g.centers = self.g.centers[:2]
        self.fail(1); self.fail(1)
        self.assertIn(1, self.g.center_skip)
        self.fail(0); self.fail(0)
        self.assertNotIn(0, self.g.center_skip)                          # 最后一个能用的不跳
        self.assertTrue(any("最后一个能用的中心 → 不跳过" in m for m in self.logs))

    def test_zero_disables(self):
        self.cfg["area"]["center_skip_fails"] = 0
        for _ in range(4):
            self.fail(1)
        self.assertEqual(self.g.center_skip, set())


class Strings(unittest.TestCase):
    def test_files(self):
        app = _rd(os.path.join(ROOT, "gui", "app.py"))
        self.assertRegex(app, r"熊德自动打怪 v8\.5[4-9]")
        for k in ("miss_limit", "miss_backoff_s", "center_skip_fails"):
            self.assertIn(f'"{k}"', app)
        self.assertTrue(re.search(r"v8\.54.*回春", _rd(os.path.join(ROOT, "README_中文.md"))))
        self.assertIn("v8.54", _rd(os.path.join(ROOT, "bot", "runner.py")))


if __name__ == "__main__":
    unittest.main()

"""v8.37（t169u）：去掉 v8.35 的“怪逃跑追着打”，战斗恢复成 v8.34 的打法；v8.36 的战后回春术开关保留。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config as bcfg  # noqa: E402
from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.state_machine import Grinder, State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fo(thp, rng="melee", arange="in", hp=90, **kw):
    o = obs(hp=hp, thp=thp, combat=True, rng=rng, arange=arange, aggro=True, form="bear",
            skills=[({"name": "槌击", "key": "2", "priority": 1}, 1.0)], **kw)
    o.frame_size = (1920, 1080)
    o.marker = (1800, 1000)
    return o


class NoChase(Base):
    LEGACY = False

    def setUp(self):
        super().setUp()
        self.cfg["buffs"]["enabled"] = False
        self.g._has_plate_templates = lambda now: False
        for o in (fo(100), fo(100), fo(60), fo(30), fo(20)):
            self.clock.t += 0.5
            self.g.tick(o)
        self.assertEqual(self.g.state, State.COMBAT)

    def run_away(self, n=24):
        for _ in range(n):
            self.clock.t += 0.5
            self.g.tick(fo(9, rng="ranged", arange="out"))

    def test_no_w_hold_no_flee_log(self):
        self.run_away()
        self.assertNotIn("w", self.inp.held)
        self.assertNotIn("down:w", self.inp.history)
        self.assertFalse(any("逃跑" in m or "追怪" in m for m in self.logs))

    def test_old_far_recovery_still_runs(self):           # v8.34：怪跑出近战 → 原来的“距离太远”补救
        self.run_away(6)
        self.assertTrue(any("距离太远" in m for m in self.logs), self.logs[-8:])

    def test_old_give_up_path(self):                       # 补救几次无效 → 跳过这只怪（v8.34 行为）
        self.run_away(30)
        self.assertTrue(any("补救无效" in m for m in self.logs), self.logs[-8:])
        self.assertFalse(any("没追上" in m for m in self.logs))

    def test_no_chase_attributes(self):
        for name in ("_flee_tick", "_chase_end", "_chase_outside"):
            self.assertFalse(hasattr(Grinder, name), name)


class NoFleeConfig(unittest.TestCase):
    def test_default_has_no_flee(self):
        self.assertNotIn("flee", DEFAULT_CONFIG)

    def test_old_flee_section_dropped(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"flee": {"enabled": True, "max_s": 10}, "heal": {"post_rejuv_enabled": False}}, f)
        cfg = bcfg.load_config(p)
        self.assertNotIn("flee", cfg)
        self.assertFalse(cfg["heal"]["post_rejuv_enabled"])     # v8.36 保留

    def test_gui_has_no_flee_fields(self):
        with open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('"flee"', src)
        self.assertIn('"post_rejuv_enabled"', src)
        self.assertRegex(src, r"熊德自动打怪 v8\.(3[7-9]|[4-9]\d)")   # v8.38 起标题版本号往上走

    def test_kept_features(self):
        self.assertEqual(DEFAULT_CONFIG["nameplate"]["far_skip_s"], 5.0)             # v8.34
        self.assertTrue(DEFAULT_CONFIG["heal"]["post_rejuv_enabled"])                # v8.36
        self.assertTrue(hasattr(Grinder, "_next_center"))                            # v8.33


if __name__ == "__main__":
    unittest.main()

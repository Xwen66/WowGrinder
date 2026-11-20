import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG, load_config  # noqa: E402


class TestConfigMigrate(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return load_config(p)

    def test_old_v5_config_upgraded(self):
        old = {"keys": {"regrowth": "7", "rejuvenation": "6", "bear_form": "f2"},
               "heal": {"mode": "regrowth", "heal_pct": 40, "heal_cooldown_s": 8.0, "regrowth_cast_ms": 1600,
                        "rest_to_pct": 85},
               "safety": {"tick_ms": 200}}
        cfg = self.load(old)
        self.assertEqual(cfg["keys"]["healing_touch"], "7")      # 沿用原来的愈合键
        self.assertNotIn("regrowth", cfg["keys"])
        self.assertEqual(cfg["keys"]["bear_form"], "f2")
        self.assertEqual(cfg["heal"]["heal_pct"], 40)      # v8.46 默认 40
        self.assertEqual(cfg["heal"]["rest_to_pct"], 85)          # 用户自己改的保留
        self.assertNotIn("mode", cfg["heal"])
        self.assertEqual(cfg["heal"]["cast_s"], 3.0)
        self.assertEqual(cfg["recover"], DEFAULT_CONFIG["recover"])
        self.assertEqual(cfg["safety"]["tick_ms"], 200)
        self.assertEqual(cfg["move"]["mode"], "stationary_tab")

    def test_v6_config_switched_to_stationary(self):
        cfg = self.load({"move": {"enabled": True, "approach_timeout_s": 12}, "keys": {"healing_touch": "5"}})
        self.assertEqual(cfg["move"]["mode"], "stationary_tab")
        self.assertEqual(cfg["stationary"]["max_tabs"], 6)

    def test_user_choice_click_kept(self):
        cfg = self.load({"move": {"mode": "click"}})
        self.assertEqual(cfg["move"]["mode"], "click")

    def test_custom_heal_pct_kept(self):
        cfg = self.load({"heal": {"mode": "regrowth", "heal_pct": 45}})
        self.assertEqual(cfg["heal"]["heal_pct"], 45)

    def test_new_config_untouched(self):
        cfg = self.load({"keys": {"healing_touch": "9"}, "heal": {"heal_pct": 40, "critical_pct": 15}})
        self.assertEqual(cfg["keys"]["healing_touch"], "9")
        self.assertEqual(cfg["heal"]["heal_pct"], 40)


if __name__ == "__main__":
    unittest.main()

"""v8.30（t157u）：战后治疗每场只一次（残留战斗时治疗过，真正脱战不再治疗）。（v8.54 起没有战后回春术了）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_state_machine import Base, obs  # noqa: E402
from bot.config import DEFAULT_CONFIG  # noqa: E402


class PostBase(Base):
    MODE = "click"
    LEGACY = False
    POST = True
    def setUp(self):
        super().setUp()
        self.cfg["cat"]["combat_form"] = "猎豹"
        self.k.update(cat_form="z", cancel_form="v", healing_touch="e", interact="`", loot="`",
                      cat_builder="1", cat_finisher="2", target_attacker="x")
        self.cfg["buffs"]["enabled"] = False
        self.cfg["loot"]["enabled"] = False

    def after(self, hp, combat=False, n=20):
        """脱战后若干帧（v8.54 起没有回春术；按了 e 以后显示人形）。"""
        for _ in range(n):
            self.clock.t += 0.4
            healed = "e" in self.keys()
            self.g.tick(obs(hp=hp + (3 if healed else 0), kind="none", combat=combat,
                            form="caster" if healed and self.g.state.name == "RESTING" else "cat",
                            mana=80, casting=False))

    def fight(self, hp=88, mana=80):
        self.g.tick(obs(hp=100, thp=100, form="cat", mana=mana))
        self.clock.t += 0.5
        self.g.tick(obs(hp=hp, thp=60, combat=True, form="cat", mana=mana, rng="melee"))
        self.clock.t += 0.5
        self.g.tick(obs(hp=hp, thp=10, combat=True, form="cat", mana=mana, rng="melee"))





class TestV830(PostBase):
    def test_residual_combat_heals_once(self):
        """残留战斗时生命之触治疗过一次 → 真正脱战不再治疗（v8.54：回春术删掉了，改用生命之触测）。"""
        self.fight(50)
        self.inp.history.clear()
        for _ in range(6):                     # 杀完怪“战斗中”还残留，没挨打 → 先治疗一次
            self.clock.t += 0.4
            self.g.tick(obs(hp=50, kind="none", combat=True, form="cat", mana=80, casting=False))
            if "e" in self.keys():
                break
        self.assertEqual(self.keys().count("e"), 1)
        for _ in range(6):                     # 读条
            self.clock.t += 0.4
            self.g.tick(obs(hp=50, kind="none", combat=True, form="caster", mana=70, casting=True))
        for _ in range(20):                    # 读完，真正脱战 → 不再治疗
            self.clock.t += 0.4
            self.g.tick(obs(hp=60, kind="none", combat=False, form="cat", mana=60, casting=False))
        self.assertEqual(self.keys().count("e"), 1)

    def test_rejuv_settings_gone(self):        # v8.54：战后回春术删掉了
        for k in ("rejuv_no_repeat_s", "rejuv_cost_pct", "post_rejuv_enabled", "post_rejuv_below_pct"):
            self.assertNotIn(k, DEFAULT_CONFIG["heal"])
        self.assertNotIn("rejuvenation", DEFAULT_CONFIG["keys"])


if __name__ == "__main__":
    unittest.main()

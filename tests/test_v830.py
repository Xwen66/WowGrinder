"""v8.30（t157u）：战后治疗每场只一次（残留战斗时治疗过，真正脱战不再治疗）；回春术 12 秒内不重复放。"""
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
        self.k.update(cat_form="z", cancel_form="v", healing_touch="e", rejuvenation="g", interact="`", loot="`",
                      cat_builder="1", cat_finisher="2", target_attacker="x")
        self.cfg["buffs"]["enabled"] = False
        self.cfg["loot"]["enabled"] = False

    def after(self, hp, combat=False, n=20):
        """脱战后若干帧：按了 g 以后显示人形、血量涨一点（回春术生效）。"""
        for _ in range(n):
            self.clock.t += 0.4
            healed = "g" in self.keys()
            self.g.tick(obs(hp=hp + (3 if healed else 0), kind="none", combat=combat,
                            form="caster" if healed and self.g.state.name == "RESTING" else "cat",
                            mana=80, casting=False))

    def fight(self, hp=88, mana=80):
        self.g.tick(obs(hp=100, thp=100, form="cat", mana=mana))
        self.clock.t += 0.5
        self.g.tick(obs(hp=hp, thp=60, combat=True, form="cat", mana=mana, rng="melee"))
        self.clock.t += 0.5
        self.g.tick(obs(hp=hp, thp=10, combat=True, form="cat", mana=mana, rng="melee"))

    def gs(self):
        return self.keys().count("g")




class TestV830(PostBase):
    def test_residual_combat_heals_once(self):
        self.fight(88)
        self.inp.history.clear()
        for _ in range(6):                     # 杀完怪“战斗中”还残留，没挨打 → 先治疗一次
            self.clock.t += 0.4
            self.g.tick(obs(hp=88, kind="none", combat=True, form="cat", mana=80, casting=False))
        self.assertEqual(self.gs(), 1)
        for _ in range(8):                     # 回春术生效（人形）
            self.clock.t += 0.4
            self.g.tick(obs(hp=90, kind="none", combat=True, form="caster", mana=70, casting=False))
        for _ in range(20):                    # 真正脱战 → 不再治疗
            self.clock.t += 0.4
            self.g.tick(obs(hp=91, kind="none", combat=False, form="cat", mana=60, casting=False))
        self.assertEqual(self.gs(), 1)

    def test_rejuv_not_repeated_within_12s(self):
        self.fight(88)
        self.inp.history.clear()
        self.after(88, n=6)
        self.assertEqual(self.gs(), 1)
        self.fight(90)                         # 马上又打一只（< 12 秒）
        self.inp.history.clear()
        self.after(90, n=10)
        self.assertEqual(self.gs(), 0)
        self.assertTrue(any("不重复放回春术" in m for m in self.logs))

    def test_rejuv_again_after_12s(self):
        self.fight(88)
        self.after(88)
        self.clock.t += 15
        self.fight(85)
        self.inp.history.clear()
        self.after(85, n=10)
        self.assertEqual(self.gs(), 1)

    def test_default_setting(self):
        self.assertEqual(DEFAULT_CONFIG["heal"]["rejuv_no_repeat_s"], 12)


if __name__ == "__main__":
    unittest.main()

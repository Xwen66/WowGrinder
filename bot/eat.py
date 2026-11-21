"""v8.49（t201u）自动进食：保持“充分进食”buff（+5% 经验，15 分钟）。

插件 v0.14：块 40 = 充分进食（yes/low/no/unknown），块 41 = 正在进食 + 签名。
没有 buff、脱战、附近没怪（没被打、没选中活怪、屏幕近处没有能打的怪的标记、刚打完要等 quiet_s 秒）
→ 退出猎豹（人形才能吃）→ 按进食键（游戏里做 /use 食物 的宏放到这个键）→ 原地不动吃 ≥ min_s 秒，
buff 出来就结束，最多 max_s 秒 → 变回战斗形态接着打。被打马上停下迎战。
按完 verify_s 秒还没看到“进食”光环 → 认定没食物了：停止自动进食，retry_min 分钟后再试（或重开 WowGrinder）。
"""
from __future__ import annotations

EAT_DEFAULTS = {
    "enabled": True,          # GUI“自动进食（充分进食 buff）”
    "key": "",                # 进食键：游戏里做宏 /use 食物名，放到动作条，键位填这里；空 = 不吃
    "min_s": 11.0,            # 至少吃这么久（吃满 10 秒才有充分进食）
    "max_s": 30.0,            # 最多吃这么久（buff 一直不出来也结束）
    "verify_s": 2.5,          # 按完多久还没在进食 → 没食物了
    "quiet_s": 3.0,           # 打完怪/被打后至少过这么久才吃
    "retry_min": 30.0,        # 没食物后多少分钟再试一次
    "max_restarts": 1,        # 吃到一半被打断（没 buff）重新按几次
}


class EatMixin:
    def _eat_cfg(self) -> dict:
        c = dict(EAT_DEFAULTS)
        c.update(self.cfg.get("eat") or {})
        return c

    def _eat_init(self):
        self.eat = None               # 正在进食 {"phase", "t", "t_press", "t_eat", "restarts"}
        self.eat_retry_at = -1e9      # 没食物/放弃后这个时间之前不再吃
        self.out_of_food = False
        self.eat_noaddon_noted = False

    def _eat_key(self) -> str:
        return str(self._eat_cfg().get("key") or "").strip()

    def _eat_label(self) -> str:
        if self.eat is not None:
            return "🍖 进食中"
        if self.out_of_food:
            return "（🍖没食物了）"
        return ""

    def _eat_want(self, obs, now, dead_pct) -> bool:
        c = self._eat_cfg()
        if not c.get("enabled", True) or self.state.name != "SEARCHING" or now < self.eat_retry_at:
            return False
        if obs.source != "addon" or getattr(obs, "player_dead", False) or getattr(obs, "ghost", False):
            return False
        if not getattr(obs, "eatinfo", False):
            if not self.eat_noaddon_noted and self._eat_key():
                self.eat_noaddon_noted = True
                self.log("⚠️ 自动进食要插件 v0.14（读“充分进食”buff 和是否在进食）。把新插件复制到 AddOns\\BotStatus 后在游戏里 /reload")
            return False
        if getattr(obs, "well_fed", None) != "no":
            return False                       # 有 buff / 读不到 → 不吃
        if not self._eat_key():
            self._warn("eatkey", "🍖 没有“充分进食”buff，但“进食键”没填 → 不吃（在游戏里做 /use 食物 的宏，键位填到“进食键”）", 600)
            return False
        if obs.in_combat or self._being_hit(obs) or self.pending_loot or self.walking:
            return False
        if self._alive(obs, dead_pct) or (obs.target_kind == "dead" and obs.lootable and self._loot_on()):
            return False
        if now - max(self.last_combat_time, getattr(self, "last_hit_t", -1e9), self.last_kill_t) < float(c["quiet_s"]):
            return False
        if self._clickable(obs, now):
            return False                       # 附近（屏幕近处）还有能打的怪
        return True

    def _eat_tick(self, obs, now, dead_pct) -> bool:
        """返回 True = 这一帧在处理进食（别的不做）。"""
        e = self.eat
        if e is None:
            if not self._eat_want(obs, now, dead_pct):
                return False
            if self.far is not None:
                self._far_end("先吃东西（充分进食）", skip=False)
            self._stop_walk()
            self.eat = e = {"phase": "shift", "t": now, "t_press": None, "t_eat": None, "restarts": 0, "shift_t": -1e9}
            self.log(f"🍖 没有“充分进食”buff，附近没怪 → 先变人形，按进食键「{self._eat_key()}」吃 ≥ "
                     f"{float(self._eat_cfg()['min_s']):.0f} 秒")
        c = self._eat_cfg()
        if obs.in_combat or self._being_hit(obs):
            self.log("🍖 进食时被打/进战斗 → 停止进食，先打怪")
            return self._eat_end(obs, now, retry_s=0)
        if getattr(obs, "well_fed", None) in ("yes", "low") and (e["t_eat"] is None or now - e["t_eat"] >= float(c["min_s"])):
            return self._eat_end(obs, now, msg="🍖 已经有“充分进食”buff → 吃完了，变回战斗形态继续", retry_s=0)
        if e["phase"] == "shift":
            if obs.form != "caster" and self._in_any_form(obs):
                if now - e["shift_t"] >= 1.0:
                    e["shift_t"] = now
                    self._to_caster(obs)
                if now - e["t"] > 6.0:
                    self.log("🍖 6 秒还没变回人形 → 这次不吃，5 分钟后再试")
                    return self._eat_end(obs, now, retry_s=300)
                return True
            self._ctx("自动进食（充分进食 buff）")
            self.inp.press(self._eat_key())
            e["phase"], e["t_press"] = "wait", now
            return True
        if e["phase"] == "wait":
            if getattr(obs, "eating", False):
                e["phase"], e["t_eat"] = "eating", now
                self.dlog("[进食] 看到“进食”光环，原地不动吃")
                return True
            if now - e["t_press"] >= float(c["verify_s"]):
                self.out_of_food = True
                mins = float(c["retry_min"])
                self.log(f"🍖 按了进食键 {float(c['verify_s']):.1f} 秒还没在进食 → 认定没有食物了，停止自动进食"
                         f"（{mins:.0f} 分钟后再试；补了食物重开 WowGrinder 马上恢复）")
                self._event("out_of_food", "按进食键后没在进食")
                return self._eat_end(obs, now, retry_s=mins * 60)
            return True
        # eating
        el = now - e["t_eat"]
        if el >= float(c["max_s"]):
            return self._eat_end(obs, now, msg=f"🍖 吃了 {el:.0f} 秒 buff 还没出来 → 不等了，变回战斗形态（5 分钟后再看）",
                                 retry_s=300)
        if not getattr(obs, "eating", False):
            if el >= float(c["min_s"]):
                return self._eat_end(obs, now, msg=f"🍖 吃完了（{el:.0f} 秒），变回战斗形态继续", retry_s=60)
            if e["restarts"] < int(c["max_restarts"]):
                e["restarts"] += 1
                e["phase"], e["t_eat"] = "shift", None
                self.log(f"🍖 吃了 {el:.1f} 秒就停了（还没 buff）→ 再按一次进食键")
                return True
            return self._eat_end(obs, now, msg="🍖 进食又被打断 → 这次不吃了（5 分钟后再看）", retry_s=300)
        return True

    def _eat_end(self, obs, now, msg=None, retry_s=0.0) -> bool:
        self.eat = None
        if retry_s:
            self.eat_retry_at = now + retry_s
        if msg:
            self.log(msg)
        if obs.in_combat or self._being_hit(obs):
            if not self._is_bear(obs):
                self._to_bear(obs)
            return False                       # 让后面的战斗流程接手
        if not self._is_bear(obs):
            self._to_bear(obs)
        return True

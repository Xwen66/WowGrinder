"""v8.40 Tab 找怪（点击模式）。

找怪时（脱战、没目标）先按 Tab（选怪键 keys.target），插件 v0.13 读目标信息检查：
  活的、能打；不是灰名（被别人抢了）、不是精英（elite / rareelite / worldboss）、不比我高 level_diff 级以上（骷髅 ?? 也算）、
  不是玩家；我自己离中心不超过 半径 × 追怪上限倍数。稀有（rare，非精英）照样打。
  合格 → 走原来的“选中目标 → 右键走过去开打”流程；不合格 → 这一轮再按 1 次 Tab 换下一个（最多 max_per_cycle 次），
  还不行就按清除目标键，不转身，接着走原来的流程（屏幕标记 → 远处姓名板/红名），cooldown_s 秒内找怪不再按 Tab。
移动中（巡逻去下一个中心、走回中心、朝远处姓名板/红名走）：每 move_interval_s 秒（± move_jitter_s）按一次 Tab，
  合格 → 停下过去打；不合格 → 清目标，接着走（不转身）。跑尸、战斗、拾取/剥皮、回血、补 buff 时不按。
插件还是 v0.12（读不到 玩家/精英/等级）→ 保守：不用 Tab 找怪（日志提示复制 v0.13 后 /reload）。
不合格的目标记 memory_s 秒（按 类型/玩家/精英/等级差/血量 的“特征”），同一轮又 Tab 到它 → 这一轮结束。
"""
from __future__ import annotations

from .navigation import distance

TS_DEFAULTS = {
    "enabled": True,          # GUI“Tab 找怪”
    "skip_tapped": True,      # 灰名（被别人抢了）不打
    "skip_elite": True,       # 精英（含稀有精英/世界首领）不打
    "skip_high": True,        # 比我高 level_diff 级以上不打（骷髅 ?? 也不打）
    "level_diff": 3,
    "skip_player": True,      # 玩家不打
    "max_per_cycle": 2,       # 一轮找怪最多按几次 Tab
    "cooldown_s": 4.0,        # 一轮没找到合格的 → 这么久内找怪不再按 Tab（走屏幕标记/远处姓名板）
    "verify_s": 0.6,          # 按完 Tab 等多久看有没有选上
    "memory_s": 30.0,         # 不合格的目标记多久
    "while_moving": True,     # GUI“移动中也按 Tab”
    "move_interval_s": 1.8,   # 移动中每隔几秒按一次
    "move_jitter_s": 0.4,     # 随机 ±
}


class TabSearchMixin:
    def _ts_cfg(self) -> dict:
        c = dict(TS_DEFAULTS)
        c.update(self.cfg.get("tab_search") or {})
        return c

    def _ts_init(self):
        self.ts_pend = None          # (按 Tab 的时间, "search"/"move", 说明)
        self.ts_n = 0                # 这一轮按了几次
        self.ts_cd_until = -1e9
        self.ts_bad = []             # [(特征, 时间)]
        self.ts_move_next = -1e9
        self.ts_ignore_until = -1e9  # 刚清掉不合格目标：这段时间里还看到的目标是它，别去打
        self.ts_accept_t = -1e9
        self.ts_old_noted = False
        self.ts_last_press = -1e9

    def _ts_key(self) -> str:
        return (self.cfg["keys"].get("target") or "tab").strip() or "tab"

    def _ts_on(self, obs, moving=False) -> bool:
        c = self._ts_cfg()
        if not c.get("enabled", True) or (moving and not c.get("while_moving", True)):
            return False
        if self.stationary or self.cfg.get("nameplate", {}).get("select_method", "click") != "click":
            return False                   # 原地模式本来就用 Tab
        if obs.source != "addon":
            return False
        if not getattr(obs, "tinfo", False):
            if not self.ts_old_noted:
                self.ts_old_noted = True
                self.log("⚠️ Tab 找怪要插件 v0.13（读目标是不是玩家/精英/等级差）。现在读不到 → 保守起见先不用 Tab 找怪；"
                         "把新插件复制到 AddOns\\BotStatus 后在游戏里 /reload")
            return False
        return True

    def _ts_ignoring(self, now) -> bool:
        return self.ts_pend is not None or now < self.ts_ignore_until

    @staticmethod
    def _ts_sig(obs):
        return (obs.target_kind, getattr(obs, "t_player", None), getattr(obs, "t_elite", None),
                getattr(obs, "t_level_diff", None), int(float(obs.target_hp or 0) // 5))

    def _ts_leash(self, obs):
        """离中心超过 半径 × 追怪上限 → 返回说明；否则 None。"""
        a = self.cfg.get("area", {})
        if not a.get("enabled") or obs.pos is None or self.home is None:
            return None
        want = int(self.home[2] or 0) if len(self.home) > 2 else 0
        if want and obs.map_id is not None and obs.map_id != want:
            return None
        lim = float(a.get("radius", 3)) * float(a.get("leash_ratio", 1.3))
        d = distance(obs.pos, self.home[:2])
        if d > lim:
            return f"在追怪上限外（我离中心 {d:.2f} > {lim:.2f}）"
        return None

    def _ts_reject(self, obs, dead_pct):
        """不合格的原因；合格返回 None。"""
        c = self._ts_cfg()
        k = obs.target_kind
        if k == "tapped":
            if c.get("skip_tapped", True):
                return "灰名（被别人抢了）"
        elif k == "friendly":
            return "友方/不能打"
        elif k == "dead":
            return "死的"
        elif k != "enemy":
            return f"读不到目标类型（{k}，保守不打）"
        if obs.target_hp <= dead_pct:
            return "死的"
        if c.get("skip_player", True):
            if obs.t_player is None:
                return "读不到是不是玩家（保守不打）"
            if obs.t_player:
                return "玩家"
        if c.get("skip_elite", True):
            if obs.t_elite is None:
                return "读不到是不是精英（保守不打）"
            if obs.t_elite:
                return "精英"
        if c.get("skip_high", True):
            n = int(c.get("level_diff", 3))
            if obs.t_skull:
                return "骷髅（等级 ??）"
            if obs.t_level_diff is None:
                return "读不到等级（保守不打）"
            if obs.t_level_diff >= n:
                return f"比我高 {obs.t_level_diff} 级（≥ {n}）"
        return self._ts_leash(obs)

    def _ts_desc(self, obs) -> str:
        d = obs.t_level_diff
        lv = "??" if obs.t_skull else ("?" if d is None else f"{d:+d}")
        extra = "，稀有" if getattr(obs, "t_rare", False) else ""
        return f"血量 {obs.target_hp:.0f}%，等级差 {lv}，距离 {obs.range}{extra}"

    def _ts_remember(self, obs, now) -> bool:
        """记下不合格目标。返回 True = 最近已经记过（Tab 绕回来了）。"""
        mem = float(self._ts_cfg().get("memory_s", 30))
        self.ts_bad = [(s, t) for s, t in self.ts_bad if now - t < mem]
        sig = self._ts_sig(obs)
        seen = any(s == sig for s, _ in self.ts_bad)
        self.ts_bad.append((sig, now))
        return seen

    def _ts_end_cycle(self, now, obs=None, clear=False):
        self.ts_pend = None
        self.ts_n = 0
        self.ts_cd_until = now + float(self._ts_cfg().get("cooldown_s", 4.0))
        if clear:
            self._clear_target(obs)
            self.ts_ignore_until = now + 1.0

    def _ts_press(self, now, mode, delay=True):
        self._ctx(f"Tab 找怪（{'移动中' if mode == 'move' else '找怪'}）")
        self.inp.press(self._ts_key(), delay=delay)
        self.ts_pend = (now, mode)
        self.ts_last_press = now

    # ---------------------------------------------------------------- 找怪（停着）
    def _ts_search(self, obs, now, dead_pct) -> bool:
        """_search 里没目标时调用：返回 True = 这一帧按了 Tab / 在等结果 / 刚清掉不合格目标。"""
        if obs.in_combat or not self._ts_on(obs):
            self.ts_pend = None
            return False
        if now < self.ts_ignore_until:
            return obs.target_exists
        if self.ts_pend is not None:
            return self._ts_check(obs, now, dead_pct)
        if now < self.ts_cd_until or obs.target_exists:
            return False
        if self.ts_n >= int(self._ts_cfg().get("max_per_cycle", 2)):
            self._ts_end_cycle(now)
            return False
        self.ts_n += 1
        self._ts_press(now, "search")
        return True

    def _ts_ready(self, obs, now) -> bool:
        """这一帧找怪时会先按 Tab 吗（给“非战斗看到怪就打”让路用）。"""
        if obs.in_combat or obs.target_exists or not self._ts_on(obs):
            return False
        return self.ts_pend is not None or (now >= self.ts_cd_until and now >= self.ts_ignore_until)

    def _ts_check(self, obs, now, dead_pct) -> bool:
        """按过 Tab 后检查选上的目标。True = 这一帧处理了（等/换下一个/清目标）；False = 合格或没选上 → 继续原来的流程。"""
        t0, mode = self.ts_pend
        c = self._ts_cfg()
        if not obs.target_exists:
            if now - t0 < float(c.get("verify_s", 0.6)):
                return True
            self.log(f"🔎 Tab 没选到怪（第 {self.ts_n} 次）→ 不转身，看屏幕标记/远处姓名板")
            self._ts_end_cycle(now)
            return False
        why = self._ts_reject(obs, dead_pct)
        if why is None:
            self.log(f"🔎 Tab 选中能打的怪（{self._ts_desc(obs)}）→ 过去打")
            self.ts_pend = None
            self.ts_n = 0
            self.ts_accept_t = now
            return False
        again = self._ts_remember(obs, now)
        if not again and self.ts_n < int(c.get("max_per_cycle", 2)):
            self.log(f"🔎 Tab 选中的怪不合格：{why}（{self._ts_desc(obs)}）→ 再按一次 Tab 换下一个")
            self.ts_n += 1
            self._ts_press(now, "search")
            return True
        self.log(f"🔎 Tab 选中的怪不合格：{why}（{self._ts_desc(obs)}）→ 清目标，不转身，"
                 f"看屏幕标记/远处姓名板（{float(c.get('cooldown_s', 4)):.0f} 秒内找怪不再按 Tab）")
        self._ts_end_cycle(now, obs, clear=True)
        return True

    def _ts_keep(self, obs, now, dead_pct) -> bool:
        """刚用 Tab 选中、合格的怪：在追怪上限内就算我在范围外也先打（不走回中心）。"""
        return (now - self.ts_accept_t < 15.0 and self._alive(obs, dead_pct)
                and obs.target_kind not in ("friendly", "tapped") and self._ts_leash(obs) is None)

    # ---------------------------------------------------------------- 移动中
    def _ts_moving(self, obs, now, dead_pct, where) -> bool:
        """走路时（巡逻/回中心/朝远处走）调用。返回 True = 选中合格的怪，已经停下、转到找怪状态（下一帧走原来的开打流程）。"""
        if obs.in_combat or not self._ts_on(obs, moving=True):
            return False
        if self._pull_mana_low(obs, now):
            return False                 # v8.47：蓝不到开怪线 → 走路时不按 Tab 拉怪
        c = self._ts_cfg()
        if self.ts_pend is None and obs.target_exists and now >= self.ts_ignore_until \
                and now - self.ts_last_press < 2.0:
            self.ts_pend = (self.ts_last_press, "move")      # 目标出来得晚：还是按 Tab 选上的，照样检查
        if self.ts_pend is not None:
            t0, mode = self.ts_pend
            if not obs.target_exists:
                if now - t0 < float(c.get("verify_s", 0.6)):
                    return False
                self.ts_pend = None
                self.dlog(f"[Tab] {where}：Tab 没选到怪，接着走")
        if self.ts_pend is not None:
            why = self._ts_reject(obs, dead_pct)
            self.ts_pend = None
            if why is None:
                self.log(f"🔎 {where}：Tab 选中能打的怪（{self._ts_desc(obs)}）→ 停下过去打")
                self.ts_accept_t = now
                self.ts_n = 0
                if getattr(self, "far", None) is not None:
                    self._far_end("Tab 选中了能打的怪", skip=False)
                else:
                    self._stop_walk()
                from .state_machine import State
                self.state = State.SEARCHING
                return True
            self._ts_remember(obs, now)
            self.log(f"🔎 {where}：Tab 选中的怪不合格：{why}（{self._ts_desc(obs)}）→ 清目标，接着走（不转身）")
            self._clear_target(obs)
            self.ts_ignore_until = now + 1.0
            return False
        if now < self.ts_move_next or now < self.ts_ignore_until or obs.target_exists:
            return False
        iv, jit = float(c.get("move_interval_s", 1.8)), float(c.get("move_jitter_s", 0.4))
        self.ts_move_next = now + max(0.5, iv + self.rng.uniform(-jit, jit))
        self._ts_press(now, "move", delay=False)
        return False

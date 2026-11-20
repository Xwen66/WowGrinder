"""v8.38 被多只怪同时打 → 临时变熊抗（战斗形态=猎豹时才用）。

插件 v0.12 没有“几只怪在打我”的读数（块 33 只是屏幕上姓名板总数，远处的、没进战斗的都算），
所以用两个保守的判断，任意一个成立就算“多只”：
  1. 最近 fast_window_s 秒（默认 5）自己掉血 ≥ fast_drop_pct（默认 20%）——单只同级怪一般掉不到这么快；
  2. 选中的怪还活着、但它的目标不是我（插件“打我”=否），我却在掉血，fast_window_s 秒内 ≥ other_hits 次（默认 2）
     ——说明有别的怪在打我。
变熊后：按 熊形态键 → 熊形态循环（重殴键，可选横扫键）；战斗中低血治疗照旧（退熊 → 治疗之触 → 变回熊）。
变回猎豹：脱战 2 秒以上；或 变熊至少 min_bear_s 秒 + 期间杀了一只 + calm_s 秒没再判断到多只（≈只剩 1 只）。
"""
from __future__ import annotations

from collections import deque

MB_DEFAULTS = {
    "enabled": False,       # v8.47（t190u）：默认关 —— 来回变身太费蓝，一直用猎豹
    "fast_drop_pct": 20,
    "fast_window_s": 5.0,
    "other_hits": 2,
    "calm_s": 8.0,
    "min_bear_s": 10.0,
    "cooldown_s": 10.0,
    "min_mana_pct": 40,
    "maul_key": "1",
    "swipe_key": "",
    "swipe_every_s": 6.0,
    "press_interval_s": 1.5,
}


class MultiBearMixin:
    def _mb_cfg(self) -> dict:
        c = dict(MB_DEFAULTS)
        c.update(self.cfg.get("multi_bear") or {})
        return c

    def _mb_init(self):
        self.mb_active = False
        self.mb_t0 = -1e9
        self.mb_off_t = -1e9
        self.mb_kills0 = 0
        self.mb_last_sig = -1e9
        self.mb_ooc_since = None
        self.mb_hist = deque()
        self.mb_other = deque()
        self.mb_press_t = -1e9
        self.mb_swipe_t = -1e9
        self.mb_fix_t = -1e9

    def _mb_signal(self, obs, now, mb) -> str | None:
        """更新掉血记录；返回“多只怪”的理由（没有 = None）。"""
        from .state_machine import ALIVE_KINDS
        win = float(mb["fast_window_s"])
        if obs.in_combat and obs.hp_readable:
            self.mb_hist.append((now, float(obs.player_hp)))
        else:
            self.mb_hist.clear()
        while self.mb_hist and now - self.mb_hist[0][0] > win:
            self.mb_hist.popleft()
        if obs.in_combat and self.hp_drop >= 1.0 and obs.source == "addon" and obs.target_exists \
                and obs.target_kind in ALIVE_KINDS and not bool(getattr(obs, "aggro", False)):
            self.mb_other.append(now)
        while self.mb_other and now - self.mb_other[0] > win:
            self.mb_other.popleft()
        if not obs.in_combat:
            return None
        if self.mb_hist:
            drop = max(h for _, h in self.mb_hist) - float(obs.player_hp)
            if drop >= float(mb["fast_drop_pct"]):
                return f"{win:.0f} 秒内掉血 {drop:.0f}%（≥ {float(mb['fast_drop_pct']):.0f}%）"
        if len(self.mb_other) >= int(mb["other_hits"]):
            return f"选中的怪没在打我，但 {win:.0f} 秒内还挨了 {len(self.mb_other)} 下（有别的怪在打我）"
        return None

    def _multi_bear_tick(self, obs, now) -> bool:
        """返回 True = 这一帧按了变形键（本帧不再做别的）。"""
        from .config import is_cat
        from .state_machine import State
        mb = self._mb_cfg()
        base_cat = is_cat(self.cfg)
        if not base_cat or not mb.get("enabled", True):
            if self.mb_active:
                self.mb_active = False
                self.mb_off_t = now
                self.log("🐆 “被多只怪打时变熊”关了 → 回到正常战斗形态")
            self.mb_hist.clear()
            self.mb_other.clear()
            return False
        why = self._mb_signal(obs, now, mb)
        if why:
            self.mb_last_sig = now
        busy = self.state in (State.HEALING, State.RESTING, State.CORPSE, State.SKINNING, State.BUFFING, State.STOPPED)
        if self.mb_active:
            if obs.in_combat:
                self.mb_ooc_since = None
            else:
                self.mb_ooc_since = self.mb_ooc_since if self.mb_ooc_since is not None else now
            off = None
            if self.mb_ooc_since is not None and now - self.mb_ooc_since >= 2.0:
                off = "脱战了"
            elif now - self.mb_t0 >= float(mb["min_bear_s"]) and self.kills > self.mb_kills0 \
                    and now - self.mb_last_sig >= float(mb["calm_s"]):
                off = f"杀了 {self.kills - self.mb_kills0} 只，{float(mb['calm_s']):.0f} 秒没再判断到多只（只剩 1 只了）"
            if off is None or self.state == State.HEALING:
                return False
            self.mb_active = False
            self.mb_off_t = now
            self.log(f"🐆 {off} → 变回猎豹（这次熊形态 {now - self.mb_t0:.0f} 秒）")
            if busy or obs.form == "cat":
                return False
            self._to_bear(obs)            # 现在战斗形态 = 猎豹 → 按猎豹形态键
            return True
        # 没在熊：变回猎豹没成功（例如蓝不够）还停在熊 → 隔 3 秒再按一次猎豹键
        if obs.form == "bear" and not busy and now - self.mb_off_t < 120 and now - self.mb_fix_t >= 3.0 \
                and self.mb_off_t > -1e9:
            self.mb_fix_t = now
            self.dlog("[多只变熊] 还在熊形态 → 再按一次猎豹形态键")
            self._to_bear(obs)
            return True
        if not why or busy or now - self.mb_off_t < float(mb["cooldown_s"]):
            return False
        if obs.hp_readable and float(obs.player_hp) < float(self.cfg["heal"].get("heal_pct", 30)):
            return False                  # 血量低于治疗线 → 先治疗（治疗完还在被多只打再变熊）
        mana = obs.mana
        if mana is not None and float(mana) < float(mb["min_mana_pct"]):
            self._warn("mbmana", f"🐻 判断被多只怪打（{why}），但法力 {float(mana):.0f}% < {float(mb['min_mana_pct']):.0f}% → 不变熊，照旧猎豹打", 20)
            return False
        key = str(self.cfg["keys"].get("bear_form") or "").strip()
        if not key:
            self._warn("mbnokey", "🐻 判断被多只怪打，但“熊形态键”没填 → 不变熊", 60)
            return False
        self.mb_active = True
        self.mb_t0 = now
        self.mb_kills0 = self.kills
        self.mb_ooc_since = None
        self.mb_press_t = self.mb_swipe_t = -1e9
        self._event("multi_bear", why)
        self.log(f"🐻 同时被多只怪打（{why}）→ 变熊抗：熊形态键「{key}」，重殴「{mb.get('maul_key') or '-'}」"
                 + (f"，横扫「{mb['swipe_key']}」" if str(mb.get("swipe_key") or "").strip() else ""))
        if self.walking:
            self._stop_walk()
        self._to_bear(obs)                # 现在战斗形态 = 熊 → 按熊形态键
        return True

    def _bear_rotate(self, obs):
        """临时熊形态循环：横扫（可选，每 N 秒）→ 重殴。自动攻击照常。"""
        mb = self._mb_cfg()
        now = self.clock()
        if now - self.mb_press_t < float(mb["press_interval_s"]):
            return None
        sw = str(mb.get("swipe_key") or "").strip()
        if sw and now - self.mb_swipe_t >= float(mb["swipe_every_s"]):
            self.mb_swipe_t = self.mb_press_t = now
            self._ctx("熊：横扫")
            self.inp.press(sw)
            self.dlog(f"[熊] 横扫「{sw}」")
            return "横扫"
        maul = str(mb.get("maul_key") or "").strip()
        if maul:
            self.mb_press_t = now
            self._ctx("熊：重殴")
            self.inp.press(maul)
            self.dlog(f"[熊] 重殴「{maul}」")
            return "重殴"
        return None

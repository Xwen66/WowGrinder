"""v8.46（t184u/t185u）提高效率：
- ManaRestMixin：打完怪蓝不够（< 变身预留 + 治疗之触 + 余量）又掉了血（< 60%）→ 先回蓝，不拉新怪；被打照常迎战。
- WalkFarMixin：选中的怪离得远（插件距离 far / ranged）→ 先按住前进朝它走，走到近战距离再按开怪键，
  不再“开怪 → 距离太远补救 → 换目标”空转。走的时候不算战斗；超时 / 卡住 → 换回原来的右键走过去。"""
from __future__ import annotations

from .navigation import distance

MR_DEFAULTS = {
    "mana_rest_enabled": True,     # 蓝不够时先回蓝再开怪
    "mana_rest_hp_below": 60.0,    # 血量低于这个才回蓝（血多就直接打）
    "mana_rest_margin_pct": 10.0,  # 回到 变身预留 + 治疗之触耗蓝 + 这个余量
    "mana_rest_hp_ok": 90.0,       # 回蓝途中血回到这个也算好了
    "mana_rest_timeout_s": 120.0,  # 最多等这么久
    "drink_key": "",               # 喝水键（空 = 不喝，原地等回蓝）
    "pull_min_mana_pct": 50.0,     # v8.47：脱战时法力低于这个就不开怪，先回蓝（不管血量）
}
FLOOR_TIMEOUT_S = 300.0            # v8.47：只因为“法力 < 开怪线”在回蓝时，最多等这么久（防止读数异常卡死）
WF_DEFAULTS = {
    "walk_far": True,              # 怪远就先走过去再开打
    "walk_far_max_s": 15.0,        # 最多走这么久
    "walk_far_stuck_s": 2.5,       # 坐标这么久没变 = 卡住
}


class ManaRestMixin:
    def _mr_cfg(self) -> dict:
        c = dict(MR_DEFAULTS)
        h = self.cfg.get("heal") or {}
        c.update({k: h[k] for k in MR_DEFAULTS if k in h})
        return c

    def _mr_need(self) -> float:
        return self._reserve() + self._cost("heal") + float(self._mr_cfg()["mana_rest_margin_pct"])

    def _mr_floor(self) -> float:
        try:
            return max(0.0, float(self._mr_cfg().get("pull_min_mana_pct", 50) or 0))
        except (TypeError, ValueError):
            return 50.0

    def _pull_mana_low(self, obs, now=None) -> bool:
        """v8.47（t190u）：脱战、插件读得到法力、法力 < 开怪线（默认 50%）→ 不开新怪（Tab/看到的怪/远处的怪）。被打不管。"""
        c = self._mr_cfg()
        if not c["mana_rest_enabled"] or obs.in_combat or obs.source != "addon":
            return False
        now = self.clock() if now is None else now
        if now < getattr(self, "mr_skip_until", -1e9):
            return False
        if obs.mana is None or obs.mana <= 0 or obs.player_hp <= 0:
            return False
        return obs.mana < self._mr_floor()

    def _mr_low_hp_rule(self, obs) -> bool:
        """v8.46：血低（< mana_rest_hp_below）+ 蓝不够 变身预留 + 治疗之触 + 余量。"""
        c = self._mr_cfg()
        return obs.player_hp < float(c["mana_rest_hp_below"]) and obs.mana is not None and obs.mana < self._mr_need()

    def _mr_target(self, obs) -> float:
        """回蓝回到多少：开怪线；血还低（< mana_rest_hp_ok）且是因为血低开始回的 → 还要够 变身预留 + 治疗之触 + 余量。"""
        t = self._mr_floor()
        if getattr(self, "mr_hp_rule", False) and obs.player_hp < float(self._mr_cfg()["mana_rest_hp_ok"]):
            t = max(t, self._mr_need())
        return t

    def _need_mana_rest(self, obs, now, hit=False) -> bool:
        c = self._mr_cfg()
        if not c["mana_rest_enabled"] or obs.in_combat or hit or obs.source != "addon":
            return False
        if now < getattr(self, "mr_skip_until", -1e9):
            return False
        if not getattr(obs, "hp_readable", True) or obs.player_hp <= 0 or obs.mana is None or obs.mana <= 0:
            return False
        if obs.mana < self._mr_floor():
            return True                     # v8.47：法力 < 开怪线 → 不管血量，先回蓝
        return self._mr_low_hp_rule(obs)

    def _start_mana_rest(self, obs, now):
        from .state_machine import State
        c = self._mr_cfg()
        if self.walking:
            self._stop_walk()
        if getattr(self, "far", None) is not None:
            self.far = None
        drink = str(c.get("drink_key") or "").strip()
        need = self._mr_need()
        floor = self._mr_floor()
        self.rest_mode = "mana"
        self.state = State.RESTING
        self.mr_t0, self.mr_drank, self.mr_shift_t, self.mr_note_t = now, False, -1e9, now
        self.mr_hp_rule = self._mr_low_hp_rule(obs)
        how = (f"人形喝水「{drink}」" if drink else "原地等回蓝（不变形，省变身的蓝）")
        if self.mr_hp_rule:
            self.log(f"💤 先回蓝再开怪：血量 {obs.player_hp:.0f}% < {float(c['mana_rest_hp_below']):.0f}%，法力 {obs.mana:.0f}% < "
                     f"{need:.0f}%（变{self._cform_cn()}预留 {self._reserve():.0f}% + 治疗之触约 {self._cost('heal'):.0f}% + 余量 "
                     f"{float(c['mana_rest_margin_pct']):.0f}%）→ 不拉新怪，{how}"
                     f"；到 {max(need, floor):.0f}%（血量 ≥ {float(c['mana_rest_hp_ok']):.0f}% 时到 {floor:.0f}%）再找怪；被打照常迎战")
        else:
            self.log(f"💤 先回蓝再开怪：法力 {obs.mana:.0f}% < 开怪线 {floor:.0f}% → 不拉新怪，{how}"
                     f"；回到 {floor:.0f}% 再找怪；被打照常迎战")
        self._event("mana_rest", f"血 {obs.player_hp:.0f}% 蓝 {obs.mana:.0f}% → 先回蓝")

    def _mana_rest_end(self, obs, now, msg):
        from .state_machine import State
        self.rest_mode = None
        self.rest_end_t = now
        self.state = State.SEARCHING
        self.log(msg)
        h = self.cfg["heal"]
        if not obs.in_combat and obs.player_hp < float(h.get("post_ht_below_pct", 70)) and self._post_on() \
                and not self._mana_short(obs, "heal"):
            self.post_due, self.post_healed = True, False      # 蓝够了 → 顺便放一个治疗之触再打
            return self.start_rest(obs, "回蓝完成，血还低")
        if not self._is_bear(obs):
            self._to_bear(obs)

    def _mana_rest(self, obs, now):
        from .state_machine import State
        c = self._mr_cfg()
        if obs.in_combat and (self._being_hit(obs) or self._aggro(obs)):
            self.rest_mode = None
            self.log(f"💤 回蓝时被打 → 变{self._cform_cn()}迎战")
            if not self._is_bear(obs):
                self._to_bear(obs)
            self.state = State.SEARCHING
            return
        need = self._mr_target(obs)
        floor = self._mr_floor()
        mana = obs.mana
        if mana is not None and mana >= need:
            if need <= floor and getattr(self, "mr_hp_rule", False) and obs.player_hp >= float(c["mana_rest_hp_ok"]):
                return self._mana_rest_end(obs, now, f"💤 血量回到 {obs.player_hp:.0f}%、法力 {mana:.0f}% ≥ 开怪线 {floor:.0f}%"
                                                     f"（用了 {now - self.mr_t0:.0f} 秒）→ 继续找怪")
            return self._mana_rest_end(obs, now, f"💤 回蓝完成：法力 {mana:.0f}% ≥ {need:.0f}%（用了 {now - self.mr_t0:.0f} 秒）→ 继续找怪")
        limit = float(c["mana_rest_timeout_s"])
        if mana is not None and mana < floor:
            limit = max(limit, FLOOR_TIMEOUT_S)     # v8.47：法力不到开怪线 → 多等（最多 300 秒）
        if now - self.mr_t0 > limit:
            self.mr_skip_until = now + 60.0
            return self._mana_rest_end(obs, now, f"⚠️ 回蓝 {limit:.0f} 秒还没到 {need:.0f}%（法力 {mana}）"
                                                 "→ 不等了，继续找怪；60 秒内不再停下回蓝")
        drink = str(c.get("drink_key") or "").strip()
        if drink and not self.mr_drank:
            if self._in_any_form(obs):
                if now - self.mr_shift_t >= 1.5:
                    self.mr_shift_t = now
                    self._to_caster(obs)
                return
            if obs.casting:
                return
            self.mr_drank = True
            self._ctx("回蓝：喝水")
            self.inp.press(drink)
            self.log(f"🥤 喝水「{drink}」")
            return
        if now - self.mr_note_t >= 15.0:
            self.mr_note_t = now
            self.dlog(f"[回蓝] 法力 {mana} / 要 {need:.0f}%，血量 {obs.player_hp:.0f}%（{now - self.mr_t0:.0f} 秒）")


class WalkFarMixin:
    def _wf_cfg(self) -> dict:
        c = dict(WF_DEFAULTS)
        m = self.cfg.get("move") or {}
        c.update({k: m[k] for k in WF_DEFAULTS if k in m})
        return c

    def _wf_want(self, obs, hit) -> bool:
        """选中的活怪离得远、还没进战斗、没挨打 → 先走过去（不按开怪键、不右键）。"""
        c = self._wf_cfg()
        if self._wf_plate(obs):
            return False                    # v8.50：看得到它的姓名板/标记 → 直接右键点，让游戏自己走过去
        return bool(c["walk_far"]) and not obs.in_combat and not hit and obs.source == "addon" \
            and obs.range in ("far", "ranged") and obs.pos is not None

    def _wf_plate(self, obs) -> bool:
        """v8.50：选中的怪的姓名板（或插件标在它中间的标记）在屏幕上。"""
        if not self._plates_first():
            return False
        return obs.selected_plate is not None or self._has_marker(obs)

    def _wf_start(self, obs, now):
        from .state_machine import State
        self.wf = {"t0": now, "pos": obs.pos, "pos_t": now, "steer_t": -1e9}
        self.opened = False                 # 走到近战距离后 _click_opener 再按开怪键
        self.state = State.APPROACHING
        self.approach_start = now
        self.log(f"🚶 选中的怪离得远（距离 {obs.range}）→ 先朝它走过去，到近战距离再开打（不算战斗）")

    def _wf_end(self, msg=None):
        self.wf = None
        if self.walking:
            self._stop_walk()
        if msg:
            self.log(msg)

    def _wf_step(self, obs, now) -> bool:
        """接近目标时的“走过去”。True = 这一帧处理了；False = 交回原来的接近逻辑（到近战/进战斗/不再走）。"""
        from .state_machine import State
        wf = getattr(self, "wf", None)
        if not wf:
            return False
        c = self._wf_cfg()
        if obs.in_combat or obs.range == "melee" or self._being_hit(obs):
            self._wf_end()
            self.dlog(f"[走过去] 到了（距离 {obs.range}，战斗 {obs.in_combat}），用了 {now - wf['t0']:.1f} 秒")
            return False
        if now - wf["t0"] > float(c["walk_far_max_s"]):
            self._wf_end(f"🚶 朝远处的怪走了 {now - wf['t0']:.0f} 秒还没到近战距离 → 换目标")
            self._clear_target(obs)
            self.state = State.SEARCHING
            return True
        if obs.pos is not None:
            if wf["pos"] is None or distance(wf["pos"], obs.pos) >= float(self.cfg.get("area", {}).get("move_eps", 0.03)):
                wf["pos"], wf["pos_t"] = obs.pos, now
            elif self.walking and now - wf["pos_t"] > float(c["walk_far_stuck_s"]):
                self._wf_end(f"🚶 朝远处的怪走，{float(c['walk_far_stuck_s']):.1f} 秒坐标没变（卡住了）→ 改成右键点它走过去")
                self._engage(obs)
                return True
        if self._wf_plate(obs) and not self._rclick_wait(now):
            self._wf_end("🖱️ 走过去的路上看到它的姓名板了 → 马上右键点它，让游戏自己走过去")
            self._engage(obs)
            return True
        if self._turn_toward(obs):           # 看不到它的姓名板 → 先转过去（转太多次会自己放弃目标）
            if self.walking:
                self._stop_walk()
            return True
        if self.state != State.APPROACHING:
            self._wf_end()
            return True
        tx = None
        if getattr(obs, "marker_center", False) and getattr(obs, "marker", None) is not None:
            tx = obs.marker
        elif obs.selected_plate is not None:
            p = obs.selected_plate
            tx = (p["x"] + p["w"] / 2, p["y"] + p["h"] / 2)
        if tx is not None and now - wf["steer_t"] >= 0.5:
            deg = self._far_bearing(obs, tx[0], tx[1])
            if abs(deg) > 15:
                wf["steer_t"] = now
                self._far_turn(deg, f"走过去：目标偏 {deg:+.0f}°，修正方向")
        self._start_walk(obs)
        return True

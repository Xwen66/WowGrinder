"""v8.44：断线自动重连（t181u）。
截图认出断线弹窗 / 登录界面 / 选角色界面（bot/glue.py）→ 每 interval_s 秒（默认 10）按一次 Enter，直到回到游戏世界
（插件色块连续 ok_s 秒读得到、且没再看到登录界面）→ 继续打怪。不设放弃时间。
只在游戏窗口在前台时才会走到这里（runner 先检查前台）。
注意：选角色界面左上角会被插件读取器误读成“读得到插件”（source=addon、血 0、目标死了），所以截图认出的界面优先。"""
from __future__ import annotations

RC_DEFAULTS = {
    "enabled": True,        # 断线自动重连
    "interval_s": 10.0,     # 每隔几秒按一次 Enter
    "confirm_s": 2.0,       # 连续认出这么久才开始按（防误判）
    "ok_s": 3.0,            # 插件色块连续读得到这么久（且没再看到登录界面）才算回到游戏
    "blind_after_s": 30.0,  # v8.45：重连中，界面没认出来、插件也读不到 这么久之后，照样按 Enter（读条/没认出来的登录界面）
    "lost_shot_s": 30.0,    # v8.45：平时插件读不到这么久（又没认出断线界面）→ 存一张截图查原因（10 分钟一次）
    "key": "enter",
}


class ReconnectMixin:
    def _rc_init(self):
        self.rc_active = False      # 正在重连
        self.rc_since = None        # 第一次认出断线/登录界面的时间
        self.rc_last = -1e9         # 上次按 Enter
        self.rc_n = 0
        self.rc_wait_noted = False
        self.rc_ok_since = None     # 重连中：插件色块开始连续读得到的时间
        self.rc_seen = -1e9         # v8.45：重连中最后一次认出界面 / 读到插件的时间
        self.rc_lost_since = None   # v8.45：插件色块读不到、又没认出断线界面 的开始时间（存截图查原因）

    def _rc_cfg(self) -> dict:
        c = dict(RC_DEFAULTS)
        c.update(self.cfg.get("reconnect") or {})
        return c

    def _reconnect_tick(self, obs, now) -> bool:
        """True = 这一帧交给重连处理，别的都不做。"""
        c = self._rc_cfg()
        if not c.get("enabled", True):
            return False
        if obs.source != "addon" and not getattr(obs, "glue", False) and not self.rc_active:
            # v8.45：读不到插件又没认出来 → 30 秒后存一张截图 + 分数（10 分钟一次），下次没认出来的界面能拿来做模板
            if self.rc_lost_since is None:
                self.rc_lost_since = now
            elif now - self.rc_lost_since >= float(c.get("lost_shot_s", 30.0)):
                self._event("addon_lost", f"读不到插件 {now - self.rc_lost_since:.0f} 秒，也没认出断线界面"
                                          f"（{getattr(obs, 'glue_info', '')}）", 600)
        else:
            self.rc_lost_since = None
        if getattr(obs, "glue", False) or obs.source == "addon":
            self.rc_seen = now
        if getattr(obs, "glue", False):
            self.rc_wait_noted = False
            self.rc_ok_since = None
            if not self.rc_active:
                if self.rc_since is None:
                    self.rc_since = now
                if now - self.rc_since < float(c.get("confirm_s", 2.0)):
                    return True             # 先确认，别的都不做（这个界面会被当成 0 血/死亡）
                self.rc_active, self.rc_n, self.rc_last = True, 0, -1e9
                if self.walking:
                    self._stop_walk()
                try:
                    self.inp.release_all()
                except Exception:  # noqa: BLE001
                    pass
                self.log(f"🔌 认出断线/登录/选角色界面（{obs.glue_what}）→ 每 {float(c['interval_s']):.0f} 秒按一次 Enter 重连")
                self._event("disconnect", f"断线/登录界面：{obs.glue_what}", 0)
            self._rc_press(c, now, obs)
            return True
        self.rc_since = None
        if not self.rc_active:
            return False
        if obs.source == "addon":
            if self.rc_ok_since is None:
                self.rc_ok_since = now
            if now - self.rc_ok_since < float(c.get("ok_s", 3.0)):
                return True                 # 再看一会儿，确认真的进游戏了
            self.log(f"✅ 重连成功（按了 {self.rc_n} 次 Enter）→ 继续打怪")
            self._event("reconnected", "重连成功")
            from .state_machine import State
            self.state = State.SEARCHING
            self._reset_target_tracking(now)
            self.zero_hp_since = None
            self.addon_seen = True
            self.rc_active, self.rc_wait_noted, self.rc_ok_since = False, False, None
            return False
        self.rc_ok_since = None
        # v8.45：已经在重连了，现在既没认出界面、插件也读不到（读条 / 没认出来的登录界面）。
        # 先等 blind_after_s（30 秒，读条一般更快；刚进游戏插件偶尔闪一下读不到，不能这时按 Enter —— 会打开聊天框），
        # 还是这样 → 照样每 10 秒按 Enter，直到进游戏（登录界面按 Enter = 登录）
        blind = float(c.get("blind_after_s", 30.0))
        if not self.rc_wait_noted:
            self.rc_wait_noted = True
            self.log(f"⏳ 重连中：没认出界面、也还没进游戏（读条或登录中）→ 等着；{blind:.0f} 秒后还这样就照样按 Enter")
        if now - self.rc_seen >= blind:
            self._rc_press(c, now, obs)
        return True

    def _rc_press(self, c, now, obs=None):
        if now - self.rc_last >= float(c.get("interval_s", 10.0)):
            self.rc_last = now
            self.rc_n += 1
            self._ctx(f"断线重连：按 Enter（第 {self.rc_n} 次）")
            self.inp.press(str(c.get("key") or "enter"))
            o = obs if obs is not None else getattr(self, "last_obs", None)
            what = getattr(o, "glue_what", "") if o is not None else ""
            self.log(f"🔌 断线 → 按 Enter 重连（第 {self.rc_n} 次）" + (f"：看到 {what}" if what else "：界面没认出来，照样按"))
            # v8.63：每按一次都存截图（看按完以后变成什么界面）；最多每 interval_s 秒一张
            self._event("reconnect_press", f"按 Enter 重连第 {self.rc_n} 次（{what or '没认出界面'}；"
                                           f"{getattr(o, 'glue_info', '') if o is not None else ''}）", 0)

    def reconnect_label(self) -> str:
        return f"🔌 断线重连中（按了 {self.rc_n} 次 Enter）"

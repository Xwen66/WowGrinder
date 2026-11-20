"""v8.31 自动跑尸（需要插件 BotStatus v0.12）。

死亡 → 等 N 秒 → 按“释放灵魂/复活键”（宏：/run RepopMe()）→ 灵魂状态按 自己坐标+朝向 走向尸体（左右转 + 按住前进，
卡住就跳/平移/绕一下）→ 出现“复活”框（或走到尸体附近）停下 → 转一圈看附近有没有会打我的怪（插件 v0.12 灵魂状态下
给敌对的怪画 黄|蓝 标记），有就等，最多等 N 秒 → 按同一个键复活（宏：/run RetrieveCorpse()）→ 人形回血回蓝到设定值
（血低先放一次治疗之触）→ 变回战斗形态继续打怪。连续死多少次都不停。
"""
from __future__ import annotations

import math

from .navigation import angle_diff, bearing_to, distance

CORPSE_DEFAULTS = {
    "enabled": True,          # 自动跑尸（关 = 死了就停止，跟以前一样）
    "release_delay_s": 5.0,   # 死后等几秒再释放灵魂
    "retry_s": 3.0,           # 按了释放/复活没反应，隔几秒再按
    "arrive_dist": 0.3,       # 没出现“复活”框时：离尸体这么近（地图坐标）也算到了
    "safe_check": True,       # 复活前转一圈看附近有没有怪
    "near_pct": 35,           # 标记在屏幕上方 N% 以下 = 离得近
    "safe_wait_s": 30.0,      # 附近有怪最多等几秒（到时间照样复活）
    "safe_rescan_s": 5.0,     # 有怪时隔几秒再看一圈
    "run_timeout_s": 600.0,   # v8.38：跑尸每隔这么多秒写一次提醒（不再停止，一直试）
    "stuck_s": 2.5,           # 按着前进这么久位置没变 = 卡住
    "rest_hp_pct": 90,        # 复活后血量回到多少再打怪
    "rest_mana_pct": 80,      # 复活后法力回到多少再打怪
    "rest_timeout_s": 120.0,  # 复活后最多回多久
    "route": [],              # v8.39 跑尸路线点 [x, y, 地图]（从复活点开始）
    "route_arrive_dist": 0.15,
    "route_shortcut_dist": 1.0,
    "frozen_s": 60.0,
}


# v8.38 绕路偏角（度，正 = 往左）：先小后大，左右交替
DETOURS = (60, -60, 90, -90, 120, -120, 150, -150, 180)


class CorpseRunMixin:
    def _cr_cfg(self) -> dict:
        c = dict(CORPSE_DEFAULTS)
        c.update(self.cfg.get("corpse") or {})
        return c

    def _corpse_on(self) -> bool:
        return bool(self._cr_cfg().get("enabled", True))

    def _cr_key(self) -> str:
        return (self.cfg["keys"].get("spirit") or "").strip()

    def corpse_label(self) -> str:
        cr = getattr(self, "cr", None)
        if not cr:
            return ""
        return {"dead": "死亡，等释放灵魂", "run": "跑尸中", "check": "到尸体了，看附近有没有怪",
                "wait": "附近有怪，等它走开", "revive": "复活中", "rest": "复活后回血回蓝"}.get(cr["phase"], "跑尸")

    # ------------------------------------------------------------------ 入口
    def _corpse_tick(self, obs, now) -> bool:
        """返回 True = 这一帧由跑尸处理了。"""
        if getattr(obs, "death_info", False) and not getattr(self, "np_noted", False):
            self.np_noted = True
            nd = getattr(obs, "np_dist", None)
            self.log(f"📏 插件 v0.12：姓名板显示距离 {nd if nd is not None else '读不到'} 码"
                     "（插件已调到客户端允许的最大值；更远的怪只显示名字，程序朝红名走过去）")
        cr = getattr(self, "cr", None)
        if cr is None:
            if not (obs.player_dead and obs.source == "addon"):
                return False
            if not self._corpse_on():
                return False
            if not getattr(obs, "death_info", False):
                self._warn("crold", "⚠️ 死了，但插件不是 v0.12（没有尸体坐标）→ 不能自动跑尸，照旧停止。"
                                    "把新插件复制到游戏 AddOns 文件夹，游戏里 /reload", 30)
                return False
            self._cr_begin(obs, now)
        return self._cr_step(obs, now)

    def _cr_begin(self, obs, now):
        from .state_machine import State
        self._stop_walk()
        try:
            self.inp.release_all()
        except Exception:  # noqa: BLE001
            pass
        self.deaths = getattr(self, "deaths", 0) + 1
        if getattr(self, "mb_active", False):      # v8.38：死了 → 不再是“多只怪临时熊”，复活后回猎豹
            self.mb_active = False
            self.mb_off_t = now
        self.pending_loot = 0
        self.post_due = False
        ghost = bool(getattr(obs, "ghost", False))
        self.cr = {"phase": "run" if ghost else "dead", "t": now, "press_t": -1e9, "presses": 0,
                   "run_t0": now, "prog_pos": obs.pos, "prog_t": now, "stuck": 0, "miss_t": None,
                   "safe_t0": None, "scan_i": 0, "near": 0, "wait_until": 0.0, "rest_t0": None,
                   "heal_t": -1e9, "walking": False}
        self.state = State.CORPSE
        self._event("death", f"第 {self.deaths} 次死亡", 0)
        if ghost:
            self.log(f"💀 已经是灵魂状态（第 {self.deaths} 次死亡）→ 跑尸")
        else:
            d = float(self._cr_cfg()["release_delay_s"])
            self.log(f"💀 死亡（这次运行第 {self.deaths} 次）→ {d:.0f} 秒后释放灵魂，然后自动跑尸")

    def _cr_phase(self, phase, now):
        self.cr["phase"] = phase
        self.cr["t"] = now
        self.cr["press_t"] = -1e9
        self.cr["presses"] = 0

    def _cr_press(self, why):
        key = self._cr_key()
        if not key:
            self._warn("crnokey", "⚠️ 没填“释放灵魂/复活键”（基本设置页）→ 不能自动释放/复活，请手动点", 30)
            return False
        self._ctx(why)
        self.inp.press(key)
        self.cr["press_t"] = self.clock()
        self.cr["presses"] += 1
        return True

    # ------------------------------------------------------------------ 每帧
    def _cr_step(self, obs, now) -> bool:
        from .state_machine import State
        c, cr = self._cr_cfg(), self.cr
        self.state = State.CORPSE
        ph = cr["phase"]
        dead, ghost = bool(obs.player_dead), bool(getattr(obs, "ghost", False))
        if ph in ("dead", "run", "check", "wait", "revive") and not dead:
            self._stop_walk()
            self.log(f"✨ 已复活（第 {self.deaths} 次死亡）→ 先回血回蓝"
                     f"（血 ≥ {c['rest_hp_pct']}%、蓝 ≥ {c['rest_mana_pct']}%，最多 {float(c['rest_timeout_s']):.0f} 秒）")
            self._cr_phase("rest", now)
            cr["rest_t0"] = now
            return True
        if ph == "rest":
            return self._cr_rest(obs, now)
        if ph != "dead" and not ghost:
            # 灵魂状态没了但还是死的（比如又变回尸体）→ 重新释放
            self._stop_walk()
            self._cr_phase("dead", now)
            ph = "dead"
        if ph == "dead":
            if ghost:
                self.log("👻 已释放灵魂 → 朝尸体跑")
                self._cr_phase("run", now)
                cr["run_t0"], cr["prog_pos"], cr["prog_t"] = now, obs.pos, now
                return True
            if now - cr["t"] < float(c["release_delay_s"]) or now - cr["press_t"] < float(c["retry_s"]):
                return True
            if self._cr_press("释放灵魂（宏 /run RepopMe()）"):
                self.log(f"👻 释放灵魂：按「{self._cr_key()}」（第 {cr['presses']} 次）")
                if cr["presses"] >= 3:
                    self._warn("crrel", f"⚠️ 按了 {cr['presses']} 次「{self._cr_key()}」还没释放灵魂："
                                        "检查游戏里这个键有没有绑宏 /run RepopMe()（见说明）", 30)
            return True
        every = max(60.0, float(c["run_timeout_s"]))
        if now - cr["run_t0"] > every * (cr.get("long_n", 0) + 1):
            # v8.38：不再停止，一直试；每 run_timeout_s 秒写一次提醒（不响）
            cr["long_n"] = cr.get("long_n", 0) + 1
            d0, dm = cr.get("d0"), cr.get("min_corpse_d")
            self.log(f"👻 跑尸已经 {now - cr['run_t0']:.0f} 秒还没复活（卡住 {cr['stuck']} 次"
                     + (f"，离尸体最近 {dm:.2f}，开始时 {d0:.2f}" if d0 is not None and dm is not None else "")
                     + "）→ 不停止，继续绕路试")
        if ph == "run":
            return self._cr_run(obs, now)
        if ph in ("check", "wait"):
            return self._cr_check(obs, now)
        if ph == "revive":
            if not obs.res_popup and obs.corpse_pos is not None and obs.pos is not None \
                    and distance(obs.pos, obs.corpse_pos) > float(c["arrive_dist"]) * 2:
                self.log("👻 “复活”框没了（离尸体太远）→ 再走近一点")
                self._cr_phase("run", now)
                return True
            if now - cr["press_t"] >= float(c["retry_s"]):
                if self._cr_press("复活（宏 /run RetrieveCorpse()）"):
                    self.log(f"✨ 复活：按「{self._cr_key()}」（第 {cr['presses']} 次）")
                    if cr["presses"] >= 3:
                        self._warn("crres", f"⚠️ 按了 {cr['presses']} 次「{self._cr_key()}」还没复活："
                                            "检查宏里有没有 RetrieveCorpse()（见说明），或者离尸体太远", 30)
            return True
        return True

    # ------------------------------------------------------------------ 跑
    def _cr_run(self, obs, now) -> bool:
        c, cr, a = self._cr_cfg(), self.cr, self.cfg["area"]
        if obs.res_popup:
            self._stop_walk()
            self.log("👻 出现“复活”框 → 到尸体旁边了")
            return self._cr_arrived(now)
        if obs.pos is None or obs.corpse_pos is None:
            self._stop_walk()
            cr["miss_t"] = cr["miss_t"] or now
            self._warn("crnopos", "👻 读不到" + ("自己坐标" if obs.pos is None else "尸体坐标")
                                  + "（副本里/别的大陆？）→ 原地等", 10)
            return True
        cr["miss_t"] = None
        d = distance(obs.pos, obs.corpse_pos)
        cr.setdefault("d0", d)
        cr["min_corpse_d"] = min(d, cr.get("min_corpse_d", d))
        if d <= float(c["arrive_dist"]):
            self._stop_walk()
            self.log(f"👻 走到尸体附近了（距离 {d:.2f}）")
            return self._cr_arrived(now)
        self._cr_frozen(obs, now)
        tgt, tname, dt = self._cr_target(obs, d)   # v8.39：先走路线点，走完（或抄近路）再去尸体
        cr["tgt"] = tgt
        best = cr.get("best_d")
        if cr.get("best_key") != tname:
            best = None                           # 换了目标（到了一个路线点）→ 卡住等级从头来
            cr["best_key"] = tname
        if best is None or dt < best - 0.15:
            # v8.38：离目标明显更近了 → 换了个地方，卡住升级/记住的方向从头来
            if best is not None and cr.get("lvl", 0):
                self.dlog(f"[跑尸] 离{tname} {dt:.2f}（之前最近 {best:.2f}）→ 绕路有效，卡住等级清零")
            cr["best_d"], cr["lvl"], cr["blocked"], cr["detour_i"] = dt, 0, [], 0
        if self.walking:
            if cr["prog_pos"] is None or distance(obs.pos, cr["prog_pos"]) > float(a.get("move_eps", 0.03)):
                cr["prog_pos"], cr["prog_t"] = obs.pos, now
            elif now - cr["prog_t"] > float(c["stuck_s"]):
                return self._cr_unstuck(obs, now)
        else:
            cr["prog_pos"], cr["prog_t"] = obs.pos, now
        if obs.facing is None:
            self._warn("crnoface", "👻 读不到朝向 → 先往前走", 10)
            self._start_walk(obs)
            return True
        delta = angle_diff(bearing_to(obs.pos, tgt), obs.facing)
        if now - cr.get("log_t", -1e9) >= 10:
            cr["log_t"] = now
            self.log(f"👻 跑尸：" + (f"去{tname}，离它 {dt:.2f}，" if tname != "尸体" else "")
                     + f"离尸体 {d:.2f}（地图坐标），方向偏 {math.degrees(delta):+.0f}°")
        if abs(delta) > math.radians(float(a.get("face_tolerance_deg", 25))):
            self._stop_walk()
            self._turn(delta)
            return True
        if abs(delta) > math.radians(float(a.get("walk_correct_deg", 8))):
            self._turn(delta)
        self._start_walk(obs)
        return True

    # ------------------------------------------------------------------ v8.39 跑尸路线
    def _cr_route_init(self, obs, d):
        """第一次跑的时候：取当前地图上的路线点；从离自己最近的点开始（已经走过它了就从下一个开始）。"""
        cr, c = self.cr, self._cr_cfg()
        raw = list(c.get("route") or [])
        pts = []
        for p in raw:
            try:
                m = int(p[2]) if len(p) > 2 else 0
                if m and obs.map_id and m != int(obs.map_id):
                    continue
                pts.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError, IndexError):
                continue
        cr["route"], cr["wi"] = pts, len(pts)
        if not raw:
            return
        if not pts:
            self.log(f"🗺️ 跑尸路线有 {len(raw)} 个点，但都不在当前地图 {obs.map_id} → 直接朝尸体走")
            return
        dists = [distance(obs.pos, p) for p in pts]
        j = min(range(len(pts)), key=lambda i: dists[i])
        if j + 1 < len(pts) and distance(obs.pos, pts[j + 1]) < distance(pts[j], pts[j + 1]):
            j += 1                                # 已经走过最近的点（离下一个点比它们之间还近）→ 从下一个开始
        cr["wi"] = j
        self.log(f"🗺️ 跑尸路线：共 {len(pts)} 个点，从 #{j + 1} 开始（离它 {dists[j]:.2f}，离尸体 {d:.2f}）"
                 + ("" if j == 0 else "（前面的点离得更远/已经走过，跳过）") + "；走完再朝尸体走")

    def _cr_target(self, obs, d):
        """返回 (目标坐标, 名字, 距离)。有路线点没走完 → 下一个路线点；走完/离尸体够近 → 尸体。"""
        cr, c = self.cr, self._cr_cfg()
        if cr.get("route") is None:
            self._cr_route_init(obs, d)
        pts, n = cr["route"], len(cr["route"])
        arrive = float(c.get("route_arrive_dist", 0.15))
        short = float(c.get("route_shortcut_dist", 1.0))
        while cr["wi"] < n:
            i = cr["wi"]
            dw = distance(obs.pos, pts[i])
            if dw <= arrive:
                cr["wi"] += 1
                self.log(f"🗺️ 走到路线点 #{i + 1}/{n}（距离 {dw:.2f}）"
                         + (f"→ 去 #{i + 2}" if i + 1 < n else "→ 路线走完了，朝尸体走") + f"（离尸体 {d:.2f}）")
                continue
            if d <= short and d < dw:
                cr["wi"] = n
                self.log(f"🗺️ 离尸体只有 {d:.2f}（≤ {short:.2f}，比离路线点 #{i + 1} 的 {dw:.2f} 还近）"
                         f"→ 不走剩下的 {n - i} 个点，直接去尸体")
                break
            return pts[i], f"路线点 #{i + 1}/{n}", dw
        return obs.corpse_pos, "尸体", d

    def _cr_frozen(self, obs, now):
        """v8.38/v8.39：位置 frozen_s 秒一点没变 → 截图 + 提示（只提示，不做别的）。"""
        cr, c = self.cr, self._cr_cfg()
        ref = cr.get("mv_ref")
        if ref is None or distance(obs.pos, ref[0]) > 0.05:
            cr["mv_ref"] = (obs.pos, now)
            return
        lim = float(c.get("frozen_s", 60.0))
        if now - ref[1] >= lim and now - cr.get("frozen_t", -1e9) >= 300:
            cr["frozen_t"] = now
            self._event("corpse_frozen", f"跑尸 {now - ref[1]:.0f} 秒位置没变", 60)
            self.log(f"👻 ⚠️ 跑尸 {now - ref[1]:.0f} 秒位置一点都没变（离目标一点都没变近）：可能角色根本没在动"
                     "（被什么挡住/卡在地形里），或者插件坐标没更新 → 请看一眼游戏画面（已截图）")

    def _cr_detour(self, obs):
        """v8.38：选绕路方向（相对“朝尸体的方向”的偏角，度）。从小到大轮着试，跳过记住的走不通的朝向（±25°）。"""
        cr = self.cr
        tgt = cr.get("tgt") or obs.corpse_pos     # v8.39：绕路相对“朝当前目标（路线点/尸体）的方向”
        direct = bearing_to(obs.pos, tgt) if (obs.pos is not None and tgt is not None) else None
        blocked = cr.setdefault("blocked", [])
        n = len(DETOURS)
        for k in range(n):
            off = DETOURS[(cr.get("detour_i", 0) + k) % n]
            if direct is None:
                cr["detour_i"] = cr.get("detour_i", 0) + k + 1
                return off
            head = direct + math.radians(off)
            if all(abs(angle_diff(head, b)) > math.radians(25) for b in blocked):
                cr["detour_i"] = cr.get("detour_i", 0) + k + 1
                return off
        blocked.clear()                           # 各个方向都卡过 → 忘掉重来
        off = DETOURS[cr.get("detour_i", 0) % n]
        cr["detour_i"] = cr.get("detour_i", 0) + 1
        return off

    def _cr_unstuck(self, obs, now) -> bool:
        """v8.38 跑尸卡住，越来越用力地绕：
        第 1 次（在一个地方）：跳 + 平移；之后：后退 → 换方向（±60/90/120/150/180°，跳过记住的走不通的朝向）→
        往前走 2/3/4/5/6 秒（越卡越久）。离尸体明显更近了就从头来。一直试，不停止。"""
        cr, k = self.cr, self.cfg["keys"]
        cr["stuck"] += 1
        cr["lvl"] = cr.get("lvl", 0) + 1
        n, lvl = cr["stuck"], cr["lvl"]
        if obs.facing is not None:
            bl = cr.setdefault("blocked", [])
            bl.append(obs.facing)                 # 记住这个朝向走不通
            del bl[:-12]
        loud = n <= 5 or n % 10 == 0              # 前几次和每 10 次写日志，其余只写详细日志
        if n == 5 or n % 30 == 0:
            self._event("corpse_stuck", f"跑尸卡住第 {n} 次", 60)   # 存一张截图，方便看卡在哪
        say = self.log if loud else self.dlog
        if lvl == 1:
            say(f"👻 跑尸卡住了（第 {n} 次）→ 跳 + 平移")
            if k.get("jump"):
                self.inp.press(k["jump"])
            if k.get("strafe"):
                self.inp.press(k["strafe"], hold_ms=500)
        else:
            off = self._cr_detour(obs)
            walk_ms = 1000 * min(6, lvl)
            back = k.get("back")
            say(f"👻 跑尸卡住了（第 {n} 次，等级 {lvl}）→ " + ("后退 + " if back else "")
                + f"往{'左' if off > 0 else '右'}绕 {abs(off)}° 走 {walk_ms / 1000:.0f} 秒")
            self._stop_walk()
            if back:
                self.inp.press(back, hold_ms=700)
            if k.get("jump") and lvl % 2:
                self.inp.press(k["jump"])
            self._turn(math.radians(off))
            self.inp.press(k.get("forward", "w"), hold_ms=walk_ms)
        cr["prog_pos"], cr["prog_t"] = obs.pos, self.clock()
        return True

    def _cr_arrived(self, now) -> bool:
        c = self._cr_cfg()
        if c.get("safe_check", True):
            self._cr_phase("check", now)
            self.cr.update(safe_t0=now, scan_i=0, near=0)
        else:
            self._cr_phase("revive", now)
        return True

    # ------------------------------------------------------------------ 复活前看附近有没有怪
    def _cr_near(self, obs) -> int:
        fw, fh = getattr(obs, "frame_size", (0, 0)) or (0, 0)
        if not fh:
            return 0
        lim = fh * float(self._cr_cfg()["near_pct"]) / 100.0
        return sum(1 for p in (obs.nameplates or []) if p.get("kind") != "selected"
                   and lim <= p["y"] + p["h"] / 2 <= fh * 0.85)

    def _cr_check(self, obs, now) -> bool:
        c, cr = self._cr_cfg(), self.cr
        if cr["phase"] == "wait":
            if now < cr["wait_until"]:
                return True
            self._cr_phase("check", now)
            cr.update(scan_i=0, near=0)
            return True
        if not obs.res_popup and obs.corpse_pos is not None and obs.pos is not None \
                and distance(obs.pos, obs.corpse_pos) > float(c["arrive_dist"]) * 2:
            self._cr_phase("run", now)
            return True
        cr["near"] += self._cr_near(obs)
        if cr["scan_i"] < 3:                      # 4 个方向各看一眼（每次右转 90°）
            cr["scan_i"] += 1
            self._turn(-math.pi / 2)
            self.inp.sleep(float(self.cfg.get("scan", {}).get("settle_ms", 350)) / 1000.0)   # 等姓名板出来
            return True
        self._turn(-math.pi / 2)                 # 转回原来的方向
        waited = now - cr["safe_t0"]
        if cr["near"] and waited < float(c["safe_wait_s"]):
            self.log(f"👀 尸体附近看到 {cr['near']} 次会打我的怪 → {float(c['safe_rescan_s']):.0f} 秒后再看"
                     f"（已等 {waited:.0f}/{float(c['safe_wait_s']):.0f} 秒）")
            self.cr["phase"] = "wait"
            cr["wait_until"] = now + float(c["safe_rescan_s"])
            return True
        if cr["near"]:
            self.log(f"👀 附近还有怪，但已经等了 {waited:.0f} 秒 → 照样复活")
        else:
            self.log("👀 附近没看到会打我的怪 → 复活")
        if obs.res_delay:
            self._warn("crdelay", f"⏳ 复活还要等 {obs.res_delay:.0f} 秒", 5)
        self._cr_phase("revive", now)
        return True

    # ------------------------------------------------------------------ 复活后回血回蓝
    def _cr_rest(self, obs, now) -> bool:
        from .state_machine import State
        c, cr, h = self._cr_cfg(), self.cr, self.cfg["heal"]
        hp, mana = obs.player_hp, obs.mana
        if obs.in_combat or self._being_hit(obs):
            self.log("⚔️ 复活后被打了 → 先打怪（回血回蓝中断）")
            return self._cr_done(obs, fight=True)
        hp_ok = hp >= float(c["rest_hp_pct"])
        mana_ok = mana is None or mana >= float(c["rest_mana_pct"])
        if hp_ok and mana_ok:
            self.log(f"✨ 复活后回好了（血 {hp:.0f}%，蓝 {mana if mana is not None else '?'}）→ 变回{self._cform_cn()}继续打怪")
            return self._cr_done(obs)
        if now - cr["rest_t0"] > float(c["rest_timeout_s"]):
            self.log(f"✨ 复活后回了 {float(c['rest_timeout_s']):.0f} 秒（血 {hp:.0f}%，蓝 {mana if mana is not None else '?'}）"
                     f" → 不等了，变回{self._cform_cn()}继续")
            return self._cr_done(obs)
        if obs.casting:
            return True
        key = (self.cfg["keys"].get("healing_touch") or "").strip()
        if not hp_ok and key and hp < 70 and (mana is None or mana >= 35) \
                and now - cr["heal_t"] >= float(h.get("cast_s", 3.0)) + 2.0:
            cr["heal_t"] = now
            self._ctx("复活后回血：治疗之触")
            self.inp.press(key)
            self.log(f"🩹 复活后血量 {hp:.0f}% → 放治疗之触「{key}」")
            return True
        if now - cr.get("rest_log_t", -1e9) >= 15:
            cr["rest_log_t"] = now
            self.log(f"🩹 复活后回血回蓝中：血 {hp:.0f}%，蓝 {mana if mana is not None else '?'}（人形站着回）")
        self.state = State.CORPSE
        return True

    def _cr_done(self, obs, fight=False) -> bool:
        from .state_machine import State
        self.cr = None
        self.state = State.SEARCHING
        self.last_combat_time = self.clock()
        self.zero_hp_since = None
        self._to_bear(obs)
        return not fight

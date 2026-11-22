"""v8.55（t222u）炉石回城修理（需要插件 BotStatus v0.15）。

耐久 ≤ durability_pct（脱战、在找怪/回中心时，连续 confirm_s 秒）→ 按炉石键（游戏里宏：/cancelform 换行 /use 炉石）
→ 看到读条 → 读完换地图/坐标跳了 = 到了 → 按录好的修理路线走到修理商（路线 #1 = 炉石落点，F4 标的点 = 修理商）
→ 按“选修理商键”（宏 /tar 商人名字）+ 互动键 → 插件 v0.15 打开商人自动全部修理（顺便卖灰色物品）
→ 耐久回到 repaired_pct → 按 Esc 关商人 → 照路线后半段（修理商 → 刷怪区）走回去（换地图时朝最后一段的方向一直走，直到进入下一张图）
→ 走到最近的同地图中心，接着打怪。

任何一步失败（按了没反应、卡住太多次、超时、走错地图、修不了）→ 停止 + 截图，不会到处乱走。
炉石在冷却 / 没设置好 → 接着打，耐久 ≤ stop_pct 才停止。
"""
from __future__ import annotations

import math

from .navigation import angle_diff, bearing_to, distance

REPAIR_DEFAULTS = {
    "enabled": True,          # 炉石回城修理（还要录好路线、填好炉石键/选修理商键才会用）
    "use_hearth": True,       # v8.56：关 = 不用炉石，先走到最近的中心，再把回程路线倒着走到修理商，修完照回程路线走回来
    "durability_pct": 20,     # 最低耐久 ≤ 这个 → 回城修理
    "stop_pct": 5,            # 炉石冷却/没设置好修不了时：耐久 ≤ 这个就停止（0 = 不停）
    "confirm_s": 3.0,         # 耐久低要连续这么久才回城（防读错）
    "route": [],              # 修理路线 [x, y, 地图]：#1 = 炉石落点 … 修理商 … 回到刷怪区
    "vendor_i": -1,           # 路线里哪个点是修理商（0 开始；-1 = 还没标）
    "arrive_dist": 0.25,      # 离路线点多近算到（地图坐标）
    "start_max_dist": 3.0,    # 炉石落地后离路线最近的点超过这个 → 不对（炉石绑在别处？）→ 停止
    "stuck_s": 2.5,           # 按着前进这么久位置没变 = 卡住
    "stuck_max": 8,           # 同一段卡住超过这么多次 → 停止
    "seg_timeout_s": 300.0,   # 一段路（去修理商 / 回刷怪区）最多走多久
    "cross_timeout_s": 20.0,  # 走到地图边上，最多再往前走多久进入下一张图
    "cast_timeout_s": 25.0,   # 炉石读条（10 秒）最多等多久
    "load_timeout_s": 90.0,   # 读完到落地（读图）最多等多久
    "vendor_tries": 3,        # 选修理商 + 互动 最多按几次
    "vendor_wait_s": 5.0,     # 按完等多久商人窗口没开就再按
    "repaired_pct": 90,       # 耐久回到这个 = 修好了
    "repair_wait_s": 8.0,     # 商人窗口开了最多等多久修好
    "max_attempts": 3,        # 炉石被打断（被怪打）最多重试几次，再不行就停止
    "total_timeout_s": 1200.0,  # 整个修理流程最多多久
    # v8.57（t225u）
    "ignore_combat": True,    # 去修理商 / 走回刷怪区的路上：被怪打也不还手，接着走（血量低于下面这条线才还手）
    "fight_back_hp_pct": 40,  # 无视怪物时，血量 ≤ 这个 → 先还手（会照常治疗），打完接着走；0 = 永远不还手
    "bag_trip": True,         # 背包满了也走修理流程（插件打开商人自动卖掉灰色物品，顺便修理）
    "bag_trip_cd_s": 1800.0,  # 卖完垃圾后这么久内背包又满了 → 不再专门回去卖，照旧只捡钱
    "sell_wait_s": 3.0,       # 背包满去卖垃圾：商人窗口开着至少等这么久（插件分批卖）再关
    "cat_travel": True,       # 去修理商 / 走回来的路上用猎豹形态走（快）
}

DETOURS = (60, -60, 90, -90, 120, -120)


def repair_route_msg(route, vendor_i, vendor) -> str:
    """F5/F4 加了一个修理路线点后的日志。"""
    n = len(route)
    p = route[-1]
    head = (f"🔧 已标记修理商 = 修理路线点 #{n}" if vendor else f"🔧 已添加修理路线点 #{n}")
    tip = ""
    if n == 1 and not vendor:
        tip = "（#1 应该是炉石落点）"
    elif vendor:
        tip = "（接着往回走，每到拐弯/换地图前后按 F5，一直记到刷怪区的中心附近）"
    elif 0 <= int(vendor_i) < n - 1:
        tip = f"（回程第 {n - 1 - int(vendor_i)} 个点）"
    return f"{head}：{float(p[0]):.2f},{float(p[1]):.2f}（地图 {p[2]}），共 {n} 个，已保存" + tip


class RepairMixin:
    def _rp_cfg(self) -> dict:
        c = dict(REPAIR_DEFAULTS)
        c.update(self.cfg.get("repair") or {})
        return c

    def _rp_why_cn(self, why) -> str:
        return {"dur": "修理", "bag": "卖垃圾", "both": "修理 + 卖垃圾"}.get(why, "修理")

    def _rp_init(self):
        self.rp = None                # 正在修理 {"phase", ...}
        self.rp_low_since = None
        self.rp_attempts = 0
        self.rp_old_noted = False
        self.rp_bag_cd = -1e9           # v8.57：这个时间之前背包满了不专门回去卖

    def repair_label(self) -> str:
        rp = getattr(self, "rp", None)
        if not rp:
            return ""
        return {"cast": "🔧 读炉石", "load": "🔧 回城中", "to_vendor": "🔧 去修理商", "vendor": "🔧 找修理商",
                "repairing": "🔧 修理中", "back": "🔧 走回刷怪区"}.get(rp["phase"], "🔧 修理")

    # ------------------------------------------------------------------ 设置检查
    def _rp_missing(self) -> list[str]:
        c, k = self._rp_cfg(), self.cfg["keys"]
        out = []
        if c.get("use_hearth", True) and not (k.get("hearth") or "").strip():
            out.append("炉石键")
        if not (k.get("vendor_target") or "").strip():
            out.append("选修理商键")
        if not (k.get("interact") or "").strip():
            out.append("互动键")
        route = list(c.get("route") or [])
        vi = int(c.get("vendor_i", -1) if c.get("vendor_i") is not None else -1)
        if not route:
            out.append("修理路线")
        elif not (0 <= vi < len(route)):
            out.append("修理商位置（F4）")
        elif vi >= len(route) - 1:
            out.append("修理商之后走回刷怪区的路线点")
        return out

    def _rp_route(self):
        pts = []
        for i, p in enumerate(self._rp_cfg().get("route") or []):
            try:
                pts.append((float(p[0]), float(p[1]), int(p[2]) if len(p) > 2 and p[2] else 0, i, f"路线点 #{i + 1}"))
            except (TypeError, ValueError, IndexError):
                continue
        return pts

    def _rp_stop_low(self, dur, why) -> bool:
        stop = float(self._rp_cfg().get("stop_pct", 5) or 0)
        if stop > 0 and dur is not None and dur <= stop:
            self._stop_walk()
            self.stop(f"耐久 {dur:.0f}% ≤ {stop:.0f}%，{why} → 停止，等你来修")
            return True
        return False

    # ------------------------------------------------------------------ 入口
    def _repair_tick(self, obs, now) -> bool:
        """True = 这一帧由修理处理了。"""
        if getattr(self, "rp", None) is not None:
            return self._rp_step(obs, now)
        return self._rp_want(obs, now)

    def _rp_want(self, obs, now) -> bool:
        from .state_machine import State
        c = self._rp_cfg()
        if not c.get("enabled", True) or obs.source != "addon":
            return False
        if not getattr(obs, "repinfo", False):
            if not self.rp_old_noted and (c.get("route") or []):
                self.rp_old_noted = True
                self.log("⚠️ 炉石回城修理要插件 v0.15（读耐久/炉石冷却/商人窗口）。把新插件复制到 AddOns\\BotStatus 后在游戏里 /reload")
            return False
        dur = getattr(obs, "durability", None)
        if dur is None or obs.player_dead or getattr(obs, "ghost", False):
            return False
        line = float(c.get("durability_pct", 20))
        low = dur <= line
        bag = bool(c.get("bag_trip", True)) and bool(getattr(self, "bag_full", False)) \
            and now >= getattr(self, "rp_bag_cd", -1e9)
        if not low and not bag:
            self.rp_low_since = None
            return False
        if self.state not in (State.SEARCHING, State.RETURNING) or obs.in_combat:
            return False
        if self.rp_low_since is None:
            self.rp_low_since = now
        if now - self.rp_low_since < float(c.get("confirm_s", 3.0)):
            return False
        why = "both" if (low and bag) else ("dur" if low else "bag")
        miss = self._rp_missing()
        if miss:
            if not low:
                self._warn("rpbagmiss", f"🎒 背包满了，但修理路线/键没设置好（缺：{'、'.join(miss)}）→ 不回去卖，照旧只捡钱", 600)
                return False
            self._warn("rpmiss", f"🔧 耐久 {dur:.0f}% ≤ {line:.0f}%，但回城修理没设置好（缺：{'、'.join(miss)}）→ 不回城，接着打", 600)
            return self._rp_stop_low(dur, "修理没设置好")
        if not c.get("use_hearth", True):
            self._rp_begin_walk(obs, now, dur, why)
            return True
        if not low and (not getattr(obs, "has_hearth", False) or getattr(obs, "hearth_cd", None) is None
                        or obs.hearth_cd > 0):
            self.rp_bag_cd = now + 300.0
            self.rp_low_since = None
            cd = getattr(obs, "hearth_cd", None)
            self.log("🎒 背包满了，但炉石" + ("不在背包里" if not getattr(obs, "has_hearth", False) else
                                         (f"还要 {cd} 分钟" if cd else "冷却读不到"))
                     + " → 先照旧只捡钱，5 分钟后再看")
            return False
        if not getattr(obs, "has_hearth", False):
            self._warn("rpnohs", f"🔧 耐久 {dur:.0f}% ≤ {line:.0f}%，但背包里没有炉石 → 不回城，接着打", 600)
            return self._rp_stop_low(dur, "背包里没有炉石")
        cd = getattr(obs, "hearth_cd", None)
        if cd is None or cd > 0:
            self._warn("rpcd", f"🔧 耐久 {dur:.0f}% ≤ {line:.0f}%，但炉石"
                               + (f"还要 {cd} 分钟才能用" if cd else "冷却读不到")
                               + f" → 接着打，耐久 ≤ {float(c.get('stop_pct', 5)):.0f}% 就停止", 300)
            return self._rp_stop_low(dur, "炉石还在冷却")
        if self.rp_attempts >= int(c.get("max_attempts", 3)):
            self._stop_walk()
            self.stop(f"回城修理已经被打断 {self.rp_attempts} 次（读炉石时被怪打）")
            return True
        self._rp_begin(obs, now, dur, why)
        return True

    def _rp_begin(self, obs, now, dur, why="dur"):
        from .state_machine import State
        self._stop_walk()
        self.rp = {"phase": "cast", "t": now, "t0": now, "press_t": -1e9, "presses": 0, "cast_seen": False,
                   "map0": obs.map_id, "pos0": obs.pos, "dur0": dur, "paused": False, "why": why}
        self.state = State.REPAIR
        key = self.cfg["keys"].get("hearth")
        self.log(f"{self._rp_reason(dur, why)} → 炉石回城{self._rp_why_cn(why)}"
                 f"（按「{key}」，现在在地图 {obs.map_id}）")
        self._event("repair", f"{self._rp_reason(dur, why)} → 炉石回城{self._rp_why_cn(why)}", 0)

    def _rp_reason(self, dur, why) -> str:
        line = float(self._rp_cfg()["durability_pct"])
        if why == "bag":
            return f"🎒 背包满了（耐久 {dur:.0f}%）"
        if why == "both":
            return f"🔧 耐久 {dur:.0f}% ≤ {line:.0f}% 而且背包满了"
        return f"🔧 耐久 {dur:.0f}% ≤ {line:.0f}%"

    def _rp_begin_walk(self, obs, now, dur, why="dur"):
        """v8.56：不用炉石 → 先走到最近的中心，再把回程路线（修理商之后的点）倒着走到修理商。"""
        from .state_machine import State
        self._stop_walk()
        c = self._rp_cfg()
        self.rp = {"phase": "to_vendor", "t": now, "t0": now, "press_t": -1e9, "presses": 0, "cast_seen": False,
                   "map0": obs.map_id, "pos0": obs.pos, "dur0": dur, "paused": False, "walk": True, "why": why}
        self.state = State.REPAIR
        self.log(f"{self._rp_reason(dur, why)} → 走路去{self._rp_why_cn(why)}（不用炉石）："
                 "先到最近的中心，再把回程路线倒着走到修理商")
        self._event("repair", f"{self._rp_reason(dur, why)} → 走路去{self._rp_why_cn(why)}", 0)
        self._rp_seg_walk_out(obs, now)

    def _rp_walk_out_pts(self, obs):
        """[最近的同地图中心] + 回程路线倒过来 + 修理商点。"""
        c = self._rp_cfg()
        route = self._rp_route()
        vi = int(c.get("vendor_i", -1))
        back = [p for p in route if p[3] > vi]
        vend = [p for p in route if p[3] == vi]
        cands = [(distance(obs.pos, cc[:2]), i) for i, cc in enumerate(self.centers)
                 if obs.pos is not None and self._same_map(cc, obs.map_id)]
        if not cands and self.home is not None and obs.pos is not None and self._same_map(self.home, obs.map_id):
            cands = [(distance(obs.pos, self.home[:2]), -1)]
        if not cands:
            return None
        _, i = min(cands)
        cc = self.centers[i] if i >= 0 else self.home
        cm = int(cc[2]) if len(cc) > 2 and cc[2] else int(obs.map_id or 0)
        center = (float(cc[0]), float(cc[1]), cm, -1, f"中心 #{i + 1}" if i >= 0 else "中心")
        return [center] + list(reversed(back)) + vend

    def _rp_seg_walk_out(self, obs, now):
        pts = self._rp_walk_out_pts(obs)
        if pts is None:
            return self._rp_fail(f"走路去修理：地图 {obs.map_id} 上没有中心")
        rp = self.rp
        rp.update(seg=("out", None), seg_name="走去修理商", pts=pts, wi=0, seg_t0=now, prog_pos=obs.pos, prog_t=now,
                  stuck=0, lvl=0, cross_t0=None, miss_t=None, log_t=-1e9)
        self.log(f"🔧 走去修理商：先去{pts[0][4]}（离它 {distance(obs.pos, pts[0][:2]):.2f}），"
                 f"再倒着走回程路线 {len(pts) - 2} 个点 → {pts[-1][4]}（修理商）")
        return True

    def _rp_resume_out(self, obs, now, rp):
        """打完怪接着走“去修理商”：从离自己最近的点接着走（同地图，不回头去已经走过的中心）。"""
        pts = rp["pts"]
        same = [k for k, p in enumerate(pts) if not p[2] or not obs.map_id or p[2] == int(obs.map_id)]
        if obs.pos is None or not same:
            return
        k = min(same, key=lambda j: distance(obs.pos, pts[j][:2]))
        k = max(k, min(rp["wi"], len(pts) - 1))          # 不回头去已经走过的点
        rp.update(wi=k, seg_t0=now, prog_pos=obs.pos, prog_t=now, stuck=0, lvl=0, cross_t0=None)

    def _rp_fail(self, why) -> bool:
        self._stop_walk()
        self.rp = None
        self.stop(f"回城修理失败：{why}")
        return True

    # ------------------------------------------------------------------ 每帧
    def _rp_step(self, obs, now) -> bool:
        from .state_machine import State
        c, rp = self._rp_cfg(), self.rp
        if obs.player_dead or getattr(obs, "ghost", False):
            self._stop_walk()
            self.rp = None
            self.state = State.SEARCHING
            self.log("💀 回城修理途中死了 → 修理流程取消（复活后耐久还低会再试）")
            return False
        if now - rp["t0"] > float(c["total_timeout_s"]):
            return self._rp_fail(f"整个流程超过 {float(c['total_timeout_s']):.0f} 秒")
        ph = rp["phase"]
        if ph == "cast":
            return self._rp_cast(obs, now, c, rp)
        if ph == "load":
            return self._rp_load(obs, now, c, rp)
        if ph in ("to_vendor", "back"):
            if rp.get("paused"):
                if obs.in_combat or self.state not in (State.SEARCHING, State.RETURNING, State.REPAIR,
                                                       State.APPROACHING):
                    return False              # 打完怪、回完血再接着走（v8.56：不主动找怪，接近别的怪也放弃）
                rp["paused"] = False
                self.state = State.REPAIR
                self.log("🔧 打完了 → 接着走修理路线")
                if rp["seg"][0] == "out":
                    self._rp_resume_out(obs, now, rp)
                elif not self._rp_seg(obs, now, rp["seg"][0], rp["seg"][1], rp["seg_name"], start_near=False):
                    return True
                if self.rp is None:
                    return True
            elif obs.in_combat:
                if self._rp_ignore(obs, now, c, rp):
                    pass                      # v8.57：不还手，接着走
                else:
                    self._stop_walk()
                    rp["paused"] = True
                    self.state = State.SEARCHING
                    self.log("🔧 修理路上被打了 → 先还手，打完接着走（不主动找别的怪）")
                    return False
            elif rp.get("ign_noted"):
                rp["ign_noted"] = False
                self.log("🔧 脱战了（怪没追上/打不着）→ 接着走")
            self.state = State.REPAIR
            self._rp_cat(obs, now, c, rp)
            res = self._rp_walk(obs, now, c, rp)
            if res != "done":
                return True
            if ph == "to_vendor":
                self._stop_walk()
                rp.update(phase="vendor", t=now, press_t=-1e9, tries=0)
                self.log("🔧 到修理商这里了 → 选修理商 + 互动")
                return True
            return self._rp_finish(obs, now)
        if ph == "vendor":
            return self._rp_vendor(obs, now, c, rp)
        if ph == "repairing":
            return self._rp_repairing(obs, now, c, rp)
        return True

    def _rp_ignore(self, obs, now, c, rp) -> bool:
        """v8.57：战斗中要不要无视怪接着走。血量 ≤ fight_back_hp_pct → 不再无视，先还手（照常治疗）。"""
        if not c.get("ignore_combat", True):
            return False
        line = float(c.get("fight_back_hp_pct", 40) or 0)
        hp = obs.player_hp if getattr(obs, "hp_readable", True) else None
        if line > 0 and hp is not None and hp <= line:
            self.log(f"🔧 修理路上被打，血量 {hp:.0f}% ≤ {line:.0f}% → 不跑了，先还手（会照常治疗），打完接着走")
            rp["ign_noted"] = False
            return False
        if not rp.get("ign_noted"):
            rp["ign_noted"] = True
            self.log(f"🔧 修理路上被打了 → 不理它，接着走（血量 ≤ {line:.0f}% 才还手）" if line > 0
                     else "🔧 修理路上被打了 → 不理它，接着走")
        return True

    def _rp_cat(self, obs, now, c, rp):
        """v8.57：去修理商 / 走回来的路上用猎豹形态走。只在插件确定不是猎豹时才按（猎豹里再按会退出变形）。"""
        if not c.get("cat_travel", True):
            return
        key = str(self.cfg["keys"].get("cat_form") or "").strip()
        if not key:
            self._warn("rpcatkey", "⚠️ 修理路上想用猎豹形态走，但“猎豹形态键”没填（基本页）→ 用现在的形态走", 600)
            return
        if obs.form in ("unknown", "cat", None) or obs.casting:
            return
        if now - rp.get("cat_t", -1e9) < 3.0:
            return
        rp["cat_t"] = now
        rp["cat_n"] = rp.get("cat_n", 0) + 1
        self._ctx("修理路上：变猎豹")
        self.inp.press(key)
        if rp["cat_n"] <= 1 or rp["cat_n"] % 10 == 0:
            self.log(f"🐆 修理路上变猎豹形态走（按「{key}」，现在形态 {obs.form}）")
        self.bear_belief = self._cform() == "cat"

    def _rp_cast(self, obs, now, c, rp) -> bool:
        if obs.in_combat:
            self._stop_walk()
            self.rp = None
            self.rp_attempts += 1
            from .state_machine import State
            self.state = State.SEARCHING
            self.log(f"🔧 读炉石时被打了 → 先打怪，打完再回城（第 {self.rp_attempts} 次被打断）")
            return False
        if obs.casting:
            if not rp["cast_seen"]:
                rp["cast_seen"], rp["cast_t"] = True, now
                self.dlog("[修理] 炉石读条中")
            if now - rp["cast_t"] > float(c["cast_timeout_s"]):
                return self._rp_fail(f"炉石读条超过 {float(c['cast_timeout_s']):.0f} 秒")
            return True
        if rp["cast_seen"]:
            rp.update(phase="load", t=now)
            self.log("🔧 炉石读完 → 等落地")
            return True
        if now - rp["press_t"] >= 3.0:
            if rp["presses"] >= 3:
                return self._rp_fail(f"按了 {rp['presses']} 次炉石键「{self.cfg['keys'].get('hearth')}」都没开始读条"
                                     "（宏对吗？/cancelform 换行 /use 炉石）")
            rp["presses"] += 1
            rp["press_t"] = now
            self._ctx("回城修理：炉石")
            self.inp.press(self.cfg["keys"]["hearth"])
            self.log(f"🔧 按炉石「{self.cfg['keys']['hearth']}」（第 {rp['presses']} 次）")
        return True

    def _rp_load(self, obs, now, c, rp) -> bool:
        m0, p0 = rp.get("map0"), rp.get("pos0")
        moved = (obs.map_id and m0 and int(obs.map_id) != int(m0)) or \
                (obs.pos is not None and p0 is not None and distance(obs.pos, p0) > 3.0)
        if moved:
            self.log(f"🏠 炉石到了（地图 {m0} → {obs.map_id}，坐标 "
                     + (f"{obs.pos[0]:.2f},{obs.pos[1]:.2f}" if obs.pos else "?") + "）→ 照路线去修理商")
            vi = int(c.get("vendor_i", -1))
            rp["phase"] = "to_vendor"
            self._rp_seg(obs, now, 0, vi, "去修理商")
            return True
        if not obs.casting and now - rp["t"] > 4.0 and obs.pos is not None:
            if rp["presses"] >= 3:
                return self._rp_fail("炉石读条被打断了 3 次")
            rp.update(phase="cast", cast_seen=False, press_t=-1e9)
            self.log("🔧 炉石读完 4 秒还在原地（读条被打断？）→ 再按一次")
            return True
        if now - rp["t"] > float(c["load_timeout_s"]):
            return self._rp_fail(f"炉石读完 {float(c['load_timeout_s']):.0f} 秒还没落地")
        return True

    def _rp_vendor(self, obs, now, c, rp) -> bool:
        if getattr(obs, "merchant_open", False):
            rp.update(phase="repairing", t=now)
            self.log("🔧 商人窗口开了 → 插件自动全部修理（顺便卖灰色物品）")
            return True
        if now - rp["press_t"] >= float(c["vendor_wait_s"]):
            if rp["tries"] >= int(c["vendor_tries"]):
                return self._rp_fail(f"按了 {rp['tries']} 次选修理商 + 互动，商人窗口都没开"
                                     "（选修理商的宏 /tar 名字 对吗？路线最后一个点离修理商够近吗？）")
            rp["tries"] += 1
            rp["press_t"] = now
            k = self.cfg["keys"]
            if rp["tries"] >= 2 and obs.form in ("cat", "bear") and self._cancel_distinct():
                self.log(f"🔧 商人窗口没开，现在是{obs.form}形态 → 先取消变形再试")
                self.inp.press(k["cancel_form"])
            self._ctx("回城修理：选修理商 + 互动")
            self.inp.press(k["vendor_target"])
            self.inp.press(k["interact"])
            self.log(f"🔧 选修理商「{k['vendor_target']}」+ 互动「{k['interact']}」（第 {rp['tries']} 次）")
        return True

    def _rp_repairing(self, obs, now, c, rp) -> bool:
        dur = getattr(obs, "durability", None)
        why = rp.get("why", "dur")
        if why != "dur" and getattr(obs, "merchant_open", False) and now - rp["t"] < float(c.get("sell_wait_s", 3.0)):
            return True                       # v8.57：等插件分批卖完灰色物品
        if dur is not None and dur >= float(c["repaired_pct"]):
            self.log(f"🔧 {'卖完垃圾' if why == 'bag' else '修好了'}（耐久 {rp['dur0']:.0f}% → {dur:.0f}%）→ 关商人窗口，照路线走回刷怪区")
            if getattr(obs, "merchant_open", False):
                self.inp.press("esc")
            rp["phase"] = "back"
            vi = int(c.get("vendor_i", -1))
            self._rp_seg(obs, now, vi + 1, len(self._rp_route()) - 1, "走回刷怪区", start_near=False)
            return True
        if getattr(obs, "merchant_open", False) and not getattr(obs, "merchant_repair", False) and now - rp["t"] > 1.5 \
                and why != "bag":
            return self._rp_fail("这个商人不能修理（选修理商的宏选错人了？）")
        if now - rp["t"] > float(c["repair_wait_s"]):
            return self._rp_fail(f"商人窗口开了 {float(c['repair_wait_s']):.0f} 秒，耐久还是 "
                                 + (f"{dur:.0f}%" if dur is not None else "读不到") + "（钱不够？）")
        return True

    def _rp_finish(self, obs, now) -> bool:
        self._stop_walk()
        cands = [(distance(obs.pos, cc[:2]), i) for i, cc in enumerate(self.centers)
                 if obs.pos is not None and self._same_map(cc, obs.map_id)]
        if not cands:
            if obs.pos is not None and not self.centers and self.home is not None and self._same_map(self.home, obs.map_id):
                cands = [(distance(obs.pos, self.home[:2]), -1)]
            else:
                return self._rp_fail(f"修理路线走完了，但地图 {obs.map_id} 上没有中心")
        d, i = min(cands)
        if i >= 0:
            self.ci, self.home = i, self.centers[i]
        self.travel = False
        dur = getattr(obs, "durability", None)
        self.log(f"🔧 回城修理完成（耐久 {dur if dur is not None else '?'}%，用了 {now - self.rp['t0']:.0f} 秒）"
                 f"→ 走回中心" + (f" #{i + 1}" if i >= 0 else "") + f"（离它 {d:.2f}）")
        if self.rp.get("why", "dur") != "dur":
            cd = float(self._rp_cfg().get("bag_trip_cd_s", 1800.0))
            self.rp_bag_cd = now + cd
            self.bag_full, self.bag_noresp, self.bag_corpses, self.bag_skip = False, 0, 0, False
            self.log(f"🎒 卖完垃圾 → 恢复拾取/剥皮；{cd / 60:.0f} 分钟内背包又满了就不再专门回去卖，照旧只捡钱")
        self.rp = None
        self.rp_attempts = 0
        self.rp_low_since = None
        self._to_bear(obs)
        self.start_return(obs)
        return True

    # ------------------------------------------------------------------ 走路线
    def _rp_seg(self, obs, now, a, b, name, start_near=True) -> bool:
        rp, c = self.rp, self._rp_cfg()
        pts = [p for p in self._rp_route() if a <= p[3] <= b]
        rp.update(seg=(a, b), seg_name=name, pts=pts, wi=0, seg_t0=now, prog_pos=obs.pos, prog_t=now,
                  stuck=0, lvl=0, cross_t0=None, miss_t=None, log_t=-1e9)
        if not pts:
            self.log(f"🔧 {name}：路线是空的 → 直接算到了")
            rp["wi"] = 0
            return True
        if obs.pos is None:
            return True
        same = [k for k, p in enumerate(pts) if not p[2] or not obs.map_id or p[2] == int(obs.map_id)]
        if not same:
            if start_near:
                return self._rp_fail(f"{name}：路线上没有当前地图 {obs.map_id} 的点（炉石绑在别的旅店？路线录错了？）")
            return True                          # 回程：当前地图的点在后面也没有 → 走路时按“换地图”处理
        k = min(same, key=lambda j: distance(obs.pos, pts[j][:2]))
        dk = distance(obs.pos, pts[k][:2])
        if start_near and dk > float(c["start_max_dist"]):
            return self._rp_fail(f"{name}：离路线最近的点 {pts[k][4]} 还有 {dk:.2f}（> {float(c['start_max_dist']):.2f}）"
                                 "→ 位置不对（炉石绑在别的旅店？）")
        if k + 1 < len(pts) and pts[k + 1][2] == pts[k][2] and dk > float(c["arrive_dist"]) and \
                distance(obs.pos, pts[k + 1][:2]) < distance(pts[k][:2], pts[k + 1][:2]):
            k += 1
        rp["wi"] = k
        self.log(f"🔧 {name}：共 {len(pts)} 个点，从 {pts[k][4]} 开始（离它 {distance(obs.pos, pts[k][:2]):.2f}）")
        return True

    def _rp_walk(self, obs, now, c, rp) -> str:
        pts = rp["pts"]
        n = len(pts)
        if rp["wi"] >= n:
            return "done"
        if now - rp["seg_t0"] > float(c["seg_timeout_s"]):
            self._rp_fail(f"{rp['seg_name']}走了 {float(c['seg_timeout_s']):.0f} 秒还没走完")
            return "fail"
        if obs.pos is None:
            self._stop_walk()
            rp["miss_t"] = rp["miss_t"] or now
            if now - rp["miss_t"] > 15:
                self._rp_fail("15 秒读不到坐标")
                return "fail"
            return "walking"
        rp["miss_t"] = None
        cur = int(obs.map_id or 0)
        for _ in range(n + 1):
            if rp["wi"] >= n:
                self._stop_walk()
                return "done"
            p = pts[rp["wi"]]
            if p[2] and cur and p[2] != cur:
                later = [k for k in range(rp["wi"], n) if pts[k][2] == cur]
                if later:
                    k = min(later, key=lambda j: distance(obs.pos, pts[j][:2]))
                    self.log(f"🔧 已经在地图 {cur} → 从{pts[k][4]}接着走")
                    rp.update(wi=k, cross_t0=None, stuck=0, lvl=0)
                    continue
                return self._rp_cross(obs, now, c, rp, p)
            rp["cross_t0"] = None
            d = distance(obs.pos, p[:2])
            if d <= float(c["arrive_dist"]):
                rp["wi"] += 1
                rp["stuck"], rp["lvl"] = 0, 0
                self.dlog(f"[修理] 到{p[4]}（距离 {d:.2f}）")
                continue
            break
        p = pts[rp["wi"]]
        if self._rp_stuck(obs, now, c, rp):
            return "walking" if self.rp is not None else "fail"
        if now - rp["log_t"] >= 10:
            rp["log_t"] = now
            self.log(f"🔧 {rp['seg_name']}：去{p[4]}（离它 {distance(obs.pos, p[:2]):.2f}，"
                     f"还剩 {n - rp['wi']} 个点）")
        self._rp_steer(obs, bearing_to(obs.pos, p[:2]))
        return "walking"

    def _rp_cross(self, obs, now, c, rp, p) -> str:
        """下一个点在别的地图：朝最后一段路的方向一直走，直到进入那张图。"""
        if rp["cross_t0"] is None:
            rp["cross_t0"] = now
            self.log(f"🔧 下一个点在地图 {p[2]}（现在 {obs.map_id}）→ 照原方向往前走，进入那张图")
        if now - rp["cross_t0"] > float(c["cross_timeout_s"]):
            self._rp_fail(f"往地图 {p[2]} 走了 {float(c['cross_timeout_s']):.0f} 秒还没进去")
            return "fail"
        i = rp["wi"]
        pts = rp["pts"]
        head = None
        if i >= 2 and pts[i - 1][2] == pts[i - 2][2] == int(obs.map_id or 0):
            head = bearing_to(pts[i - 2][:2], pts[i - 1][:2])
        if self._rp_stuck(obs, now, c, rp):
            return "walking" if self.rp is not None else "fail"
        if head is None or obs.facing is None:
            self._start_walk(obs)
        else:
            self._rp_steer(obs, head)
        return "walking"

    def _rp_steer(self, obs, head):
        a = self.cfg["area"]
        if obs.facing is None:
            self._start_walk(obs)
            return
        delta = angle_diff(head, obs.facing)
        if abs(delta) > math.radians(float(a.get("face_tolerance_deg", 25))):
            self._stop_walk()
            self._turn(delta)
            return
        if abs(delta) > math.radians(float(a.get("walk_correct_deg", 8))):
            self._turn(delta)
        self._start_walk(obs)

    def _rp_stuck(self, obs, now, c, rp) -> bool:
        """卡住了 → 跳 + 平移 / 后退 + 绕（60/90/120°）；同一段卡住超过 stuck_max 次 → 停止。True = 这一帧处理了。"""
        a, k = self.cfg["area"], self.cfg["keys"]
        if not self.walking:
            rp["prog_pos"], rp["prog_t"] = obs.pos, now
            return False
        if rp["prog_pos"] is None or distance(obs.pos, rp["prog_pos"]) > float(a.get("move_eps", 0.03)):
            rp["prog_pos"], rp["prog_t"] = obs.pos, now
            return False
        if now - rp["prog_t"] <= float(c["stuck_s"]):
            return False
        rp["stuck"] += 1
        rp["lvl"] += 1
        if rp["stuck"] > int(c["stuck_max"]):
            self._rp_fail(f"{rp['seg_name']}卡住 {rp['stuck'] - 1} 次了")
            return True
        if rp["stuck"] == 3:
            self._event("repair_stuck", "修理路上卡住", 60)
        lvl = rp["lvl"]
        if lvl == 1:
            self.log(f"🔧 修理路上卡住了（第 {rp['stuck']} 次）→ 跳 + 平移")
            if k.get("jump"):
                self.inp.press(k["jump"])
            if k.get("strafe"):
                self.inp.press(k["strafe"], hold_ms=500)
        else:
            off = DETOURS[(lvl - 2) % len(DETOURS)]
            self.log(f"🔧 修理路上卡住了（第 {rp['stuck']} 次）→ 后退 + 往{'左' if off > 0 else '右'}绕 {abs(off)}°")
            self._stop_walk()
            if k.get("back"):
                self.inp.press(k["back"], hold_ms=700)
            self._turn(math.radians(off))
            self.inp.press(k.get("forward", "w"), hold_ms=1000 * min(4, lvl))
        rp["prog_pos"], rp["prog_t"] = obs.pos, self.clock()
        return True

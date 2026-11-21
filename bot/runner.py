"""后台线程：截屏 → 识别 → 状态机，GUI 通过回调拿状态。"""
from __future__ import annotations

import os
import random
import threading
import time

from .capture import ScreenCapture
from .detector import Detector
from .input import InputController, StopRequested
from .logfile import SessionLog, ShotSaver, obs_snapshot
from .state_machine import Grinder, State


def err_backoff(streak: int) -> float:
    """v8.43：连续出错第 n 次等多久：2、4、8、16、30、30…秒（1 分钟没出错就从 2 秒重新算）。"""
    return float(min(30.0, 2.0 * (2 ** max(0, streak - 1))))
from .window import game_in_foreground


class BotRunner:
    def __init__(self, cfg: dict, log, on_status=None, on_home=None, on_centers=None, on_route=None):
        self.cfg = cfg
        self.on_route = on_route
        self.on_home = on_home
        self.on_centers = on_centers
        self.log = log
        self.on_status = on_status or (lambda st: None)
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.thread: threading.Thread | None = None
        self.grinder: Grinder | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return
        self.stop_event.clear()
        self.paused.clear()
        self.thread = threading.Thread(target=self._run, name="WowGrinder", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def toggle_pause(self) -> bool:
        if self.paused.is_set():
            self.paused.clear()
        else:
            self.paused.set()
        return self.paused.is_set()

    def _run(self):
        cfg = self.cfg
        lc = cfg.get("log", {})
        flog = SessionLog(keep=lc.get("keep_files", 20), max_mb=lc.get("max_mb", 5)) if lc.get("enabled", True) else None
        gui_log = self.log

        def log(msg):
            gui_log(msg)
            if flog:
                flog.write(msg)

        def dlog(msg):
            if flog:
                flog.write(msg, "  · ")

        self.log_path = flog.path if flog else ""
        if flog and flog.path:
            gui_log(f"日志文件：{flog.path}")
        cap = ScreenCapture(cfg["misc"].get("monitor", 1))
        det = Detector(cfg)
        inp = InputController(cfg, self.stop_event, log)
        inp.on_key = dlog
        shots = ShotSaver(keep=int(lc.get("keep_shots", 50) or 50)) if flog else None
        if shots is not None:
            shots.prune()                       # v8.25：启动时先把多余的旧截图删掉
        acfg = cfg.get("addon", {})
        last_snap = ""
        mon = cap.monitor
        self.grinder = g = Grinder(cfg, inp, log, screen_offset=(mon["left"], mon["top"]), dlog=dlog)
        g.on_home = self.on_home
        g.peek_plates = lambda: det.fresh_plates(cap.grab())   # v8.28：点前再截一张图确认标记还在原位
        g.on_centers = self.on_centers
        g.on_route = self.on_route
        dlog(f"配置：模式={cfg['move'].get('mode')} 治疗线={cfg['heal'].get('heal_pct')}% 回血 低于{cfg['heal'].get('rest_below_pct')}%→到{cfg['heal'].get('rest_to_pct')}% "
             f"拾取/剥皮键={cfg['keys'].get('interact') or cfg['keys'].get('loot')} 剥皮={cfg['loot'].get('skin')} "
             f"清除目标={cfg['keys'].get('clear_target')} 点姓名板={cfg.get('stationary', {}).get('plate_click')} "
             f"buff={'开' if cfg.get('buffs', {}).get('enabled') else '关'}(荆棘 {cfg['keys'].get('thorns')} / 印记 {cfg['keys'].get('motw')}) "
             f"键位 攻击={cfg['keys'].get('attack')} 熊={cfg['keys'].get('bear_form')} 取消变形={cfg['keys'].get('cancel_form')} "
             f"治疗之触={cfg['keys'].get('healing_touch')} Tab={cfg['keys'].get('target')}")
        hl0 = cfg["heal"]
        dlog(f"v8.27 战后治疗={'开' if hl0.get('post_enabled', True) else '关'}：血量 < {hl0.get('post_ht_below_pct', 70)}% "
             f"生命之触「{cfg['keys'].get('healing_touch')}」/ 否则 "
             + (f"< {hl0.get('post_rejuv_below_pct', 85)}% 回春术「{cfg['keys'].get('rejuvenation')}」/ 更高不治疗；"
                if hl0.get("post_rejuv_enabled", True)
                else "不治疗（v8.36 战后放回春术=关）；")
             + f"互动键「{cfg['keys'].get('interact')}」")
        nc0 = cfg.get("nameplate", {})
        dlog(f"v8.28 找怪：插件 v0.11 标记={'开' if nc0.get('use_enemy_markers', True) else '关'} 顶部 {nc0.get('skip_top_pct', 23)}% 不点 "
             f"先停稳 {nc0.get('stop_settle_s', 0.3)}s 点前确认 {nc0.get('confirm_px', 25)}px 点后验证 {nc0.get('verify_s', 0.6)}s "
             f"点空拉黑 {nc0.get('bad_s', 8)}s 巡逻连续 {nc0.get('patrol_frames', 2)} 帧 远处红名={'开' if nc0.get('far_names', True) else '关'}")
        ts0 = cfg.get("tab_search", {})
        dlog(f"v8.40 Tab 找怪={'开' if ts0.get('enabled', True) else '关'}（键「{cfg['keys'].get('target') or 'tab'}」，要插件 v0.13）"
             f" 移动中也按={'开' if ts0.get('while_moving', True) else '关'}（每 {ts0.get('move_interval_s', 1.8)} 秒）"
             f" 不打：{'灰名 ' if ts0.get('skip_tapped', True) else ''}{'精英 ' if ts0.get('skip_elite', True) else ''}"
             f"{('高 ' + str(ts0.get('level_diff', 3)) + ' 级以上 ') if ts0.get('skip_high', True) else ''}"
             f"{'玩家' if ts0.get('skip_player', True) else ''}")
        cr0 = cfg.get("corpse", {})
        dlog(f"v8.31 自动跑尸={'开' if cr0.get('enabled', True) else '关'}：死后 {cr0.get('release_delay_s', 5)} 秒按「{cfg['keys'].get('spirit', '')}」释放灵魂，"
             f"复活前看怪={'开' if cr0.get('safe_check', True) else '关'}（最多等 {cr0.get('safe_wait_s', 30)} 秒），"
             f"复活后回到 血 {cr0.get('rest_hp_pct', 90)}% 蓝 {cr0.get('rest_mana_pct', 80)}%；"
             f"v8.39 跑尸路线 {len(cr0.get('route') or [])} 个点（{str(cfg['keys'].get('hotkey_add_route') or '-').upper()}=添加），"
             f"到点 {cr0.get('route_arrive_dist', 0.15)} 抄近路 {cr0.get('route_shortcut_dist', 1.0)}")
        lt0 = cfg.get("loot", {})
        dlog(f"v8.29 背包满检测={'开' if lt0.get('bagfull_detect', True) else '关'}：连续 {lt0.get('bagfull_presses', 5)} 次按互动键没反应 → 关掉拾取/剥皮只打怪"
             f"（两具尸体确认={'开' if lt0.get('bagfull_two_corpses', False) else '关'}）；"
             f"v8.48 背包满只捡钱={'开' if lt0.get('full_bag_money_only', True) else '关'}"
             f"（不剥皮，捡过的尸体记 {lt0.get('full_bag_memory_s', 300)} 秒）")
        np0, ts0 = cfg.get("nameplate", {}), cfg.get("tab_search", {})
        dlog(f"v8.50 找怪顺序={'标记 → 红名 → Tab' if np0.get('plates_first', True) else 'Tab → 标记 → 红名（旧）'}"
             f"（朝红名走看到标记 {np0.get('far_click_frames', 1)} 帧就点；转 {np0.get('red_static_turns', 2)} 次不动=界面；"
             f"红名路上按 Tab={'开' if ts0.get('far_walk_tab', False) else '关'}；界面排除区 {len(np0.get('red_ui_exclude') or [])} 个）")
        ea0 = cfg.get("eat", {})
        dlog(f"v8.49 自动进食={'开' if ea0.get('enabled', True) else '关'}（进食键「{ea0.get('key') or '没填'}」，"
             f"没“充分进食”buff、附近没怪时变人形吃 ≥ {ea0.get('min_s', 11)} 秒；要插件 v0.14）；背包满只捡钱不再按 Esc")
        from .config import key_conflicts
        for c in key_conflicts(cfg):
            log(f"⚠️ 键位冲突：{c}，请在“基本设置”页改一个")
        ar, hl = cfg.get("area", {}), cfg["heal"]
        dlog(f"设置：区域刷怪={'开' if ar.get('enabled') else '关'} 半径={ar.get('radius')} 开始位置当中心={ar.get('home_on_start', True)} "
             f"追怪上限=半径×{ar.get('leash_ratio', 1.3)} | 变身预留法力 猎豹={hl.get('reserve_cat_pct', 30)}% 熊={hl.get('reserve_bear_pct', 30)}% "
             f"治疗之触≈{hl.get('heal_cost_pct', 20)}% buff≈{hl.get('buff_cost_pct', 10)}% | "
             f"热键 {str(cfg['keys'].get('hotkey_area_check') or '-').upper()}=检查范围 "
             f"{str(cfg['keys'].get('hotkey_set_home') or '-').upper()}=设当前位置为中心 "
             f"{str(cfg['keys'].get('hotkey_add_center') or '-').upper()}=添加中心 | v8.23 多中心 {len(ar.get('centers') or [])} 个："
             + (" ".join(f"#{i + 1}({c[0]},{c[1]},{c[2] if len(c) > 2 else 0})" for i, c in enumerate(ar.get('centers') or [])) or "-"))
        last_state = None
        frame = None
        preview = bool(cfg["misc"].get("debug_preview"))
        warned_bg = False
        last_source = None
        last_gamma = ""
        err_streak, err_last = 0, -1e9      # v8.43：出错永远重试；连续出错等待 2→4→8→16→30 秒，1 分钟没出错清零
        try:
            delay = int(cfg["misc"].get("capture_delay_s", 3))
            for i in range(delay, 0, -1):
                log(f"{i} 秒后开始，请切到游戏窗口…")
                inp.sleep(1)
            log("开始打怪")
            try:   # v8.43：启动时的提示出错也不影响打怪
                if (cfg["keys"].get("clear_target") or "").strip().lower() == "f3":
                    log("ℹ️ 清除目标键 = F3：请确认游戏里有 /cleartarget 宏放在 F3（v8.3 起不再用 Esc，Esc 没目标时会打开游戏菜单）")
                from .config import cat_missing, is_cat
                if is_cat(cfg):
                    cm = cat_missing(cfg)
                    kk = cfg["keys"]
                    log("🐆 战斗形态 = 猎豹：形态键「%s」爪击「%s」斜掠「%s」终结技「%s」潜行「%s」开怪键「%s」（猎豹和熊一样）猛虎之怒「%s」"
                        % tuple(kk.get(n) or "-" for n in ("cat_form", "cat_builder", "cat_rake", "cat_finisher",
                                                            "cat_prowl", "attack", "cat_tigers_fury"))
                        + "（连击点/能量要插件 v0.9，读不到就按次数轮转）")
                    if cm:
                        log("⚠️ 猎豹模式缺少：" + "、".join(cm) + "（“按键”页填好；缺的键不会按）")
                ta = (cfg["keys"].get("target_attacker") or "").strip().lower()
                if ta:
                    clash = [n for n, v in cfg["keys"].items()
                             if n != "target_attacker" and isinstance(v, str) and v.strip().lower() == ta]
                    log(f"ℹ️ 选择攻击我的怪键 = {ta.upper()}：请确认游戏里有宏（/cleartarget 换行 /targetenemy）放在 {ta.upper()}"
                        + (f"；⚠️ 这个键和 {', '.join(clash)} 重复了" if clash else ""))
            except StopRequested:
                raise
            except Exception as e:  # noqa: BLE001
                log(f"⚠️ 启动提示出错（不影响打怪）：{type(e).__name__}: {e}")
            while not self.stop_event.is_set():
                try:
                    if self.paused.is_set():
                        if g.walking:
                            g._stop_walk()  # 暂停时松开前进键
                        if g.area_cmds or g.route_cmds:     # 暂停时按 F9/F8/F7/F6 也处理（不按键）
                            try:
                                o = det.observe(cap.grab(), want_nameplates=False)
                                if o.source == "addon":
                                    g.last_obs = o
                                    if g.route_cmds:
                                        g._route_tick(o, time.monotonic())
                                    if g.area_cmds:
                                        g._area_tick(o, time.monotonic())
                                else:
                                    g.area_cmds.clear()
                                    g.route_cmds.clear()
                                    log("📍 读不到插件色块，检查不了范围（游戏窗口被挡住？）")
                            except Exception as e:  # noqa: BLE001
                                g.area_cmds.clear()
                                g.route_cmds.clear()
                                log(f"📍 检查范围出错：{e}")
                        self.on_status({"state": "暂停", "kills": g.kills, "area": g.area_label()})
                        inp.sleep(0.2)
                        continue
                    if cfg["misc"].get("window_check", True) and not game_in_foreground(cfg["misc"].get("window_keywords", ""),
                                                                                    cfg["misc"].get("window_exes", "")):
                        if g.walking:
                            g._stop_walk()
                        if not warned_bg:
                            log("游戏窗口不在前台，暂不按键")
                            warned_bg = True
                        self.on_status({"state": "等待游戏窗口", "kills": g.kills})
                        inp.sleep(0.5)
                        continue
                    warned_bg = False
                    frame = cap.grab()
                    obs = det.observe(frame, want_nameplates=g.wants_nameplates())
                    if obs.gamma_note and obs.gamma_note != last_gamma:
                        log("ℹ️ " + obs.gamma_note)
                    last_gamma = obs.gamma_note or last_gamma
                    if obs.source != last_source:
                        log("状态来源：BotStatus 插件" if obs.source == "addon"
                            else f"状态来源：截图识别（{obs.addon_reason}）")
                        last_source = obs.source
                    t_tick = time.monotonic()
                    g.tick(obs)
                    took = time.monotonic() - t_tick
                    if g.state != last_state:
                        dlog(f"[状态] {last_state.value if last_state else '-'} → {g.state.value} | 血量 {obs.player_hp:.0f}%"
                             f"({obs.hp_source}) 法力 {obs.mana} 形态 {obs.form} 战斗 {obs.in_combat} 目标 {obs.target_kind} "
                             f"{obs.target_hp:.0f}% 施法 {obs.casting}")
                        last_state = g.state
                    if took > 1.0:
                        dlog(f"[耗时] 这一步用了 {took:.1f} 秒（状态 {g.state.value}）")
                    snap = obs_snapshot(obs)
                    if snap != last_snap:
                        dlog("[观测] " + snap)
                        last_snap = snap
                    while g.events:
                        kind, text = g.events.pop(0)
                        paths = shots.save(frame, kind, (acfg.get("x", 0), acfg.get("y", 0)), int(acfg.get("block", 10))) if shots else []
                        dlog(f"[事件] {kind}：{text}" + (f" → 截图 {', '.join(os.path.basename(p) for p in paths)}" if paths else ""))
                    self.on_status({"state": g.status_label(), "player_hp": obs.player_hp, "source": obs.source,
                                    "target_hp": obs.target_hp, "target": obs.target_exists,
                                    "combat": obs.in_combat, "kills": g.kills, "area": g.area_label()})
                    if preview:
                        import cv2
                        img = det.draw_debug(frame, obs)
                        cv2.imshow("WowGrinder debug", cv2.resize(img, None, fx=0.5, fy=0.5))
                        cv2.waitKey(1)
                    tick = float(cfg["safety"]["tick_ms"]) / 1000.0
                    if time.monotonic() < getattr(g, "fast_until", -1e9):
                        tick = min(tick, 0.05 / 0.8)     # v8.6：转身前后约 50ms 一次
                    inp.sleep(tick * random.uniform(0.8, 1.3))
                except StopRequested:
                    raise
                except Exception as e:  # noqa: BLE001  v8.43：出错永远不停 → 记日志、松开按键、等一会儿继续
                    t_err = time.monotonic()
                    err_streak = err_streak + 1 if t_err - err_last < 60.0 else 1
                    err_last = t_err
                    wait = err_backoff(err_streak)
                    try:
                        import traceback
                        dlog(traceback.format_exc())
                        log(f"⚠️ 出错（连续第 {err_streak} 次）：{type(e).__name__}: {e} → {wait:.0f} 秒后继续（不会停止）")
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        g._stop_walk()
                        inp.release_all()
                    except StopRequested:
                        raise
                    except Exception:  # noqa: BLE001
                        pass
                    inp.sleep(wait)
        except StopRequested:
            pass
        except Exception as e:  # 包括 pyautogui.FailSafeException（鼠标甩到左上角）
            name = type(e).__name__
            import traceback
            log(f"⛔ 出错停止（启动阶段/意外）：{name}: {e}")
            dlog(traceback.format_exc())
        finally:
            inp.release_all()
            if g.state != State.STOPPED:
                g.state = State.STOPPED
            cap.close()
            if preview:
                try:
                    import cv2
                    cv2.destroyAllWindows()
                except Exception:
                    pass
            if g.events and shots:
                for kind, text in g.events:
                    paths = shots.save(frame, kind, (acfg.get("x", 0), acfg.get("y", 0)), int(acfg.get("block", 10)))
                    dlog(f"[事件] {kind}：{text} → 截图 {', '.join(os.path.basename(p) for p in paths)}")
                g.events.clear()
            log(f"已停止。本次击杀 {g.kills}")
            if flog:
                flog.close()
            self.on_status({"state": "已停止", "kills": g.kills})

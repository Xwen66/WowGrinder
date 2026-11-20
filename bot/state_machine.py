"""打怪状态机。

默认模式 stationary_tab（v7，原地不动）：
    找怪 ─ 按 Tab 选最近的敌人 ─ 死的/被抢/友方/够不到 → 再按 Tab（最多 N 次）→ 随机等 2-4 秒
      │     （可选：原地按方向键转一点视角再找；不按 W/S，不点击移动）
      ▼ 目标够得到（普通攻击在范围）或怪在打我
    战斗 ─ 按攻击键开自动攻击；面向错误/打不出伤害 → 原地左右小幅转身；
      │    目标够不到且没在打我 → Tab 换成正在打我的怪
      ▼ 目标死亡 → 拾取（可选）→ 回血到 90% → 找怪

v8：
  * 战后回血：杀完怪后“战斗中”标记会残留几秒，以前因此直接去 Tab 下一只、回血被跳过。
    现在先判断要不要回血（在拾取之后、Tab 之前）；残留战斗期间只要没在掉血就照样回血。
  * 战斗治疗不再“一口气按完”：退熊 → 确认退出 → 按治疗之触 → 确认开始读条（没读条就重按，
    覆盖变形后的公共冷却）→ 读完 → 变回熊。每一步、每次不治疗的原因都写日志。
  * 面向：插件在目标姓名板上方画 青|洋红 标记 → 算出怪在左/右/身后多少度，一次转到位；
    转速用插件朝向自动校准；面向错误改用计数器；转 ±45/±90 不行再转 180°。

旧模式 click（点击移动，下面的说明）：

    找怪 ─ 有未选中姓名板？─ 是 → 右键点离屏幕中心最近的那个
      │                         （点击移动：自动转向、走过去、开始自动攻击）
      │                  └ 否 → 按一下转向键（默认 小键盘右/方向键右）转视角，直到看到姓名板
      ▼
    接近 ─ 进战斗/近战距离/目标掉血 → 战斗
      │   面向错误或每隔几秒：再右键一次选中姓名板；超时 → 换怪
      ▼
    战斗 ─ 血量 < 30% → 取消熊形态 → 治疗之触（站着读条）→ 熊形态；治疗后仍 < 15% → 停止 + 提示音
      │   太远/够不到/打不出伤害 → 补救：右键点姓名板 → 转一点 → 后退半步 → 3 次无效跳过这只怪（战斗中不按互动键）
      │   否则按优先级放技能
      ▼ 目标死亡
    拾取（可选）→ 回血：人形治疗之触，直到血量 ≥ 90% → 变回熊 → 找怪

状态来源：优先 BotStatus 插件色块；读不到心跳时用截图识别（obs.source == "screen"）。
tick(obs) 只根据一次观测做一个决定，便于用假的识别器做单元测试。
"""
from __future__ import annotations

import random
import time
from enum import Enum

import math

from .config import cat_missing, is_cat
from .detector import nearest_to
from .marker import marker_bearing, plate_bearing
from .sound import alarm
from .input import StopRequested
from .navigation import angle_diff, area_label, area_report, bearing_to, distance, turn_command


from .corpse import CorpseRunMixin  # noqa: E402
from .multibear import MultiBearMixin  # noqa: E402
from .tabsearch import TabSearchMixin  # noqa: E402
from .reconnect import ReconnectMixin  # noqa: E402
from .efficiency import ManaRestMixin, WalkFarMixin  # noqa: E402

class State(str, Enum):
    SEARCHING = "寻找目标"
    APPROACHING = "接近目标"
    COMBAT = "战斗中"
    HEALING = "治疗中"
    RESTING = "战后回血"
    RETURNING = "回范围中心"
    LOOTING = "拾取中"
    SKINNING = "剥皮中"
    BUFFING = "补 buff 中"
    CORPSE = "跑尸中"
    STOPPED = "已停止"


ALIVE_KINDS = ("enemy", "unknown")


class Grinder(CorpseRunMixin, MultiBearMixin, TabSearchMixin, ReconnectMixin, ManaRestMixin, WalkFarMixin):
    def __init__(self, cfg: dict, inp, log=print, clock=time.monotonic, screen_offset=(0, 0), beep=alarm, dlog=None):
        self.cfg = cfg
        self.inp = inp
        self.dlog = dlog or (lambda m: None)   # 只写日志文件的详细记录（v8）
        self.events: list[tuple[str, str]] = []  # v8：关键事件（死亡/该治疗没治疗/面向失败/停止）→ runner 截图

        def _log(msg, _orig=log):
            try:
                inp.context = msg          # 紧跟着的按键，日志里写这条作为原因
            except Exception:  # noqa: BLE001
                pass
            _orig(msg)
        self.log = _log
        self.clock = clock
        self.screen_offset = screen_offset
        self.beep = beep
        self.rng = random.Random()
        self.tabs = 0                    # 连续按 Tab 次数（原地模式）
        self.tab_rounds = 0
        self.last_tab = -1e9
        self.tab_pause_until = -1e9
        self.face_attempts = 0
        self.face_last: float | None = None
        self.last_attack_press = -1e9
        self.fight_heals = 0             # 本场战斗已经治疗了几次
        self._warned: dict[str, float] = {}
        self.skip_until = -1e9           # 跳过的怪：skip_pos 附近的姓名板暂时不点
        self.skip_pos = None
        self.last_target_plate = None
        self.turn_flip = False
        # v8 治疗
        self.heal_phase = ""
        self.heal_prev = None
        self.heal_t0 = self.heal_phase_t = self.heal_next_press = self.heal_last_press = 0.0
        self.heal_presses = self.heal_shift_presses = 0
        self.heal_last_shift = 0.0
        self.last_kill_t = -1e9
        self.prev_hp: float | None = None
        self.hp_drop = 0.0
        self.hp_unknown_since: float | None = None
        self.hp_unknown_beeped = False
        self.last_hp_dlog = -1e9
        self.rest_presses = 0
        self.rest_last_press = -1e9
        self.rest_seen_cast = False
        self.rest_combat_noted = False
        # v8 面向
        self.turn_rate = float(cfg.get("stationary", {}).get("turn_rate_dps", 180))
        self._cal_pending = None
        self.fc_last: int | None = None
        self.prev_err_facing = False
        self.last_facing_err_t = -1e9
        self.facing_errs = 0
        self.sweep_i = 0
        self.sweep_off = 0.0
        self.sweep_sign = 1            # v8.6：上次盲转先往哪边（+1 右 / -1 左）；第一次没线索时先左
        self.face_small = 0            # v8.6：标记在正前方仍报错时的小幅转身次数
        self.last_turn_sign = 0
        self.face_good_sign = 0        # 上次转身后打出伤害的方向
        self.marker_side = 0           # 最近一次看到标记偏哪边
        self.fast_until = -1e9         # v8.6：转身前后 runner 用 50ms 循环
        self.face_need = False
        self.face_clear_t = -1e9
        self._noplate_warned = False
        self.menu_closes = 0
        self.menu_paused = False
        self.rest_skip_until = -1e9    # v8.6：回血超时后这段时间内不再脱战回血
        self.atk_presses = 0           # v8.9 选择攻击我的怪键 已按次数
        self.next_break_at = None      # v8.10 随机休息：下次休息时间
        self.break_until = None        # v8.10 正在休息：休息到
        self.hz_last_t = None          # v8.10 上一帧时间（休息中打怪时冻结倒计时）
        self.move_next_kills = None    # v8.10 再杀几只怪做一次小动作
        self.move_kills0 = 0
        self.move_last_t = None
        self.move_idle_s = None
        self.last_atk_press = -1e9
        self.skin_t0 = -1e9
        self.pending_loot = 0          # v8.4：战斗中杀掉的怪，脱战后再拾取/剥皮
        self.pending_loot_t = -1e9
        # v8.29 背包满检测：连按互动键没反应 N 次 → 认定背包满，本次运行关掉拾取/剥皮
        self.bag_full = False
        self.bag_try = None            # 正在看反应的一轮按键 {"t","n","still","resp"}
        self.bag_noresp = 0            # 连续没反应的按键次数
        self.bag_corpses = 0           # 已经因为没反应放弃了几具尸体（“两具确认”用）
        self.bag_skip = False          # 放弃了这具尸体：下次进战斗前不再捡选中的尸体
        self.bag_probe = False         # 只差最后一次：这轮只按拾取不按剥皮，看反应
        self.cr = None                 # v8.31 跑尸状态（None = 没在跑尸）
        self.deaths = 0                # v8.31 这次运行死了几次（不限次数）
        self.rest_end_t = -1e9         # v8.7：回血结束时间（尸体过期从这之后算）
        self.caster_hold = False       # v8.7：回血/补 buff 后先留在人形拾取/剥皮，捡完再变熊
        self.buff_queue = []           # v8.4：要补的 buff
        self.buff_press_t = None
        self.buff_next = -1e9
        self.buff_t0 = -1e9
        self.buff_tries = {}
        self.buff_retry_until = {}
        self.buff_last_cast = {}
        self.plate_pending = None      # (x, y, t) 刚点过的姓名板
        self.plate_fails = 0
        self.plate_last = -1e9
        self.plate_bad = []            # [(x, y, t)] 点了没选上的位置，8 秒内不再点
        self.plate_tpl_cache = (-1e9, False)
        self.last_clear_far = -1e9
        self.skin_seen_cast = False
        self.menu_last = -1e9
        self.pull_presses = 0
        self.pulling = False
        self._reset_recovery(clock())
        self.state = State.SEARCHING
        self.stop_reason = ""
        self.kills = 0
        self._mb_init()                  # v8.38：被多只怪打 → 临时变熊
        self._ts_init()                  # v8.40：Tab 找怪
        self._rc_init()                  # v8.44：断线自动重连
        self.wf = None                   # v8.46：朝远处选中的怪走过去
        self.rest_mode = None            # v8.46：RESTING 的子模式（post / mana）
        self.mr_skip_until = -1e9        # v8.46：先回蓝再开怪
        self.spell_t = -1e9              # v8.47：最近一次在治疗/回春术/补 buff/跑尸复活（3 秒内变身不测耗蓝）
        self.shift_samples = {}          # v8.47：实测变身耗蓝 最近 3 次（取中位数）
        self.lm_prev_cast = False        # v8.47：上一帧在读条
        now = clock()
        self.last_combat_time = now
        self.no_target_since: float | None = None
        self.zero_hp_since: float | None = None
        self.last_heal_time = -1e9
        self.click_fails = 0
        self.click_pending = None        # v8.28：(x, y, t) 找怪右键点的标记（截图坐标），等 verify_s 秒看选没选上
        self.confirm_fails = 0           # v8.28：点前确认连续不通过次数
        self.halt_t = None               # v8.28：看到标记先停下：开始停的时间
        self.far = None                  # v8.28：正朝远处红名走 {t0, seen, pos, pos_t}
        self.far_skip_until = -1e9
        self.far_seen_n = 0
        self.travel_seen = 0             # v8.28：巡逻路上连续几帧看到标记
        self.eng_seen = 0                # v8.32：非战斗连续几帧看到能打的怪的标记
        self.eng_log_t = -1e9
        self.plate_src_logged = None
        self.peek_plates = None          # v8.28：runner 设：再截一张图 → (未选中姓名板, 来源)
        self.click_hit_t = -1e9          # v8.28：找怪右键点中目标的时间（接近途中不再点）
        self.scan_turns = 0
        self.offscreen_turns = 0
        self.approach_start = now
        self.last_engage = -1e9
        self.last_interact = -1e9
        self.bear_belief = True          # 截图模式下不知道形态，只能自己记
        self.rest_start = now
        self.rest_next_cast = -1e9
        self.rejuv_time = -1e9           # v8.30：上次战后回春术放出来的时间（N 秒内不重复放）
        self.post_healed = False         # v8.30：这场战斗已经战后治疗过（真正脱战时不再补一次）
        self.last_obs = None
        self._area_warned = ""
        self.return_start = now
        self.prog_pos = None
        self.prog_time = now
        self.stuck_tries = 0
        self.walking = False
        self.pos_missing_since: float | None = None
        # v8.17 范围中心 / 热键 / 法力预留
        a = cfg.get("area", {}) or {}
        self.home_from_start = bool(a.get("home_on_start", True))
        self.home = None if self.home_from_start else (
            float(a.get("center_x", 50)), float(a.get("center_y", 50)), int(a.get("map_id", 0) or 0))
        self.area_cmds: list[str] = []
        self.route_cmds: list[str] = []  # v8.39：F6 添加跑尸路线点（死了/灵魂状态也处理）
        self.on_route = None             # 回调 (list)：F6 改了跑尸路线 → GUI 保存
        self.area_retry: dict[str, float] = {}
        self.on_home = None            # 回调 (x, y, map_id)：F8 设了新中心 → GUI 保存
        # v8.23 多中心巡逻：centers = [[x, y, map_id], ...]，1→2→3→2→1 来回走（v8.33）；home = 当前要去/所在的中心
        self.centers = [tuple(c[:3]) if len(c) >= 3 else (c[0], c[1], 0) for c in (a.get("centers") or []) if len(c) >= 2]
        self.ci = 0                    # 当前中心序号（0 开始）
        self.cdir = 1                  # v8.33 来回走的方向：+1 = 编号变大，-1 = 编号变小（1→2→3→2→1→2…）
        self.return_block_until = -1e9 # v8.33 回中心卡住（只有 1 个中心）→ 这段时间不再走回去，就地找怪
        self.near_death_noted = False  # v8.33 这场战斗已经写过“血量很危险，继续打”
        self.dead_idle = False         # v8.43：死了（跑尸关着）→ 不停止，等活过来
        self.travel = False            # 正在从一个中心走去下一个中心（路上看到怪也打）
        self.last_rclick = -1e9        # v8.24 上一次右键时间
        self.rclick_pos = self.rclick_range = None
        self.travel_look_t = -1e9
        self.travel_tab_t = -1e9
        self.on_centers = None         # 回调 (list)：F7/F8 改了中心列表 → GUI 保存
        if self.centers:
            self.home = None           # 有中心列表：读到坐标后从最近的中心开始
        self.mh_pos = None             # 读不到朝向时：走路的坐标变化估算朝向
        self.mh_heading = None
        self.mana_cost: dict[str, float] = {}   # 实测耗蓝 %：shift_cat / shift_bear / heal / buff
        self.prev_form = None
        self.prev_mana = None
        self.heal_mana0 = None
        self.rest_mana0 = None
        self.buff_mana0 = None
        self.cast_peak = None           # v8.42：读条中见过的最高法力（蓝在读完时才扣，读条期间还在回）
        self.heal_inflight = None       # v8.42：(时间, 法力)：等读条超时就结束了，但治疗之触还在读 → 蓝还没扣
        self.heal_costs: list[float] = []   # v8.42：这次运行实测的治疗之触耗蓝（取最近几次的最大值）
        self.shift_wait_noted = False
        self.spin_deg = 0.0             # v8.19 找怪累计转了多少度
        self.spin_due = False
        self.spin_wait_until = -1e9
        self._reset_target_tracking(now)

    @property
    def stationary(self) -> bool:
        return self.cfg.get("move", {}).get("mode", "stationary_tab") == "stationary_tab"

    def wants_nameplates(self) -> bool:
        """战斗中只有在面向错误/距离不对时才需要找选中姓名板（省 CPU）。
        原地模式：只有面向有问题、又看不到插件标记时，才用“选中姓名板”图片当备用。"""
        if self.stationary:
            o = self.last_obs
            if self.state == State.SEARCHING and self._st().get("plate_click", True) and \
                    (o is None or not o.target_exists or not o.in_combat):
                # v8.3：没目标 → 找姓名板点；v8.16：脱战时目标是死的/友方/被抢/太远也要找（v8.14 换怪先点姓名板，
                # 以前有目标时不截姓名板，换怪实际上全按了 Tab）
                return True
            return bool(self.state == State.COMBAT and self.face_need and o is not None and o.marker is None)
        if self.state != State.COMBAT:
            return True
        o = self.last_obs
        return bool(o and (o.error in ("facing", "range") or o.range in ("ranged", "far")
                           or o.err_facing or o.err_far or o.attack_range == "out"))

    # ------------------------------------------------------------ 工具
    def _reset_recovery(self, now):
        self.combat_start = now
        self.last_damage_t: float | None = None   # 最近一次打出伤害（目标掉血 或 插件战斗日志）
        self.rec_fails = 0
        self.rec_last: float | None = None        # 最近一次补救动作时间

    def _enter_combat(self, now):
        self.state = State.COMBAT
        self._reset_recovery(now)
        self.last_drop_time = now
        self.last_combat_time = now

    def _warn(self, key, msg, every=10.0):
        now = self.clock()
        if now - self._warned.get(key, -1e9) >= every:
            self._warned[key] = now
            self.log(msg)

    def _reset_target_tracking(self, now):
        self.fclick_n, self.fclick_t = 0, -1e9      # v8.17 右键姓名板修面向：换目标清零
        self.opened = False                         # v8.21 点击模式：这只怪按过开怪键没有
        self.face_backed, self.face_melee_turns = False, 0
        self.melee_backs, self.melee_cycle, self.melee_act_t = 0, 0, None
        self.last_target_hp = 101.0
        self.last_drop_time = now
        self.seen_alive = False
        self.target_gone_ticks = 0

    def _event(self, kind, text, every=10.0):
        """记录关键事件（runner 会存截图）。同类事件 every 秒内只记一次。"""
        now = self.clock()
        if now - self._warned.get("ev_" + kind, -1e9) >= every:
            self._warned["ev_" + kind] = now
            self.events.append((kind, text))

    def _ctx(self, text):
        try:
            self.inp.context = text
        except Exception:  # noqa: BLE001
            pass

    def stop(self, reason: str):
        if self.state != State.STOPPED:
            self.state = State.STOPPED
            self.stop_reason = reason
            self.log(f"⛔ 自动停止：{reason}")
            self._event("death" if "死亡" in reason else "stop", reason, 0)
        self.inp.stop_event.set()

    def _stall_stop(self, now) -> bool:
        """目标血量 stuck_timeout_s 秒没下降。v8.43：永远不停止（不管战斗补救开没开）→ 写提醒、截图、重新计时，继续打。"""
        s = self.cfg["safety"]
        self.log(f"⚠️ 目标血量 {s['stuck_timeout_s']} 秒没有下降（可能卡住/够不到/没蓝变身）→ 不停止，继续打")
        self._event("stall", f"目标血量 {s['stuck_timeout_s']} 秒没有下降（不停止）")
        self.last_drop_time = now
        return False

    def danger_stop(self, reason: str):
        """危险情况：停止并响提示音。"""
        self.stop(reason)
        if self.cfg["heal"].get("beep", True) and self.beep:
            try:
                self.beep()
            except Exception:  # noqa: BLE001
                pass

    @property
    def stopped(self) -> bool:
        return self.state == State.STOPPED

    def _fix_attacker_kind(self, obs):
        """v8.8：插件把目标报成“被别人抢了”（UnitIsTapDenied，灰名），但它正在打我 → 当敌人打。
        日志 21:15:47–21:16:19：灰名怪“打我=是、近战距离”，旧版当成被抢的怪一直 Tab 找别的，差点被打死。"""
        if not (obs.target_exists and obs.target_kind == "tapped"):
            return
        hitting = self._aggro(obs) or (obs.in_combat and self.hp_drop >= 1.0 and obs.range == "melee")
        if hitting:
            obs.target_kind = "enemy"
            self._warn("tappedattacker", f"⚔️ 目标显示“被别人抢了/灰名”，但它在打我（打我={obs.aggro}，"
                                         f"距离={obs.range}，掉血={self.hp_drop:.0f}%）→ 当敌人打，不换目标", 10.0)

    def _alive(self, obs, dead_pct):
        return obs.target_exists and obs.target_kind in ALIVE_KINDS and obs.target_hp > dead_pct

    def _clear_target(self, obs=None):
        """清目标。v8.1：没有目标时绝不按（Esc 在没目标时会打开游戏菜单）。"""
        key = (self.cfg["keys"].get("clear_target") or "").strip().lower()
        if not key:
            return
        o = obs if obs is not None else self.last_obs
        if o is not None and not o.target_exists:
            self.dlog(f"[清目标] 已经没有目标，不按「{key}」")
            return
        self._ctx("清除目标")
        self.inp.press(key)

    def _click_plate(self, p):
        n = self.cfg.get("nameplate", {})
        x = p["x"] + p["w"] / 2 + self.screen_offset[0]
        y = p["y"] + p["h"] / 2 + float(n.get("click_offset_y", 0)) + self.screen_offset[1]
        btn = n.get("click_button", "right")
        self.inp.click_at(x, y, button=btn)
        self._note_rclick(btn)

    # ---- v8.28 找怪点标记：过滤 / 点前确认 / 点后验证 / 先停下 / 远处红名
    def _nc(self):
        return self.cfg.get("nameplate", {})

    def _top_limit(self, fh) -> float:
        return fh * float(self._nc().get("skip_top_pct", 23) or 0) / 100.0

    def _is_bad(self, cx, cy, now) -> bool:
        n = self._nc()
        bs, br = float(n.get("bad_s", 8.0)), float(n.get("bad_radius_px", 60))
        return any(now - bt < bs and math.hypot(cx - bx, cy - by) < br for bx, by, bt in self.plate_bad)

    def _clickable(self, obs, now, top=False) -> list:
        """能点的“未选中姓名板”（插件标记或模板）：去掉左上角界面、底部动作条、点空过的位置、屏幕最上面一条（远处）。
        top=True → 反过来只要最上面一条里的（远处的怪，朝它走）。"""
        fw, fh = obs.frame_size
        mx, my = (self._st().get("plate_ui_margin") or [320, 140])[:2]
        lim = self._top_limit(fh)
        out = []
        for p in obs.nameplates:
            if p.get("kind") != "unselected":
                continue
            cx, cy = p["x"] + p["w"] / 2, p["y"] + p["h"] / 2
            if (cx < mx and cy < my) or cy > fh * 0.85 or self._is_bad(cx, cy, now):
                continue
            if (cy < lim) != top:
                continue
            out.append(p)
        return out

    def _log_plate_source(self, obs):
        if obs.source != "addon":
            return
        src = getattr(obs, "plate_source", "template")
        if src != self.plate_src_logged:
            self.plate_src_logged = src
            if src == "marker":
                self.log("🟨 找怪用插件 v0.11 的 黄|蓝 标记（只点能打的活怪，不再用姓名板模板）")
            else:
                self.log(f"⚠️ 找怪用姓名板模板（备用）：{getattr(obs, 'plate_note', '') or '插件标记不可用'}")

    def _halt_first(self, obs, now) -> bool:
        """v8.28：看到标记 → 先停下（松开前进键），停稳 stop_settle_s 秒再点。返回 True = 还在停，这一帧不点。"""
        settle = float(self._nc().get("stop_settle_s", 0.3))
        moving = bool(getattr(obs, "moving", False))
        if self.walking or moving:
            if self.walking:
                self._stop_walk()
            elif moving and now - getattr(self, "halt_tap_t", -1e9) >= 1.0 \
                    and now - getattr(self, "walk_stop_t", -1e9) >= 0.6:   # 刚松开 w 的那一下还在减速，不算
                # 自己没按前进键却在动 = 点地面后的自动走路（点击移动）→ 轻点一下后退键把它停住
                self.halt_tap_t = now
                self._ctx("停下：取消点击移动")
                self.inp.press(self.cfg["keys"].get("back") or "s", hold_ms=60, delay=False)
            if self.halt_t is None:
                self.log("✋ 看到能打的怪 → 先停下（松开移动键），停稳再右键点")
            self.halt_t = now
            return True
        if self.halt_t is not None and now - self.halt_t < settle:
            return True
        self.halt_t = None
        return False

    def _confirm_plate(self, p, now):
        """v8.28 点前确认：再截一张图，标记还在原位（confirm_px 内）才点；返回要点的姓名板（新位置）或 None。"""
        peek = self.peek_plates
        tol = float(self._nc().get("confirm_px", 25))
        if peek is None or tol <= 0:
            return p
        try:
            fresh, src = peek()
        except Exception as e:  # noqa: BLE001
            self.dlog(f"[姓名板] 点前确认截图失败：{e} → 直接点")
            return p
        cx, cy = p["x"] + p["w"] / 2, p["y"] + p["h"] / 2
        best, bd = None, 1e9
        for q in fresh:
            d = math.hypot(q["x"] + q["w"] / 2 - cx, q["y"] + q["h"] / 2 - cy)
            if d < bd:
                best, bd = q, d
        if best is not None and bd <= tol:
            self.confirm_fails = 0
            return best
        self.confirm_fails += 1
        if best is not None and self.confirm_fails >= 3:
            self.confirm_fails = 0
            self.dlog(f"[姓名板] 点前确认连续 3 次位置都在变 → 点最新的位置（差 {bd:.0f} 像素）")
            return best
        self.dlog(f"[姓名板] 点前确认：({cx:.0f},{cy:.0f}) 的标记" + ("不见了" if best is None else f"移动了 {bd:.0f} 像素")
                  + f"（允许 {tol:.0f}）→ 这次不点")
        return None

    def _click_check(self, obs, now, dead_pct) -> bool:
        """v8.28 点后验证：verify_s 秒内要选上能打的怪，否则算点空 → 那个位置 bad_s 秒内不点。返回 True = 还在等。"""
        cp = self.click_pending
        if cp is None:
            return False
        fx, fy, t = cp
        if self._alive(obs, dead_pct):
            self.click_pending = None
            self.click_fails = 0
            self.click_hit_t = now
            self.dlog(f"[姓名板] 点 ({fx:.0f},{fy:.0f}) 后 {now - t:.1f} 秒选上了怪")
            return False
        if obs.target_kind == "none" and not obs.in_combat and now - t < float(self._nc().get("verify_s", 0.6)):
            return True
        self.click_pending = None
        self.click_fails += 1
        self.plate_bad.append((fx, fy, now))
        why = "没选上怪" if obs.target_kind == "none" else f"选上的目标不能打（{obs.target_kind}）"
        self.log(f"[姓名板] 点 ({fx:.0f},{fy:.0f}) 后 {now - t:.1f} 秒{why} → 算点空（连续第 {self.click_fails} 次），"
                 f"{float(self._nc().get('bad_s', 8)):.0f} 秒内不再点那里")
        return False

    def _far_targets(self, obs, now) -> list:
        """远处的怪：红色名字 + 屏幕最上面一条里的标记。返回 [(cx, cy, 说明)]。"""
        out = [(p["x"] + p["w"] / 2, p["y"] + p["h"] / 2, "远处姓名板") for p in self._clickable(obs, now, top=True)]
        for n in getattr(obs, "red_names", None) or []:
            cx, cy = n["x"] + n["w"] / 2, n["y"] + n["h"] / 2
            if not self._is_bad(cx, cy, now):
                out.append((cx, cy, "红名"))
        return out

    def _far_bearing(self, obs, cx, cy) -> float:
        fw, fh = obs.frame_size
        deg, _ = marker_bearing(cx, cy, fw, fh, float(self._st().get("fov_deg", 90)), edge_px=-1, bottom_frac=2.0)
        return deg

    def _far_turn(self, deg, why):
        k = self.cfg["keys"]
        ms = max(40.0, min(1200.0, abs(deg) / max(30.0, self.turn_rate) * 1000.0))
        key = k.get("turn_right", "right") if deg > 0 else k.get("turn_left", "left")
        self._ctx(why)
        self.inp.press(key, hold_ms=ms, delay=False)

    def _far_begin(self, obs, now) -> bool:
        n = self._nc()
        if not n.get("far_names", True) or now < self.far_skip_until or obs.in_combat \
                or getattr(obs, "plate_source", "template") != "marker":
            return False
        tg = self._far_targets(obs, now)
        if not tg:
            return False
        fw, _ = obs.frame_size
        cx, cy, kind = min(tg, key=lambda t: abs(t[0] - fw / 2))
        deg = self._far_bearing(obs, cx, cy)
        turn = abs(deg) > float(n.get("far_turn_deg", 12))
        self.log(f"🚶 远处只看到{kind}（{cx:.0f},{cy:.0f}，偏 {deg:+.0f}°）→ " + (f"转 {deg:+.0f}° " if turn else "")
                 + f"朝它走，出了标记就停下点（最多 {float(n.get('far_walk_max_s', 10)):.0f} 秒，不按互动键）")
        if turn:
            self._far_turn(deg, f"朝远处{kind}转身")
        self.far = {"t0": now, "seen": now, "pos": obs.pos, "pos_t": now, "steer_t": now}
        self.far_seen_n = 0
        self._start_walk(obs)
        return True

    def _far_end(self, why, skip=True):
        self._stop_walk()
        self.far = None
        self.far_seen_n = 0
        if skip:
            self.far_skip_until = self.clock() + float(self._nc().get("far_skip_s", 20))
        self.log(f"🚶 停止朝远处的怪走：{why}" + (f"（{float(self._nc().get('far_skip_s', 20)):.0f} 秒内不再朝远处走）" if skip else ""))

    def _far_step(self, obs, now):
        n, f = self._nc(), self.far
        if self._clickable(obs, now):
            self.far_seen_n += 1
        else:
            self.far_seen_n = 0
        if self.far_seen_n >= int(n.get("patrol_frames", 2)):
            return self._far_end("出现能点的标记 → 停下点", skip=False)
        if self._outside_area(obs):
            return self._far_end("走到范围边上了")
        if now - f["t0"] > float(n.get("far_walk_max_s", 10)):
            return self._far_end(f"走了 {now - f['t0']:.0f} 秒还没出标记")
        if obs.pos is not None:
            if f["pos"] is None or distance(f["pos"], obs.pos) >= float(self.cfg.get("area", {}).get("move_eps", 0.03)):
                f["pos"], f["pos_t"] = obs.pos, now
            elif now - f["pos_t"] > 2.5:
                return self._far_end("2.5 秒坐标没变（卡住了）")
        tg = self._far_targets(obs, now)
        if tg:
            f["seen"] = now
            fw, _ = obs.frame_size
            cx, cy, kind = min(tg, key=lambda t: abs(t[0] - fw / 2))
            deg = self._far_bearing(obs, cx, cy)
            if abs(deg) > float(n.get("far_turn_deg", 12)) * 1.5 and now - f["steer_t"] >= 0.5:
                f["steer_t"] = now
                self.dlog(f"[远处] {kind} 偏 {deg:+.0f}° → 边走边转")
                self._far_turn(deg, f"朝远处{kind}修正方向")
        elif now - f["seen"] > float(n.get("far_lost_s", 1.5)):
            return self._far_end(f"{now - f['seen']:.1f} 秒看不到那个名字了")
        self._start_walk(obs)

    # ---- v8.24 右键频率：任意两次右键至少隔 click_interval_s（默认 3 秒），点完先判断要不要再点
    def _rgap(self) -> float:
        return float(self.cfg.get("move", {}).get("click_interval_s", 3.0))

    def _note_rclick(self, btn="right"):
        if str(btn).lower() != "right":
            return
        o = self.last_obs
        self.last_rclick = self.clock()
        self.rclick_pos = getattr(o, "pos", None) if o is not None else None
        self.rclick_range = getattr(o, "range", None) if o is not None else None
        self.rclick_err_t = None

    def _rclick_wait(self, now) -> bool:
        """上一次右键还没过 click_interval_s 秒。"""
        return now - getattr(self, "last_rclick", -1e9) < self._rgap()

    _RANK = {"melee": 1, "ranged": 2, "far": 3}

    def _rclick_need(self, obs, now):
        """点过以后（已过间隔）还要不要再点：返回原因字符串；None = 不用点（角色在走近）。"""
        if obs.error in ("facing", "range") or obs.err_facing or getattr(obs, "err_far", False):
            return "还在报" + ("面向错误" if (obs.error == "facing" or obs.err_facing) else "距离太远")
        p0, p1 = getattr(self, "rclick_pos", None), getattr(obs, "pos", None)
        if p0 is not None and p1 is not None:
            moved = distance(p0, p1)
            if moved < float(self.cfg.get("area", {}).get("move_eps", 0.03)):
                return f"{now - self.last_rclick:.0f} 秒角色没在走（坐标没变）"
            self.rclick_pos = p1       # 在走：下一次从这里比
            return None
        r0, r1 = self._RANK.get(getattr(self, "rclick_range", None) or ""), self._RANK.get(obs.range or "")
        if r1 is None or r0 is None or r1 >= r0:
            self.rclick_range = obs.range
            return "距离没有变近"
        self.rclick_range = obs.range
        return None

    def _scan_turn(self, ms=None, search=True):
        sc = self.cfg.get("scan", {})
        key = self.cfg["keys"].get("scan_turn") or self.cfg["keys"].get("turn")
        if key:
            hold = float(ms if ms is not None else sc.get("tap_ms", 200))
            self.inp.press(key, hold_ms=hold)
            self.inp.sleep(float(sc.get("settle_ms", 350)) / 1000.0)
            if search:      # v8.19：累计找怪转了多少度（按住时间 × 转身速度）
                self.spin_deg = getattr(self, "spin_deg", 0.0) + hold / 1000.0 * float(self.turn_rate)
                if sc.get("spin_home", True) and self.spin_deg >= float(sc.get("spin_deg", 360)):
                    self.spin_due = True

    def _spin_reset(self):
        self.spin_deg = 0.0
        self.spin_due = False

    # ---- v8.23 多中心巡逻
    def _same_map(self, c, map_id) -> bool:
        cm = int(c[2] or 0) if len(c) > 2 else 0
        return not cm or map_id is None or int(map_id or 0) == 0 or int(map_id) == cm

    def _next_center(self, obs):
        """v8.33 来回走：下一个（同地图的）中心 (序号, 中心)，1→2→3→2→1→2…（cdir = 方向，到头就掉头）；
        别的地图的中心跳过（照原方向往后找）。只有 1 个中心或别的都不在这张地图 → None（照旧回同一个中心）。"""
        n = len(self.centers)
        if n < 2:
            return None
        i, d = min(max(self.ci, 0), n - 1), (1 if self.cdir >= 0 else -1)
        for _ in range(2 * n):
            j = i + d
            if j < 0 or j >= n:
                d = -d
                j = i + d
            i = j
            if i == self.ci:
                continue
            c = self.centers[i]
            if self._same_map(c, obs.map_id):
                self.cdir = d
                return i, c
            self._warn(f"cmap{i}", f"🚩 中心 #{i + 1} 在地图 {c[2]}，现在在地图 {obs.map_id} → 跳过这个中心", 30)
        return None

    def _start_dir(self):
        """v8.33：从最近的中心开始时的方向：先往编号大的走；已经是最后一个就往回走。"""
        self.cdir = -1 if self.centers and self.ci >= len(self.centers) - 1 else 1

    def set_centers(self, centers):
        """GUI 改了中心列表（删除/清空）。"""
        self.centers = [tuple(c[:3]) for c in centers]
        if not self.centers:
            self.travel = False
            return
        self.ci = min(max(self.ci, 0), len(self.centers) - 1)     # v8.33：删了中心 → 序号夹到范围内，方向重置
        self._start_dir()
        if self.home is not None and tuple(self.home) not in self.centers:
            self.home = self.centers[self.ci]
            self.travel = False

    def _save_centers(self):
        if self.on_centers:
            try:
                self.on_centers([[round(c[0], 2), round(c[1], 2), int(c[2] or 0)] for c in self.centers])
            except Exception:  # noqa: BLE001
                pass

    def _travel_look(self, obs, now) -> bool:
        """v8.23：在两个中心之间走 → 看到能打的怪就停下去打。返回 True = 停下了（转到找怪）。"""
        st = self.cfg.get("scan", {})
        if not self.stationary:      # v8.28：连续 patrol_frames 帧都看到能点的标记才停
            self.travel_seen = self.travel_seen + 1 if self._clickable(obs, now) else 0
        if self._pull_mana_low(obs, now):
            return False                 # v8.47：蓝不到开怪线 → 路上不停下打，走到中心再回蓝
        if now - self.travel_look_t < float(st.get("patrol_look_s", 1.0)):
            return False
        dead_pct = float(self.cfg.get("detect", {}).get("dead_pct", 1.0))
        found = None
        if self._alive(obs, dead_pct) and obs.target_kind not in ("friendly", "tapped") and obs.range != "far" \
                and not self._ts_ignoring(now):
            found = f"选中了能打的怪（血量 {obs.target_hp:.0f}%）"
        elif not self.stationary:
            plates = self._clickable(obs, now)
            need = int(self._nc().get("patrol_frames", 2))
            if plates and self.travel_seen >= need and not (now < self.skip_until and self.skip_pos is not None):
                found = f"连续 {self.travel_seen} 帧看到 {len(plates)} 个能打的怪的标记"
        elif self.cfg["keys"].get("target") and now - self.travel_tab_t >= float(st.get("patrol_tab_s", 2.5)):
            self.travel_tab_t = now
            self._ctx("巡逻路上 Tab 找怪")
            self.inp.press(self.cfg["keys"]["target"])
        if found is None:
            return False
        self.travel_look_t = now + float(st.get("patrol_resume_s", 3.0))   # 打完/没选上 3 秒内不再停
        self._stop_walk()
        self.log(f"🚩 去中心 #{self.ci + 1} 的路上{found} → 停下打怪，打完接着走")
        self.state = State.SEARCHING
        return True

    def _spin_home(self, obs, now) -> bool:
        """v8.19：找怪转满一圈还没找到能打的怪 → 走回范围中心（已在中心附近就等几秒）。返回 True = 这一帧处理了。"""
        sc, a = self.cfg.get("scan", {}), self.cfg.get("area", {})
        deg = getattr(self, "spin_deg", 0.0)
        self._spin_reset()
        self.tabs = self.tab_rounds = 0
        if self.home is None or obs.pos is None:
            self.log(f"🔄 找怪转了约 {deg:.0f}° 没找到能打的怪，但" + ("还没有范围中心（按 F8 设）" if self.home is None
                                                              else "读不到坐标") + " → 不回中心，接着找")
            return False
        hm = int(self.home[2] or 0)
        if hm and obs.map_id is not None and obs.map_id != hm:
            self.log(f"🔄 找怪转了约 {deg:.0f}° 没找到怪，但当前地图 {obs.map_id} 不是中心所在地图 {hm} → 不回中心（按 F8 设新中心）")
            return False
        r = float(a.get("radius", 3))
        nxt = self._next_center(obs)
        if nxt is not None:
            i, c = nxt
            self.ci, self.home, self.travel = i, c, True
            self.travel_look_t = self.travel_tab_t = now
            d = distance(obs.pos, c[:2])
            self.log(f"🚩 找怪转了约 {deg:.0f}° 没找到能打的怪 → 走去下一个中心 #{i + 1}/{len(self.centers)} "
                     f"{c[0]:.2f},{c[1]:.2f}（距离 {d:.2f}，直线走；路上看到能打的怪也打）")
            self.start_return(obs)
            return True
        d = distance(obs.pos, self.home[:2])
        if d <= r * float(sc.get("spin_near_ratio", 0.2)):
            w = float(sc.get("spin_wait_s", 3.0))
            self.spin_wait_until = now + w
            self.log(f"🔄 找怪转了约 {deg:.0f}° 没找到能打的怪，已经在中心附近（距离 {d:.2f}）→ 等 {w:.0f} 秒再找")
            return True
        self.log(f"🔄 找怪转了约 {deg:.0f}° 没找到能打的怪 → 走回范围中心（距离 {d:.2f}，半径 {r:.2f}；路上被打会停下迎战）")
        self.start_return(obs)
        return True

    # ---- 形态
    # ---- v8.15 战斗形态：熊 / 猎豹（_is_bear/_to_bear 这些名字保留，意思是“在不在战斗形态/变回战斗形态”）
    def _cat(self) -> bool:
        # v8.38：被多只怪打临时变熊时，战斗形态当成“熊”
        return is_cat(self.cfg) and not getattr(self, "mb_active", False)

    def _cform(self) -> str:
        return "cat" if self._cat() else "bear"

    def _cform_cn(self) -> str:
        return "猎豹" if self._cat() else "熊"

    def _form_key(self) -> str:
        k = self.cfg["keys"]
        if self._cat():
            key = str(k.get("cat_form") or "").strip()
            if not key:
                self._warn("nocatkey", "⚠️ 战斗形态选了“猎豹”，但“猎豹形态键”没填 → 没法变猎豹。请在“按键”页填上", 60)
            return key
        return k["bear_form"]

    def _cancel_key(self) -> str:
        """取消变形键。猎豹模式下，如果“取消变形键”和熊形态键一样（再按一次就退出的用法），就改按猎豹形态键。"""
        k = self.cfg["keys"]
        c = str(k.get("cancel_form") or "").strip()
        if self._cat():
            if not c or c.lower() == str(k.get("bear_form") or "").strip().lower():
                return self._form_key()
            return c
        return c or k["bear_form"]

    def _is_bear(self, obs) -> bool:
        if obs.form in ("bear", "caster", "cat", "travel", "other"):
            return obs.form == self._cform()
        return self.bear_belief

    def _shift_need(self) -> float:
        """v8.42：变回战斗形态要多少蓝：实测变身耗蓝；没测到 → 预留线。"""
        form = self._cform()
        learned = self.mana_cost.get("shift_" + form)
        if learned:
            return float(learned)
        return float(self.cfg["heal"].get("reserve_cat_pct" if form == "cat" else "reserve_bear_pct", 30) or 0)

    def _to_bear(self, obs):
        if not self._is_bear(obs):
            key = self._form_key()
            need = self._shift_need()
            mana = obs.mana
            if (key and obs.form == "caster" and need > 0 and mana is not None and 0 < mana < need):
                # v8.42：蓝不够变身 → 别一直按（按了也只是报错），等回蓝；每 5 秒试一次以防读数不准
                now = self.clock()
                if not self.shift_wait_noted:
                    self.shift_wait_noted = True
                    self.log(f"💧 法力 {mana:.0f}% 不够变{self._cform_cn()}（约需 {need:.0f}%）→ 等回蓝再变，不连按（每 5 秒试一次）")
                if now - getattr(self, "shift_try_t", -1e9) < 5.0:
                    self.bear_belief = True
                    return
                self.shift_try_t = now
            else:
                self.shift_wait_noted = False
            if key:
                self._ctx(f"变回{self._cform_cn()}形态（当前形态 {obs.form}）")
                self.inp.press(key)
        self.bear_belief = True

    def _cancel_distinct(self) -> bool:
        """v8.38：取消变形键是单独的键（/cancelform 宏），不是熊/猎豹形态键 → 在任何形态按它都只会退出变形。"""
        k = self.cfg["keys"]
        c = str(k.get("cancel_form") or "").strip().lower()
        return bool(c) and c not in (str(k.get("bear_form") or "").strip().lower(),
                                     str(k.get("cat_form") or "").strip().lower())

    def _in_any_form(self, obs) -> bool:
        """要退出变形才能放治疗：在战斗形态里；或者（v8.38）在另一个战斗形态里（熊↔猎豹没变回来）且取消键是单独的键。"""
        if self._is_bear(obs):
            return True
        return obs.form in ("bear", "cat") and self._cancel_distinct()

    def _to_caster(self, obs):
        if self._in_any_form(obs):
            key = self._cancel_key()
            if key:
                self._ctx(f"退出{self._cform_cn()}形态（准备放治疗之触）")
                self.inp.press(key)
        self.bear_belief = False

    # ---- 攻击/面向：右键选中的姓名板（点击移动会自动转向并走过去）
    def _engage(self, obs):
        now = self.clock()
        self.last_engage = now
        if getattr(self, "rclick_pos", None) is None and obs.pos is not None:
            self.rclick_pos = obs.pos      # 右键那一帧没读到坐标 → 从现在开始比
        if self._rclick_wait(now) and (obs.selected_plate is not None or self._has_marker(obs)):
            self.dlog(f"[右键] {now - self.last_rclick:.1f} 秒前刚右键点过（间隔 {self._rgap():.0f} 秒）→ 不再点，角色已经在走过去")
            return
        if obs.selected_plate is not None:
            self._click_plate(obs.selected_plate)
        elif self._click_marker(obs):
            pass                    # v8.22：右键点插件标在目标姓名板中间的标记
        elif obs.in_combat or self._being_hit(obs):
            # v8.22：战斗中/挨打时绝不按互动键（会去剥身边的尸体）→ 按开怪键
            self.inp.press(self.cfg["keys"]["attack"])
        elif self.cfg["keys"].get("interact") and now - getattr(self, "last_interact", -1e9) >= 3.0:
            self.last_interact = now     # 脱战走过去：互动键最多 3 秒一次
            self.inp.press(self.cfg["keys"]["interact"])
        else:
            self.inp.press(self.cfg["keys"]["attack"])

    @staticmethod
    def _has_marker(obs) -> bool:
        return bool(getattr(obs, "marker_center", False) and getattr(obs, "marker", None) is not None)

    def _click_marker(self, obs, why=None) -> bool:
        """v8.22：右键点目标姓名板中间的标记（插件 v0.10）。找不到标记返回 False。"""
        if not getattr(obs, "marker_center", False) or getattr(obs, "marker", None) is None:
            return False
        fc = self.cfg.get("face_click", {}) or {}
        x = obs.marker[0] + self.screen_offset[0]
        y = obs.marker[1] + float(fc.get("offset_y", 0) or 0) + self.screen_offset[1]
        btn = str(fc.get("button") or "right").strip().lower()
        if why:
            self._ctx(why)
        self.inp.click_at(x, y, button=btn, restore=bool(fc.get("restore_mouse", True)))
        self._note_rclick(btn)
        return True

    def _offscreen(self, obs) -> bool:
        n = self.cfg.get("nameplate", {})
        return bool(n.get("turn_when_offscreen", True) and obs.selected_checked and obs.selected_plate is None)

    def _turn_toward(self, obs) -> bool:
        """有目标但看不到选中姓名板 → 转视角。返回 True 表示本 tick 已处理。转太多次就放弃目标。"""
        if not self._offscreen(obs):
            self.offscreen_turns = 0
            return False
        n = self.cfg["nameplate"]
        if self.offscreen_turns >= int(n.get("max_offscreen_turns", 8)):
            self.log("转了一圈还是看不到目标姓名板，放弃这个目标")
            self.offscreen_turns = 0
            self._clear_target()
            self.state = State.SEARCHING
            return True
        self.offscreen_turns += 1
        self._scan_turn(n.get("offscreen_turn_ms", 200), search=False)
        return True

    def _being_hit(self, obs) -> bool:
        """正在挨打：这一跳血量掉了 ≥2%，或者选中的活怪在打我。"""
        if self.hp_drop >= 2.0:
            return True
        return bool(obs.target_exists and obs.target_kind in ALIVE_KINDS and self._aggro(obs))

    def _need_rest(self, obs, now=None) -> bool:
        """要不要战后回血。v8：刚杀完怪 post_fight_grace_s 秒内“战斗中”是残留状态，只要没挨打照样回血。"""
        h = self.cfg["heal"]
        if self._post_on():
            # v8.27：每次战斗结束治疗一次（< 70% 生命之触 / 70-99% 回春术 / 满血不治疗）
            if not getattr(self, "post_due", False) or obs.player_hp >= self._post_skip_line() or obs.player_hp <= 0:
                return False
            if self._rejuv_cd_skip(obs):
                return False
        else:
            if obs.player_hp >= self._rest_below():
                return False
            if (now if now is not None else self.clock()) < self.rest_skip_until:
                return False
        if not obs.in_combat:
            return True
        now = self.clock() if now is None else now
        recent = now - getattr(self, "last_hit_t", -1e9) < float(h.get("recent_hit_s", 2.5))
        return now - self.last_kill_t < float(h.get("post_fight_grace_s", 8.0)) and not self._being_hit(obs) and not recent

    # ------------------------------------------------------------ v8.17 预留变身法力
    def _reserve(self, form=None) -> float:
        """要留给“变回战斗形态”的法力 %。设置值；实测变身耗蓝更高时用 实测+2。0 = 不预留。"""
        h = self.cfg["heal"]
        form = form or self._cform()
        base = float(h.get("reserve_cat_pct" if form == "cat" else "reserve_bear_pct", 30) or 0)
        if base <= 0:
            return 0.0
        learned = self.mana_cost.get("shift_" + form)
        if learned and h.get("reserve_learn", True):
            return max(base, learned + 2.0)
        return base

    def _cost(self, kind) -> float:
        h = self.cfg["heal"]
        if kind == "heal" and self.heal_costs:
            return max(self.heal_costs[-3:])          # v8.42：最近 3 次实测里最贵的那次（保守）
        if kind in self.mana_cost:
            return self.mana_cost[kind]
        if kind == "rejuv":
            return float(h.get("rejuv_cost_pct", 12) or 0)
        return float(h.get("heal_cost_pct" if kind == "heal" else "buff_cost_pct", 20 if kind == "heal" else 10) or 0)

    def _mana_short(self, obs, kind) -> str | None:
        """放一个治疗之触/buff 后法力会低于预留线 → 返回原因；够 / 读不到法力 → None。"""
        r = self._reserve()
        mana = obs.mana
        if r <= 0 or mana is None or mana <= 0:
            return None
        c = self._cost(kind)
        margin = float(self.cfg["heal"].get("reserve_margin_pct", 3) or 0) if kind in ("heal", "rejuv") else 0.0  # v8.42
        pend = self._inflight_cost(obs) if kind in ("heal", "rejuv") else 0.0
        after = mana - pend - c
        if after < r + margin:
            name = {"heal": "治疗之触", "rejuv": "回春术"}.get(kind, "buff")
            extra = f"，上一个治疗之触还在读、约 {pend:.0f}% 还没扣" if pend else ""
            return (f"预计{name}后法力 {after:.0f}%（现在 {mana:.0f}% − {name}约 {c:.0f}%{extra}）"
                    f" < 变{self._cform_cn()}需要 {r + margin:.0f}%（预留 {r:.0f}% + 余量 {margin:.0f}%）")
        return None

    def _inflight_cost(self, obs) -> float:
        """v8.42：上一个治疗之触按超时结束、但游戏里还在读 → 它的蓝还没扣，算进去。蓝掉了/过了 6 秒 → 已经扣了。"""
        inf = self.heal_inflight
        if not inf:
            return 0.0
        t0, m0 = inf
        if self.clock() - t0 > 6.0 or obs.mana is None or (m0 is not None and obs.mana < m0 - 5):
            self.heal_inflight = None
            return 0.0
        return self._cost("heal")

    def _track_cast_peak(self, obs):
        """v8.42：读条中记下最高法力；实测耗蓝 = 读条中最高 − 读完后（读条期间回的蓝不算进去，以前会测低）。"""
        if obs.casting and obs.mana is not None and obs.mana > 0:
            self.cast_peak = obs.mana if self.cast_peak is None else max(self.cast_peak, obs.mana)

    def _learn_mana(self, obs):
        """实测变身耗蓝：上一帧人形 → 这一帧猎豹/熊，法力掉了多少。
        v8.47（t189u/t190u）：治疗之触读条超时还在读、刚放完回春术、刚复活回蓝 时变身会把别的蓝算进去（测出 45-48%，
        变身预留被抬到 50%，挡掉了好几次救命的治疗）→ 3 秒内有治疗/回春/补 buff/复活、上一帧在读条 → 不测；
        取最近 3 次的中位数，最多 = 设置的预留 + reserve_learn_cap_pct（默认 8）。"""
        mana, form = obs.mana, obs.form
        pf, pm = self.prev_form, self.prev_mana
        prev_cast, self.lm_prev_cast = self.lm_prev_cast, bool(obs.casting)
        self.prev_form, self.prev_mana = form, mana
        if pf != "caster" or form not in ("cat", "bear") or not pm or not mana or mana <= 0:
            return
        drop = pm - mana
        if not 5 <= drop <= 70:
            return
        if prev_cast or obs.casting or self.heal_inflight or self.clock() - self.spell_t < 3.0:
            self.dlog(f"[法力] 变{'猎豹' if form == 'cat' else '熊'}掉了 {drop:.0f}%，但刚治疗/回春/复活或还在读条 → 不算变身耗蓝")
            return
        h = self.cfg["heal"]
        base = float(h.get("reserve_cat_pct" if form == "cat" else "reserve_bear_pct", 30) or 0)
        cap = base + float(h.get("reserve_learn_cap_pct", 8) or 0) if base > 0 else 70.0
        sm = (self.shift_samples.get(form, []) + [drop])[-3:]
        self.shift_samples[form] = sm
        med = sorted(sm)[len(sm) // 2] if len(sm) % 2 else sum(sorted(sm)[len(sm) // 2 - 1:len(sm) // 2 + 1]) / 2
        val = min(med, cap)
        old = self.mana_cost.get("shift_" + form)
        self.mana_cost["shift_" + form] = val
        drop = val
        if old is None or abs(old - drop) >= 3:
            cn = "猎豹" if form == "cat" else "熊"
            self.log(f"🔋 实测变{cn}耗蓝约 {drop:.0f}%（这次 {pm:.0f}% → {mana:.0f}%，最近 {len(sm)} 次中位数，上限 {cap:.0f}%）；变身预留 = {self._reserve(form):.0f}%")

    def _learn_cost(self, kind, before, obs, lo=3, hi=60):
        peak, self.cast_peak = self.cast_peak, None
        if before is None or obs.mana is None or obs.mana <= 0:
            return
        if obs.casting:                 # v8.42：还在读 → 蓝还没扣，不测
            return
        if kind == "heal" and peak is not None:
            before = max(before, peak)
        drop = before - obs.mana
        if lo <= drop <= hi:
            old = self.mana_cost.get(kind)
            self.mana_cost[kind] = drop
            if kind == "heal":
                self.heal_costs = (self.heal_costs + [drop])[-5:]
            if old is None or abs(old - drop) >= 3:
                self.dlog(f"[法力] 实测{ {'heal': '治疗之触', 'rejuv': '回春术'}.get(kind, 'buff') }耗蓝约 {drop:.0f}%")

    def _rest_below(self) -> float:
        """v8.2：低于这条线才开始脱战回血（以前用“回血到 90%”当开始线 → 每杀一只都变人形）。"""
        h = self.cfg["heal"]
        to = float(h.get("rest_to_pct", 90))
        return min(float(h.get("rest_below_pct", to)), to)

    @staticmethod
    def _hp_src(obs) -> str:
        return {"bin": "插件数值", "bar": "插件进度条", "screen": "截图血条"}.get(getattr(obs, "hp_source", ""), "?")

    def _note_scan_turns(self):
        """v8.6：转视角找怪不再有次数上限（以前 40 次就停止），每 40 次记一条日志。"""
        if self.scan_turns and self.scan_turns % 40 == 0:
            self.log(f"ℹ️ 已经转了 {self.scan_turns} 次视角还没找到怪，继续找（不会自动停止）")

    def _close_game_menu(self, now):
        """插件报告游戏菜单（Esc 菜单）开着 → 按一次 Esc 关掉；
        v8.6：关了 2 次还开着 → 认为是你自己打开的，暂停（不按任何键），等菜单关掉自动继续。"""
        if self.menu_closes >= 2:
            if not self.menu_paused:
                self.menu_paused = True
                self.log("⏸️ 游戏菜单按了 2 次 Esc 还开着（可能是你自己打开的）→ 暂停，不按任何键；菜单关掉后自动继续")
            return
        if now - self.menu_last < 2.0:
            return
        self.menu_closes += 1
        self.menu_last = now
        self.log(f"⚠️ 游戏菜单被打开了 → 按一次 Esc 关掉（第 {self.menu_closes} 次）")
        self._event("game_menu", "游戏菜单开着")
        self._ctx("关闭游戏菜单")
        self.inp.press("esc")
        self.menu_last = self.clock()

    def _track_hp_unknown(self, obs, now):
        """v8：战斗中读不到自己血量超过 hp_unknown_warn_s 秒 → 大声报警（日志 + 提示音一次）。"""
        h = self.cfg["heal"]
        if obs.in_combat and not obs.hp_readable:
            if self.hp_unknown_since is None:
                self.hp_unknown_since = now
                self.dlog("[治疗判断] 战斗中读不到自己血量（插件血条画不出来）")
            elif now - self.hp_unknown_since >= float(h.get("hp_unknown_warn_s", 3.0)):
                self._event("heal_skipped", "战斗中读不到血量")
                self._warn("hp", f"⚠️⚠️⚠️ 战斗中已经 {now - self.hp_unknown_since:.0f} 秒读不到自己血量（插件画不出血条）！"
                                 "不会自动治疗，请看着点！把聊天框里 BotStatus 的报错发给我", 5.0)
                if not self.hp_unknown_beeped and h.get("beep", True) and self.beep:
                    self.hp_unknown_beeped = True
                    try:
                        self.beep()
                    except Exception:  # noqa: BLE001
                        pass
        else:
            if self.hp_unknown_since is not None:
                self.dlog(f"[治疗判断] 血量又能读到了（{obs.player_hp:.0f}%）")
            self.hp_unknown_since = None
            self.hp_unknown_beeped = False

    # ------------------------------------------------------------ 主决策
    def tick(self, obs):
        if self.stopped:
            return
        self.last_obs = obs
        now = self.clock()
        self._ctx(f"状态 {self.state.value}")
        if self.far is not None and (self.state != State.SEARCHING or obs.source != "addon"
                                     or getattr(obs, "game_menu", False)):
            self._far_end("状态变了/读不到插件", skip=False)
        h, s, d = self.cfg["heal"], self.cfg["safety"], self.cfg["detect"]
        dead_pct = float(d["dead_pct"])

        if self.wf and self.state != State.APPROACHING:
            self._wf_end()                  # v8.46：不在接近目标了（被打/治疗/换目标）→ 松开前进键
        if self._reconnect_tick(obs, now):  # v8.44：断线/登录/选角色界面 → 每 10 秒按 Enter，进游戏后继续
            return

        # 1) 死亡检测
        if obs.source == "addon":
            self.addon_seen = True
        elif getattr(self, "addon_seen", False):
            # v8.12：本次已经读到过插件，现在突然读不到（多半是 WowGrinder 界面/别的窗口挡住了游戏，或游戏在读条/切图）
            # → 截图识别的血量不可信，不判断死亡、不按键，等插件色块回来
            self.zero_hp_since = None
            if self.state != State.STOPPED:
                self._warn("addonlost", f"⏸️ 读不到插件色块（来源={obs.source}，可能游戏窗口被挡住）→ 不判断死亡、暂停按键，等插件恢复", 10.0)
            return
        if self.route_cmds and obs.source == "addon":
            self._route_tick(obs, now)       # v8.39：F6 记跑尸路线点（死了/灵魂状态也能记）
        if self._corpse_tick(obs, now):      # v8.31：死了 → 释放灵魂 → 跑尸 → 复活 → 回血回蓝
            return
        self._bag_watch(obs, now)
        # v8.43：死了不再停止（跑尸开着时上面 _corpse_tick 已经处理；跑尸关着/插件太旧 → 不按键，等活过来再继续）
        dead_now = bool(obs.player_dead)
        if not dead_now and obs.player_hp <= dead_pct and obs.hp_readable:
            self.zero_hp_since = self.zero_hp_since if self.zero_hp_since is not None else now
            if now - self.zero_hp_since < float(s.get("death_hp_seconds", 2.0)):
                return
            dead_now = True
        elif not dead_now:
            self.zero_hp_since = None
        if dead_now:
            if not self.dead_idle:
                self.dead_idle = True
                why = "跑尸关着" if not self._corpse_on() else "插件读不到死亡信息，跑不了尸"
                self.log(f"💀 死亡（{why}）→ 不停止，等待（不按键），活过来后继续")
                self._event("death", f"死亡（{why}），等待")
                if self.walking:
                    self._stop_walk()
            return
        if self.dead_idle:
            self.dead_idle = False
            self.zero_hp_since = None
            self.log("✨ 活过来了 → 继续打怪")
            self._reset_target_tracking(now)
            self.state = State.SEARCHING

        if getattr(obs, "game_menu", False):
            return self._close_game_menu(now)
        if self.menu_paused:
            self.menu_paused = False
            self.log("▶️ 游戏菜单关了，继续")
        self.menu_closes = 0
        self.hp_drop = 0.0 if self.prev_hp is None else self.prev_hp - obs.player_hp
        if self.hp_drop >= 2.0:
            self.last_hit_t = now          # v8.17：最近一次掉血（多只怪时，杀完一只别马上变人形回血）
        self.prev_hp = obs.player_hp
        self._calibrate_turn(obs, now)
        self._fix_attacker_kind(obs)
        if obs.casting or self.heal_inflight or self.state in (State.HEALING, State.BUFFING, State.CORPSE) \
                or (self.state == State.RESTING and getattr(self, "rest_mode", None) == "post"):
            self.spell_t = now               # v8.47：刚治疗/回春/补 buff/复活 → 3 秒内变身不测耗蓝
        self._learn_mana(obs)
        self._track_cast_peak(obs)
        self._area_tick(obs, now)

        self._break_clock(obs, now)
        was = getattr(self, "prev_in_combat", False)     # v8.27：每次战斗结束 → 战后治疗一次
        if obs.in_combat and not was:
            self.post_due = False
            self.post_healed = False                     # v8.30：新的一场战斗
        elif was and not obs.in_combat:
            if self.post_healed:                         # v8.30：残留战斗时已经治疗过 → 这场不再治疗
                if self.post_due or self._post_on():
                    self.dlog("[战后治疗] 真正脱战了，但这场战斗已经治疗过 → 不再治疗")
                self.post_due = False
            else:
                self.post_due = True
        if obs.in_combat and self.hp_drop >= 2.0 and self.post_healed \
                and not (self.state == State.RESTING and getattr(self, "rest_mode", None) == "post"):
            self.post_healed = False                     # v8.30：治疗完又挨打了 = 接着打下一只，战斗结束再治疗
        self.prev_in_combat = bool(obs.in_combat)
        if self._multi_bear_tick(obs, now):     # v8.38：被多只怪打 → 变熊抗；只剩 1 只/脱战 → 变回猎豹
            return
        if obs.in_combat:
            self.last_combat_time = now
            self._spin_reset()
            self.spin_wait_until = -1e9
            if self.state == State.RETURNING:  # 回程中被打 → 停下迎战
                self._stop_walk()
                self.log("回中心途中被打，停下迎战")
                self.state = State.SEARCHING

        # 2) 战斗中低血量 → 治疗之触；治疗过还是很低 → 停止 + 提示音
        fighting = self.state in (State.COMBAT, State.APPROACHING) or bool(obs.in_combat)
        if not fighting:
            self.fight_heals = 0
            self.near_death_noted = False
        self._track_hp_unknown(obs, now)
        if obs.in_combat and obs.source == "addon" and obs.attack_slot is False:
            self._warn("slot", "⚠️ 插件在动作条上没找到“普通攻击”，判断不了距离。请把 普通攻击 拖到任意格子", 60)
        if self.state == State.HEALING:
            return self._heal_step(obs, now)
        if fighting and self.state != State.RESTING and obs.hp_readable:
            hp, line = obs.player_hp, float(h["heal_pct"])
            if hp < float(h.get("critical_pct", 15)) and self.fight_heals >= 1:
                if self._corpse_on() and getattr(obs, "death_info", False):
                    # v8.33：开着自动跑尸（插件 v0.12）→ 不停止、不响，继续打；死了自动跑尸
                    if not self.near_death_noted:
                        self.near_death_noted = True
                        self.log(f"⚠️ 治疗过后血量仍只有 {hp:.0f}%（< {h.get('critical_pct', 15)}%），很危险 → 继续打；死了自动跑尸")
                elif not self.near_death_noted:
                    # v8.43：不再停止 → 每场战斗提醒一次（开着提示音就响一下），继续打
                    self.near_death_noted = True
                    self.log(f"⚠️ 治疗过后血量仍只有 {hp:.0f}%（< {h.get('critical_pct', 15)}%），很危险 → 不停止，继续打")
                    self._event("danger", f"治疗过后血量仍只有 {hp:.0f}%")
                    if h.get("beep", True) and self.beep:
                        try:
                            self.beep()
                        except Exception:  # noqa: BLE001
                            pass
            if hp < line:
                left = float(h["heal_cooldown_s"]) - (now - self.last_heal_time)
                short = self._mana_short(obs, "heal") if left <= 0 else None
                if short:
                    # v8.17：治疗会把法力用到预留线以下 → 不治疗，留着蓝变身，继续打
                    self._warn("healreserve_v842", f"💧 留蓝变{self._cform_cn()}：{short} → 不再治疗", 5.0)
                    self._event("heal_reserve", f"血量 {hp:.0f}% 但法力要留给变身")
                    self._warn("healreserve", f"🩹 不治疗：血量 {hp:.0f}% < 治疗线 {line:.0f}%，但{short} → 留着蓝，继续打", 5.0)
                    corpse_ok = self._corpse_on() and getattr(obs, "death_info", False)   # v8.33：开着跑尸 → 不响
                    if hp < float(h.get("critical_pct", 15)) and now - getattr(self, "reserve_beep_t", -1e9) > 20:
                        self.reserve_beep_t = now
                        self.log(f"⚠️⚠️ 血量 {hp:.0f}% 低于危险线，法力不够在留出变身蓝的情况下治疗"
                                 + ("（继续打；死了自动跑尸）" if corpse_ok else "（提示音，继续打）"))
                        if h.get("beep", True) and self.beep and not corpse_ok:
                            try:
                                self.beep()
                            except Exception:  # noqa: BLE001
                                pass
                elif left <= 0:
                    return self.heal(obs, "战斗中血量低")
                else:                     # v8.33：只有真的在冷却才写“离上次治疗才…”（以前法力不够时也写，误导）
                    self._event("heal_skipped", f"血量 {hp:.0f}% 治疗冷却中")
                    self._warn("healcd", f"🩹 不治疗：血量 {hp:.0f}%（{self._hp_src(obs)}）< {line:.0f}%，"
                                         f"但离上次治疗才 {now - self.last_heal_time:.1f} 秒（间隔 {h['heal_cooldown_s']} 秒）", 2.0)
            elif hp < 40:
                self._event("heal_skipped", f"血量 {hp:.0f}% ≥ 治疗线 {line:.0f}%")
                self._warn("heal40", f"🩹 不治疗：血量 {hp:.0f}%（{self._hp_src(obs)}）还没低于治疗线 {line:.0f}%"
                                     "（觉得太晚可以在“技能循环”页调高“战斗中治疗线”）", 3.0)
            elif obs.in_combat and now - self.last_hp_dlog >= 3.0:
                self.last_hp_dlog = now
                self.dlog(f"[治疗判断] 战斗中血量 {hp:.0f}%（{self._hp_src(obs)}）≥ 治疗线 {line:.0f}% → 不需要")

        if self.state == State.SKINNING:
            return self._skin(obs, now)
        if self.state == State.BUFFING:
            return self._buff_step(obs, now)
        if self.state == State.RESTING:
            return self._rest(obs, now)
        if self.stationary:
            if self.state == State.RETURNING:
                return self._return(obs, now)       # v8.17：原地模式也能走回中心
            if self.state == State.COMBAT:
                return self._st_combat(obs, now, dead_pct)
            if self.state == State.APPROACHING:
                return self._st_wait(obs, now, dead_pct)
            return self._st_search(obs, now, dead_pct)
        if self.state == State.RETURNING:
            return self._return(obs, now)
        if self.state == State.COMBAT:
            return self._combat(obs, now, dead_pct)
        if self.state == State.APPROACHING:
            return self._approach(obs, now, dead_pct)
        return self._search(obs, now, dead_pct)

    # ------------------------------------------------------------ 找怪
    def _search(self, obs, now, dead_pct):
        k, s = self.cfg["keys"], self.cfg["safety"]
        n = self.cfg.get("nameplate", {})

        hit = bool(obs.in_combat) and self._being_hit(obs)
        self._log_plate_source(obs)
        if self.far is not None and not obs.in_combat and not hit and self._pull_mana_low(obs, now):
            self._far_end("法力低于开怪线，先回蓝", skip=False)     # v8.47
        if self.far is not None:
            if not obs.in_combat and not hit and self._ts_moving(obs, now, dead_pct, "朝远处走"):
                return                  # v8.40：移动中 Tab 选中合格的怪 → 已停下，下一帧开打
            if self.far is not None:
                if obs.in_combat or hit or (obs.target_exists and not self._ts_ignoring(now)):
                    self._far_end("进战斗/有目标了", skip=False)
                else:
                    return self._far_step(obs, now)
        if self._click_check(obs, now, dead_pct):
            return                      # v8.28：刚点了标记，等 verify_s 秒看选没选上
        if (self.ts_pend is not None or now < self.ts_ignore_until) and self._ts_search(obs, now, dead_pct):
            return                      # v8.40：刚按了 Tab → 检查选上的怪（不合格换下一个/清掉）
        if not obs.in_combat:
            self.atk_presses = 0
        if obs.target_kind in ("friendly", "tapped"):
            if obs.in_combat:
                return self._find_attacker(obs, now, "友方/被别人抢了，我在战斗中")
            self.log("目标是友方/被别人抢了，清掉")
            return self._clear_target()
        if obs.target_kind == "dead" and (obs.in_combat or hit):
            # v8.17：还在战斗中（多只怪）→ 不拾取不剥皮，先换正在打我的怪（尸体脱战后再捡）
            return self._combat_attacker(obs, now, hit, "目标死了但还在战斗中，换正在打我的怪（尸体脱战后再捡）")
        mr = self._need_mana_rest(obs, now, hit)     # v8.46：蓝不够、血也低 → 先回蓝，不拉新怪（有尸体先捡）
        if mr and not self.pending_loot and obs.target_kind != "dead":
            return self._start_mana_rest(obs, now)
        if not mr and self._engage_first(obs, now, dead_pct, hit, ""):
            return                      # v8.32：非战斗看到能打的怪 → 先打
        if self._pending_loot_step(obs, now, dead_pct, hit):
            return
        if obs.target_kind == "dead":
            if obs.lootable and not self._bag_skip_corpse():
                return self.loot(obs, count_kill=False)
            return self._clear_target()

        if self._need_rest(obs):
            return self.start_rest(obs, "找怪前血量不足")

        if not obs.in_combat and not hit and not self._alive(obs, dead_pct):
            if getattr(self, "spin_due", False) and self._spin_home(obs, now):
                return
            if now < getattr(self, "spin_wait_until", -1e9):
                return
        # v8.18：点击模式以前从不检查 buff（只有原地模式和回血结束时才补）→ 荆棘术/野性印记掉了就一直不补
        if not obs.in_combat and not hit and not self._alive(obs, dead_pct) and self._maybe_buff(obs, now):
            return

        if now >= self.return_block_until and self._outside_area(obs) \
                and not (self.travel and self._alive(obs, dead_pct) and obs.target_kind not in ("friendly", "tapped")) \
                and not self._ts_keep(obs, now, dead_pct):          # v8.40：Tab 选中的怪在追怪上限内 → 先打
            if self.travel and now < self.travel_look_t and not obs.target_exists \
                    and self._clickable(obs, now) and self.click_fails < 2:
                pass                # v8.23：巡逻路上刚看到姓名板 → 先点它（点不中 2 次再接着走）
            else:
                if obs.target_exists:
                    self._clear_target()
                return self.start_return(obs)

        if self._alive(obs, dead_pct):
            if self.atk_presses:
                self.log(f"🎯 按“选择攻击我的怪键”后选中目标（血量 {obs.target_hp:.0f}%，打我={obs.aggro}）")
                self.atk_presses = 0
            self.click_fails = 0
            self.scan_turns = 0
            self._spin_reset()
            self.no_target_since = None
            self.halt_t = None
            hit_click = now - self.click_hit_t < 1.5      # v8.28：刚右键点中的（角色已经在走过去）
            if not hit_click and self._turn_toward(obs):
                return
            self._reset_target_tracking(now)
            self.last_target_hp = obs.target_hp
            self.last_combat_time = now
            if not hit_click and self._wf_want(obs, hit):
                self.last_target_plate = obs.selected_plate
                return self._wf_start(obs, now)      # v8.46：怪远 → 先走过去，不按开怪键、不右键
            if self.cfg.get("move", {}).get("opener_first"):
                self.inp.press(k["attack"])
                self.opened = True
            if hit_click:
                self.click_hit_t = -1e9
                self.log(f"选中目标（血量 {obs.target_hp:.0f}%），右键点中了，角色走过去 → 接近途中不再点姓名板（出问题才再点）")
                if obs.pos is not None:
                    self.rclick_pos = obs.pos
            else:
                self.log(f"选中目标（血量 {obs.target_hp:.0f}%），右键走过去开打")
                self._engage(obs)
            self.last_target_plate = obs.selected_plate
            if obs.in_combat and obs.range == "melee":
                self._enter_combat(now)
            else:
                self.state = State.APPROACHING
                self.approach_start = now
            return

        if obs.in_combat:  # 被打了但没目标 → v8.17：先按“选择攻击我的怪键”（x），再 Tab；战斗中不点姓名板
            return self._combat_attacker(obs, now, hit, "战斗中没目标，选正在打我的怪")

        self._note_no_target(now)     # v8.5：“最长找不到怪/最长不进战斗”不再停止，只记日志，继续找

        if self._ts_search(obs, now, dead_pct):
            return                      # v8.40：每次找怪先按 Tab；没选到/不合格 → 下面原来的流程（屏幕标记 → 远处）
        plates = self._clickable(obs, now)     # v8.28：最上面一条（远处）、点空过的位置不点
        if plates and now < self.skip_until and self.skip_pos is not None:   # 刚放弃的怪附近的姓名板先不点
            sx, sy = self.skip_pos
            far = [p for p in plates if math.hypot(p["x"] + p["w"] / 2 - sx, p["y"] + p["h"] / 2 - sy) > 80]
            if not far:
                self.log("只看到刚放弃的那只怪，转一下视角")
                return self._scan_turn()
            plates = far
        method = n.get("select_method", "click")
        if plates and method == "click":
            self.scan_turns = 0
            if self.click_fails < int(n.get("max_click_fails", 3)):
                return self._click_plates(obs, now, plates)
            self.click_fails = 0
            if n.get("tab_fallback"):
                return self.inp.press(k["target"])
            self.log("点了几次都没选上，转一下视角")
            return self._scan_turn()

        if method == "tab":
            return self.inp.press(k["target"])

        # v8.28：只有远处的红名/最上面一条的标记 → 转过去朝它走（不按互动键）
        if method == "click" and self._far_begin(obs, now):
            return
        # 屏幕里一个姓名板都没有 → 转视角找
        sc = self.cfg.get("scan", {})
        self.scan_turns += 1
        self._note_scan_turns()
        self._scan_turn()

    def _click_plates(self, obs, now, plates):
        """v8.28 点姓名板标记：先停稳 → 右键间隔 → 点前确认 → 右键点 → 记下等 verify_s 秒确认。"""
        n = self.cfg.get("nameplate", {})
        if self._halt_first(obs, now):
            return              # v8.28：先停下，停稳再点
        if str(n.get("click_button", "right")).lower() == "right" and self._rclick_wait(now):
            return              # v8.24：离上次右键不到 3 秒，先等
        fw, fh = obs.frame_size
        p = self._confirm_plate(nearest_to(plates, fw / 2, fh / 2), now)
        if p is None:
            return              # v8.28：点前确认没通过，这次不点
        self._ctx("找怪：右键点姓名板标记")
        self._click_plate(p)
        self.click_pending = (p["x"] + p["w"] / 2, p["y"] + p["h"] / 2, self.clock())   # 从点完算起
        self.log(f"🖱️ 右键点{'插件标记' if p.get('from_marker') else '姓名板（模板）'}选怪 "
                 f"({p['x'] + p['w'] / 2:.0f},{p['y'] + p['h'] / 2:.0f})，屏幕上能点的 {len(plates)} 个")

    # ------------------------------------------------------------ v8.32 非战斗看到怪就打
    def _engage_first(self, obs, now, dead_pct, hit, why) -> bool:
        """v8.32：不在战斗中、没选中活怪，连续 patrol_frames 帧看到能打的怪的插件标记（屏幕最上面一条不算）
        → 先打它（停下 → 右键点），本来要做的（why：拾取尸体/补 buff/等一会/回中心）打完再做。
        例外（不打断）：回血回蓝没到线（_need_rest）、拾取/剥皮/补 buff/回血读条中（这些状态根本不会走到这里）、
        跑尸和复活后回血、在范围外（走回中心途中）。返回 True = 这一帧在点怪。"""
        n = self.cfg.get("nameplate", {})
        if not n.get("engage_any", True) or self.stationary or n.get("select_method", "click") != "click":
            return False
        if obs.in_combat or hit or self._alive(obs, dead_pct) or obs.casting:
            self.eng_seen = 0
            return False
        if getattr(obs, "plate_source", "template") != "marker" or obs.source != "addon":
            return False                       # 只信插件标记（模板会点到地面）
        if self.click_fails >= int(n.get("max_click_fails", 3)):
            return False
        plates = self._clickable(obs, now)
        if plates and now < self.skip_until and self.skip_pos is not None:
            sx, sy = self.skip_pos
            plates = [p for p in plates if math.hypot(p["x"] + p["w"] / 2 - sx, p["y"] + p["h"] / 2 - sy) > 80]
        if not plates:
            self.eng_seen = 0
            return False
        self.eng_seen += 1
        if self._need_rest(obs, now):
            return False                       # 例外：血/蓝没回到线，先回
        if self._outside_area(obs) and not self.travel:
            return False                       # 范围外（要走回中心）：不在外面拉怪
        plain = (obs.target_kind == "none" and not self.pending_loot and not getattr(self, "spin_due", False)
                 and now >= getattr(self, "spin_wait_until", -1e9))
        if plain and self._ts_ready(obs, now):
            self.eng_seen = 0
            return False                       # v8.40：本来就在找怪 → 先按 Tab（_search 里），Tab 不行再点标记
        need = 1 if plain else int(n.get("patrol_frames", 2))     # 本来就在找怪：跟以前一样第 1 帧就点
        if self.halt_t is None and self.eng_seen < need:
            return True                        # 要放下别的事去打：再看 1 帧确认（这一帧先不拾取/不走）
        if plain:
            why = ""
        elif obs.target_kind == "dead":
            if obs.lootable and self._loot_on() and not self._bag_skip_corpse():
                self.pending_loot = min(self.pending_loot + 1, 5)
                self.pending_loot_t = now
                why = f"选中的尸体记下（共 {self.pending_loot} 具），"
            else:
                why = "选中的尸体（没东西捡）"
        elif self.pending_loot:
            why = f"还有 {self.pending_loot} 具尸体没捡，"
        else:
            why = "转满一圈要回中心/在中心等，"
        if why and now - self.eng_log_t >= 5.0:
            self.eng_log_t = now
            self.log(f"⚔️ 非战斗看到 {len(plates)} 个能打的怪 → 先打（{why.rstrip('，')}，打完再做）")
        self.scan_turns = 0
        self._click_plates(obs, now, plates)
        return True

    # ------------------------------------------------------------ 原地模式（stationary_tab）
    def _st(self):
        return self.cfg.get("stationary", {})

    @staticmethod
    def _in_range(obs):
        """True=普通攻击够得到 / False=够不到 / None=不知道。"""
        if obs.attack_range == "in":
            return True
        if obs.attack_range == "out":
            return False
        if obs.range == "melee":
            return True
        if obs.range in ("ranged", "far"):
            return False
        return None

    def _pullable(self, obs) -> bool:
        """v8.1：目标在约 30 码内（插件距离块=黄）→ 可以按开怪键把它拉过来。
        人形下熊的近战技能判断不了距离，近战范围的怪也显示成“30 码内”，所以不能当成“够不到”。"""
        return bool(self._st().get("pull_ranged", True)) and obs.range == "ranged"

    @staticmethod
    def _aggro(obs) -> bool:
        return bool(getattr(obs, "aggro", False))

    def _press_attack(self, now):
        k = self.cfg["keys"].get("attack")
        if k:
            self._ctx("开普通攻击/开怪键")
            self.inp.press(k)
            self.last_attack_press = now

    # ------------------------------------------------------------ v8.10 随机小动作 / 随机休息
    def _hz(self):
        return self.cfg.get("humanize", {}) or {}

    def _break_clock(self, obs, now):
        """休息中进了战斗/在回血拾取 → 倒计时暂停（打完接着休息剩下的时间）。"""
        last, self.hz_last_t = self.hz_last_t, now
        if self.break_until is not None and last is not None:
            if obs.in_combat or self.state != State.SEARCHING:
                self.break_until += now - last

    def break_remaining(self, now=None):
        if self.break_until is None:
            return None
        now = self.clock() if now is None else now
        return max(0.0, self.break_until - now)

    def status_label(self):
        if getattr(self, "rc_active", False):
            return self.reconnect_label()
        rem = self.break_remaining()
        bag = "（🎒背包满：只打怪）" if self.bag_full else ""
        if self.cr is not None:
            return f"👻 {self.corpse_label()}（第 {self.deaths} 次死亡）" + bag
        if rem is None:
            return self.state.value + bag
        m, s = divmod(int(rem), 60)
        return f"随机休息 还剩 {m}:{s:02d}" + ("" if self.state == State.SEARCHING else f"（{self.state.value}）") + bag

    def _humanize(self, obs, now) -> bool:
        """脱战、两只怪之间调用。返回 True = 这一帧不找怪。"""
        if self.state == State.RETURNING:      # v8.17：走回中心时不做随机小动作/休息
            return False
        hz = self._hz()
        if hz.get("break_enabled", True):
            if self.break_until is not None:
                if now < self.break_until:
                    return True
                self.break_until = None
                self._schedule_break(now)
                self.no_target_since = None
                self.tabs = self.tab_rounds = 0
                self.log("▶️ 随机休息结束，继续打怪")
                return False
            if self.next_break_at is None:
                self._schedule_break(now)
            elif now >= self.next_break_at:
                dur = self.rng.uniform(float(hz.get("break_min_min", 2.0)), float(hz.get("break_max_min", 3.0))) * 60
                self.break_until = now + dur
                self.next_break_at = None
                self.log(f"☕ 随机休息 {dur / 60:.1f} 分钟（不按 Tab、不动；被怪打会照常战斗，打完接着休息）")
                return True
        if hz.get("move_enabled", True) and self.cfg["move"].get("mode") == "stationary_tab":
            if self.move_next_kills is None:
                self._schedule_move(now)
            elif not getattr(obs, "casting", False) and (
                    self.kills - self.move_kills0 >= self.move_next_kills
                    or now - max(self.move_last_t, self.last_kill_t) >= self.move_idle_s):
                self._small_move(now)
                return True
        return False

    def _schedule_break(self, now):
        hz = self._hz()
        mins = self.rng.uniform(float(hz.get("hunt_min_min", 12)), float(hz.get("hunt_max_min", 18)))
        self.next_break_at = now + mins * 60
        self.log(f"☕ 下次随机休息在 {mins:.1f} 分钟后")

    def _schedule_move(self, now):
        hz = self._hz()
        self.move_kills0 = self.kills
        self.move_last_t = now
        self.move_next_kills = self.rng.randint(int(hz.get("move_kills_min", 1)), max(int(hz.get("move_kills_min", 1)),
                                                                                   int(hz.get("move_kills_max", 4))))
        self.move_idle_s = self.rng.uniform(float(hz.get("move_idle_min_s", 30)), float(hz.get("move_idle_max_s", 120)))

    def _small_move(self, now):
        hz, k = self._hz(), self.cfg["keys"]
        fwd, back = k.get("forward") or "w", k.get("back") or "s"
        ms = self.rng.uniform(float(hz.get("move_min_ms", 150)), float(hz.get("move_max_ms", 400)))
        back_ms = ms * float(hz.get("back_ratio", 1.55))
        first_fwd = self.rng.random() < 0.5
        seq = [(fwd, ms), (back, back_ms)] if first_fwd else [(back, back_ms), (fwd, ms)]
        why = (f"杀了 {self.kills - self.move_kills0} 只" if self.kills - self.move_kills0 >= self.move_next_kills
               else f"{now - max(self.move_last_t, self.last_kill_t):.0f} 秒没动")
        self.log(f"🚶 随机小动作（{why}）：" + " → ".join(f"{'前进' if key == fwd else '后退'} {h:.0f}ms" for key, h in seq)
                 + "，回到原位")
        for key, h in seq:
            self._ctx("随机小动作")
            self.inp.press(key, hold_ms=h, delay=False)
            self.inp.sleep(self.rng.uniform(0.08, 0.2))
        self._schedule_move(self.clock())

    def _find_attacker(self, obs, now, why):
        """v8.9：战斗中/挨打且没有能打的目标 → 先按“选择攻击我的怪键”（游戏里的宏：/cleartarget + /targetenemy，
        清掉目标后选最近的敌人，几乎总是贴身打我的那只），最多按 attacker_max_presses 次，还不行再 Tab。"""
        key = (self.cfg["keys"].get("target_attacker") or "").strip().lower()
        st = self._st()
        if not key or self.atk_presses >= int(st.get("attacker_max_presses", 2)):
            return self._tab(now, why)
        if now - self.last_atk_press < float(st.get("attacker_interval_s", 0.8)):
            return
        self.atk_presses += 1
        self.last_atk_press = now
        self.log(f"🎯 {why} → 按选择攻击我的怪键「{key}」（第 {self.atk_presses} 次）| 插件：战斗={obs.in_combat} "
                 f"目标={obs.target_kind} 血 {obs.target_hp:.0f}% 打我={obs.aggro} 距离={obs.range} 掉血={self.hp_drop:.0f}%")
        self._ctx(f"选择攻击我的怪（{why}）")
        self.inp.press(key)

    def _combat_attacker(self, obs, now, hit, why):
        """v8.17：战斗中没目标。真的在挨打 → 马上按 x/Tab；刚杀完怪、战斗状态还在但没掉血 → 先等几秒
        （多半是残留的战斗状态，按 x 会去拉一只没打我的新怪，尸体就捡不了了）。"""
        g = float(self.cfg["loot"].get("residual_wait_s", 6.0))
        if not hit and now - self.last_kill_t < g:
            self._warn("residual", f"⏳ 刚杀完怪，战斗状态还在但没掉血 → 最多等 {g:.0f} 秒（掉血就马上找打我的怪；脱战就拾取）", 5.0)
            return
        return self._find_attacker(obs, now, why)

    def _tab(self, now, why=""):
        """按 Tab 换目标；连续 max_tabs 次没找到合适的 → 随机等 2-4 秒（可选原地转一点再找）。"""
        st, k = self._st(), self.cfg["keys"]
        if now < self.tab_pause_until:
            return
        if now - self.last_tab < float(st.get("tab_interval_s", 0.6)):
            return
        if self.tabs >= int(st.get("max_tabs", 6)):
            self.tabs = 0
            self.tab_rounds += 1
            pause = self.rng.uniform(float(st.get("pause_min_s", 2.0)), float(st.get("pause_max_s", 4.0)))
            self.tab_pause_until = now + pause
            self.atk_presses = 0
            self.log(f"按了 {st.get('max_tabs', 6)} 次 Tab 没找到能打的怪，等 {pause:.1f} 秒再找")
            if st.get("turn_search", True) and self.tab_rounds >= int(st.get("turn_after_rounds", 1)):
                sc = self.cfg.get("scan", {})
                self.scan_turns += 1
                self._note_scan_turns()
                self._scan_turn()
            return
        self._ctx(f"Tab 选怪（{why or '找怪'}，第 {self.tabs + 1} 次）")
        self.inp.press(k.get("target") or "tab")
        self.tabs += 1
        self.last_tab = now
        if why and self.tabs == 1:
            self.log(f"Tab 选怪（{why}）")

    def _pending_loot_step(self, obs, now, dead_pct, hit) -> bool:
        """v8.4/8.7/8.17（两种模式共用）：战斗中杀的怪记在 pending_loot；顺序 = 先回血 → 补 buff → 拾取/剥皮 → 变回战斗形态。
        返回 True = 这一帧做了事。"""
        if self.pending_loot and self._need_rest(obs, now):         # v8.7：先回血，再捡尸体
            self.log(f"🩹 顺序：先回血 → 补 buff（还是人形）→ 拾取/剥皮 {self.pending_loot} 具 → 变回{self._cform_cn()}")
            self.start_rest(obs, "战斗结束，先回血再拾取")
            return True
        if self.pending_loot and not obs.in_combat and not hit:      # v8.4：脱战了 → 一具一具捡
            expire = float(self.cfg["loot"].get("pending_expire_s", 180))
            if now - max(self.pending_loot_t, self.rest_end_t) > expire:
                self.log(f"💰 有 {self.pending_loot} 具尸体没捡，但已经过了 {expire:.0f} 秒（尸体可能没了），不捡了")
                self.pending_loot = 0
            elif self._alive(obs, dead_pct) and not self._aggro(obs):
                self.log("💰 脱战了，要捡尸体，先清掉选中的活怪（免得交互键去打它）")
                self._clear_target(obs)
                return True
            elif not self._alive(obs, dead_pct):
                if not self._need_rest(obs, now) and self._maybe_buff(obs, now, why="脱战，捡尸体前"):
                    return True
                self.pending_loot -= 1
                self.log(f"💰 脱战了（插件战斗块=否）→ 拾取/剥皮尸体（还剩 {self.pending_loot} 具没捡）")
                self.loot(obs, count_kill=False)
                return True
        if self.caster_hold and (not self.pending_loot or obs.in_combat):
            self.caster_hold = False
            if not self._is_bear(obs):
                self.log(f"🐾 " + ("尸体都捡完了" if not self.pending_loot else "又进战斗了") + f" → 变回{self._cform_cn()}")
                self._to_bear(obs)
                return True
        return False

    def _st_search(self, obs, now, dead_pct):
        s = self.cfg["safety"]
        hit = bool(obs.in_combat) and self._being_hit(obs)
        if hit and not self._is_bear(obs) and obs.form != "unknown":
            # v8.8：在挨打却还是人形（例如回血/拾取时）→ 先变熊（1 秒内只按一次，熊键=取消键时多按会又退出熊）
            self.caster_hold = False
            if now - self.heal_last_shift >= 1.0:
                self.heal_last_shift = now
                self.log(f"⚔️ 正在挨打（掉血 {self.hp_drop:.0f}%，打我={obs.aggro}）但还是{obs.form}形态 → 先变{self._cform_cn()}")
                return self._to_bear(obs)
        if obs.target_kind == "dead" and hit:
            return self._find_attacker(obs, now, "被打了，先打怪（尸体脱战后再捡）")
        if self._pending_loot_step(obs, now, dead_pct, hit):
            return
        if obs.target_kind == "dead" and obs.lootable and not obs.in_combat \
                and not self._bag_skip_corpse():   # v8.4：只在脱战后捡；v8.29：放弃的尸体/背包满不再捡
            return self.loot(obs, count_kill=False)
        if not obs.in_combat:
            self.atk_presses = 0
        if not obs.in_combat and not hit and not self.pending_loot and not self._alive(obs, dead_pct):
            if getattr(self, "spin_due", False) and self._spin_home(obs, now):
                return
            if now < getattr(self, "spin_wait_until", -1e9):
                return
        if not obs.in_combat and not hit and not self.pending_loot and now >= self.return_block_until \
                and self._outside_area(obs) and not (self.travel and self._alive(obs, dead_pct) and obs.target_kind not in ("friendly", "tapped")
                         and obs.range != "far"):
            # v8.17：原地模式也管范围：脱战、尸体捡完、离中心超过半径 → 走回中心（随机小动作暂停）
            # v8.23：在中心之间走、选中了能打的怪 → 先打（上面的条件）
            if self._alive(obs, dead_pct) and not self._aggro(obs):
                self._clear_target(obs)
            return self.start_return(obs)
        if self._need_rest(obs, now):            # v8：先回血再 Tab（以前死目标直接 Tab，回血被跳过）
            return self.start_rest(obs, "找怪前血量不足")
        if not obs.in_combat and not self._alive(obs, dead_pct) and self._maybe_buff(obs, now):
            return
        if obs.player_hp < self._rest_below() and obs.in_combat:
            if obs.player_hp < 40:
                self._event("heal_skipped", f"血量 {obs.player_hp:.0f}% 战斗中不回血")
            self._warn("restskip", f"🩹 不回血：血量 {obs.player_hp:.0f}% 但正在战斗中"
                                   f"（{'在掉血/怪在打我' if self._being_hit(obs) else '不是刚杀完怪（' + str(self.cfg['heal'].get('post_fight_grace_s', 8)) + ' 秒内）'}），先打", 5.0)
        if not obs.in_combat and not hit and self._humanize(obs, now):
            return
        if obs.target_kind == "dead":
            return self._pick_target(obs, now, "目标已死")
        if obs.target_kind in ("friendly", "tapped"):
            if obs.in_combat:
                return self._find_attacker(obs, now, "友方/被别人抢了，我在战斗中")
            return self._pick_target(obs, now, "友方/被别人抢了")
        if self._alive(obs, dead_pct):
            inr = self._in_range(obs)
            pull = self._pullable(obs)
            if inr is False and not pull and not (obs.in_combat and self._aggro(obs)):
                if obs.range == "far" and self._clear_far(obs, now):
                    return
                if hit:
                    return self._find_attacker(obs, now, "目标" + ("太远" if obs.range == "far" else "够不到") + "，我在挨打")
                return self._pick_target(obs, now, "目标太远（30 码外）" if obs.range == "far" else "目标够不到")
            if self.atk_presses:
                self.log(f"🎯 按“选择攻击我的怪键”后选中目标（血量 {obs.target_hp:.0f}%，打我={obs.aggro}，"
                         f"距离={obs.range}，类型={obs.target_kind}）")
                self.atk_presses = 0
            if self.plate_pending is not None:
                self.dlog(f"[姓名板] 点击选中了目标（血量 {obs.target_hp:.0f}%）")
                self.plate_pending = None
                self.plate_fails = 0
            self.tabs = self.tab_rounds = self.scan_turns = 0
            self._spin_reset()
            self.no_target_since = None
            self._reset_target_tracking(now)
            self.last_target_hp = obs.target_hp
            self.last_combat_time = now
            self._open_attack(obs, now)
            self.pull_presses = 1
            if inr is True or obs.in_combat:
                self.log(f"选中目标（血量 {obs.target_hp:.0f}%），开打")
                self._enter_combat(now)
            else:
                self.pulling = pull or inr is None
                self.log(f"选中目标（血量 {obs.target_hp:.0f}%），"
                         + ("在 30 码内，按开怪键拉怪，原地等它过来" if pull else "判断不了距离，按开怪键，等它过来"))
                self.state = State.APPROACHING
                self.approach_start = now
            return
        if obs.in_combat:
            return self._combat_attacker(obs, now, hit, "被打了，选正在打我的怪")
        self._note_no_target(now)     # v8.5：“最长找不到怪/最长不进战斗”不再停止，只记日志，继续找
        self._pick_target(obs, now, "找怪")

    def _note_no_target(self, now):
        """v8.5：删掉了“最长找不到怪 / 最长不进战斗 → 停止”。现在只每 60 秒记一条日志，继续 Tab/点姓名板/转视角找怪。"""
        if self.no_target_since is None:
            self.no_target_since = now
            self.no_target_logged = 0
        n = int((now - self.no_target_since) // 60)
        if n > getattr(self, "no_target_logged", 0):
            self.no_target_logged = n
            idle = now - self.last_combat_time
            self.log(f"ℹ️ 已经 {now - self.no_target_since:.0f} 秒没找到能打的怪（{idle:.0f} 秒没进战斗），继续找（不会自动停止）")

    # ---- v8.3：没目标时左键点姓名板
    def _has_plate_templates(self, now) -> bool:
        t, ok = self.plate_tpl_cache
        if now - t > 10.0:
            from .config import list_nameplate_templates
            ok = bool(list_nameplate_templates("unselected"))
            self.plate_tpl_cache = (now, ok)
        return ok

    def _plate_ok(self, p, fw, fh, now) -> bool:
        st = self._st()
        cx, cy = p["x"] + p["w"] / 2, p["y"] + p["h"] / 2
        mx, my = (st.get("plate_ui_margin") or [320, 140])[:2]
        if cx < mx and cy < my:                       # 左上角头像/插件色块
            return False
        if cy > fh * 0.85 or cy < self._top_limit(fh):  # 底部动作条 / v8.28 最上面一条（远处）
            return False
        for bx, by, bt in self.plate_bad:
            if now - bt < 8.0 and math.hypot(cx - bx, cy - by) < 60:
                return False
        return True

    # ---- v8.14 选怪统一入口：点姓名板和 Tab 选到的目标走同一套流程
    def _pick_target(self, obs, now, why):
        """换/选下一个目标。脱战时先点“未选中姓名板”，点不了（没模板/屏幕上没有/连续点不中/等待中）再 Tab。
        选中以后不管是点的还是 Tab 的，都由 _st_search 同一段代码判断（死/友方/被抢/太远/拉怪/开打）。"""
        if self.plate_pending is not None and obs.target_exists and obs.target_kind != "none":
            px, py, pt = self.plate_pending
            if now - pt < float(self._st().get("plate_select_wait_s", 0.7)):
                return                                # 刚点，等目标切换过来
            self.plate_pending = None
            self._bad_from_click(px, py, now)
            self.log(f"[姓名板] 点了 ({px:.0f},{py:.0f}) 后目标不能打（{why}，类型={obs.target_kind} 距离={obs.range}）"
                     "→ 8 秒内不再点它，换一个")
        if not obs.in_combat and self._st_plate_click(obs, now, why):
            return
        return self._tab(now, why)

    def _bad_from_click(self, px, py, now):
        """点击坐标（屏幕坐标）→ 截图坐标，记为 8 秒内不点。"""
        oy = float(self.cfg.get("nameplate", {}).get("click_offset_y", 0))
        self.plate_bad.append((px - self.screen_offset[0], py - oy - self.screen_offset[1], now))

    def _mark_target_plate_bad(self, obs, now):
        """放弃当前目标时：它的姓名板（标记下方）8 秒内不点。"""
        m = getattr(obs, "marker", None)
        if m:
            self.plate_bad.append((m[0], m[1] + 15, now))

    def _st_plate_click(self, obs, now, why="找怪") -> bool:
        """没目标：点最靠近屏幕中间的“未选中姓名板”。返回 True = 这一 tick 已处理（点了或在等结果）。"""
        st = self._st()
        if not st.get("plate_click", True):
            return False
        if getattr(obs, "plate_source", "template") != "marker" and not self._has_plate_templates(now):
            self._warn("noplatetpl", "ℹ️ 没录“未选中姓名板”模板 → 只用 Tab 选怪（在“姓名板”页点“截取”录一张）", 3600)
            return False
        interval = float(st.get("plate_click_interval_s", 0.8))
        if now < self.tab_pause_until:
            return False                              # v8.14：跟 Tab 一样，一轮没找到 → 一起等
        if self.plate_pending is not None:
            px, py, pt = self.plate_pending
            if now - pt < float(st.get("plate_select_wait_s", 0.7)):
                return True                           # 等目标出现（v8.14：0.35 → 0.7 秒，日志里有点中了但 0.35 秒内还没显示的）
            self.plate_pending = None
            self.plate_fails += 1
            self._bad_from_click(px, py, now)
            self.dlog(f"[姓名板] 点了 ({px:.0f},{py:.0f}) 没选上目标（连续第 {self.plate_fails} 次）")
        self.plate_bad = [b for b in self.plate_bad if now - b[2] < 8.0]
        if self.plate_fails >= int(st.get("plate_max_fails", 3)):
            self.plate_fails = 0
            self.log(f"连续点了 {st.get('plate_max_fails', 3)} 次姓名板都没选上，这次改按 Tab")
            return False
        if self.tabs >= int(st.get("max_tabs", 6)):
            return False                              # v8.14：这一轮次数用完 → 交给 _tab 统一等待/转视角
        fw, fh = obs.frame_size
        plates = [p for p in obs.nameplates if p.get("kind") == "unselected" and self._plate_ok(p, fw, fh, now)]
        if not plates:
            return False
        if now - self.plate_last < interval:
            return True                               # 有姓名板，等点击间隔，不按 Tab
        p = nearest_to(plates, fw / 2, fh / 2)
        x = p["x"] + p["w"] / 2 + self.screen_offset[0]
        y = p["y"] + p["h"] / 2 + float(self.cfg.get("nameplate", {}).get("click_offset_y", 0)) + self.screen_offset[1]
        btn = st.get("plate_click_button", "left")
        self._ctx("点姓名板选怪")
        self.inp.click_at(x, y, button=btn, restore=bool(st.get("plate_restore_mouse", True)))
        self.plate_pending = (x, y, now)
        self.plate_last = now
        self.tabs += 1        # v8.14：点姓名板也算一次找怪（跟 Tab 共用“连续 N 次没找到 → 等一会儿/转视角”）
        self.log(f"🖱️ {'左' if btn == 'left' else '右'}键点姓名板选怪（{why}，第 {self.tabs} 次）({x:.0f},{y:.0f})，"
                 f"匹配度 {p.get('score', 0):.2f}（屏幕上 {len(plates)} 个）")
        return True

    def _clear_far(self, obs, now) -> bool:
        """v8.3：目标在 30 码外 → 按清除目标键。返回 True = 已按（或刚按过在等）。"""
        st, key = self._st(), (self.cfg["keys"].get("clear_target") or "").strip()
        if not st.get("clear_far", True) or not key:
            return False
        if now - self.last_clear_far < 0.8:
            return True
        if key.lower() == "esc":
            self._warn("escclear", "⚠️ 清除目标键是 Esc：有时会打开游戏菜单。建议做 /cleartarget 宏放到 F3，把“清除目标键”改成 f3", 600)
        self.last_clear_far = now
        if self.plate_pending is not None:            # 刚点的姓名板是只远怪 → 8 秒内别再点它
            self._bad_from_click(self.plate_pending[0], self.plate_pending[1], now)
            self.plate_pending = None
        self.tabs += 1        # 算进这一轮找怪次数，避免 Tab→太远→清除 无限循环
        self.log(f"目标太远（30 码外，插件距离块红）→ 按清除目标键「{key}」")
        self._clear_target(obs)
        return True

    def _st_wait(self, obs, now, dead_pct):
        """选中了但不知道够不够得到：原地等怪过来（被打/进范围/掉血 → 战斗）。"""
        if not self._alive(obs, dead_pct):
            self.target_gone_ticks += 1
            if self.target_gone_ticks >= 2:
                if obs.target_kind == "dead":
                    return self.fight_over(obs)
                self.state = State.SEARCHING
            return
        self.target_gone_ticks = 0
        dropped = obs.target_hp < self.last_target_hp - 0.5
        if obs.in_combat or self._in_range(obs) is True or dropped:
            self.log("进入战斗")
            self._enter_combat(now)
            if dropped:
                self.last_damage_t = now
            return self._st_combat(obs, now, dead_pct)
        st = self._st()
        if obs.error == "form" and not self._warned.get("pullform"):
            self._warn("pullform", f"⚠️ 开怪键「{self.cfg['keys'].get('attack')}」报“形态不对”（例如熊形态下放月火术）。"
                                   "请把开怪键换成当前形态能放的技能", 60)
        far = obs.range == "far" or (self._in_range(obs) is False and not self._pullable(obs))
        limit = float(st.get("pull_wait_s", 8.0)) if self.pulling else float(st.get("wait_in_range_s", 5.0))
        if far or now - self.approach_start > limit:
            self.log("目标太远，换一个" if far else f"按了开怪键 {limit:.0f} 秒怪还没过来，换一个")
            self.state = State.SEARCHING
            if obs.range == "far" and self._clear_far(obs, now):
                return
            self._mark_target_plate_bad(obs, now)
            return self._pick_target(obs, now, "拉的怪没过来，换一个")
        if self.pull_presses < int(st.get("pull_max_presses", 2)) and now - self.last_attack_press >= float(st.get("pull_retry_s", 2.5)):
            self.pull_presses += 1
            self.dlog(f"[拉怪] {now - self.approach_start:.1f} 秒还没进战斗，再按一次开怪键")
            self._press_attack(now)

    def _st_combat(self, obs, now, dead_pct):
        s, st = self.cfg["safety"], self._st()
        if not self._alive(obs, dead_pct):
            if obs.in_combat and obs.target_kind == "none" and not self.seen_alive:
                self.state = State.SEARCHING
                return self._tab(now, "被打了")
            self.target_gone_ticks += 1
            if self.target_gone_ticks < 2 and obs.target_kind != "dead":
                return
            if self.seen_alive or obs.target_kind == "dead":
                return self.fight_over(obs)
            self.state = State.SEARCHING
            return
        self.target_gone_ticks = 0
        self.seen_alive = True
        if obs.target_hp < self.last_target_hp - 0.5:
            self.last_target_hp = obs.target_hp
            self.last_drop_time = now
            self.last_damage_t = now
        elif obs.target_hp > self.last_target_hp + 5:
            self.last_target_hp = obs.target_hp
            self.last_drop_time = now
        if obs.since_damage is not None and not math.isinf(obs.since_damage):
            t = now - obs.since_damage
            if t >= self.combat_start - 0.5 and (self.last_damage_t is None or t > self.last_damage_t):
                self.last_damage_t = t
        stalled = now - self.last_drop_time
        if stalled > float(s["stuck_timeout_s"]) and self._stall_stop(now):
            return
        if obs.form not in ("unknown", self._cform()):
            return self._to_bear(obs)
        if obs.autoattack is False and now - self.last_attack_press >= 2.0:
            return self._press_attack(now)

        self._update_facing_errors(obs, now)
        self._fclick_quiet(now)
        # v8.13：转身后 N 秒没有新的面向错误 → 面向修好了，计数清零（不再看“怪掉血”：荆棘术反伤也会掉血）
        quiet = float(self._st().get("face_quiet_s", 5.0))
        if self.face_last is not None and now - self.face_last >= quiet and self.last_facing_err_t <= self.face_last + 0.2:
            self.dlog(f"[面向] 转身后 {now - self.face_last:.1f} 秒没有新的面向错误 → 面向正常，计数清零")
            if self.last_turn_sign:
                self.face_good_sign = self.last_turn_sign
            self._face_reset()
        inr = self._in_range(obs)
        facing = self._facing_problem(obs, now)
        if inr is False and not facing:
            waited = now - (self.last_damage_t or self.combat_start)
            if not self._aggro(obs) or waited > float(st.get("wait_in_range_s", 5.0)):
                self.log("目标够不到" + ("且没在打我" if not self._aggro(obs) else "，等太久") + "，Tab 换正在打我的怪")
                self.last_drop_time = now
                self._reset_recovery(now)
                self.last_tab = -1e9
                self.tabs = 0
                self.state = State.SEARCHING
                self._mark_target_plate_bad(obs, now)
                return self._pick_target(obs, now, "目标够不到")
            return self._rotate(obs)   # 怪在打我、正在走过来：先放技能等它
        nd_s = max(4.0, float(st.get("no_damage_s", 4.5)))
        b = self._target_bearing(obs)
        off = b is not None and (b[1] in ("side_edge", "behind") or abs(b[0]) > float(st.get("face_offcenter_deg", 25)))
        if off:      # v8.6：标记明显偏一边/贴边 → 2.5 秒没伤害就转（不用等 4.5 秒）
            nd_s = float(st.get("no_damage_offcenter_s", 2.5))
        first_s = nd_s if off else max(nd_s, float(self.cfg["recover"].get("first_damage_s", 4.0)))
        no_dmg = (self.last_damage_t is None and now - self.combat_start > first_s) \
            or (self.last_damage_t is not None and now - self.last_damage_t > nd_s)
        self.face_need = bool(facing or no_dmg)
        if self._face_fix(obs, now, facing, no_dmg):
            return
        self._rotate(obs)

    # ---- v8 面向
    def _face_reset(self):
        self.face_attempts, self.face_last = 0, None
        self.face_clear_t = self.clock()
        self.sweep_i, self.sweep_off = 0, 0.0
        self.face_small = 0
        self.face_backed = False       # v8.25 近战面向错误：后退半步（每轮一次）
        self.face_melee_turns = 0
        self.melee_backs, self.melee_cycle, self.melee_act_t = 0, 0, None   # v8.26 贴脸标记在中间：后退/右键循环

    def _update_facing_errors(self, obs, now):
        """面向错误计数：v0.7 插件的计数器（0-7 循环）有变化 = 新报错；旧插件用 1.5 秒灯的上升沿。"""
        fc = getattr(obs, "facing_count", None)
        n = 0
        if fc is not None:
            if self.fc_last is not None and fc != self.fc_last:
                n = (fc - self.fc_last) % 8
            self.fc_last = fc
        else:
            cur = bool(obs.err_facing or obs.error == "facing")
            if cur and not self.prev_err_facing:
                n = 1
            self.prev_err_facing = cur
        if n:
            self.facing_errs += n
            self.last_facing_err_t = now
            self.face_err_times = [t for t in getattr(self, "face_err_times", []) if now - t <= 10.0] + [now] * n
            self.dlog(f"[面向] 新的“面向错误” ×{n}（累计 {self.facing_errs}）")

    def _facing_problem(self, obs, now) -> bool:
        """上次转身（之后 0.2 秒）以后又出现了面向错误。"""
        since = max(self.combat_start - 1.0, self.face_clear_t)
        if self.face_last is not None:
            since = max(since, self.face_last + 0.2)
        since = max(since, getattr(self, "fclick_t", -1e9) + 0.2)
        return self.last_facing_err_t > since

    def _target_bearing(self, obs):
        """(角度, 类型, 来源) 或 None。角度正 = 怪在右边。"""
        st = self._st()
        w, h = obs.frame_size
        fov = float(st.get("fov_deg", 90))
        if getattr(obs, "marker", None):
            deg, kind = marker_bearing(obs.marker[0], obs.marker[1], w, h, fov)
            return deg, kind, "姓名板标记"
        if obs.selected_plate is not None:
            deg, kind = plate_bearing(obs.selected_plate, w, h, fov)
            return deg, kind, "选中姓名板图片"
        return None

    def _face_fix(self, obs, now, facing, no_dmg) -> bool:
        """返回 True = 本 tick 转身/换目标了。v8.6：误报不转；正前方仍报错 → 小幅转向标记那边；盲转先往标记那边。"""
        st, k = self._st(), self.cfg["keys"]
        if getattr(obs, "marker_state", "") == "no_plate" and not self._noplate_warned:
            self._noplate_warned = True
            self.log("ℹ️ 目标没有姓名板：请在游戏里打开敌方姓名板（默认 V 键），面向判断会准很多")
        if facing:
            r = self._face_click(obs, now)
            if r == "clicked":
                return True
            if r == "wait":
                return False         # 刚右键点过，等它转过来（照常放技能，不左右转）
        settle = float(st.get("face_settle_s", 0.25))
        if self.face_last is not None and now - self.face_last < max(settle, 0.1):
            self.fast_until = max(self.fast_until, now + 0.3)
            return False
        if facing and self._melee_back(obs, now):
            return True
        b = self._target_bearing(obs)
        ok_deg = float(st.get("face_ok_deg", 60))
        center = float(st.get("face_center_deg", 15))
        why = None
        if b is not None:
            deg, kind, src = b
            if abs(deg) > 3:
                self.marker_side = 1 if deg > 0 else -1
            side = "右" if deg > 0 else "左"
            if kind == "behind":
                why = f"{src}在屏幕下边 → 怪在身后（约 {abs(deg):.0f}°）"
            elif kind == "side_edge":
                why = f"{src}贴在屏幕{side}边 → 怪在{side}侧"
            elif abs(deg) > ok_deg:
                why = f"{src}：怪在{side}边约 {abs(deg):.0f}°"
            elif (facing or no_dmg) and abs(deg) > center:
                why = f"{'面向错误' if facing else '打不出伤害'}，{src}：怪在{side}边约 {abs(deg):.0f}°"
            if why:
                if self.face_attempts >= int(st.get("face_max_attempts", 6)):
                    return self._face_give_up(now, "转身")
                self._face_turn(deg, why)
                return True
            if kind == "front" and abs(deg) <= center:
                if no_dmg and not facing:
                    # v8.1：标记就在正前方，又没报面向错误 → 转身没用（多半是怪逃跑/够不到），不乱转
                    self._warn("frontnodmg", f"[面向] {src}在正前方（约 {deg:+.0f}°）且没有面向错误，"
                                             "打不出伤害不是朝向问题，不转身", 5.0)
                    return False
                if facing and obs.range == "melee":
                    # v8.26：贴脸 + 标记在中间还报面向错误 → 怪在脚下/身上，不盲转：后退 2 次 → 右键点一次标记 → 循环
                    return self._melee_centered(obs, now, deg, src)
                steps = self._face_steps()
                if facing and self.face_small < len(steps):
                    # v8.13：每个新的面向错误都要纠正（不再因为“怪在掉血”当误报：荆棘术反伤也会掉血）。
                    # 怪贴身时，标记在屏幕上会被“挤”到中间（镜头在身后几码），所以 ±15° 内不代表怪在正前方；
                    # 只信标记的左右方向，转的角度逐次加大 15° → 25° → 40°，再不行按原来的顺序扫一圈。
                    if self.face_last is not None and now - self.face_last < float(st.get("face_interval_s", 0.5)):
                        return False
                    if self.face_attempts >= int(st.get("face_max_attempts", 6)):
                        return self._face_give_up(now, "转身")
                    if abs(deg) > 2:
                        sgn = 1 if deg > 0 else -1
                    else:
                        self.face_alt = -getattr(self, "face_alt", -1)
                        sgn = self.face_alt
                    step = steps[self.face_small]
                    self.face_small += 1
                    self._face_turn(sgn * step, f"面向错误（插件计数+1），{src}在中间（{deg:+.0f}°；怪贴身时标记会挤到中间，"
                                                f"不代表在正前方）→ 往{'右' if sgn > 0 else '左'}转 {step:.0f}°（第 {self.face_small} 次）")
                    return True
        if facing or no_dmg:
            if self.face_last is not None and now - self.face_last < float(st.get("face_interval_s", 0.5)):
                return False
            return self._face_sweep(now, "面向错误" if facing else "打不出伤害", seen=b is not None)
        return False

    def _click_facing_now(self, obs, now) -> bool:
        """v8.20：点击模式现在是不是“面向错误”（报错灯亮，或 1.5 秒内有新的面向错误）。"""
        if obs.err_facing or obs.error == "facing":
            return True
        return self.last_facing_err_t > max(self.combat_start - 1.0, getattr(self, "fclick_t", -1e9) + 0.2) \
            and now - self.last_facing_err_t < 1.5

    # ---- v8.17 面向错误 → 右键点目标姓名板（标记在姓名板中间）
    def _fclick_quiet(self, now):
        """点过姓名板后 face_quiet_s 秒（默认 5）没有新的面向错误 → 算修好，次数清零。"""
        if not getattr(self, "fclick_n", 0):
            return
        quiet = float(self._st().get("face_quiet_s", 5.0))
        if now - max(self.fclick_t, self.last_facing_err_t) >= quiet:
            self.log(f"✅ 右键姓名板后 {quiet:.0f} 秒没有新的面向错误 → 面向修好了")
            self.fclick_n = 0        # fclick_t 留着：之前的旧报错不再算新的

    def _face_click(self, obs, now):
        """返回 'clicked' = 这一帧点了；'wait' = 刚点过、等它转（别左右转）；None = 不点（用原来的转身）。
        找不到标记绝不点。"""
        fc = self.cfg.get("face_click", {}) or {}
        if not fc.get("enabled", True):
            return None
        if not getattr(obs, "marker_center", False):
            if obs.source == "addon" and obs.target_exists:
                self._warn("fclickold", "ℹ️ 面向错误右键点姓名板需要插件 v0.10（标记在姓名板中间）：请复制新插件并在游戏里 /reload；"
                                        "现在先用原来的左右转", 600)
            return None
        if obs.range == "melee" and not fc.get("in_melee", False):
            self._warn("fclickmelee", "[面向] 已经在近战距离 → 不右键点，往标记那边转身（不按互动键）", 10)
            return None
        if obs.marker is None:
            why = ("目标没有姓名板（游戏里敌方姓名板没开，或目标太远/被挡）" if getattr(obs, "marker_state", "") == "no_plate"
                   else "屏幕上没找到目标姓名板标记（插件 v0.10 已加载，但这一帧没看到标记）")
            self._warn("fclicknomk", f"[面向] {why} → 这次不右键，先转身", 10)
            return None
        n, cd, mx = getattr(self, "fclick_n", 0), float(fc.get("cooldown_s", 0.9)), int(fc.get("max_clicks", 2))
        if n and getattr(obs, "facing_count", None) is not None and self.last_facing_err_t <= self.fclick_t + 0.2:
            return "wait"          # 点完以后还没有新的面向错误（报错灯还亮着是旧的）
        if n >= mx:
            self._warn("fclickmax", f"🖱️ 右键姓名板 {n} 次还是面向错误 → 先往标记那边转一下身，转完再右键点", 5)
            return None
        if now - self.fclick_t < cd or self._rclick_wait(now):
            return "wait"          # v8.24：离上一次右键不到 click_interval_s 秒
        x = obs.marker[0] + self.screen_offset[0]
        y = obs.marker[1] + float(fc.get("offset_y", 0) or 0) + self.screen_offset[1]
        btn = str(fc.get("button") or "right").strip().lower()
        self.fclick_n, self.fclick_t = n + 1, now
        self.log(f"🖱️ 面向错误 → {'右' if btn == 'right' else '左'}键点目标姓名板（标记 {int(obs.marker[0])},{int(obs.marker[1])}），"
                 f"角色自动转向（第 {n + 1}/{mx} 次）")
        self._ctx("面向错误：点目标姓名板")
        self.inp.click_at(x, y, button=btn, restore=bool(fc.get("restore_mouse", True)))
        self._note_rclick(btn)
        self.fast_until = max(self.fast_until, now + 0.5)
        return "clicked"

    def _face_steps(self):
        raw = self._st().get("face_small_steps", "15,25,40")
        try:
            vals = [float(x) for x in str(raw).replace("，", ",").split(",") if str(x).strip()]
        except ValueError:
            vals = []
        return [v for v in vals if v > 0] or [15.0, 25.0, 40.0]

    def _melee_centered(self, obs, now, deg, src) -> bool:
        """v8.26（t148u）：近战距离 + 标记在中间 + 面向错误 → 不盲转。
        后退一步（melee_back_ms）最多 melee_backs 次 → 还报错就右键点一次标记（守 3 秒右键间隔）→ 再循环；
        循环 melee_cycles 轮还不行 → 跟原来一样放弃（怪在打我就不换目标，重新来）。"""
        fc, k, st = self.cfg.get("face_click", {}) or {}, self.cfg["keys"], self._st()
        ms = float(fc.get("melee_back_ms", 300) or 0)
        nb = max(0, int(fc.get("melee_backs", 2)))
        maxc = max(1, int(fc.get("melee_cycles", 3)))
        if self.face_last is not None and now - self.face_last < float(st.get("face_interval_s", 0.5)):
            return False
        act = getattr(self, "melee_act_t", None)
        if act is not None and getattr(obs, "facing_count", None) is not None and self.last_facing_err_t <= act + 0.2:
            return False          # 上一步之后还没有新的面向错误（报错灯亮着是旧的）→ 照常打，先别动
        backs, cyc = getattr(self, "melee_backs", 0), getattr(self, "melee_cycle", 0)
        if cyc >= maxc:
            self.log(f"⚠️ 贴脸面向错误：后退 + 右键点已经 {cyc} 轮还是报错")
            return self._face_give_up(now, "贴脸后退/右键")
        if ms > 0 and k.get("back") and backs < nb:
            self.melee_backs = backs + 1
            self.log(f"🔙 贴脸面向错误，{src}在中间（{deg:+.0f}°，怪多半在脚下/身上）→ 不转身，后退一步"
                     f"（「{k['back']}」按住 {ms:.0f}ms，第 {backs + 1}/{nb} 次，第 {cyc + 1}/{maxc} 轮）")
            self._ctx("面向：贴脸后退")
            self.inp.press(k["back"], hold_ms=ms, delay=False)
        else:
            if self._rclick_wait(now):
                self._warn("mcwait", f"[面向] 后退 {backs} 次还是面向错误 → 等右键间隔（{self._rgap():.0f} 秒）到了再右键点标记", 3)
                return False
            if not self._click_marker(obs, "面向：贴脸右键点标记"):
                self.log(f"⚠️ 后退 {backs} 次还是面向错误，但这一帧没看到标记 → 这轮不右键，接着后退")
            else:
                self.fclick_t = now
                self.log(f"🖱️ 后退 {backs} 次还是面向错误 → 右键点一次目标姓名板（标记 {int(obs.marker[0])},{int(obs.marker[1])}，"
                         f"第 {cyc + 1}/{maxc} 轮；不按互动键）")
            self.melee_backs, self.melee_cycle = 0, cyc + 1
        self.melee_act_t = now
        self.face_last = self.clock()
        self.fast_until = self.face_last + 1.0
        return True

    def _melee_back(self, obs, now) -> bool:
        """v8.25：近战距离内转过一次身还报面向错误 → 后退半步（怪多半贴在身边/身后），之后再报错就扫一圈。"""
        k = self.cfg["keys"]
        ms = float((self.cfg.get("face_click") or {}).get("melee_back_ms", 300) or 0)
        if ms <= 0 or not k.get("back") or obs.range != "melee" or getattr(self, "face_backed", False) \
                or getattr(self, "face_melee_turns", 0) < 1:
            return False
        if self.face_last is not None and now - self.face_last < float(self._st().get("face_interval_s", 0.5)):
            return False
        b = self._target_bearing(obs)
        if b is not None and b[1] == "front" and abs(b[0]) <= float(self._st().get("face_center_deg", 15)):
            return False          # v8.26：标记在中间 → 交给 _melee_centered（后退 2 次 → 右键一次）
        self.face_backed = True
        self.face_small = len(self._face_steps())       # 后退过了：之后再报错直接扫一圈
        self.log(f"🔙 近战距离：转身后还是面向错误 → 后退半步（「{k['back']}」按住 {ms:.0f}ms），怪多半贴在身边/身后")
        self._ctx("面向：近战后退半步")
        self.inp.press(k["back"], hold_ms=ms, delay=False)
        self.face_last = self.clock()
        self.fast_until = self.face_last + 1.0
        return True

    def _face_turn(self, deg, why):
        st, k = self._st(), self.cfg["keys"]
        self._ctx(f"面向：{why}")
        rate = max(30.0, self.turn_rate)
        ms = max(60.0, min(1400.0, abs(deg) / rate * 1000.0))
        key = k.get("turn_right", "right") if deg > 0 else k.get("turn_left", "left")
        o = self.last_obs
        pre = getattr(o, "facing", None) if o is not None else None
        t0 = self.clock()
        self.inp.press(key, hold_ms=ms, delay=False)     # v8.6 (2)：转身后不加随机停顿
        self.face_attempts += 1
        if o is not None and getattr(o, "range", None) == "melee":
            self.face_melee_turns = getattr(self, "face_melee_turns", 0) + 1
        self.face_last = self.clock()
        self.last_turn_sign = 1 if deg > 0 else -1
        self.fast_until = self.face_last + 1.0           # v8.6 (3)：转完 1 秒内 50ms 循环
        if pre is not None:
            self._cal_pending = (pre, ms, 1 if deg > 0 else -1, self.face_last)
        self.log(f"🔄 {why} → {'右' if deg > 0 else '左'}转 {abs(deg):.0f}°（按住 {ms:.0f}ms，转速 {rate:.0f}°/s，第 {self.face_attempts} 次）")
        self.dlog(f"[面向] 转身按键用时 {self.face_last - t0:.2f}s")

    def _calibrate_turn(self, obs, now):
        """用插件朝向（GetPlayerFacing）测实际转了多少度 → 校准每秒转速。"""
        if self._cal_pending is None or obs.facing is None:
            return
        pre, ms, sign, t = self._cal_pending
        if now - t < 0.1:
            return
        self._cal_pending = None
        d = math.degrees((pre - obs.facing + math.pi) % (2 * math.pi) - math.pi) * sign  # 右转 = 朝向变小
        if ms >= 150 and abs(d) < 3:
            self._warn("turnkey", "⚠️ 按了左/右转键但朝向没变：检查游戏里 方向键左/右 是否绑定为“左转/右转”", 30)
            return
        if ms < 100 or d <= 5:
            return
        meas = d / (ms / 1000.0)
        if 60.0 <= meas <= 600.0:
            old = self.turn_rate
            self.turn_rate = 0.5 * self.turn_rate + 0.5 * meas
            self.dlog(f"[转速校准] 按住 {ms:.0f}ms 实际转了 {d:.0f}° → {meas:.0f}°/s；转速 {old:.0f} → {self.turn_rate:.0f}°/s")

    def _face_give_up(self, now, why) -> bool:
        k = self.cfg["keys"]
        o = self.last_obs
        if o is not None and self._aggro(o) and o.target_kind in ALIVE_KINDS:
            # v8.8：怪正在打我 → 不换目标（换了它照样打我），重新开始转身
            self.log(f"⚔️ {why} {self.face_attempts} 次还没打到，但这只怪正在打我 → 不换目标，重新转身")
            self._face_reset()
            self._reset_recovery(now)
            self.last_drop_time = now
            return True
        self.log(f"⏭️ {why} {self.face_attempts} 次还是打不到，Tab 换目标")
        self._event("facing_failed", f"{why} {self.face_attempts} 次失败")
        self._face_reset()
        self._reset_recovery(now)
        self.last_drop_time = now
        self.inp.press(k.get("target") or "tab")
        self.last_tab = now
        self.state = State.SEARCHING
        return True

    SWEEP = (45.0, -45.0, 90.0, -90.0, 180.0)   # 相对开始时的朝向 × 先转的方向：同侧 45、另一侧 45、同侧 90、另一侧 90、身后

    def _face_sweep(self, now, why, seen=False) -> bool:
        """看不到标记时的盲转：左 45° → 右 45° → 左 90° → 右 90° → 180°；还不行 → Tab 换目标。"""
        if self.sweep_i >= len(self.SWEEP) or self.face_attempts >= int(self._st().get("face_max_attempts", 6)):
            return self._face_give_up(now, f"盲转（{why}）")
        if self.sweep_i == 0:
            # v8.6 (5)：先往标记那边；没有标记 → 上次转对的方向 → 跟上次开始方向相反（不再每次都先左）
            self.sweep_sign = self.marker_side or self.face_good_sign or -self.sweep_sign
        base = self.SWEEP[self.sweep_i]
        target = base if base == 180.0 else base * self.sweep_sign
        delta = target - self.sweep_off
        self.sweep_i += 1
        self.sweep_off = target
        how = "标记在正前方仍报错" if seen else "看不到标记"
        self._face_turn(delta, f"{why}，{how}，盲转到 {'+' if target > 0 else ''}{target:.0f}°")
        return True

    # ------------------------------------------------------------ 区域刷怪：回中心
    def _area_warn(self, msg):
        if self._area_warned != msg:
            self._area_warned = msg
            self.log("⚠️ 区域刷怪：" + msg)

    # ------------------------------------------------------------ v8.17 范围：中心点 / 热键 / 走回去
    def request_area(self, cmd: str):
        """热键线程调用：'check' = 检查是否在范围内；'home' = 把当前位置设为中心；'route' = 添加跑尸路线点。下一帧处理。"""
        if cmd == "route":
            self.route_cmds.append(cmd)
            return
        self.area_cmds.append(cmd)

    def _route_tick(self, obs, now):
        """v8.39：F6 → 当前位置加到跑尸路线末尾（读不到坐标就下一帧再试，最多 1.5 秒）。"""
        retry = []
        while self.route_cmds:
            cmd = self.route_cmds.pop(0)
            if obs.pos is None:
                t0 = self.area_retry.setdefault("route", now)
                if now - t0 < 1.5:
                    retry.append(cmd)
                    continue
                self.area_retry.pop("route", None)
                self.log("🗺️ 添加跑尸路线点失败：读不到坐标（插件没读到 / 在副本里）")
                continue
            self.area_retry.pop("route", None)
            p = [round(obs.pos[0], 2), round(obs.pos[1], 2), int(obs.map_id or 0)]
            route = self.cfg.setdefault("corpse", {}).setdefault("route", [])
            route.append(p)
            self.log(f"🗺️ 已添加跑尸路线点 #{len(route)}：{p[0]:.2f},{p[1]:.2f}（地图 {p[2]}），共 {len(route)} 个，已保存"
                     + ("（#1 应该是复活点/墓地）" if len(route) == 1 else ""))
            if self.on_route:
                try:
                    self.on_route([list(x) for x in route])
                except Exception:  # noqa: BLE001
                    pass
        self.route_cmds[:0] = retry

    def set_route(self, route):
        """GUI 改了跑尸路线（删除/清空）。正在跑的这次不变，下次死亡起用新路线。"""
        self.cfg.setdefault("corpse", {})["route"] = [list(p) for p in route]

    def _hk(self, name, default):
        return str(self.cfg["keys"].get(name) or default).upper()

    def _area_tick(self, obs, now):
        a = self.cfg.get("area", {})
        if self.home is None and self.centers and obs.pos is not None:
            same = [(distance(obs.pos, c[:2]), i) for i, c in enumerate(self.centers) if self._same_map(c, obs.map_id)]
            if same:
                d, i = min(same)
                self.ci, self.home = i, self.centers[i]
                self._start_dir()
                nn = len(self.centers)
                order = " → ".join(f"#{j + 1}" for j in list(range(nn)) + list(range(nn - 2, 0, -1))) + " → #1"
                self.log(f"🚩 多中心巡逻：共 {nn} 个中心，从最近的 #{i + 1} {self.home[0]:.2f},{self.home[1]:.2f} 开始"
                         f"（距离 {d:.2f}，半径 {float(a.get('radius', 3)):.2f}）；转一圈没找到怪 → 走去下一个，来回走 "
                         + order + "（先往" + ("编号小" if self.cdir < 0 else "编号大") + "的方向）")
            else:
                self._warn("cnomap", f"🚩 中心列表里没有在当前地图 {obs.map_id} 的中心 → 这次用"
                           + ("开始位置当中心" if self.home_from_start else "保存的中心"), 60)
                if not self.home_from_start:
                    self.home = (float(a.get("center_x", 50)), float(a.get("center_y", 50)), int(a.get("map_id", 0) or 0))
        if self.home is None and self.home_from_start and obs.pos is not None:
            self.home = (obs.pos[0], obs.pos[1], int(obs.map_id or 0))
            self.log(f"📍 记下开始位置为范围中心 {obs.pos[0]:.2f},{obs.pos[1]:.2f}（地图 {obs.map_id}），半径 {float(a.get('radius', 3)):.2f}"
                     + ("" if a.get("enabled") else "（区域刷怪没开：只记录，不会走回去）")
                     + f"；{self._hk('hotkey_area_check', 'f9')}=检查范围，{self._hk('hotkey_set_home', 'f8')}=改设当前位置为中心")
        retry = []
        while self.area_cmds:
            cmd = self.area_cmds.pop(0)
            if cmd in ("add", "home") and obs.pos is None:
                # v8.24：这一帧没读到坐标 → 下一帧再试，最多 1.5 秒（以前一帧读坏就直接失败）
                t0 = self.area_retry.setdefault(cmd, now)
                if now - t0 < 1.5:
                    retry.append(cmd)
                    continue
                self.area_retry.pop(cmd, None)
            else:
                self.area_retry.pop(cmd, None)
            if cmd == "add":
                if obs.pos is None:
                    self.log("🚩 添加中心失败：读不到坐标（插件没读到 / 在副本里）")
                    continue
                p = (round(obs.pos[0], 2), round(obs.pos[1], 2), int(obs.map_id or 0))
                if not self.centers and self.home is not None and distance(p[:2], self.home[:2]) > 0.01:
                    self.centers.append(tuple(self.home))          # 原来的中心（开始位置/F8）当 #1
                    self.log(f"🚩 原来的范围中心 {self.home[0]:.2f},{self.home[1]:.2f}（地图 {self.home[2]}）记为中心 #1")
                self.centers.append(p)
                if self.home is None:
                    self.home, self.ci = p, len(self.centers) - 1
                self.log(f"🚩 已添加中心 #{len(self.centers)}：{p[0]:.2f},{p[1]:.2f}（地图 {p[2]}），共 {len(self.centers)} 个，已保存"
                         + ("" if len(self.centers) > 1 else "（再加一个才会在中心之间走）"))
                self._save_centers()
                continue
            if cmd == "home":
                if obs.pos is None:
                    self.log("📍 设中心失败：读不到坐标（插件没读到 / 在副本里）")
                    continue
                self.home = (obs.pos[0], obs.pos[1], int(obs.map_id or 0))
                self.log(f"📍 已把当前位置设为范围中心 {obs.pos[0]:.2f},{obs.pos[1]:.2f}（地图 {obs.map_id}），已保存"
                         + ("；原来的多中心列表已清空，它现在是中心 #1（按 "
                            + self._hk("hotkey_add_center", "f7") + " 继续添加）" if len(self.centers) > 1 else ""))
                self.centers = [(round(obs.pos[0], 2), round(obs.pos[1], 2), int(obs.map_id or 0))] if self.centers else []
                self.ci, self.travel, self.cdir = 0, False, 1
                self._save_centers()
                self._area_warned = ""
                if self.on_home:
                    try:
                        self.on_home(obs.pos[0], obs.pos[1], int(obs.map_id or 0))
                    except Exception:  # noqa: BLE001
                        pass
            else:
                self.log(area_report(obs.pos, obs.facing, obs.map_id, self.home, float(a.get("radius", 3)),
                                     bool(a.get("enabled")), self._hk("hotkey_set_home", "f8"))
                         + (f" | 状态 {self.state.value}"))
        self.area_cmds[:0] = retry

    def area_label(self) -> str:
        a, o = self.cfg.get("area", {}), self.last_obs
        if o is None:
            return "-"
        lab = area_label(o.pos, o.map_id, self.home, float(a.get("radius", 3)), bool(a.get("enabled")))
        if self.centers and len(self.centers) > 1:
            lab = f"#{self.ci + 1}/{len(self.centers)} " + lab
        if self.state == State.RETURNING:
            return ("去中心中 " if self.travel else "回中心中 ") + lab
        return lab

    def _outside_area(self, obs, ratio=1.0) -> bool:
        a = self.cfg.get("area", {})
        if not a.get("enabled") or obs.in_combat:
            return False
        if obs.pos is None:
            self._area_warn("插件读不到坐标（插件失联或在副本里），暂时不管范围")
            return False
        if self.home is None:
            self._area_warn(f"还没有范围中心，按 {self._hk('hotkey_set_home', 'f8')} 把当前位置设为中心")
            return False
        want_map = int(self.home[2] or 0)
        if want_map and obs.map_id is not None and obs.map_id != want_map:
            self._area_warn(f"当前地图 {obs.map_id} 不是中心所在地图 {want_map}（换了区域？"
                            f"按 {self._hk('hotkey_set_home', 'f8')} 把当前位置设为新中心），暂时不管范围")
            return False
        self._area_warned = ""
        if distance(obs.pos, self.home[:2]) <= float(a["radius"]) * ratio:
            return False
        return not self._auto_switch_center(obs, ratio)

    def _auto_switch_center(self, obs, ratio=1.0, why="") -> bool:
        """v8.46（t185u）就近切换中心：脱战、离当前中心超出半径（×ratio），但人在另一个（同地图）中心的半径（×ratio）里
        → 当前中心直接换成那个（几个都在 → 最近的），不走回原来的。去下一个中心的路上（travel）不换。
        追怪上限检查（ratio>1）也用：离当前中心超过追怪上限、但在另一个中心半径里 → 换过去，接着追。
        来回走的方向：换到编号大的 → 接着往大走；换到编号小的 → 接着往小走；到头就掉头（1→2→3→2→1 照旧）。"""
        a = self.cfg.get("area", {})
        if not a.get("auto_switch_center", True) or self.travel or obs.in_combat or obs.pos is None \
                or len(self.centers) < 2 or self.home is None:
            return False
        lim = float(a["radius"]) * min(1.0, ratio)    # 追怪上限（×2）检查时也只认“在另一个中心半径里”，免得越追越远
        cand = [(distance(obs.pos, c[:2]), i) for i, c in enumerate(self.centers)
                if i != self.ci and self._same_map(c, obs.map_id)]
        cand = [(d, i) for d, i in cand if d <= lim]
        if not cand:
            return False
        d, j = min(cand)
        old = self.ci
        n = len(self.centers)
        self.cdir = 1 if j > old else -1
        if j >= n - 1:
            self.cdir = -1
        elif j <= 0:
            self.cdir = 1
        self.ci, self.home = j, self.centers[j]
        self.patrol_skips = 0
        self._spin_reset()
        self.log(f"🚩 已在中心 #{j + 1} 范围内（离它 {d:.2f}，半径 {lim:.2f}）→ 切换，不走回 #{old + 1}{why}")
        return True

    def start_return(self, obs):
        a = self.cfg["area"]
        d = distance(obs.pos, self.home[:2])
        self.log(f"🧭 离中心{' #' + str(self.ci + 1) if len(self.centers) > 1 else ''} {d:.2f}（半径 {float(a['radius']):.2f}），"
                 + ("走过去" if self.travel else "走回中心")
                 + ("" if obs.facing is not None else "（读不到朝向：边走边用坐标变化算方向）"))
        self.state = State.RETURNING
        self.return_start = self.clock()
        self.stuck_tries = 0
        self.prog_pos, self.prog_time = obs.pos, self.clock()
        self.pos_missing_since = None
        self.mh_pos, self.mh_heading = None, None

    def _start_walk(self, obs):
        if not self.walking:
            self.inp.key_down(self.cfg["keys"].get("forward", "w"))
            self.walking = True
            self.prog_pos, self.prog_time = obs.pos, self.clock()
            self.mh_pos = obs.pos

    def _stop_walk(self):
        if self.walking:
            self.inp.key_up(self.cfg["keys"].get("forward", "w"))
            self.walking = False
            self.walk_stop_t = self.clock()

    def _turn(self, delta):
        a, k = self.cfg["area"], self.cfg["keys"]
        side, ms = turn_command(delta, float(a.get("turn_rate_dps", 180)))
        key = k.get("turn_left", "left") if side == "left" else k.get("turn_right", "right")
        if ms >= 15:
            self.inp.press(key, hold_ms=ms)
            if self.mh_heading is not None:     # 读不到朝向时：按转了多少推算新朝向
                turned = math.radians(float(a.get("turn_rate_dps", 180))) * ms / 1000.0
                self.mh_heading = (self.mh_heading + (turned if side == "left" else -turned)) % (2 * math.pi)
            self.mh_pos = None

    def _motion_heading(self, obs):
        """读不到朝向：按住前进时用坐标变化算朝向（走了 ≥ move_eps×2 才算）。"""
        a = self.cfg["area"]
        if self.walking and self.mh_pos is not None and obs.pos is not None:
            if distance(self.mh_pos, obs.pos) >= float(a.get("move_eps", 0.03)) * 2:
                self.mh_heading = bearing_to(self.mh_pos, obs.pos)
                self.mh_pos = obs.pos
        elif self.walking and self.mh_pos is None:
            self.mh_pos = obs.pos
        return self.mh_heading

    def _return_give_up(self, obs, now, why):
        """v8.33/v8.38：回中心/去中心卡住 3 次或超时 → 不停止。有别的中心（同地图）→ 照原方向去再下一个中心；
        没有（只有 1 个中心 / 都试过一轮）→ 就地找怪，60 秒内不再走回中心。"""
        self._stop_walk()
        self.patrol_skips = getattr(self, "patrol_skips", 0) + 1
        nxt = self._next_center(obs) if self.patrol_skips <= max(1, len(self.centers)) else None
        old = self.ci
        where = f"去中心 #{old + 1}" if (self.travel or len(self.centers) > 1) else "回中心"
        if nxt is not None:
            self.ci, self.home = nxt
            self.travel = True
            self.travel_look_t = self.travel_tab_t = now
            self.log(f"🚩 {where}{' ' if why.startswith('的') else '：'}{why} → 照原方向改去中心 "
                     f"#{self.ci + 1}/{len(self.centers)}（不停止）")
            return self.start_return(obs)
        self.patrol_skips = 0
        self.travel = False
        self.return_block_until = now + 60.0
        self.state = State.SEARCHING
        self.no_target_since = None
        self.last_combat_time = now
        self._spin_reset()
        self.log(f"🚩 {where}{' ' if why.startswith('的') else '：'}{why}，没有别的中心可去 → 不停止，就地找怪，60 秒内不再走回中心")
        return

    def _return(self, obs, now):
        a, k = self.cfg["area"], self.cfg["keys"]
        center = self.home[:2] if self.home else (float(a["center_x"]), float(a["center_y"]))
        if obs.pos is None:
            self._stop_walk()
            self.pos_missing_since = self.pos_missing_since or now
            if now - self.pos_missing_since > 2.0:
                self._area_warn("回程中读不到坐标，放弃回中心")
                self.state = State.SEARCHING
            return
        self.pos_missing_since = None
        if not self.travel and self._auto_switch_center(obs, why="（走回去的路上）"):
            self._stop_walk()               # v8.46：走回原中心的路上进了另一个中心的范围 → 就在这里找怪
            self.state = State.SEARCHING
            self.no_target_since = None
            self.last_combat_time = now
            return
        d = distance(obs.pos, center)
        if d <= float(a["radius"]) * float(a.get("arrive_ratio", 0.5)):
            self._stop_walk()
            if self.travel:
                self.patrol_skips = 0
                self.log(f"🚩 到达中心 #{self.ci + 1}/{len(self.centers)}（距离 {d:.2f}）→ 在这里找怪")
                self.travel = False
                self._spin_reset()
            else:
                self.log(f"🧭 已回到中心附近（距离 {d:.2f}）")
            self.state = State.SEARCHING
            self.no_target_since = None
            self.last_combat_time = now
            return
        limit = float(a.get("return_timeout_s", 90))
        if self.travel:
            limit = max(limit, float(self.cfg.get("scan", {}).get("patrol_timeout_s", 300)))
        if now - self.return_start > limit:
            # v8.38：超时不再停止 → 跟卡住 3 次一样：照原方向去下一个中心；只有 1 个中心 → 就地找怪，60 秒后再回
            return self._return_give_up(obs, now, f"超过 {limit:.0f} 秒还没到")
        if self._ts_moving(obs, now, float(self.cfg.get("detect", {}).get("dead_pct", 1.0)),
                           f"去中心 #{self.ci + 1} 的路上" if self.travel else "走回中心的路上"):
            return                      # v8.40：移动中 Tab 选中合格的怪 → 停下打
        if self.travel and self._travel_look(obs, now):
            return

        # 卡住检测：按着前进但位置不变
        if self.walking:
            if distance(obs.pos, self.prog_pos) > float(a.get("move_eps", 0.03)):
                self.prog_pos, self.prog_time = obs.pos, now
            elif now - self.prog_time > float(a.get("stuck_s", 3.0)):
                self.stuck_tries += 1
                if self.stuck_tries > 2:
                    # v8.33：卡住 3 次不再停止
                    return self._return_give_up(obs, now, "的路上卡住了（跳 + 平移 2 次都没用）")
                self.log(f"回中心卡住了，第 {self.stuck_tries} 次尝试跳跃+平移")
                if k.get("jump"):
                    self.inp.press(k["jump"])
                if k.get("strafe"):
                    self.inp.press(k["strafe"], hold_ms=500)
                self.prog_time = now
                self.mh_pos = None
                return

        facing = obs.facing
        if facing is None:
            facing = self._motion_heading(obs)
            if facing is None:      # 还不知道朝向：先往前走一小段，用坐标变化算
                return self._start_walk(obs)
        delta = angle_diff(bearing_to(obs.pos, center), facing)
        if abs(delta) > math.radians(float(a.get("face_tolerance_deg", 25))):
            self._stop_walk()  # 偏差大：停下原地转
            return self._turn(delta)
        if abs(delta) > math.radians(float(a.get("walk_correct_deg", 8))):
            self._turn(delta)  # 偏差小：边走边微调
        self._start_walk(obs)

    # ------------------------------------------------------------ 接近
    def _approach(self, obs, now, dead_pct):
        m = self.cfg.get("move", {})
        if not self._alive(obs, dead_pct):
            self.target_gone_ticks += 1
            if self.target_gone_ticks >= 2:
                if obs.target_kind == "dead":
                    return self.fight_over(obs)
                self.log("接近途中目标丢失")
                self.state = State.SEARCHING
            return
        self.target_gone_ticks = 0
        if obs.selected_plate is not None:
            self.last_target_plate = obs.selected_plate
        if not obs.in_combat and not self.travel and self._outside_area(obs, float(self.cfg.get("area", {}).get("leash_ratio", 1.3))):
            # v8.17：追怪追出范围太远（还没开打）→ 放弃这只，走回中心
            self.log("🧭 追怪追出范围太远（还没进战斗）→ 放弃这只怪，走回中心")
            self._wf_end()
            self._clear_target(obs)
            return self.start_return(obs)
        if self._wf_step(obs, now):
            return                          # v8.46：还在朝远处的怪走
        if obs.in_combat or obs.range == "melee" or obs.target_hp < self.last_target_hp - 0.5:
            self.log("进入战斗")
            dropped = obs.target_hp < self.last_target_hp - 0.5
            self._enter_combat(now)
            if dropped:
                self.last_damage_t = now
            return self._combat(obs, now, dead_pct)
        if now - self.approach_start > float(m.get("approach_timeout_s", 12)):
            self.log("走不过去/超时，换目标")
            self._clear_target()
            self.state = State.SEARCHING
            return
        if self._turn_toward(obs):
            return
        # v8.24：点过以后先判断要不要再点；要点也至少隔 click_interval_s（默认 3 秒）
        if getattr(self, "rclick_pos", None) is None and obs.pos is not None:
            self.rclick_pos = obs.pos
        if not self._rclick_wait(now) and now - self.last_engage >= max(self._rgap(), 1.0):
            why = self._rclick_need(obs, now)
            if why:
                if getattr(obs, "moving", False) or self.walking:
                    # v8.28：角色还在动（在走过去）→ 不点；停下来还有问题才点
                    self._stop_walk()
                    self.dlog(f"[接近] {why}，但角色还在走 → 先不点，停下来还有问题再点")
                    return
                self.log(f"🖱️ 接近目标：{why} → 角色已停下，再右键点一次（间隔 ≥ {self._rgap():.0f} 秒）")
                self._engage(obs)

    # ------------------------------------------------------------ 战斗
    def _combat(self, obs, now, dead_pct):
        s, m = self.cfg["safety"], self.cfg.get("move", {})
        if not self._alive(obs, dead_pct):
            self.target_gone_ticks += 1
            if self.target_gone_ticks < 2 and obs.target_kind != "dead":  # 防抖
                return
            if self.seen_alive or obs.target_kind == "dead":
                return self.fight_over(obs)
            self.state = State.SEARCHING
            return
        self.target_gone_ticks = 0
        self.seen_alive = True

        if obs.selected_plate is not None:
            self.last_target_plate = obs.selected_plate
        if obs.target_hp < self.last_target_hp - 0.5:
            self.last_target_hp = obs.target_hp
            self.last_drop_time = now
            self.last_damage_t = now        # Python 侧：目标血条变短 = 打出伤害
        elif obs.target_hp > self.last_target_hp + 5:  # 换了新目标
            self.last_target_hp = obs.target_hp
            self.last_drop_time = now

        stalled = now - self.last_drop_time
        if stalled > float(s["stuck_timeout_s"]) and self._stall_stop(now):
            return
        if stalled > float(s["retarget_s"]) and obs.in_combat is False:
            self.log("目标长时间没掉血且没进战斗，换目标")
            self._clear_target()
            self.state = State.SEARCHING
            return

        if obs.form not in ("unknown", self._cform()):  # 插件说不是战斗形态 → 先变回去
            return self._to_bear(obs)
        if self._click_opener(obs, now):
            return
        # 插件战斗日志：距上次伤害秒数
        if obs.since_damage is not None and not math.isinf(obs.since_damage):
            t = now - obs.since_damage
            if self.last_damage_t is None or t > self.last_damage_t:
                if t >= self.combat_start - 0.5:
                    self.last_damage_t = t
        self._update_facing_errors(obs, now)       # v8.17：点击模式也用插件的面向错误计数
        self._fclick_quiet(now)
        quiet = float(self._st().get("face_quiet_s", 5.0))
        if self.face_last is not None and now - self.face_last >= quiet and self.last_facing_err_t <= self.face_last + 0.2:
            self.dlog(f"[面向] 转身后 {now - self.face_last:.1f} 秒没有新的面向错误 → 面向正常，计数清零")
            self._face_reset()          # v8.25：点击模式也清零（以前只有原地模式清，转身次数会越攒越多）
        fr = None
        if obs.err_facing or obs.error == "facing" or self.last_facing_err_t > max(self.combat_start - 1.0, self.fclick_t + 0.2) \
                and now - self.last_facing_err_t < 1.5:
            fr = self._face_click(obs, now)
            if fr == "clicked":
                return
        if fr == "wait":
            pass                    # 刚右键点过姓名板，等它转过来：不做别的补救，照常放技能
        elif self._click_facing_now(obs, now):
            # v8.20：面向错误绝不按互动键。右键姓名板不行/找不到标记 → 转身（往标记那边转，没标记就扫一圈）
            if self._face_fix(obs, now, True, False):
                self.fclick_n = 0   # 转过一次后，下一个面向错误又先右键点姓名板
                return
        elif self.cfg.get("recover", {}).get("enabled", True):
            if self._recover(obs, now):
                return
        else:
            repress = float(m.get("repress_interval_s", 2.0))
            if (obs.error in ("facing", "range") or obs.range in ("ranged", "far")) \
                    and now - self.last_engage >= repress:
                return self._engage(obs)  # 右键选中姓名板：转向 + 贴过去
        self._rotate(obs)

    # ------------------------------------------------------------ 战斗补救
    def _fight_problem(self, obs, now) -> str | None:
        r = self.cfg.get("recover", {})
        if obs.err_facing or obs.error == "facing":
            return "面向错误"
        if obs.err_far or obs.error == "range" or obs.range in ("ranged", "far"):
            return "距离太远"
        if obs.attack_range == "out":
            return "普通攻击够不到"
        if obs.err_los or obs.error == "los":
            return "视线被挡"
        if self.last_damage_t is None:
            if now - self.combat_start > float(r.get("first_damage_s", 4.0)):
                return f"开打 {r.get('first_damage_s', 4.0)} 秒还没打出伤害"
        elif now - self.last_damage_t > float(r.get("no_damage_s", 2.0)):
            return f"{now - self.last_damage_t:.0f} 秒没打出伤害"
        return None

    def _recover(self, obs, now) -> bool:
        """返回 True = 本 tick 做了补救动作。v8.22：右键标记 → 转一点+右键 → 后退半步+右键（不按互动键）；连续 max_fails 次无效 → 跳过这只怪。"""
        r, k = self.cfg.get("recover", {}), self.cfg["keys"]
        # 补救后打出了伤害 → 算成功，计数清零
        if self.rec_last is not None and self.last_damage_t is not None and self.last_damage_t > self.rec_last:
            if self.rec_fails:
                self.log("补救成功，打出伤害了")
            self.rec_fails, self.rec_last = 0, None
        why = self._fight_problem(obs, now)
        if why is None or why == "面向错误":
            return False            # v8.20：面向错误只用右键姓名板/转身，不按互动键
        if self.rec_last is not None:
            if now - self.rec_last < float(r.get("check_interval_s", 1.5)):
                return False       # 等上一次补救生效
            self.rec_fails += 1
        if self.rec_fails >= int(r.get("max_fails", 3)):
            return self._give_up(now, why)
        step = self.rec_fails
        # v8.22：战斗中补救绝不按互动键（以前按 g，身边有尸体就会去剥皮）→ 右键点目标姓名板标记
        has_mk = self._has_marker(obs)
        what = "右键点目标姓名板" if has_mk else ("右键点姓名板" if obs.selected_plate is not None else None)
        if obs.range == "melee":
            what = None            # v8.24：近战距离内不右键，只转视角/用技能
        elif what is not None and self._rclick_wait(now):
            return False           # v8.24：离上一次右键不到 click_interval_s 秒，先等
        if what is None:
            why2 = ("已经在近战距离（v8.24 近战内不右键）" if obs.range == "melee"
                    else "插件不是 v0.10（标记不在姓名板中间）" if not getattr(obs, "marker_center", False)
                    else "这一帧没看到目标姓名板标记")
            self._warn("recnomk", f"[补救] {why2} → 不能右键点，改成转视角/开怪键", 10)
        far = why in ("距离太远", "普通攻击够不到")     # 太远就别后退
        self.log(f"🔧 {why} → 补救第 {step + 1} 次：" + ("" if step == 0 else "转一点视角 + " if (step == 1 or far) else "后退半步 + ")
                 + (what or "转视角 + 开怪键"))
        if step == 1 or what is None or (step >= 2 and far):
            self.turn_flip = not self.turn_flip
            key = k.get("turn_left", "left") if self.turn_flip else k.get("turn_right", "right")
            self.inp.press(key, hold_ms=float(r.get("turn_ms", 150)))
        elif step >= 2 and k.get("back"):
            self.inp.press(k["back"], hold_ms=float(r.get("back_ms", 300)))
        if what is not None:
            self._engage(obs)       # 先点选中姓名板图片，没有就点标记；不会按互动键
        elif k.get("attack"):
            self.inp.press(k["attack"])
        self.last_engage = now
        self.rec_last = self.clock()
        return True

    def _give_up(self, now, why) -> bool:
        r = self.cfg.get("recover", {})
        self.log(f"⏭️ 连续 {self.rec_fails} 次补救无效（{why}），清目标，跳过这只怪 {r.get('skip_s', 30)} 秒")
        self.skip_until = now + float(r.get("skip_s", 30))
        p = self.last_target_plate
        self.skip_pos = None if p is None else (p["x"] + p["w"] / 2, p["y"] + p["h"] / 2)
        self._clear_target()
        self._reset_recovery(now)
        self._reset_target_tracking(now)
        self.state = State.SEARCHING
        return True

    def fight_over(self, obs):
        """目标死亡：计数 → 拾取（可选）→ 清目标 → 回血到 90% 再找怪。"""
        self.kills += 1
        self.log(f"✅ 击杀 #{self.kills}")
        return self.loot(obs, count_kill=False, fresh=True)

    # ------------------------------------------------------------ 动作
    def _open_attack(self, obs, now):
        """开怪。v8.15 猎豹 + 开了“先潜行”+ 目标已在近战距离、还没进战斗：潜行 → 开怪键；其他情况按开怪键（v8.21 猎豹和熊都用开怪键 f）。"""
        c, k = self.cfg.get("cat") or {}, self.cfg["keys"]
        prowl = str(k.get("cat_prowl") or "").strip()
        opener = str(k.get("attack") or "").strip()      # v8.21：猎豹和熊都用开怪键（f）开怪，潜行后也按它
        if self._cat() and c.get("use_prowl") and prowl and opener and not obs.in_combat and obs.range == "melee":
            self.log(f"🐆 目标在近战距离、还没进战斗 → 潜行「{prowl}」，再按开怪键「{opener}」")
            self._ctx("猎豹：潜行")
            self.inp.press(prowl)
            self.inp.sleep(float(c.get("prowl_wait_s", 0.6)))
            self._ctx("猎豹：潜行后按开怪键")
            self.inp.press(opener)
            self.last_attack_press = now
            self.cat_fight = None
            return
        if self._cat() and c.get("use_prowl") and obs.range != "melee":
            self.dlog("[猎豹] 开了“先潜行”，但目标不在近战距离（原地模式要把怪拉过来）→ 照常按开怪键")
        self._press_attack(now)

    def _click_opener(self, obs, now) -> bool:
        """v8.21：点击模式开打时先按一次开怪键（f），猎豹和熊一样；之后照常输出循环。"""
        if getattr(self, "opened", True) or not self.cfg.get("move", {}).get("open_with_attack", True):
            return False
        if not str(self.cfg["keys"].get("attack") or "").strip():
            self.opened = True
            return False
        if obs.range == "far":
            return False            # 太远按了也放不出来，等走近
        self.opened = True
        self.log(f"⚔️ 开怪：按开怪键「{self.cfg['keys']['attack']}」（{'猎豹' if self._cat() else '熊'}）")
        self._open_attack(obs, now)
        return True

    def _rotate(self, obs):
        if getattr(self, "mb_active", False):
            return self._bear_rotate(obs)     # v8.38：被多只怪打临时变熊
        if self._cat():
            return self._cat_rotate(obs)
        return self.rotate(obs)

    def _cat_rotate(self, obs):
        """v8.15 猎豹循环：猛虎之怒（开打一次/每 N 秒）→ 连击点够了放终结技 → 斜掠（每只怪开头/每 N 秒）→ 爪击。
        插件 v0.9 读得到连击点/能量就用读数；读不到：每按 N 次攒点技能放一次终结技，能量不判断（按最短间隔轮转）。"""
        c, k = self.cfg.get("cat") or {}, self.cfg["keys"]
        now = self.clock()
        miss = cat_missing(self.cfg)
        if miss:
            self._warn("catmissing", "⚠️ 战斗形态=猎豹，但这些键没填：" + "、".join(miss) + "（“按键”页）→ 只按已填的键", 60)
        if getattr(self, "cat_fight", None) != self.combat_start:
            self.cat_fight = self.combat_start
            self.cat_builders, self.cat_rake_t, self.cat_tf_t = 0, None, None
        if now - getattr(self, "cat_last_press", -1e9) < float(c.get("press_interval_s", 1.0)):
            return None
        e, cp = obs.energy, obs.combo
        src = ("读数" if cp is not None else f"按次数（爪击 {self.cat_builders}/{c.get('finisher_after_builders', 4)}）")

        def ok(cost):
            return e is None or e >= float(cost)

        def press(key, name):
            self._ctx(f"猎豹：{name}")
            self.inp.press(key)
            self.cat_last_press = now
            self.dlog(f"[猎豹] {name}「{key}」| 连击点 {cp if cp is not None else '?'} 能量 {e if e is not None else '?'}（{src}）")
            return name

        tf = str(k.get("cat_tigers_fury") or "").strip()
        tf_every = float(c.get("tf_every_s", 0) or 0)
        if tf and (self.cat_tf_t is None or (tf_every > 0 and now - self.cat_tf_t >= tf_every)):
            self.cat_tf_t = now
            return press(tf, "猛虎之怒")
        fin = str(k.get("cat_finisher") or "").strip()
        ready = cp >= int(c.get("finisher_cp", 4)) if cp is not None \
            else self.cat_builders >= int(c.get("finisher_after_builders", 4))
        if fin and ready:
            if not ok(c.get("finisher_energy", 35)):
                return None                       # 攒能量等终结技
            self.cat_builders = 0
            return press(fin, "终结技")
        rake = str(k.get("cat_rake") or "").strip()
        every = float(c.get("rake_every_s", 9.0) or 0)
        if rake and (self.cat_rake_t is None or (every > 0 and now - self.cat_rake_t >= every)) \
                and ok(c.get("rake_energy", 35)):
            self.cat_rake_t = now
            self.cat_builders += 1
            return press(rake, "斜掠")
        b = str(k.get("cat_builder") or "").strip()
        if b and ok(c.get("builder_energy", 40)):
            self.cat_builders += 1
            return press(b, "爪击")
        return None

    def rotate(self, obs):
        cands = sorted(obs.ready_skills, key=lambda p: (p[0].get("priority", 99), -p[1]))
        for skill, score in cands:
            if self.inp.since_last(skill["key"]) * 1000 < float(skill.get("cooldown_ms", 0)):
                continue
            self._ctx(f"技能 {skill['name']}")
            self.inp.press(skill["key"])
            return skill["name"]
        return None

    # ---- v8 战斗治疗：分步、不阻塞，每步确认
    def heal(self, obs, why):
        """战斗中：（松开前进）退出熊形态 → 确认 → 治疗之触 → 确认读条 → 读完 → 变回熊。"""
        h, k = self.cfg["heal"], self.cfg["keys"]
        now = self.clock()
        self.heal_prev = self.state if self.state not in (State.HEALING, State.RESTING, State.STOPPED) else State.COMBAT
        self.state = State.HEALING
        self.fight_heals += 1
        self.heal_t0 = self.heal_phase_t = now
        self.heal_presses = self.heal_shift_presses = 0
        self.heal_next_press = now
        self.heal_chain = 1
        self.heal_cast_hp0 = obs.player_hp
        self.log(f"🩹 {why}：血量 {obs.player_hp:.0f}%（{self._hp_src(obs)}）< {h['heal_pct']}% → "
                 f"切人形放治疗之触（本场第 {self.fight_heals} 次）")
        self._stop_walk()
        if h.get("cancel_form_first", True) and self._in_any_form(obs):
            self.heal_phase = "shift"
            self._press_cancel(now)
        else:
            self.heal_phase = "cast"
            self._heal_step(obs, now)

    def _press_cancel(self, now):
        k = self.cfg["keys"]
        key = self._cancel_key()
        self._ctx(f"战斗治疗：退出{self._cform_cn()}形态（第 {self.heal_shift_presses + 1} 次）")
        self.inp.press(key)
        self.heal_shift_presses += 1
        self.heal_last_shift = self.clock()
        self.bear_belief = False
        self.dlog(f"[治疗] 按取消形态键「{key}」（第 {self.heal_shift_presses} 次）")

    def _heal_step(self, obs, now):
        h, k = self.cfg["heal"], self.cfg["keys"]
        cast_s = float(h.get("cast_s", 3.0))
        retry = float(h.get("ht_retry_s", 0.7))
        max_presses = int(h.get("ht_max_presses", 5))
        shift_to = float(h.get("shift_timeout_s", 2.5))
        if now - self.heal_t0 > shift_to + max_presses * retry + cast_s + 3.0:
            self.log(f"⚠️ 治疗流程超时（{now - self.heal_t0:.1f} 秒），放弃这次治疗")
            return self._heal_done(obs, now, False)
        if self.heal_phase == "shift":
            if not self._in_any_form(obs):
                self.dlog(f"[治疗] 已退出{self._cform_cn()}形态（{now - self.heal_t0:.2f}s，形态={obs.form}）")
                self.heal_phase, self.heal_phase_t = "cast", now
            elif now - self.heal_phase_t >= shift_to:
                key = self._cancel_key()
                self.log(f"⚠️⚠️ 按取消形态键「{key}」{self.heal_shift_presses} 次，{shift_to:.1f} 秒后还在{self._cform_cn()}形态 → 这次治疗失败。"
                         "请检查键位，或在游戏里做一个 /cancelform 宏放到别的键，把“取消变形键”改成那个键")
                return self._heal_done(obs, now, False)
            else:
                if now - self.heal_last_shift >= 1.0:
                    self._press_cancel(now)
                return
        if self.heal_phase == "cast":
            if self.heal_presses >= 1 and obs.casting:
                self.log(f"🩹 治疗之触开始读条（按了 {self.heal_presses} 次，用时 {now - self.heal_t0:.1f} 秒）")
                self.heal_phase, self.heal_phase_t = "wait", now
                self.heal_mana0 = obs.mana
                self.cast_peak = obs.mana
                return
            if self.heal_presses >= 1 and obs.casting is None:   # 截图模式：不知道有没有读条，按时间等
                self.heal_phase, self.heal_phase_t = "wait", self.heal_last_press
                return
            if now >= self.heal_next_press:
                if self.heal_presses >= max_presses:
                    err = f"，红字报错={obs.error}" if obs.error else ""
                    self.log(f"⚠️⚠️ 按了 {self.heal_presses} 次治疗之触（键「{k['healing_touch']}」）都没开始读条{err}。"
                             f"可能：没蓝 / 公共冷却 / 键位上不是治疗之触 / 还在熊形态（形态={obs.form}）")
                    return self._heal_done(obs, now, False)
                if self.heal_presses >= 1:
                    self.dlog(f"[治疗] {self.heal_presses * retry:.1f} 秒还没读条（形态={obs.form}，法力={obs.mana}），再按治疗之触")
                self._ctx(f"战斗治疗：治疗之触（第 {self.heal_presses + 1} 次）")
                self.inp.press(k["healing_touch"])
                self.heal_presses += 1
                self.heal_last_press = self.clock()
                self.heal_next_press = self.heal_last_press + retry
            return
        if self.heal_phase == "wait":
            el = now - self.heal_phase_t
            if obs.casting is None:
                if el >= cast_s:
                    return self._heal_done(obs, now, True)
                return
            if obs.casting and el < cast_s + 2.0:
                return
            if obs.casting:
                self.log(f"⚠️ 读条超过 {cast_s + 2:.0f} 秒还在读，先结束")
                self.heal_inflight = (now, obs.mana)    # v8.42：游戏里还在读，蓝还没扣
            elif el < 0.8:
                self.log(f"⚠️ 读条只持续了 {el:.1f} 秒（可能被打断）")
            return self._heal_done(obs, now, True)
        return self._heal_done(obs, now, False)

    def _heal_done(self, obs, now, ok):
        h, k = self.cfg["heal"], self.cfg["keys"]
        if ok:
            self._learn_cost("heal", self.heal_mana0, obs)
        self.heal_mana0 = None
        if self._heal_continue(obs, now, ok):
            return
        if h.get("cancel_form_first", True) and obs.form != self._cform():
            key = self._form_key()
            if key:
                self._ctx(f"治疗结束，变回{self._cform_cn()}")
                self.inp.press(key)
        self.bear_belief = True
        self.last_heal_time = self.clock()
        self.log(f"🩹 治疗{'完成' if ok else '没成功'}（用时 {now - self.heal_t0:.1f} 秒，当前血量 {obs.player_hp:.0f}%），变回{self._cform_cn()}")
        self.heal_phase = ""
        if self.state == State.HEALING:
            self.state = self.heal_prev or State.COMBAT
        # 治疗占用了几秒，别算成“没打出伤害”
        self.combat_start = self.clock()
        self.last_drop_time = self.clock()      # v8.41：治疗的时间也别算进“目标血量 N 秒没下降”
        if self.last_damage_t is not None:
            self.last_damage_t = self.clock()
        self.rec_last = None
        self.face_last = None
        self.face_clear_t = self.clock()

    def _heal_continue(self, obs, now, ok) -> bool:
        """v8.11：战斗治疗没回到“战斗中治疗回复线”（默认 90%）→ 留在人形接着放治疗之触。"""
        h = self.cfg["heal"]
        if self.state != State.HEALING:
            return False
        to = float(h.get("heal_to_pct", 90))
        chain = getattr(self, "heal_chain", 1)
        hp0 = getattr(self, "heal_cast_hp0", obs.player_hp)
        hp = obs.player_hp
        self.log(f"🩹 战斗治疗第 {chain} 个{'读完' if ok else '没放出来'}：血量 {hp0:.0f}% → {hp:.0f}%"
                 f"（{self._hp_src(obs)}，法力 {obs.mana if obs.mana is not None else '?'}，回复线 {to:.0f}%）")
        if not obs.hp_readable or hp >= to:
            return False
        why = None
        min_mana = float(h.get("heal_min_mana", 10))
        if obs.mana is not None and obs.mana < min_mana:
            why = f"法力 {obs.mana:.0f}% < {min_mana:.0f}%"
        elif self._mana_short(obs, "heal"):
            short = self._mana_short(obs, "heal")
            self.log(f"💧 留蓝变{self._cform_cn()}：{short} → 不再治疗")
            why = short + "（留着蓝变身）"
        elif chain >= int(h.get("heal_chain_max", 6)):
            why = f"已经连放 {chain} 个（上限 {h.get('heal_chain_max', 6)}）"
        elif not ok:
            why = "这个没放出来"
        elif hp < float(h.get("critical_pct", 15)):
            why = f"血量低于危险线 {h.get('critical_pct', 15)}%（按原来的规则处理）"
        elif self._is_bear(obs):
            why = "还在熊形态"
        if why:
            self.log(f"🩹 血量 {hp:.0f}% 还没到回复线 {to:.0f}%，但{why} → 不再治疗，变回{self._cform_cn()}")
            return False
        self.heal_chain = chain + 1
        self.fight_heals += 1
        self.heal_cast_hp0 = hp
        self.heal_t0 = self.heal_phase_t = now
        self.heal_presses = 0
        self.heal_next_press = now
        self.heal_phase = "cast"
        self.log(f"🩹 血量 {hp:.0f}% < 回复线 {to:.0f}% → 留在人形，继续放治疗之触（第 {self.heal_chain} 个）")
        return True

    def _post_skip_line(self) -> float:
        """v8.47（t190u）：战后血量 ≥ 这个 → 不治疗（默认 85；以前只有满血不治疗）。"""
        try:
            v = float(self.cfg["heal"].get("post_rejuv_below_pct", 85))
        except (TypeError, ValueError):
            v = 85.0
        return min(99.5, v) if v > 0 else 99.5

    def _post_on(self) -> bool:
        return bool(self.cfg["heal"].get("post_enabled", True))

    def _rejuv_cd_left(self) -> float:
        cd = float(self.cfg["heal"].get("rejuv_no_repeat_s", 12) or 0)
        return cd - (self.clock() - self.rejuv_time) if cd > 0 else 0.0

    def _rejuv_cd_skip(self, obs) -> bool:
        """v8.30：这次该放回春术，但离上次回春术不到 N 秒（身上多半还有）→ 不放，也不变形。"""
        if obs.player_hp < float(self.cfg["heal"].get("post_ht_below_pct", 70)):
            return False                         # 生命之触不受影响
        if not self.cfg["heal"].get("post_rejuv_enabled", True):
            # v8.36：关了“战后放回春术” → 不按回春术键、不变形，直接继续打（这场战斗只写一次）
            self.post_due = False
            self.post_healed = True
            self.log(f"🩹 战后治疗：血量 {obs.player_hp:.0f}% ≥ 生命之触线 "
                     f"{float(self.cfg['heal'].get('post_ht_below_pct', 70)):.0f}%，“战后放回春术”关了 → 不治疗，直接继续打")
            return True
        left = self._rejuv_cd_left()
        if left <= 0:
            return False
        self.post_due = False
        self.post_healed = True
        self.log(f"🩹 战后治疗：血量 {obs.player_hp:.0f}%，但 {self._rejuv_cd_left_txt(left)} → 不重复放回春术，也不变形")
        return True

    def _rejuv_cd_left_txt(self, left) -> str:
        cd = float(self.cfg["heal"].get("rejuv_no_repeat_s", 12) or 0)
        return f"离上次回春术才 {cd - left:.1f} 秒（{cd:.0f} 秒内不重复放）"

    def _start_post_heal(self, obs, why):
        h, k = self.cfg["heal"], self.cfg["keys"]
        if self._rejuv_cd_skip(obs):
            self.state = State.SEARCHING
            return
        line = float(h.get("post_ht_below_pct", 70))
        hp = obs.player_hp
        self.post_due = False
        self.post_healed = True                  # v8.30：这场战斗已经治疗（真正脱战时不再补）
        self.post_kind = "heal" if hp < line else "rejuv"
        self.post_key = (k.get("healing_touch") if self.post_kind == "heal" else k.get("rejuvenation")) or ""
        self.post_name = "生命之触" if self.post_kind == "heal" else "回春术"
        self.post_phase, self.post_presses, self.post_seen_cast = "start", 0, False
        self.post_t0 = self.post_last_press = self.clock()
        self.post_hp0, self.post_mana0, self.post_hit_noted = hp, obs.mana, False
        self.post_learned = False
        self.rest_mode = "post"
        self.state = State.RESTING
        rel = "<" if hp < line else "≥"
        self.log(f"🩹 战后治疗（{why}）：血量 {hp:.0f}% {rel} {line:.0f}% → 用{self.post_name}「{self.post_key}」"
                 f"（{self._hp_src(obs)}，法力 {obs.mana if obs.mana is not None else '?'}）")

    def _post_done(self, obs, now, msg):
        self.rest_end_t = now
        self.rest_mode = None
        self.log(msg)
        if not obs.in_combat and not self._is_bear(obs) and \
                self._maybe_buff(obs, now, why="战后治疗完，正好是人形"):
            return
        if not self._is_bear(obs):
            self._to_bear(obs)
        self.state = State.SEARCHING

    def _post_heal(self, obs, now):
        """v8.27（t149u）：战斗结束治疗一次。按键是宏（自带取消变形，不先按取消变形键）；
        生命之触读条中被打也读完再打；法力会低于变身预留 → 跳过；治疗完马上变回战斗形态。"""
        h = self.cfg["heal"]
        name, key, kind = self.post_name, self.post_key, self.post_kind
        if self.post_phase == "start":
            if obs.in_combat and self._being_hit(obs):
                self.rest_mode = None
                self.log(f"🩹 战后治疗：还没按{name}就挨打了 → 不治疗，先打")
                self.last_kill_t = -1e9
                self._to_bear(obs)
                self.state = State.SEARCHING
                return
            if not key:
                return self._post_done(obs, now, f"⚠️ 战后治疗：没填{name}键 → 跳过（“基本设置”页填键）")
            short = self._mana_short(obs, kind)
            if short:
                return self._post_done(obs, now, f"🩹 战后治疗：{short} → 跳过{name}，保住变{self._cform_cn()}的法力")
            mana, mn = obs.mana, float(h.get("rest_min_mana", 10))
            if mana is not None and 0 < mana < mn:
                return self._post_done(obs, now, f"🩹 战后治疗：法力 {mana:.0f}% < {mn:.0f}% → 跳过{name}")
            self._post_press(obs, now)
            self.post_phase = "wait"
            return
        if now - self.post_t0 > float(h.get("cast_s", 3.0)) + 10.0:
            return self._post_done(obs, now, f"⚠️ 战后治疗：{name}等了 {now - self.post_t0:.0f} 秒还没结束 → 不等了")
        if kind == "heal":
            if obs.casting:
                if not self.post_seen_cast:
                    self.post_seen_cast = True
                    self.dlog(f"[战后治疗] {name}读条中（按了 {self.post_presses} 次）")
                if (obs.in_combat or self.hp_drop >= 2.0) and not self.post_hit_noted:
                    self.post_hit_noted = True
                    self.log(f"🩹 {name}读条中挨打 → 读完再打")
                return
            if self.post_seen_cast or (obs.casting is None and now - self.post_last_press >= float(h.get("cast_s", 3.0)) + 0.3):
                self._learn_cost("heal", self.post_mana0, obs)
                return self._post_done(obs, now, f"🩹 {name}读完：血量 {self.post_hp0:.0f}% → {obs.player_hp:.0f}%，"
                                                 f"马上变回{self._cform_cn()}继续")
            if now - self.post_last_press >= float(h.get("ht_retry_s", 0.7)):
                if self.post_presses >= int(h.get("ht_max_presses", 5)):
                    return self._post_done(obs, now, f"⚠️ 战后治疗：按了 {self.post_presses} 次「{key}」都没开始读条"
                                                     f"（形态={obs.form}，法力={obs.mana}，报错={obs.error}）→ 不治疗了")
                self._post_press(obs, now)
            return
        # 回春术：瞬发。看到变成人形或法力掉了 = 放出来了；等公共冷却（1.5 秒）再变回去
        ok = obs.form == "caster" or (self.post_mana0 is not None and obs.mana is not None
                                      and self.post_mana0 - obs.mana >= 3)
        if ok and self.post_phase == "wait":
            self.post_phase = "gcd"
            self.post_ok_t = now
            self.rejuv_time = self.post_last_press     # v8.30：记下回春术放出来的时间
        if self.post_phase == "gcd":
            if not getattr(self, "post_learned", False) and obs.form == "caster":
                self.post_learned = True
                self._learn_cost("rejuv", self.post_mana0, obs, lo=3, hi=40)
            if now - self.post_last_press >= 1.5:
                return self._post_done(obs, now, f"🩹 回春术放好（血量 {self.post_hp0:.0f}% → {obs.player_hp:.0f}%）→ "
                                                 f"马上变回{self._cform_cn()}继续")
            return
        if now - self.post_last_press >= 1.0:
            if self.post_presses >= 3:
                return self._post_done(obs, now, f"⚠️ 战后治疗：按了 {self.post_presses} 次「{key}」回春术好像没放出来"
                                                 f"（形态={obs.form}，法力={obs.mana}，报错={obs.error}）→ 不治疗了")
            self._post_press(obs, now)

    def _post_press(self, obs, now):
        self.post_presses += 1
        self.post_last_press = now
        self._ctx(f"战后治疗：{self.post_name}（第 {self.post_presses} 次）")
        self.inp.press(self.post_key)
        self.bear_belief = False
        if self.post_presses > 1:
            self.dlog(f"[战后治疗] 再按「{self.post_key}」{self.post_name}（第 {self.post_presses} 次，形态={obs.form}，施法={obs.casting}）")

    def start_rest(self, obs, why="战斗结束"):
        if self._post_on():
            return self._start_post_heal(obs, why)
        self.rest_mode = None
        self.state = State.RESTING
        self.rest_start = self.clock()
        self.rest_next_cast = -1e9
        self.rest_presses = 0
        self.rest_seen_cast = False
        self.rest_combat_noted = False
        extra = "（战斗状态还没消失，没在挨打，照样回血）" if obs.in_combat else ""
        self.log(f"🩹 {why}（低于 {self._rest_below():.0f}% 才回血），回血到 {self.cfg['heal'].get('rest_to_pct', 90)}%"
                 f"（当前 {obs.player_hp:.0f}%，{self._hp_src(obs)}）{extra}")

    def _rest(self, obs, now):
        """脱战回血：人形下反复放治疗之触，直到 ≥ rest_to_pct，再变回熊。
        v8：刚杀完怪残留的“战斗中”不打断；只有真的在掉血/怪在打我才变熊迎战；法力读不到不挡治疗。"""
        if getattr(self, "rest_mode", None) == "post":
            return self._post_heal(obs, now)
        if getattr(self, "rest_mode", None) == "mana":
            return self._mana_rest(obs, now)            # v8.46：先回蓝再开怪
        h, k = self.cfg["heal"], self.cfg["keys"]
        target = float(h.get("rest_to_pct", 90))
        if obs.in_combat:
            if self._being_hit(obs):
                why = f"血量掉了 {self.hp_drop:.0f}%" if self.hp_drop >= 2 else "选中的怪在打我"
                self.log(f"回血时被打（{why}），变{self._cform_cn()}迎战")
                self.last_kill_t = -1e9
                self._to_bear(obs)
                self.state = State.SEARCHING
                return
            if not self.rest_combat_noted:
                self.rest_combat_noted = True
                self.dlog("[回血] 还在战斗状态但没在挨打 → 继续回血")
        if obs.player_hp >= target:
            self.rest_end_t = now
            if not obs.in_combat and self._maybe_buff(obs, now, why=f"回血完成（{obs.player_hp:.0f}%），正好是人形"):
                return
            if self.pending_loot and not obs.in_combat:
                self.log(f"血量 {obs.player_hp:.0f}% ≥ {target:.0f}%，先用人形拾取/剥皮 {self.pending_loot} 具尸体，捡完再变{self._cform_cn()}（少变一次形）")
                self.caster_hold = True
                self.state = State.SEARCHING
                return
            self.log(f"血量 {obs.player_hp:.0f}% ≥ {target:.0f}%，变回{self._cform_cn()}继续")
            self._to_bear(obs)
            self.state = State.SEARCHING
            return
        if now - self.rest_start > float(h.get("rest_timeout_s", 60)):
            # v8.6：不再停止。变回熊继续打；接下来 rest_timeout_s 秒内不再脱战回血（战斗中低血治疗照常），免得一直回血
            skip = float(h.get("rest_timeout_s", 60))
            self.rest_skip_until = now + skip
            self.rest_end_t = now
            self.log(f"⚠️ 回血 {skip:.0f} 秒还没到 {target:.0f}%（当前 {obs.player_hp:.0f}%，法力 {obs.mana}；"
                     f"可能没蓝或治疗之触键不对）→ 不等了，变回熊继续；{skip:.0f} 秒内不再脱战回血")
            self._event("rest_timeout", "回血超时")
            self._to_bear(obs)
            self.state = State.SEARCHING
            return
        if obs.casting is not None and not obs.casting and self.rest_mana0 is not None:
            self._learn_cost("heal", self.rest_mana0, obs)     # 读条结束：实测耗蓝
            self.rest_mana0 = None
        if not obs.casting:
            short = self._mana_short(obs, "heal")
            if short:
                # v8.17：再放一个治疗会把变身的蓝用掉 → 不放；先变回战斗形态，在形态里等回蓝（被打也能直接打）
                if not self._is_bear(obs) and obs.form != "unknown" and obs.mana >= self._reserve():
                    if now - self.heal_last_shift >= 1.5:
                        self.heal_last_shift = now
                        self.log(f"🩹 回血：{short} → 不再放治疗，先变回{self._cform_cn()}，在{self._cform_cn()}形态等回蓝")
                        self._to_bear(obs)
                    return
                where = f"{self._cform_cn()}形态" if self._is_bear(obs) else "人形（蓝不够变身）"
                self._warn("restreserve", f"🩹 回血：血量 {obs.player_hp:.0f}%，{short} → 在{where}等回蓝", 15.0)
                return
        if self._is_bear(obs):
            if now - self.heal_last_shift >= 1.0:
                self.heal_last_shift = now
                self.dlog(f"[回血] 退出{self._cform_cn()}形态（血量 {obs.player_hp:.0f}%）")
                self._to_caster(obs)
            return
        if obs.casting:
            if not self.rest_seen_cast:
                self.dlog(f"[回血] 治疗之触读条中（按了 {self.rest_presses} 次）")
                self.rest_mana0 = obs.mana
                self.cast_peak = obs.mana
            self.rest_seen_cast = True
            self.rest_presses = 0
            self.rest_next_cast = now + 0.3     # 读完 0.3 秒后可以再放
            return
        if now < self.rest_next_cast:
            return
        mana = obs.mana
        min_mana = float(h.get("rest_min_mana", 10))
        if mana is not None and 0 < mana < min_mana:
            self._warn("restmana", f"🩹 不放治疗：法力 {mana:.0f}% < {min_mana:.0f}%，等回蓝", 10)
            return
        if mana is not None and mana <= 0:
            self._warn("mana0", "ℹ️ 法力读成 0%（可能读不到），照样按治疗之触", 30)
        if self.rest_presses >= int(h.get("ht_max_presses", 5)) and obs.casting is not None:
            self._warn("restfail", f"⚠️ 回血：按了 {self.rest_presses} 次治疗之触都没开始读条"
                                   f"（形态={obs.form}，法力={mana}，报错={obs.error}），2 秒后再试", 10)
            self.rest_presses = 0
            self.rest_next_cast = now + 2.0
            return
        self._ctx(f"战后回血：治疗之触（第 {self.rest_presses + 1} 次）")
        self.inp.press(k["healing_touch"])
        self.rest_presses += 1
        self.rest_seen_cast = False
        self.dlog(f"[回血] 按治疗之触（血量 {obs.player_hp:.0f}%，法力 {mana}，第 {self.rest_presses} 次）")
        if obs.casting is None:    # 截图模式：不知道读条，按时间
            self.rest_next_cast = now + float(h.get("cast_s", 3.0)) + 0.3
        else:
            self.rest_next_cast = now + float(h.get("ht_retry_s", 0.7))

    def loot(self, obs, count_kill=True, fresh=False):
        k, l = self.cfg["keys"], self.cfg["loot"]
        if self.bag_try is not None and not obs.in_combat:
            self._bag_settle(obs, self.clock())      # v8.29：先结算上一轮按键有没有反应
        self.state = State.LOOTING
        if count_kill:
            self.kills += 1
        key = self._interact_key()
        fresh = fresh or count_kill
        if fresh:
            self.last_kill_t = self.clock()
            self.post_due = not self.post_healed    # v8.30：这场战斗治疗过（之后没再挨打）就不再治疗
        low = fresh and self._need_rest(obs)
        hit = bool(obs.in_combat) and self._being_hit(obs)
        # v8.17：战斗中两种模式都不捡（以前只有原地模式会等脱战；点击模式杀完一只立刻拾取/剥皮，多只怪时被打断）
        # 血量低先回血再捡：原地模式（v8.7）；点击模式脱战时还是 拾取 → 回血（跟以前一样）
        if (obs.in_combat or hit or (low and self.stationary)) and self._loot_on() and key:
            # v8.4：还在战斗中（可能还有别的怪）→ 先不捡，记下来，脱战后再捡
            # v8.7：血量低于回血线 → 先回血，回完再捡
            self.pending_loot = min(self.pending_loot + 1, 5)
            self.pending_loot_t = self.clock()
            if low and not obs.in_combat and not hit:
                self.log(f"💰 血量 {obs.player_hp:.0f}% < {self._rest_below():.0f}% → 先回血，回完再拾取/剥皮"
                         f"（记下 {self.pending_loot} 具尸体）")
            else:
                self.log(f"💰 还在战斗中（插件战斗块={'是' if obs.in_combat else '否'}，挨打={'是' if hit else '否'}），"
                         f"先不拾取/剥皮，记下 {self.pending_loot} 具尸体，先打正在打我的怪，脱战后再捡")
            self.last_combat_time = self.clock()
            self.last_kill_t = self.clock()
            self._reset_target_tracking(self.clock())
            if obs.in_combat and not self._need_rest(obs) or hit:
                self.state = State.SEARCHING      # v8.17：还在战斗 → 先找打我的怪（掉血就马上 x）
                return
            return self._after_loot(obs)
        known_empty = obs.target_kind == "dead" and obs.lootable is False
        probe = False
        if self._loot_on() and key and not known_empty:
            if not obs.target_exists:
                self.dlog("[拾取] 怪死后目标自动清掉了，照样按交互键（交互键会找身边的尸体）")
            self.inp.sleep(float(l["delay_ms"]) / 1000.0)
            self._ctx("拾取（交互键第 1 次）")
            self.inp.press(key)
            self._bag_press()
            self.log(f"💰 拾取：按交互键「{key}」")
            # v8.29：已经连续 N-1 次没反应 → 这次只按拾取（不按剥皮），看这一下有没有反应
            probe = self._bag_on() and self.bag_noresp + 1 >= self._bag_n()
            self.inp.sleep(float(l["wait_ms"]) / 1000.0)
        elif self.bag_full:
            self.dlog("[拾取] 背包满了（v8.29），这次运行不拾取/剥皮，只打怪")
        elif not l.get("enabled", True):
            self.dlog("[拾取] 拾取已关闭")
        elif not key:
            self._warn("nolootkey", "⚠️ 拾取键 / 与目标互动键都没填，不拾取也不剥皮", 60)
        else:
            self.dlog("[拾取] 尸体没有可拾取的东西，跳过拾取")
        if self.stationary:
            self.dlog("[清目标] 原地模式不按清目标键（下次 Tab 会直接换目标；没目标时按 Esc 会打开游戏菜单）")
        elif probe:
            self.dlog("[背包] 这次先不清目标，看尸体还是不是“可拾取”")
        else:
            self._clear_target(obs)
        self.last_combat_time = self.clock()
        self.last_kill_t = self.clock()
        self._reset_target_tracking(self.clock())
        if probe:
            self.state = State.SKINNING          # 借用剥皮的“等读条”状态等反应，不按剥皮
            self.skin_t0 = self.clock()
            self.skin_seen_cast = False
            self.bag_probe = True
            self.log(f"🎒 已经连续 {self.bag_noresp} 次按互动键没反应，这次只按拾取，等 1.5 秒看有没有反应")
            return
        if self._loot_on() and l.get("skin", True) and key:
            return self._start_skin(obs)
        self._after_loot(obs)

    def _after_loot(self, obs):
        if self._need_rest(obs):
            self.start_rest(obs)
        else:
            if obs.player_hp < float(self.cfg["heal"].get("rest_to_pct", 90)):
                self.dlog(f"[回血] 血量 {obs.player_hp:.0f}% ≥ 回血线 {self._rest_below():.0f}% → 不变人形，直接找下一只")
            self.state = State.SEARCHING

    def _interact_key(self) -> str:
        """v8.2：拾取 + 剥皮用“与目标互动键”；没填就用拾取键。"""
        k = self.cfg["keys"]
        key = (k.get("interact") or "").strip() or (k.get("loot") or "").strip()
        if key.lower() == "rightclick":
            self._warn("rclickloot", "⚠️ 拾取/剥皮用的是鼠标右键，会点在鼠标停的位置，常常点不到尸体。"
                                     "建议在游戏按键设置里给“与目标互动”绑一个键，填到“与目标互动键”", 300)
        elif key and key == (k.get("attack") or "").strip():
            self._warn("interactattack", f"⚠️ 与目标互动键「{key}」和开怪键一样，拾取/剥皮时会放技能，请改成不同的键", 300)
        return key

    def _start_skin(self, obs):
        l = self.cfg["loot"]
        self.state = State.SKINNING
        self.inp.sleep(float(l.get("skin_delay_ms", 300)) / 1000.0)
        key = self._interact_key()
        if obs.in_combat or self._being_hit(obs):
            self.log("🔪 进战斗/挨打了 → 不剥皮（战斗中不按互动键），先打怪")
            self.state = State.SEARCHING
            return
        self._ctx("剥皮（交互键第 2 次）")
        self.inp.press(key)
        self._bag_press()
        self.bag_probe = False
        self.skin_t0 = self.clock()
        self.skin_seen_cast = False
        self.log(f"🔪 剥皮：再按一次交互键「{key}」，等读条（约 {l.get('skin_cast_s', 2.0)} 秒，期间不 Tab）")

    def _skin(self, obs, now):
        """v8.2：剥皮读条中，不 Tab；被打就放弃；读完（或超时）再决定回血还是找下一只。"""
        l = self.cfg["loot"]
        el = now - self.skin_t0
        if obs.in_combat and el > 0.3:
            self.bag_probe = False
            self.log("🔪 剥皮时进入战斗，先打怪（这具尸体脱战后再捡/剥）")
            self.pending_loot = min(self.pending_loot + 1, 5)
            self.pending_loot_t = now
            self.last_kill_t = -1e9
            self.state = State.SEARCHING
            return
        if self._being_hit(obs):
            why = f"血量掉了 {self.hp_drop:.0f}%" if self.hp_drop >= 2 else "有怪在打我"
            self.bag_probe = False
            self.log(f"🔪 剥皮被打断（{why}），先打怪")
            self.last_kill_t = -1e9
            self.state = State.SEARCHING
            return
        if obs.error == "form":
            self._warn("skinform", "⚠️ 剥皮报错“形态不对”：这个服务器熊形态下不能剥皮，可以把“剥皮”关掉", 120)
        if obs.casting:
            if not self.skin_seen_cast:
                self.dlog(f"[剥皮] 读条中（{el:.1f} 秒）")
            self.skin_seen_cast = True
            if el < float(l.get("skin_max_s", 5.0)):
                return
        elif obs.casting is None or not self.skin_seen_cast:
            # 读不到施法，或者还没看到读条：按估计时间等
            if el < (1.5 if self.bag_probe else float(l.get("skin_cast_s", 2.0)) + 0.5):
                return
        if self.bag_probe:
            self.bag_probe = False
            self._bag_settle(obs, now)
            if not self.bag_full and not self.bag_skip and self._loot_on() and l.get("skin", True) \
                    and self._interact_key():
                return self._start_skin(obs)        # 这一下有反应 → 照常剥皮
            return self._after_loot(obs)
        if self.skin_seen_cast and not obs.casting:
            self.log(f"🔪 剥皮完成（{el:.1f} 秒）")
        elif obs.casting:
            self.log(f"🔪 剥皮 {el:.1f} 秒还在读条，不等了")
        else:
            self.log(f"🔪 没看到剥皮读条（可能不能剥/已经剥过/距离不够），{el:.1f} 秒后继续")
        self.inp.sleep(0.4)     # 剥皮的东西自动拾取
        self._bag_settle(obs, now)
        if self.bag_skip and obs.target_kind == "dead" and not self.stationary:
            self._clear_target(obs)
        self._after_loot(obs)

    # ------------------------------------------------------------ v8.29 背包满检测
    def _bag_on(self) -> bool:
        return bool(self.cfg["loot"].get("bagfull_detect", True))

    def _bag_n(self) -> int:
        return max(1, int(self.cfg["loot"].get("bagfull_presses", 5)))

    def _loot_on(self) -> bool:
        return bool(self.cfg["loot"].get("enabled", True)) and not self.bag_full

    def _bag_skip_corpse(self) -> bool:
        if self.bag_full:
            return True
        return self.bag_skip

    def _bag_press(self):
        """脱战时每按一次拾取/剥皮的互动键记一次（战斗中从不按互动键）。"""
        now = self.clock()
        if self.bag_try is None:
            self.bag_try = {"t": now, "n": 0, "still": False, "resp": False}
        self.bag_try["n"] += 1
        self.bag_try["t"] = now

    def _bag_watch(self, obs, now):
        """每帧看按键后的反应：有读条（剥皮）/ 尸体变成“不可拾取”（捡走了）= 有反应；
        尸体还是“可拾取” = 没反应的证据。进战斗 = 换了一具尸体，重新数。"""
        if obs.in_combat:
            if self.bag_try is not None or self.bag_noresp:
                self.dlog(f"[背包] 进战斗了，没反应计数清零（之前 {self.bag_noresp} 次）")
            self.bag_try = None
            self.bag_noresp = 0
            self.bag_skip = False
            return
        tr = self.bag_try
        if tr is None:
            return
        if obs.casting:
            tr["resp"] = True
        if obs.target_kind == "dead":
            if obs.lootable is False:
                tr["resp"] = True
            elif obs.lootable is True:
                tr["still"] = True
        if self.state != State.SKINNING and now - tr["t"] >= 3.0:
            self._bag_settle(obs, now)

    def _bag_settle(self, obs, now):
        tr, self.bag_try = self.bag_try, None
        if tr is None or self.bag_full:
            return
        if not self._bag_on():
            self.bag_noresp = 0
            return
        if tr["resp"]:
            if self.bag_noresp or self.bag_corpses:
                self.dlog(f"[背包] 按互动键有反应（读条/尸体捡空了）→ 没反应计数清零（之前 {self.bag_noresp} 次）")
            self.bag_noresp = 0
            self.bag_corpses = 0
            return
        if not tr["still"]:
            self.dlog("[背包] 按完互动键看不到尸体（没选中），不知道有没有反应 → 这次不算")
            return
        self.bag_noresp += tr["n"]
        n = self._bag_n()
        self.dlog(f"[背包] 按了 {tr['n']} 次互动键：没读条、尸体还是“可拾取”（报错={obs.error or '-'}）"
                  f" → 连续没反应 {self.bag_noresp}/{n} 次")
        if self.bag_noresp >= n:
            self._bag_hit(obs, now)

    def _bag_hit(self, obs, now):
        n, cnt = self._bag_n(), self.bag_noresp
        self.bag_noresp = 0
        self.bag_skip = True                 # 放弃这具尸体，进下一场战斗前不再捡
        self.pending_loot = 0
        need = 2 if self.cfg["loot"].get("bagfull_two_corpses", False) else 1
        self.bag_corpses += 1
        if self.bag_corpses < need:
            self.log(f"🎒 连按互动键 {cnt} 次没反应 → 放弃这具尸体（开了“两具尸体确认”：下一具也这样才认定背包满）")
        else:
            self.bag_full = True
            self.log(f"🎒 连按互动键 {cnt} 次没反应 → 认定背包满了，关掉拾取和剥皮，只打怪"
                     f"（这次运行有效；清完背包后停止再开始就恢复）")
            self._event("bag_full", f"连按互动键 {cnt} 次没反应（报错={obs.error or '-'}）")
        if obs.target_kind == "dead" and not self.stationary:
            self._clear_target(obs)


    # ------------------------------------------------------------ v8.4 buff：荆棘术 / 野性印记
    BUFF_NAMES = {"thorns": "荆棘术", "motw": "野性印记"}

    def _buff_status(self, obs, name):
        return getattr(obs, f"buff_{name}", None)

    def _buffs_needed(self, obs, now) -> list:
        b, k = self.cfg.get("buffs", {}), self.cfg["keys"]
        if not b.get("enabled", True):
            return []
        need = []
        for name in ("thorns", "motw"):
            cn = self.BUFF_NAMES[name]
            if not b.get(name, True) or not (k.get(name) or "").strip():
                continue
            if now < self.buff_retry_until.get(name, -1e9):
                continue
            st = self._buff_status(obs, name)
            if st == "yes":
                self.buff_tries[name] = 0
                continue
            if st == "unlearned":
                self._warn(f"unlearned_{name}", f"ℹ️ 插件说还没学会{cn}，不补（不想看到这条可以在“基本设置”关掉它）", 600)
                continue
            if st is None:
                self._warn("buffoldaddon", "⚠️ 插件太旧，读不到 buff：请复制 BotStatus v0.8 并在游戏里 /reload", 600)
                continue
            if st == "unknown":
                mins = float(b.get(f"fallback_min_{name}", 0) or 0)
                if mins <= 0:
                    self._warn(f"buffunk_{name}", f"ℹ️ 插件读不到{cn}（游戏限制），按时间补也关着 → 不补", 600)
                    continue
                if now - self.buff_last_cast.get(name, -1e9) < mins * 60:
                    continue
                self._warn(f"buffunk_{name}", f"ℹ️ 插件读不到{cn}（游戏限制）→ 改成每 {mins:.0f} 分钟补一次", 600)
            elif st == "low" and not b.get("refresh_low", True):
                continue
            need.append(name)
        return need

    def _maybe_buff(self, obs, now, why="脱战、没目标") -> bool:
        need = self._buffs_needed(obs, now)
        if not need:
            return False
        b = self.cfg.get("buffs", {})
        mana, mn = obs.mana, float(b.get("min_mana", 20))
        names = "、".join(self.BUFF_NAMES[n] for n in need)
        if mana is not None and 0 < mana < mn:
            for n in need:
                self.buff_retry_until[n] = now + float(b.get("retry_s", 30))
            self.log(f"✨ 缺 {names}，但法力 {mana:.0f}% < {mn:.0f}%，{b.get('retry_s', 30)} 秒后再看")
            return False
        short = self._mana_short(obs, "buff")
        if short:
            for n in need:
                self.buff_retry_until[n] = now + float(b.get("retry_s", 30))
            self.log(f"✨ 缺 {names}，但{short}，{b.get('retry_s', 30)} 秒后再看")
            return False
        st = {n: self._buff_status(obs, n) for n in need}
        self.log(f"✨ {why} → 补 buff：{names}（插件：" + "，".join(f"{self.BUFF_NAMES[n]}={st[n]}" for n in need) + "）")
        self.state = State.BUFFING
        self.buff_queue = list(need)
        self.buff_press_t = None
        self.buff_t0 = now
        self.buff_next = -1e9
        return True

    def _buff_done(self, obs, why, fight=False):
        self.buff_queue = []
        self.buff_press_t = None
        self.state = State.SEARCHING
        if self.pending_loot and not fight and not obs.in_combat:
            self.log(f"✨ 补 buff 结束（{why}），先用人形拾取/剥皮 {self.pending_loot} 具尸体，捡完再变{self._cform_cn()}（少变一次形）")
            self.caster_hold = True
            return
        self.log(f"✨ 补 buff 结束（{why}），变回{self._cform_cn()}")
        self._to_bear(obs)

    def _buff_step(self, obs, now):
        b, k = self.cfg.get("buffs", {}), self.cfg["keys"]
        if obs.in_combat and (self._being_hit(obs) or (obs.target_exists and self._aggro(obs))):
            self.last_kill_t = -1e9
            return self._buff_done(obs, "被打了，先打怪", fight=True)
        if now - self.buff_t0 > 25:
            return self._buff_done(obs, "超时 25 秒")
        if not self.buff_queue:
            return self._buff_done(obs, "都补好了")
        name = self.buff_queue[0]
        cn, key = self.BUFF_NAMES[name], k.get(name)
        st = self._buff_status(obs, name)
        wait = float(b.get("cast_wait_s", 2.0))
        if self.buff_press_t is not None:
            el = now - self.buff_press_t
            if st == "yes":
                self._learn_cost("buff", self.buff_mana0, obs, lo=2, hi=40)
                self.log(f"✅ {cn} 已经上了（{el:.1f} 秒）")
                self.buff_tries[name] = 0
                self.buff_last_cast[name] = now
                self.buff_queue.pop(0)
                self.buff_press_t = None
                self.buff_next = now + 0.3
                return
            if el < wait:
                return
            if st == "unknown":
                self.log(f"✨ {cn} 按过了（插件读不到 buff，按时间算，{b.get(f'fallback_min_{name}', 0)} 分钟后再补）")
                self.buff_last_cast[name] = now
                self.buff_queue.pop(0)
                self.buff_press_t = None
                return
            self.buff_tries[name] = self.buff_tries.get(name, 0) + 1
            self.buff_press_t = None
            if self.buff_tries[name] >= int(b.get("max_tries", 3)):
                self.buff_retry_until[name] = now + float(b.get("giveup_s", 300))
                self.buff_tries[name] = 0
                self.log(f"⚠️ {cn} 按了 {b.get('max_tries', 3)} 次都没上（键位「{key}」对吗？报错={obs.error}），"
                         f"{b.get('giveup_s', 300)} 秒内不再补")
                self.buff_queue.pop(0)
            else:
                self.dlog(f"[buff] {cn} {wait:.1f} 秒后还没上（插件={st}，报错={obs.error}），再按一次")
            return
        if st == "yes":
            self.dlog(f"[buff] {cn} 身上已经有了")
            self.buff_queue.pop(0)
            return
        short = self._mana_short(obs, "buff")
        if short and not obs.casting:
            for n in self.buff_queue:
                self.buff_retry_until[n] = now + float(b.get("retry_s", 30))
            return self._buff_done(obs, f"{short}，留着蓝变身，{b.get('retry_s', 30)} 秒后再补")
        if self._is_bear(obs):
            if now - self.heal_last_shift >= 1.0:
                self.heal_last_shift = now
                self.dlog(f"[buff] 退出{self._cform_cn()}形态（荆棘术/野性印记要人形放）")
                self._to_caster(obs)
            return
        if obs.casting or now < self.buff_next:
            return
        mana, mn = obs.mana, float(b.get("min_mana", 20))
        if mana is not None and 0 < mana < mn:
            for n in self.buff_queue:
                self.buff_retry_until[n] = now + float(b.get("retry_s", 30))
            return self._buff_done(obs, f"法力 {mana:.0f}% 不够，{b.get('retry_s', 30)} 秒后再补")
        self._ctx(f"补 buff：{cn}（第 {self.buff_tries.get(name, 0) + 1} 次）")
        self.inp.press(key)
        self.buff_press_t = now
        self.buff_mana0 = mana
        self.buff_next = now + 1.6
        self.log(f"✨ 补 {cn}：按「{key}」（第 {self.buff_tries.get(name, 0) + 1} 次，法力 {mana}）")


__all__ = ["Grinder", "State", "StopRequested"]

"""Tkinter 设置界面。"""
from __future__ import annotations

import copy
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from bot.config import (CONFIG_PATH, IMAGES_DIR, NAMEPLATE_DIRS, image_path, cat_missing,
                        list_nameplate_templates, load_config, missing_required, region_set,
                        save_config)
from bot.imgio import crop, imread, imwrite

from .region_selector import _photo_from_bgr, select_region

REGION_LABELS = [
    ("player_hp", "自己血条", None),
    ("target_hp", "目标血条", None),
    ("combat", "战斗标志搜索区", "combat.png"),
    ("dead", "死亡对话框搜索区", "dead.png"),
    ("target_frame", "目标框搜索区(可选)", "target_frame.png"),
    ("action_bar", "动作条(技能图标)", None),
    ("nameplates", "姓名板搜索区", None),
]
SKILL_COLS = ("name", "image", "key", "priority", "threshold", "cooldown_ms", "enabled")
SKILL_HEAD = ("名称", "图片文件", "按键", "优先级", "匹配阈值", "最短间隔ms", "启用")


def _fmt(v) -> str:
    return ",".join(str(int(x)) for x in v)


def _parse_ints(text: str, n: int) -> list[int]:
    parts = [p for p in text.replace("，", ",").replace(" ", ",").split(",") if p.strip()]
    vals = [int(float(p)) for p in parts]
    if len(vals) != n:
        raise ValueError(f"需要 {n} 个数字：{text}")
    return vals


# v8.25：调参用的设置项收进每页底部可折叠的“高级设置”（平时不用改）
ADVANCED = {
    ("keys", k) for k in ("loot", "turn", "forward", "turn_left", "turn_right", "jump", "strafe")
} | {
    ("heal", "cast_s"), ("heal", "cancel_form_first"), ("heal", "heal_cooldown_s"),
    ("loot", "bagfull_presses"), ("loot", "bagfull_two_corpses"),
    ("corpse", "safe_rescan_s"), ("corpse", "near_pct"), ("corpse", "arrive_dist"), ("corpse", "stuck_s"),
    ("corpse", "retry_s"),
    ("loot", "delay_ms"), ("loot", "wait_ms"), ("loot", "skin_cast_s"), ("loot", "residual_wait_s"),
    ("buffs", "retry_s"), ("buffs", "max_tries"), ("buffs", "fallback_min_thorns"), ("buffs", "fallback_min_motw"),
    ("addon", "x"), ("addon", "y"), ("addon", "block"), ("addon", "calib_tolerance"), ("addon", "stale_s"),
    ("heal", "reserve_learn"), ("heal", "heal_cost_pct"), ("heal", "buff_cost_pct"), ("heal", "rejuv_cost_pct"),
    ("detect", "combat_mode"), ("detect", "combat_color_ratio"), ("detect", "template_threshold"),
    ("detect", "bar_mode"), ("detect", "column_fill_ratio"), ("detect", "dead_pct"),
    ("rotation", "brightness_ratio"),
    ("cat", "finisher_after_builders"), ("cat", "builder_energy"), ("cat", "finisher_energy"),
    ("cat", "rake_energy"), ("cat", "press_interval_s"),
    ("nameplate", "click_offset_y"), ("nameplate", "max_click_fails"), ("scan", "tap_ms"), ("scan", "settle_ms"),
    ("move", "approach_timeout_s"), ("nameplate", "threshold_selected"), ("nameplate", "threshold_unselected"),
    ("nameplate", "multiscale"), ("nameplate", "scales"), ("nameplate", "downscale"),
    ("nameplate", "max_offscreen_turns"), ("stationary", "plate_click_button"), ("stationary", "plate_max_fails"),
    ("stationary", "plate_restore_mouse"),
    ("nameplate", "skip_top_pct"), ("nameplate", "stop_settle_s"), ("nameplate", "confirm_px"), ("nameplate", "verify_s"),
    ("nameplate", "bad_s"), ("nameplate", "bad_radius_px"), ("nameplate", "patrol_frames"),
    ("nameplate", "far_walk_max_s"), ("nameplate", "far_lost_s"), ("nameplate", "far_skip_s"), ("nameplate", "far_turn_deg"),
    ("scan", "spin_deg"), ("scan", "spin_near_ratio"), ("scan", "spin_wait_s"), ("area", "arrive_ratio"),
    ("area", "turn_rate_dps"), ("area", "face_tolerance_deg"), ("area", "walk_correct_deg"),
    ("area", "return_timeout_s"), ("area", "stuck_s"),
    ("stationary", "face_interval_s"), ("stationary", "face_settle_s"), ("stationary", "face_center_deg"),
    ("stationary", "face_small_steps"), ("stationary", "face_quiet_s"), ("stationary", "no_damage_offcenter_s"),
    ("stationary", "face_max_attempts"), ("stationary", "no_damage_s"), ("stationary", "use_marker"),
    ("stationary", "face_ok_deg"), ("stationary", "fov_deg"), ("stationary", "turn_rate_dps"),
    ("humanize", "move_min_ms"), ("humanize", "move_max_ms"), ("humanize", "back_ratio"),
    ("recover", "first_damage_s"), ("recover", "check_interval_s"), ("recover", "turn_ms"), ("recover", "back_ms"),
    ("recover", "skip_s"), ("face_click", "button"), ("face_click", "cooldown_s"), ("face_click", "max_clicks"),
    ("face_click", "restore_mouse"), ("face_click", "offset_y"), ("face_click", "melee_cycles"),
    ("safety", "delay_min_ms"), ("safety", "delay_max_ms"), ("safety", "hold_min_ms"), ("safety", "hold_max_ms"),
    ("safety", "tick_ms"), ("safety", "death_hp_seconds"), ("misc", "capture_delay_s"), ("misc", "debug_preview"),
    ("log", "keep_shots"),
}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WowGrinder 熊德自动打怪 v8.48")
        # v8.16：按屏幕大小开窗口（125%/150% 缩放时 940 高会超出屏幕），可以拉伸
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1100, sw - 40)}x{min(940, sh - 90)}+10+10")
        self.minsize(640, 420)
        self._pages: list[tk.Canvas] = []
        self._tab_outer: dict[str, ttk.Frame] = {}
        self.cfg = load_config()
        self.fields: dict[tuple[str, str], tuple[tk.Variable, str]] = {}
        self.region_vars: dict[str, tk.StringVar] = {}
        self.color_vars: dict[str, tk.StringVar] = {}
        self.msgq: queue.Queue = queue.Queue()
        self.runner = None
        self.hotkeys = None

        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        nb = ttk.Notebook(self)
        self.nb = nb
        self._tab_basic(nb)
        self._tab_stationary(nb)
        self._tab_regions(nb)
        self._tab_skills(nb)
        self._tab_nameplates(nb)
        self._tab_area(nb)
        self._tab_corpse(nb)
        self._tab_recover(nb)
        self._tab_safety(nb)
        self._place_advanced()
        self._sync_mode_tabs()
        self.fields[("move", "mode")][0].trace_add("write", lambda *a: self._sync_mode_tabs())
        self._bottom()
        nb.pack(side="top", fill="both", expand=True, padx=6, pady=6)   # v8.16：底部先占位，设置页占剩下的高度并可滚动
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.bind_all("<Button-4>", self._on_wheel, add="+")
        self.bind_all("<Button-5>", self._on_wheel, add="+")
        for cls in ("TCombobox", "TCheckbutton", "TEntry"):          # 鼠标停在下拉框上滚动：滚页面，不改选项
            self.bind_class(cls, "<MouseWheel>", self._on_wheel_break)
        self._start_hotkeys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        self.log("配置已读取：" + CONFIG_PATH)

    # ================================================================ 小工具
    def _page(self, nb, text):
        """v8.16：每个设置页放进可滚动的画布（竖向 + 横向滚动条，鼠标滚轮在页面任何位置都能滚）。"""
        outer = ttk.Frame(nb)
        nb.add(outer, text=text)
        self._tab_outer[text] = outer
        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
        try:
            bg = ttk.Style(self).lookup("TFrame", "background")
            if bg:
                canvas.configure(background=bg)
        except tk.TclError:
            pass
        vs = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        hs = ttk.Scrollbar(outer, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas, padding=8)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e, c=canvas: c.configure(scrollregion=c.bbox("all")))
        canvas.bind("<Configure>", lambda e, c=canvas, w=win, i=inner:
                    c.itemconfigure(w, width=max(e.width, i.winfo_reqwidth())))
        canvas._inner = inner
        self._pages.append(canvas)
        return inner

    def _page_canvas_at(self, widget):
        w = widget
        while w is not None:
            if w in self._pages:
                return w
            w = getattr(w, "master", None)
        return None

    def _wheel_units(self, e):
        if getattr(e, "num", None) == 4:
            return -3
        if getattr(e, "num", None) == 5:
            return 3
        d = getattr(e, "delta", 0) or 0
        return -max(1, abs(d) // 40) if d > 0 else (max(1, abs(d) // 40) if d < 0 else 0)

    def _on_wheel(self, e):
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except Exception:
            return
        if w is None or isinstance(w, (tk.Text, tk.Listbox)) or w.winfo_class() in ("Treeview", "Text", "Listbox"):
            return                                    # 日志框/技能表/列表自己滚
        c = self._page_canvas_at(w)
        if c is not None and c.yview() != (0.0, 1.0):
            c.yview_scroll(self._wheel_units(e), "units")

    def _on_wheel_break(self, e):
        self._on_wheel(e)
        return "break"

    def _adv_body(self, parent):
        """v8.25：这一页底部的“高级设置”折叠区（第一次用到时创建，建完所有页后再摆到页底）。"""
        c = self._page_canvas_at(parent)
        if c is None:
            return None
        adv = getattr(c, "_adv", None)
        if adv is None:
            box = ttk.Frame(c._inner)
            body = ttk.Frame(box, padding=(12, 2))
            body._n = 0
            btn = ttk.Button(box, text="▶ 高级设置（调参用，平时不用改）")
            btn.configure(command=lambda b=body, bt=btn: self._toggle_adv(b, bt))
            btn.pack(side="top", anchor="w", pady=(10, 2))
            adv = c._adv = (box, body, btn)
        return adv[1]

    def _toggle_adv(self, body, btn, show=None):
        show = (not getattr(body, "_shown", False)) if show is None else show
        body._shown = bool(show)
        if show:
            body.pack(side="top", fill="x")
            btn.configure(text="▼ 高级设置（调参用，平时不用改）")
        else:
            body.pack_forget()
            btn.configure(text="▶ 高级设置（调参用，平时不用改）")

    def _place_advanced(self):
        for c in self._pages:
            adv = getattr(c, "_adv", None)
            if adv is None:
                continue
            inner = c._inner
            if inner.grid_slaves():
                adv[0].grid(row=1000, column=0, columnspan=10, sticky="we")
            else:
                adv[0].pack(side="top", fill="x")

    def _sync_mode_tabs(self):
        """v8.25：点击移动模式下隐藏“原地打怪”页（那页只管原地模式）。"""
        tab = self._tab_outer.get("原地打怪")
        if tab is None:
            return
        try:
            click = self.fields[("move", "mode")][0].get() == "click"
            self.nb.tab(tab, state="hidden" if click else "normal")
        except tk.TclError:
            pass

    def _field(self, parent, row, label, section, key, kind="str", choices=None, tip="", col=0):
        if (section, key) in ADVANCED:
            body = self._adv_body(parent)
            if body is not None:
                parent, row, col = body, body._n, 0
                body._n += 1
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=4, pady=3)
        val = self.cfg[section][key]
        if kind == "bool":
            var = tk.BooleanVar(value=bool(val))
            w = ttk.Checkbutton(parent, variable=var)
        else:
            var = tk.StringVar(value=str(val))
            if choices:
                w = ttk.Combobox(parent, textvariable=var, values=choices, width=14, state="readonly")
            else:
                w = ttk.Entry(parent, textvariable=var, width=16)
        w.grid(row=row, column=col + 1, sticky="w", padx=4)
        if tip:
            ttk.Label(parent, text=tip, foreground="#666").grid(row=row, column=col + 2, sticky="w")
        self.fields[(section, key)] = (var, kind)
        return var

    def collect(self) -> dict:
        """把界面上的值写回 self.cfg。数字格式不对会抛 ValueError。"""
        cfg = self.cfg
        for (section, key), (var, kind) in self.fields.items():
            v = var.get()
            if kind == "int":
                v = int(float(v))
            elif kind == "float":
                v = float(v)
            elif kind == "bool":
                v = bool(v)
            else:
                v = str(v).strip()
            cfg[section][key] = v
        for key, var in self.region_vars.items():
            cfg["regions"][key] = _parse_ints(var.get(), 4)
        for key, var in self.color_vars.items():
            cfg["colors"][key] = _parse_ints(var.get(), 3)
        cfg["rotation"]["skills"] = self._skills_from_tree()
        return cfg

    def log(self, text: str):
        """线程安全。"""
        self.msgq.put(("log", f"[{time.strftime('%H:%M:%S')}] {text}"))

    def status(self, st: dict):
        self.msgq.put(("status", st))

    def _poll(self):
        try:
            while True:
                kind, payload = self.msgq.get_nowait()
                if kind == "log":
                    self.logbox.insert("end", payload + "\n")
                    self.logbox.see("end")
                elif kind == "status":
                    self._show_status(payload)
                elif kind == "call":
                    payload()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ================================================================ 基本设置
    def _tab_basic(self, nb):
        f = self._page(nb, "基本设置")
        keys = [
            ("target", "选怪键（Tab）", "游戏里绑定“选择最近的敌人”，默认 Tab"), ("attack", "开怪键", "建议放月火术等远程技能，能把怪拉过来"),
            ("bear_form", "熊形态键", ""), ("cancel_form", "取消变形键", "默认=熊形态键（再按一次退出熊）；退不出就做 /cancelform 宏填那个键"),
            ("healing_touch", "治疗之触键（生命之触）", "战斗中低血 + 战后血量 < 70% 用；宏里自带取消变形就行"),
            ("rejuvenation", "回春术键", "v8.27 战后血量 70%-99% 按它（宏：自带取消变形），默认 g"),
            ("thorns", "荆棘术键", "脱战没目标时检查，没有就人形补上（默认 r）"),
            ("motw", "野性印记键", "同上（默认 t）"),
            ("back", "后退键", "战斗补救：后退半步（默认 s）"),
            ("loot", "拾取键", "没填“与目标互动键”时才用它；也可填 rightclick（会点在鼠标位置）"),
            ("interact", "与目标互动键", "v8.27 默认 `（Esc 下面那个键，填 · 也行）：拾取、剥皮都按它；战斗中绝不按"),
            ("scan_turn", "找怪转向键", "默认 right（小键盘右/方向键右）；绑了小键盘6就填 num6"),
            ("target_attacker", "选择攻击我的怪键", "战斗中没目标/目标不对时先按它（宏：/cleartarget 换行 /targetenemy），留空=只用 Tab"),
            ("clear_target", "清除目标键", "建议做 /cleartarget 宏放 F3，填 f3（Esc 没目标时会打开游戏菜单）；留空=不按"),
            ("turn", "备用转向键", "“找怪转向键”没填时用它"),
            ("hotkey_stop", "紧急停止热键", "默认 f12"), ("hotkey_toggle", "开始/暂停热键", "默认 f10"),
            ("hotkey_area_check", "检查范围热键", "默认 f9：在游戏里按，日志+状态栏显示在不在范围内"),
            ("hotkey_set_home", "设范围中心热键", "默认 f8：把当前位置设为范围中心（有多中心时：清空列表，当前位置 = 中心 #1）"),
            ("hotkey_add_center", "添加中心热键", "默认 f7：把当前位置添加为下一个中心（多中心巡逻 1→2→3→2→1 来回走）"),
            ("hotkey_add_route", "添加跑尸路线点热键", "默认 f6：把当前位置添加为下一个跑尸路线点（从复活点开始按顺序记，见“跑尸”页）"),
            ("cat_form", "猎豹形态键", "战斗形态=猎豹 时必填"),
            ("cat_builder", "爪击键（攒连击点）", "猎豹必填"),
            ("cat_rake", "斜掠键", "可选：每只怪开头放一次"),
            ("cat_finisher", "终结技键", "猎豹必填：凶猛撕咬 或 撕扯"),
            ("cat_prowl", "潜行键", "可选：配合“先潜行”"),
            ("cat_tigers_fury", "猛虎之怒键", "可选：开打时放一次"),
            ("spirit", "释放灵魂/复活键", "v8.31 自动跑尸：游戏里做宏放到这个键（默认 ]），宏内容见“跑尸”页"),
        ]
        for i, (k, label, tip) in enumerate(keys):
            self._field(f, i, label, "keys", k, tip=tip)
        r = len(keys)
        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
        self._field(f, r + 1, "治疗之触读条(秒)", "heal", "cast_s", "float", tip="读条期间不移动；默认 3.0")
        self._field(f, r + 2, "先取消熊形态", "heal", "cancel_form_first", "bool")
        self._field(f, r + 3, "危险停止线(%)", "heal", "critical_pct", "int", tip="治疗过后仍低于此血量 → 停止 + 提示音")
        self._field(f, r + 5, "两次治疗最短间隔(秒)", "heal", "heal_cooldown_s", "float")
        self._field(f, r + 6, "启用拾取", "loot", "enabled", "bool")
        self._field(f, r + 7, "怪死后等待(ms)", "loot", "delay_ms", "int")
        self._field(f, r + 8, "拾取后等待(ms)", "loot", "wait_ms", "int")
        self._field(f, r + 9, "回血最长(秒)", "heal", "rest_timeout_s", "float", tip="超时还没回到目标血量 → 不等了，变回熊继续（不停止）")
        self._field(f, r + 10, "回血最低法力(%)", "heal", "rest_min_mana", "float", tip="插件读到法力低于此值就等回蓝")
        self._field(f, r + 11, "剥皮", "loot", "skin", "bool", tip="拾取完再按一次交互键剥皮")
        self._field(f, r + 12, "剥皮读条(秒)", "loot", "skin_cast_s", "float", tip="读条期间不 Tab；被打就放弃")
        self._field(f, r + 22, "背包满检测", "loot", "bagfull_detect", "bool",
                    tip="v8.29：脱战后连按互动键没反应（没读条、尸体一直可拾取）→ 认定背包满，这次运行关掉拾取/剥皮，只打怪")
        self._field(f, r + 23, "连续几次没反应算满", "loot", "bagfull_presses", "int", tip="默认 5（拾取+剥皮各算一次）")
        self._field(f, r + 24, "两具尸体都没反应才算满", "loot", "bagfull_two_corpses", "bool",
                    tip="更保险：第一具先放弃，下一具也连按没反应才认定背包满（默认关）")
        self._field(f, r + 26, "背包满了只捡钱（v8.48）", "loot", "full_bag_money_only", "bool",
                    tip="背包满了还按互动键拾取（钱自动进包），物品不等、马上关拾取窗口；不剥皮；捡过的尸体 5 分钟内不再捡。关掉 = 以前的只打怪")
        self._field(f, r + 13, "脱战低于多少才回血(%)", "heal", "rest_below_pct", "int",
                    tip="高于这个血量就保持熊形态直接找下一只（默认 60）")
        self._field(f, r + 14, "没目标时点姓名板选怪", "stationary", "plate_click", "bool",
                    tip="用“姓名板”页录的“未选中”模板；没录就只按 Tab")
        self._field(f, r + 16, "杀完怪残留战斗最多等(秒)", "loot", "residual_wait_s", "float",
                    tip="v8.17：杀完怪还显示战斗中但没掉血 → 等这么久再按 x 找怪（掉血马上找）；战斗中一律不拾取/剥皮")
        self._field(f, r + 18, "战后治疗（每次战斗结束）", "heal", "post_enabled", "bool",
                    tip="v8.27：血量 < 生命之触线 → 生命之触（读完再打）；< 回春术线 → 回春术；更高不治疗；蓝不够变身就跳过")
        self._field(f, r + 19, "战后放回春术", "heal", "post_rejuv_enabled", "bool",
                    tip="关 = 战后只在血量 < 生命之触线(默认70%)时放生命之触，高于就直接继续打；战斗中紧急治疗不受影响")
        self._field(f, r + 21, "回春术冷却：几秒内不重复放(秒)", "heal", "rejuv_no_repeat_s", "float",
                    tip="v8.30：上次回春术放完不到这么多秒（身上还有）→ 战后不再放、也不变形；默认 12，0 = 不限制")
        self._field(f, r + 20, "低于多少用生命之触(%)", "heal", "post_ht_below_pct", "int",
                    tip="v8.47 默认 60；开了战后治疗，上面的“脱战低于多少才回血/回血最长”就不用了")
        self._field(f, r + 25, "低于多少用回春术(%)（v8.47）", "heal", "post_rejuv_below_pct", "int",
                    tip="战后血量在 生命之触线 ~ 这个 之间放回春术；≥ 这个就不治疗（默认 85）")
        self._field(f, r + 17, "移动方式", "move", "mode", choices=["stationary_tab", "click"],
                    tip="click=点击移动走过去（你现在用的）/ stationary_tab=原地 + Tab（选 click 时隐藏“原地打怪”页）")
        ttk.Label(f, text="—— 自动补 buff（需要插件 v0.8）——").grid(row=r + 1, column=3, columnspan=2, sticky="w", padx=8)
        self._field(f, r + 2, "自动补 buff", "buffs", "enabled", "bool", col=3)
        self._field(f, r + 3, "荆棘术", "buffs", "thorns", "bool", col=3)
        self._field(f, r + 4, "野性印记", "buffs", "motw", "bool", col=3)
        self._field(f, r + 5, "补 buff 最低法力(%)", "buffs", "min_mana", "float", col=3)
        self._field(f, r + 6, "没上/蓝不够 N 秒后再试", "buffs", "retry_s", "float", col=3)
        self._field(f, r + 7, "最多连按几次", "buffs", "max_tries", "int", col=3)
        self._field(f, r + 8, "读不到时每 N 分钟补荆棘", "buffs", "fallback_min_thorns", "float", col=3)
        self._field(f, r + 9, "读不到时每 N 分钟补印记", "buffs", "fallback_min_motw", "float", col=3)
        self._field(f, r + 15, "目标太远就清除目标", "stationary", "clear_far", "bool",
                    tip="插件距离块红（30 码外）→ 按清除目标键")

    # ================================================================ 血量与区域
    def _tab_regions(self, nb):
        outer = self._page(nb, "插件与区域")
        af = ttk.LabelFrame(outer, text="BotStatus 插件（优先使用；读不到心跳时才用下面的截图区域）", padding=6)
        af.pack(fill="x")
        self._field(af, 0, "读取插件色块", "addon", "enabled", "bool", tip="推荐开启")
        self._field(af, 1, "色块左上角 x", "addon", "x", "int")
        self._field(af, 1, "y", "addon", "y", "int", col=3)
        self._field(af, 2, "块边长(px)", "addon", "block", "int")
        self._field(af, 2, "校准灰容差", "addon", "calib_tolerance", "int", col=3)
        self._field(af, 3, "心跳超时(秒)", "addon", "stale_s", "float")
        f = ttk.Frame(outer)
        f.pack(fill="both", expand=True, pady=(6, 0))
        ttk.Label(f, text="备用截图识别区域，格式：左,上,宽,高（屏幕像素）。0,0,0,0 = 未设置。姓名板搜索区必须设置。",
                  foreground="#666").grid(row=0, column=0, columnspan=5, sticky="w")
        for i, (key, label, tpl) in enumerate(REGION_LABELS, start=1):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=3)
            var = tk.StringVar(value=_fmt(self.cfg["regions"][key]))
            self.region_vars[key] = var
            ttk.Entry(f, textvariable=var, width=20).grid(row=i, column=1, sticky="w")
            ttk.Button(f, text="框选", command=lambda k=key, l=label: self.pick_region(k, l)).grid(row=i, column=2, padx=2)
            if tpl:
                ttk.Button(f, text=f"截取模板 {tpl}",
                           command=lambda t=tpl, l=label: self.capture_template(t, l)).grid(row=i, column=3, sticky="w", padx=2)
            if key in ("player_hp", "target_hp"):
                ttk.Button(f, text="自动取色(满血时)",
                           command=lambda k=key: self.auto_color(k)).grid(row=i, column=3, sticky="w", padx=2)

        r = len(REGION_LABELS) + 1
        ttk.Separator(f).grid(row=r, column=0, columnspan=5, sticky="ew", pady=6)
        colors = [("player_hp_lo", "自己血条颜色 HSV 下限"), ("player_hp_hi", "自己血条颜色 HSV 上限"),
                  ("target_hp_lo", "目标血条颜色 HSV 下限"), ("target_hp_hi", "目标血条颜色 HSV 上限"),
                  ("combat_lo", "战斗颜色 HSV 下限"), ("combat_hi", "战斗颜色 HSV 上限")]
        for j, (key, label) in enumerate(colors, start=r + 1):
            ttk.Label(f, text=label).grid(row=j, column=0, sticky="w", padx=4, pady=2)
            var = tk.StringVar(value=_fmt(self.cfg["colors"][key]))
            self.color_vars[key] = var
            ttk.Entry(f, textvariable=var, width=20).grid(row=j, column=1, sticky="w")
        ttk.Label(f, text="H 0-179, S/V 0-255。默认绿色血条。", foreground="#666").grid(row=r + 1, column=2, columnspan=3, sticky="w")

        r2 = r + len(colors) + 1
        ttk.Separator(f).grid(row=r2, column=0, columnspan=5, sticky="ew", pady=6)
        g = ttk.Frame(f)
        g.grid(row=r2 + 1, column=0, columnspan=5, sticky="w")
        self._field(g, 0, "战斗中治疗线(%)", "heal", "heal_pct", "int", tip="低于此血量切人形放治疗之触（v8.46 默认 40）")
        self._field(g, 0, "战斗中治疗回复线(%)", "heal", "heal_to_pct", "int", col=3,
                    tip="战斗治疗开始后连续放治疗之触，直到血量 ≥ 此值再变回熊（默认 90）")
        self._field(g, 1, "战斗治疗最多连放(个)", "heal", "heal_chain_max", "int", col=3, tip="防止一直放（默认 6）")
        self._field(g, 2, "战斗治疗最低法力(%)", "heal", "heal_min_mana", "float", col=3, tip="法力低于此值就不再连放，变回熊")
        self._field(g, 3, "变猎豹预留法力(%)", "heal", "reserve_cat_pct", "float", col=3,
                    tip="治疗/补buff不会把法力用到低于这条线，留着变回猎豹（默认 30；0=不预留）")
        self._field(g, 4, "变熊预留法力(%)", "heal", "reserve_bear_pct", "float", col=3, tip="同上，熊用（默认 30）")
        self._field(g, 5, "自动测变身耗蓝", "heal", "reserve_learn", "bool", col=3, tip="实测比上面高 → 用 实测+2")
        self._field(g, 6, "治疗之触耗蓝约(%)", "heal", "heal_cost_pct", "float", col=3, tip="初始估计，实测后自动更新（默认 20）")
        self._field(g, 7, "buff 耗蓝约(%)", "heal", "buff_cost_pct", "float", col=3, tip="初始估计，实测后自动更新（默认 10）")
        self._field(g, 8, "回春术耗蓝约(%)", "heal", "rejuv_cost_pct", "float", col=3, tip="v8.27 初始估计，实测后自动更新（默认 12）")
        self._field(g, 9, "变身余量(%)（v8.42）", "heal", "reserve_margin_pct", "float", col=3,
                    tip="治疗前预计“治疗后法力”要 ≥ 变身预留 + 这个余量才放，保证治完能变回猎豹（默认 3）")
        self._field(g, 10, "先回蓝再开怪（v8.46）", "heal", "mana_rest_enabled", "bool", col=3,
                    tip="打完怪 血低 + 蓝不够放一个治疗之触再变身 → 不拉新怪，等回蓝；被打照常迎战")
        self._field(g, 11, "血量低于(%)才回蓝", "heal", "mana_rest_hp_below", "float", col=3, tip="默认 60；血多就直接打")
        self._field(g, 12, "回蓝余量(%)", "heal", "mana_rest_margin_pct", "float", col=3,
                    tip="回到 变身预留 + 治疗之触耗蓝 + 这个（默认 10，约 60%）")
        self._field(g, 13, "回蓝时血回到(%)也算好", "heal", "mana_rest_hp_ok", "float", col=3, tip="默认 90")
        self._field(g, 14, "回蓝最多等(秒)", "heal", "mana_rest_timeout_s", "float", col=3, tip="默认 120；超时继续打，60 秒内不再停")
        self._field(g, 15, "喝水键", "heal", "drink_key", "str", col=3, tip="空 = 不喝水，原地等（不变形，省变身的蓝）")
        self._field(g, 16, "开怪最低法力(%)（v8.47）", "heal", "pull_min_mana_pct", "float", col=3,
                    tip="脱战时法力低于这个就不开新怪（Tab/看到的/远处的都不开），先回蓝到这个，不管血量；被打照常迎战。默认 50，0 = 不限制")
        self._field(g, 1, "战后回血到(%)", "heal", "rest_to_pct", "int", tip="血量低于“脱战低于多少才回血”时，人形回血到此血量")
        self._field(g, 2, "战斗识别方式", "detect", "combat_mode", choices=["template", "color", "none"],
                    tip="template=匹配 combat.png；color=区域内红色比例；none=不识别")
        self._field(g, 3, "颜色模式比例阈值", "detect", "combat_color_ratio", "float")
        self._field(g, 4, "模板匹配阈值", "detect", "template_threshold", "float", tip="0.75~0.85")
        self._field(g, 5, "血条计算方式", "detect", "bar_mode", choices=["rightmost", "count"])
        self._field(g, 6, "列填充比例", "detect", "column_fill_ratio", "float")
        self._field(g, 7, "视为 0 血的阈值(%)", "detect", "dead_pct", "float")

    # ================================================================ 技能循环
    def _tab_skills(self, nb):
        f = self._page(nb, "技能循环")
        top = ttk.Frame(f)
        top.pack(fill="x")
        self._field(top, 0, "循环方式", "rotation", "mode", choices=["template", "timed"],
                    tip="template=识别动作条图标是否亮起；timed=只按最短间隔轮转")
        self._field(top, 1, "变暗判定(亮度比)", "rotation", "brightness_ratio", "float",
                    tip="图标亮度低于模板的该比例→视为冷却中/怒气不够")

        cf = ttk.LabelFrame(f, text="战斗形态（v8.15）：熊 = 用下面的技能表；猎豹 = 用“按键”页的猎豹键自动轮转", padding=6)
        cf.pack(fill="x", pady=(6, 0))
        self._field(cf, 0, "战斗形态", "cat", "combat_form", choices=["熊", "猎豹"], tip="默认 熊")
        cat_items = [
            ("finisher_cp", "连击点 ≥ 几放终结技", "int", "插件 v0.9 读得到连击点时用"),
            ("finisher_after_builders", "读不到时每几次爪击放终结技", "int", "默认 4"),
            ("builder_energy", "爪击要的能量", "float", "读得到能量时才判断"),
            ("finisher_energy", "终结技要的能量", "float", ""),
            ("rake_energy", "斜掠要的能量", "float", ""),
            ("rake_every_s", "斜掠每隔(秒)", "float", "0 = 每只怪只放一次"),
            ("tf_every_s", "猛虎之怒每隔(秒)", "float", "0 = 开打时一次"),
            ("press_interval_s", "按技能最短间隔(秒)", "float", "默认 1.0（公共冷却）"),
            ("use_prowl", "先潜行再开怪", "bool", "默认关；开了：潜行后按开怪键 f"),
        ]
        for i, (k, label, kind, tip) in enumerate(cat_items, start=1):
            self._field(cf, i if i <= 5 else i - 5, label, "cat", k, kind, tip=tip, col=0 if i <= 5 else 3)

        mf = ttk.LabelFrame(f, text="被多只怪打时变熊（v8.38，战斗形态=猎豹时才用）：判断同时被多只怪打 → 变熊抗；"
                                    "只剩 1 只或脱战 → 变回猎豹", padding=6)
        mf.pack(fill="x", pady=(6, 0))
        mb_items = [
            ("enabled", "被多只怪打时变熊", "bool", "默认开。熊形态键用“按键”页的熊形态键；战斗中低血治疗照旧（退熊 → 治疗之触 → 变回熊）"),
            ("maul_key", "熊：重殴键", "str", "熊形态动作条上 重殴 的键（熊形态会换一页动作条），默认 1"),
            ("swipe_key", "熊：横扫键", "str", "可选，留空 = 不用"),
            ("fast_drop_pct", "几秒内掉血多少算多只(%)", "int", "默认 20：5 秒内掉血 ≥ 20% 就算被多只打"),
            ("min_mana_pct", "法力低于多少不变熊(%)", "int", "默认 40：变熊、之后变回猎豹/治疗都要蓝"),
            ("calm_s", "多少秒没再判断到就变回猎豹", "float", "默认 8：变熊后杀了一只，且这么多秒没再判断到多只 → 变回猎豹"),
        ]
        for i, (k, label, kind, tip) in enumerate(mb_items):
            self._field(mf, i if i < 3 else i - 3, label, "multi_bear", k, kind, tip=tip, col=0 if i < 3 else 3)

        self.tree = ttk.Treeview(f, columns=SKILL_COLS, show="headings", height=7)
        for c, h in zip(SKILL_COLS, SKILL_HEAD):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=90 if c != "image" else 170, anchor="center")
        self.tree.pack(fill="x", pady=6)
        self.tree.bind("<<TreeviewSelect>>", self._skill_selected)
        for s in self.cfg["rotation"]["skills"]:
            self._insert_skill(s)

        form = ttk.Frame(f)
        form.pack(fill="x")
        self.skill_vars = {}
        defaults = {"name": "", "image": "skills/", "key": "", "priority": "5", "threshold": "0.8",
                    "cooldown_ms": "1500", "enabled": "1"}
        for i, (c, h) in enumerate(zip(SKILL_COLS, SKILL_HEAD)):
            ttk.Label(form, text=h).grid(row=0, column=i, padx=2)
            v = tk.StringVar(value=defaults[c])
            self.skill_vars[c] = v
            ttk.Entry(form, textvariable=v, width=10 if c != "image" else 20).grid(row=1, column=i, padx=2)
        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="添加", command=self.skill_add).pack(side="left", padx=2)
        ttk.Button(btns, text="更新选中", command=self.skill_update).pack(side="left", padx=2)
        ttk.Button(btns, text="删除选中", command=self.skill_delete).pack(side="left", padx=2)
        ttk.Button(btns, text="截取选中技能的图标", command=self.skill_capture).pack(side="left", padx=10)
        ttk.Label(f, text="优先级数字越小越先放。图标在可用（亮起）时截取，只截图标本身，不要带快捷键数字。",
                  foreground="#666").pack(anchor="w")

    def _insert_skill(self, s):
        vals = [s.get(c, "") for c in SKILL_COLS]
        vals[-1] = "是" if s.get("enabled", True) else "否"
        self.tree.insert("", "end", values=vals)

    def _skill_from_form(self) -> dict:
        v = {c: self.skill_vars[c].get().strip() for c in SKILL_COLS}
        if not v["name"] or not v["key"]:
            raise ValueError("技能名称和按键不能为空")
        img = v["image"] if v["image"] and v["image"] != "skills/" else f"skills/{v['name']}.png"
        if not os.path.splitext(img)[1]:
            img += ".png"
        return {"name": v["name"], "image": img, "key": v["key"], "priority": int(float(v["priority"] or 5)),
                "threshold": float(v["threshold"] or 0.8), "cooldown_ms": int(float(v["cooldown_ms"] or 0)),
                "enabled": v["enabled"] not in ("0", "否", "false", "False", "")}

    def _skills_from_tree(self) -> list[dict]:
        out = []
        for iid in self.tree.get_children():
            vals = self.tree.item(iid, "values")
            d = dict(zip(SKILL_COLS, vals))
            out.append({"name": d["name"], "image": d["image"], "key": d["key"],
                        "priority": int(float(d["priority"])), "threshold": float(d["threshold"]),
                        "cooldown_ms": int(float(d["cooldown_ms"])), "enabled": d["enabled"] in ("是", "True", "1")})
        return out

    def _skill_selected(self, _=None):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        for c, v in zip(SKILL_COLS, vals):
            self.skill_vars[c].set("1" if (c == "enabled" and v == "是") else ("0" if c == "enabled" else v))

    def skill_add(self):
        try:
            self._insert_skill(self._skill_from_form())
        except ValueError as e:
            messagebox.showerror("技能", str(e))

    def skill_update(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showinfo("技能", "先在表格里选中一行")
        try:
            s = self._skill_from_form()
        except ValueError as e:
            return messagebox.showerror("技能", str(e))
        vals = [s[c] for c in SKILL_COLS]
        vals[-1] = "是" if s["enabled"] else "否"
        self.tree.item(sel[0], values=vals)

    def skill_delete(self):
        for iid in self.tree.selection():
            self.tree.delete(iid)

    def skill_capture(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showinfo("技能", "先在表格里选中一行（或先添加）")
        d = dict(zip(SKILL_COLS, self.tree.item(sel[0], "values")))
        self.capture_template(d["image"], f"技能图标：{d['name']}")

    # ================================================================ 姓名板与移动
    def _tab_nameplates(self, nb):
        f = self._page(nb, "姓名板与移动")
        left = ttk.Frame(f)
        left.grid(row=0, column=0, sticky="nw")
        rows = [
            ("nameplate", "click_button", "点击姓名板用", "str", ["right", "left"], "right=右键：自动转向+走过去+开打"),
            ("nameplate", "click_offset_y", "点击位置向下偏移(px)", "int", None, "0=点姓名板中心"),
            ("nameplate", "max_click_fails", "点几次没选上就转视角", "int", None, ""),
            ("nameplate", "tab_fallback", "点不中时改按 Tab", "bool", None, "默认关"),
            ("nameplate", "use_enemy_markers", "用插件 v0.11 标记找怪", "bool", None, "默认开：只点 黄|蓝 标记（插件旧才用模板）"),
            ("nameplate", "far_names", "远处红名：朝它走，出标记再点", "bool", None, "默认开；不按互动键"),
            ("nameplate", "engage_any", "非战斗看到怪就打", "bool", None,
             "v8.32 默认开：不在战斗中连续 2 帧看到能打的怪的标记 → 先停下右键点去打，拾取/补 buff/回中心打完再做；"
             "回血没到线、读条中、跑尸、在范围外不打"),
            ("nameplate", "skip_top_pct", "屏幕最上面多少%不点", "float", None, "默认 23（1080p ≈ 上面 250 像素，远处）；0=都点"),
            ("nameplate", "stop_settle_s", "看到怪先停下，停稳(秒)再点", "float", None, "默认 0.3"),
            ("nameplate", "confirm_px", "点前确认：标记偏移不超过(px)", "int", None, "默认 25；0=不确认"),
            ("nameplate", "verify_s", "点完几秒内要选上(秒)", "float", None, "默认 0.6；没选上算点空"),
            ("nameplate", "bad_s", "点空的位置几秒内不再点", "float", None, "默认 8"),
            ("nameplate", "bad_radius_px", "“那个位置”半径(px)", "int", None, "默认 60"),
            ("nameplate", "patrol_frames", "巡逻路上连续几帧看到才停", "int", None, "默认 2"),
            ("nameplate", "far_walk_max_s", "朝远处红名最多走(秒)", "float", None, "默认 10"),
            ("nameplate", "far_lost_s", "红名看不到几秒就停", "float", None, "默认 1.5"),
            ("nameplate", "far_skip_s", "走过去没结果，几秒内不再走", "float", None, "默认 5（v8.34 从 20 改短）"),
            ("nameplate", "far_turn_deg", "红名偏离超过(°)先转身", "float", None, "默认 12"),
            ("scan", "tap_ms", "找怪转视角每次(ms)", "int", None, "约 200ms ≈ 35°"),
            ("scan", "settle_ms", "转完等待(ms)", "int", None, ""),
            ("move", "approach_timeout_s", "走过去超时(秒)", "float", None, "超时还没打上→换怪"),
            ("move", "walk_far", "怪远先走过去再开打（v8.46）", "bool", None, "默认开：按住前进朝它走，到近战距离再按开怪键"),
            ("move", "walk_far_max_s", "朝远处的怪最多走(秒)", "float", None, "默认 15；没到就换目标"),
            ("move", "click_interval_s", "两次右键至少间隔(秒)", "float", None, "v8.24 默认 3；点完先判断要不要再点"),
            ("move", "opener_first", "先按开怪键再走过去", "bool", None, "默认关"),
            ("move", "open_with_attack", "走到怪身边先按开怪键", "bool", None, "默认开：猎豹和熊都用 f 开怪"),
            ("nameplate", "threshold_selected", "当前目标模板阈值", "float", None, ""),
            ("nameplate", "threshold_unselected", "未选中模板阈值", "float", None, ""),
            ("nameplate", "multiscale", "多尺度匹配", "bool", None, "远近大小不同"),
            ("nameplate", "scales", "尺度列表", "str", None, "逗号分隔"),
            ("nameplate", "downscale", "搜索缩放", "float", None, "0.5 快 / 1 准"),
            ("nameplate", "turn_when_offscreen", "看不到当前目标姓名板时转视角", "bool", None, ""),
            ("nameplate", "max_offscreen_turns", "最多转几次", "int", None, "超过就换怪"),
            ("stationary", "plate_click", "原地模式：没目标时点姓名板", "bool", None, "用右边“未选中”模板"),
            ("stationary", "plate_click_button", "原地模式点姓名板用", "str", ["left", "right"], "left=只选中不走过去"),
            ("stationary", "plate_max_fails", "原地模式点几次没选上改 Tab", "int", None, ""),
            ("stationary", "plate_restore_mouse", "点完鼠标移回原位", "bool", None, ""),
        ]
        for i, (sec, key, label, kind, choices, tip) in enumerate(rows):
            self._field(left, i, label, sec, key, kind, choices=choices, tip=tip)

        right = ttk.Frame(f, padding=(12, 0))
        right.grid(row=0, column=1, sticky="nw")
        self.np_lists = {}
        for j, (kind, title) in enumerate([("selected", "当前目标（选中）的姓名板模板"), ("unselected", "未选中的姓名板模板")]):
            box = ttk.LabelFrame(right, text=title, padding=4)
            box.grid(row=j, column=0, sticky="nw", pady=4)
            lb = tk.Listbox(box, height=6, width=34)
            lb.pack(side="top")
            self.np_lists[kind] = lb
            bb = ttk.Frame(box)
            bb.pack(fill="x")
            ttk.Button(bb, text="截取", command=lambda k=kind: self.capture_nameplate(k)).pack(side="left")
            ttk.Button(bb, text="预览", command=lambda k=kind: self.preview_nameplate(k)).pack(side="left")
            ttk.Button(bb, text="删除", command=lambda k=kind: self.delete_nameplate(k)).pack(side="left")
        ttk.Label(right, foreground="#666", justify="left", wraplength=300,
                  text="每类可放多张（远/近、红色敌对/黄色中立各截一张）。\n"
                       "只框姓名板本身（名字+血条），不要带背景太多。\n"
                       "选中的姓名板更大、更亮、有白框，必须单独截。").grid(row=2, column=0, sticky="w")
        tf = ttk.LabelFrame(right, padding=6,
                            text="Tab 找怪（v8.40，要插件 v0.13）：找怪先按选怪键（基本设置页“选怪键”，默认 Tab），"
                                 "合格就过去打；不合格/没选到 → 清目标、不转身，接着点屏幕标记/走去远处")
        tf.grid(row=3, column=0, sticky="nw", pady=(10, 4))
        ts_items = [
            ("enabled", "Tab 找怪", "bool", "默认开：每次找怪先按 Tab"),
            ("while_moving", "移动中也按 Tab", "bool", "默认开：巡逻/回中心/朝远处走时定时按；跑尸、战斗、拾取、回血时不按"),
            ("move_interval_s", "移动中每隔几秒按一次", "float", "默认 1.8（随机 ±0.4）"),
            ("skip_tapped", "不打灰名（被别人抢了）", "bool", "默认开"),
            ("skip_elite", "不打精英", "bool", "默认开（含稀有精英/世界首领；稀有非精英照打）"),
            ("skip_high", "不打比我高的怪", "bool", "默认开（骷髅 ?? 也不打）"),
            ("level_diff", "高几级以上不打", "int", "默认 3：高 3 级及以上不打"),
            ("skip_player", "不打玩家", "bool", "默认开"),
            ("max_per_cycle", "一轮最多按几次 Tab", "int", "默认 2：不合格就再按一次换下一个"),
            ("cooldown_s", "没找到合格的，几秒内不再按", "float", "默认 4：这段时间点屏幕标记/走去远处"),
        ]
        for i, (k, label, kind, tip) in enumerate(ts_items):
            self._field(tf, i, label, "tab_search", k, kind, tip=tip)
        self.refresh_nameplates()

    def refresh_nameplates(self):
        for kind, lb in self.np_lists.items():
            lb.delete(0, "end")
            for p in list_nameplate_templates(kind):
                lb.insert("end", os.path.basename(p))

    def capture_nameplate(self, kind):
        name = time.strftime(f"{kind[:3]}_%Y%m%d_%H%M%S.png")
        rel = os.path.relpath(os.path.join(NAMEPLATE_DIRS[kind], name), IMAGES_DIR)
        label = "选中的姓名板（先选中一只怪）" if kind == "selected" else "未选中的姓名板"
        self.capture_template(rel, label)
        self.refresh_nameplates()

    def _np_selected_path(self, kind):
        lb = self.np_lists[kind]
        sel = lb.curselection()
        if not sel:
            messagebox.showinfo("姓名板", "先在列表里选中一张")
            return None
        return os.path.join(NAMEPLATE_DIRS[kind], lb.get(sel[0]))

    def preview_nameplate(self, kind):
        path = self._np_selected_path(kind)
        img = imread(path) if path else None
        if img is None:
            return
        import cv2
        big = cv2.resize(img, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        top = tk.Toplevel(self)
        top.title(os.path.basename(path) + f"（{img.shape[1]}x{img.shape[0]}，放大3倍）")
        photo = _photo_from_bgr(big)
        lbl = ttk.Label(top, image=photo)
        lbl.image = photo
        lbl.pack()

    def delete_nameplate(self, kind):
        path = self._np_selected_path(kind)
        if path and messagebox.askyesno("删除", f"删除 {os.path.basename(path)}？"):
            os.remove(path)
            self.refresh_nameplates()
            self.log("已删除 " + path)

    # ================================================================ 区域刷怪
    def _tab_area(self, nb):
        f = self._page(nb, "区域刷怪")
        ttk.Label(f, foreground="#666", wraplength=820, justify="left",
                  text="脱战时如果离中心超过半径，就清掉目标、转向中心、按住前进走回去；途中被打会停下迎战。"
                       "需要 BotStatus v0.5（屏幕左上角色块下方的两行二进制块）。坐标和游戏里显示的一样（0-100）。"
                       "副本里读不到坐标，功能会自动暂停。\n"
                       "v8.17：在游戏里按 F9 = 检查在不在范围内（日志 + 底部状态栏“范围”）；F8 = 把当前位置设为中心（自动保存）。"
                       "默认开始打怪时把当前位置当中心。走回中心时不做随机小动作。").grid(row=0, column=0, columnspan=4, sticky="w")
        rows = [
            ("area", "enabled", "启用区域刷怪", "bool", "点击模式和原地模式都管"),
            ("area", "home_on_start", "开始时把当前位置当中心", "bool", "关掉 = 用下面保存的中心坐标"),
            ("area", "leash_ratio", "追怪最远(半径×)", "float", "点击模式：还没开打就追出 半径×此值 → 放弃这只，走回中心"),
            ("area", "auto_switch_center", "就近切换中心（v8.46）", "bool", "出了当前中心的范围、但在另一个中心范围里 → 直接换过去，不走回去"),
            ("scan", "spin_home", "转一圈没找到怪就回中心", "bool", "v8.19：找怪转视角累计转满下面的度数 → 走回范围中心"),
            ("scan", "spin_deg", "转多少度算一圈", "float", "默认 360"),
            ("scan", "spin_near_ratio", "已在中心附近(半径×)", "float", "在这个距离内不走，等一会儿再找（默认 0.2）"),
            ("scan", "spin_wait_s", "在中心附近时等(秒)", "float", "默认 3"),
            ("area", "center_x", "中心 X", "float", ""),
            ("area", "center_y", "中心 Y", "float", ""),
            ("area", "radius", "半径", "float", "地图坐标单位，1 ≈ 10-40 码"),
            ("area", "arrive_ratio", "到达比例", "float", "走到 半径×比例 以内停"),
            ("area", "map_id", "地图 ID", "int", "0=不检查；点下面按钮会自动填"),
            ("area", "turn_rate_dps", "转向速度(度/秒)", "float", "游戏默认约 180；转多了调大，转少了调小"),
            ("area", "face_tolerance_deg", "停下原地转的偏差(度)", "float", ""),
            ("area", "walk_correct_deg", "边走边修正的偏差(度)", "float", ""),
            ("area", "return_timeout_s", "回中心超时(秒)", "float", ""),
            ("area", "stuck_s", "卡住判定(秒)", "float", "位置不变这么久 → 跳+平移"),
            ("keys", "forward", "前进键", "str", "默认 w"),
            ("keys", "turn_left", "左转键", "str", "默认 left（方向键左）"),
            ("keys", "turn_right", "右转键", "str", "默认 right（方向键右）"),
            ("keys", "jump", "跳跃键", "str", "默认 space"),
            ("keys", "strafe", "平移键", "str", "默认 q（左平移）"),
        ]
        for i, (sec, key, label, kind, tip) in enumerate(rows, start=1):
            self._field(f, i, label, sec, key, kind, tip=tip)
        bb = ttk.Frame(f)
        bb.grid(row=len(rows) + 1, column=0, columnspan=4, sticky="w", pady=8)
        ttk.Button(bb, text="设当前位置为中心", command=self.set_area_center).pack(side="left", padx=2)
        ttk.Button(bb, text="读取当前位置", command=lambda: self.set_area_center(apply=False)).pack(side="left", padx=2)
        # v8.23 多中心巡逻列表
        cf = ttk.LabelFrame(f, text="v8.23 多中心巡逻（在游戏里站到位置按 F7 添加；F8 = 清空，当前位置当中心 #1）")
        cf.grid(row=len(rows) + 2, column=0, columnspan=4, sticky="we", pady=4)
        ttk.Label(cf, foreground="#666", wraplength=780, justify="left",
                  text="有 2 个以上中心：在当前中心按原来的方式找怪，找怪转满一圈（上面的度数）还没找到 → 直线走去下一个中心，"
                       "1→2→3→2→1→2… 来回走；所有中心共用上面的半径。路上看到能打的怪也去打，打完、回血、补 buff、拾取后接着走。"
                       "开始打怪时从最近的中心开始（列表为空时照旧用开始位置/保存的中心）。别的地图的中心会跳过。").pack(anchor="w")
        self.centers_lb = tk.Listbox(cf, height=6, width=60)
        self.centers_lb.pack(side="left", padx=4, pady=4)
        cb = ttk.Frame(cf)
        cb.pack(side="left", padx=4)
        ttk.Button(cb, text="删除选中", command=self._center_delete).pack(fill="x", pady=2)
        ttk.Button(cb, text="清空", command=self._center_clear).pack(fill="x", pady=2)
        ttk.Button(cb, text="添加当前位置", command=lambda: self._hotkey_area("add")).pack(fill="x", pady=2)
        self._refresh_centers()

    def _tab_corpse(self, nb):
        f = self._page(nb, "跑尸")
        ttk.Label(f, foreground="#666", wraplength=820, justify="left",
                  text="v8.31 自动跑尸（需要插件 BotStatus v0.12，游戏里 /reload 一次）：死了 → 等几秒按“释放灵魂/复活键” → "
                       "按坐标和朝向走回尸体（卡住会跳/平移/绕开）→ 出现“复活”框后转一圈看附近有没有会打我的怪，有就等 → "
                       "按同一个键复活 → 人形回血回蓝 → 变回战斗形态继续打。连续死多少次都不停。\n"
                       "游戏里要做一个宏，拖到动作条上，绑到“释放灵魂/复活键”（默认 ]）：\n"
                       '/run if UnitIsGhost("player") then RetrieveCorpse() elseif UnitIsDead("player") then RepopMe() end'
                       ).grid(row=0, column=0, columnspan=4, sticky="w")
        rows = [
            ("corpse", "enabled", "自动跑尸", "bool", "关 = 死了就停止（跟以前一样）"),
            ("corpse", "release_delay_s", "死后等几秒释放灵魂", "float", "默认 5"),
            ("corpse", "safe_check", "复活前看附近有没有怪", "bool", "到尸体后转一圈，看到会打我的怪就等"),
            ("corpse", "safe_wait_s", "附近有怪最多等(秒)", "float", "默认 30；到时间照样复活"),
            ("corpse", "rest_hp_pct", "复活后血量回到(%)", "int", "默认 90；血低于 70% 先放一次治疗之触"),
            ("corpse", "rest_mana_pct", "复活后法力回到(%)", "int", "默认 80"),
            ("corpse", "rest_timeout_s", "复活后最多回(秒)", "float", "默认 120"),
            ("corpse", "run_timeout_s", "跑尸多久提醒一次(秒)", "float", "默认 600；v8.38 起不再停止，一直绕路试，每隔这么久写一次日志"),
            ("corpse", "safe_rescan_s", "有怪时隔几秒再看", "float", "默认 5"),
            ("corpse", "near_pct", "屏幕上方多少%以下算近(%)", "int", "默认 35：标记在这条线以下 = 离得近"),
            ("corpse", "arrive_dist", "离尸体多近算到(地图坐标)", "float", "没出现“复活”框时用；默认 0.3"),
            ("corpse", "stuck_s", "卡住判定(秒)", "float", "按着前进这么久位置没变 → 先跳/平移，再后退 + 绕 60/90/120/150/180°（越卡越远），一直试"),
            ("corpse", "retry_s", "没反应隔几秒再按(秒)", "float", "默认 3"),
            ("corpse", "route_shortcut_dist", "路线：离尸体多近就直接去(地图坐标)", "float",
             "默认 1.0：走路线途中离尸体 ≤ 这个距离、并且比离下一个路线点还近 → 不走剩下的点，直接去尸体"),
            ("corpse", "route_arrive_dist", "路线：离路线点多近算到(地图坐标)", "float", "默认 0.15"),
        ]
        for i, (sec, key, label, kind, tip) in enumerate(rows, start=1):
            self._field(f, i, label, sec, key, kind, tip=tip)
        # v8.39 跑尸路线
        rf = ttk.LabelFrame(f, text="跑尸路线（v8.39）", padding=6)
        rf.grid(row=len(rows) + 2, column=0, columnspan=4, sticky="we", pady=8)
        hk = str(self.cfg["keys"].get("hotkey_add_route") or "f6").upper()
        ttk.Label(rf, foreground="#666", wraplength=780, justify="left",
                  text=f"活着从复活点（墓地）往刷怪区走一遍，在容易卡住的地方前后按 {hk}（或点“添加当前位置”）记点，#1 是复活点。"
                       "释放灵魂后先从离自己最近的点开始（已经走过它就从下一个开始）逐个走完，再朝尸体走；"
                       "途中离尸体够近（上面的距离）就不走剩下的点直接去尸体。某个点卡住会一直绕路直到走到，不跳过、不停止。"
                       "列表为空 = 跟以前一样直接朝尸体走。复活后照常回中心。").pack(anchor="w")
        self.route_lb = tk.Listbox(rf, height=6, width=60)
        self.route_lb.pack(side="left", padx=4, pady=4)
        rb = ttk.Frame(rf)
        rb.pack(side="left", padx=4)
        ttk.Button(rb, text="删除选中", command=self._route_delete).pack(fill="x", pady=2)
        ttk.Button(rb, text="清空", command=self._route_clear).pack(fill="x", pady=2)
        ttk.Button(rb, text="添加当前位置", command=lambda: self._hotkey_area("route")).pack(fill="x", pady=2)
        self._refresh_route()

    def _refresh_route(self):
        lb = getattr(self, "route_lb", None)
        if lb is None:
            return
        lb.delete(0, "end")
        for i, c in enumerate(self.cfg["corpse"].get("route") or []):
            lb.insert("end", f"#{i + 1}   {float(c[0]):.2f}, {float(c[1]):.2f}   地图 {c[2] if len(c) > 2 else 0}"
                      + ("   （复活点）" if i == 0 else ""))

    def _save_route(self, route, msg=None):
        """把跑尸路线写进 config.json（只改这一项）。"""
        route = [[round(float(c[0]), 2), round(float(c[1]), 2), int(c[2] if len(c) > 2 else 0)] for c in route]
        try:
            cfg = load_config()
            cfg["corpse"]["route"] = route
            save_config(cfg)
        except Exception as e:  # noqa: BLE001
            return self.log(f"🗺️ 保存跑尸路线失败：{e}")
        self.cfg["corpse"]["route"] = route
        self._refresh_route()
        if msg:
            self.log(msg)

    def _push_route(self):
        g = self.runner.grinder if (self.runner and self.runner.running) else None
        if g is not None:
            g.set_route(self.cfg["corpse"].get("route") or [])

    def _route_delete(self):
        sel = list(self.route_lb.curselection())
        if not sel:
            return
        rs = list(self.cfg["corpse"].get("route") or [])
        i = sel[0]
        c = rs.pop(i)
        self._save_route(rs, f"🗺️ 已删除跑尸路线点 #{i + 1}（{c[0]}, {c[1]}），还剩 {len(rs)} 个")
        self._push_route()

    def _route_clear(self):
        if not (self.cfg["corpse"].get("route")) or not messagebox.askyesno("清空", "清空跑尸路线？"):
            return
        self._save_route([], "🗺️ 已清空跑尸路线（跟以前一样直接朝尸体走）")
        self._push_route()

    def _refresh_centers(self):
        lb = getattr(self, "centers_lb", None)
        if lb is None:
            return
        lb.delete(0, "end")
        for i, c in enumerate(self.cfg["area"].get("centers") or []):
            lb.insert("end", f"#{i + 1}   {float(c[0]):.2f}, {float(c[1]):.2f}   地图 {c[2] if len(c) > 2 else 0}")

    def _save_centers(self, centers, msg=None):
        """把中心列表写进 config.json（只改这一项）。运行中也同步给状态机。"""
        centers = [[round(float(c[0]), 2), round(float(c[1]), 2), int(c[2] if len(c) > 2 else 0)] for c in centers]
        try:
            cfg = load_config()
            cfg["area"]["centers"] = centers
            save_config(cfg)
        except Exception as e:  # noqa: BLE001
            return self.log(f"🚩 保存中心列表失败：{e}")
        self.cfg["area"]["centers"] = centers
        self._refresh_centers()
        if msg:
            self.log(msg)

    def _push_centers(self):
        g = self.runner.grinder if (self.runner and self.runner.running) else None
        if g is not None:
            g.set_centers(self.cfg["area"].get("centers") or [])

    def _center_delete(self):
        sel = list(self.centers_lb.curselection())
        if not sel:
            return
        cs = list(self.cfg["area"].get("centers") or [])
        i = sel[0]
        c = cs.pop(i)
        self._save_centers(cs, f"🚩 已删除中心 #{i + 1}（{c[0]}, {c[1]}），还剩 {len(cs)} 个")
        self._push_centers()

    def _center_clear(self):
        if not (self.cfg["area"].get("centers")) or not messagebox.askyesno("清空", "清空所有中心？"):
            return
        self._save_centers([], "🚩 已清空中心列表（照旧用开始位置/保存的中心）")
        self._push_centers()

    def _read_location(self, purpose):
        img = self.get_screenshot(purpose)
        if img is None:
            return None
        from bot.addon_reader import AddonReader
        try:
            cfg = copy.deepcopy(self.collect())
        except ValueError as e:
            messagebox.showerror("设置有误", str(e))
            return None
        st = AddonReader(cfg).read(img)
        if not st.ok:
            messagebox.showerror("读取位置", "读不到 BotStatus 插件：" + st.reason)
            return None
        if st.pos is None:
            messagebox.showerror("读取位置", "插件在运行，但坐标块读不到：\n"
                                 "- 插件还是旧版（需要 v0.5）\n"
                                 "- 或者在副本/战场里（游戏不给坐标）")
            return None
        return st

    def set_area_center(self, apply=True):
        st = self._read_location("读取当前位置：站在想刷怪的中心点，切到游戏")
        if st is None:
            return
        import math
        from bot.navigation import distance
        x, y = st.pos
        face = "?" if st.facing is None else f"{math.degrees(st.facing):.0f}°"
        if apply:
            self.fields[("area", "center_x")][0].set(f"{x:.2f}")
            self.fields[("area", "center_y")][0].set(f"{y:.2f}")
            self.fields[("area", "map_id")][0].set(str(st.map_id or 0))
            self.log(f"区域中心 = {x:.2f}, {y:.2f}（地图 {st.map_id}，朝向 {face}）。记得点“保存设置”")
        else:
            try:
                c = (float(self.fields[("area", "center_x")][0].get()), float(self.fields[("area", "center_y")][0].get()))
                d = f"，离中心 {distance((x, y), c):.2f}"
            except ValueError:
                d = ""
            self.log(f"当前位置 {x:.2f}, {y:.2f}，朝向 {face}，地图 {st.map_id}{d}")

    # ================================================================ 安全
    def _tab_stationary(self, nb):
        f = self._page(nb, "原地打怪")
        ttk.Label(f, text="原地模式：不走路、不点击移动。没目标按 Tab；死的/被抢/够不到就继续 Tab；"
                          "面向错误或打不出伤害 → 原地左右小幅转身。怪需要自己走过来（或正在打你）。",
                  foreground="#444", wraplength=900).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(f, text="（移动方式在“基本设置”页；面向/转身的参数在“战斗补救”页）", foreground="#666"
                  ).grid(row=1, column=0, columnspan=3, sticky="w")
        items = [
            ("max_tabs", "连续 Tab 最多(次)", "int", "没找到能打的怪 → 随机等一会儿"),
            ("pause_min_s", "随机等待 最短(秒)", "float", "默认 2"),
            ("pause_max_s", "随机等待 最长(秒)", "float", "默认 4"),
            ("tab_interval_s", "两次 Tab 间隔(秒)", "float", "默认 0.6"),
            ("turn_search", "找不到怪时原地转视角", "bool", "只按方向键转身，不走路"),
            ("turn_after_rounds", "第几轮没找到开始转", "int", "默认 1"),
            ("wait_in_range_s", "等怪过来最多(秒)", "float", "选中的怪够不到/判断不了距离时"),
        ]
        for i, (k, label, kind, tip) in enumerate(items, start=2):
            self._field(f, i, label, "stationary", k, kind, tip=tip)
        hz = [
            ("move_enabled", "随机小幅移动", "bool", "两只怪之间偶尔前进一下再后退回原位（战斗中不做）"),
            ("move_min_ms", "前进按住 最短(ms)", "int", "默认 150"),
            ("move_max_ms", "前进按住 最长(ms)", "int", "默认 400"),
            ("back_ratio", "后退时间倍数", "float", "后退比跑步慢，默认 1.55 才能回原位"),
            ("move_kills_min", "每隔几只怪 最少", "int", "默认 1"),
            ("move_kills_max", "每隔几只怪 最多", "int", "默认 4"),
            ("move_idle_min_s", "没怪时每隔 最短(秒)", "float", "默认 30"),
            ("move_idle_max_s", "没怪时每隔 最长(秒)", "float", "默认 120"),
            ("break_enabled", "随机休息", "bool", "打一阵停一阵；休息时被打照样战斗"),
            ("hunt_min_min", "打多久 最短(分钟)", "float", "默认 12"),
            ("hunt_max_min", "打多久 最长(分钟)", "float", "默认 18"),
            ("break_min_min", "休息 最短(分钟)", "float", "默认 2"),
            ("break_max_min", "休息 最长(分钟)", "float", "默认 3"),
        ]
        ttk.Label(f, text="—— 随机小动作 / 随机休息 ——").grid(row=1, column=3, columnspan=2, sticky="w", padx=8)
        for i, (k, label, kind, tip) in enumerate(hz, start=2):
            self._field(f, i, label, "humanize", k, kind, tip=tip, col=3)

    def _tab_recover(self, nb):
        f = self._page(nb, "战斗补救")
        ttk.Label(f, text="太远 / 够不到 / 打不出伤害 → 右键点目标姓名板；还不行 → 转一点视角 → 后退半步（战斗中绝不按互动键）；"
                          "连续几次无效 → 清目标并跳过这只怪。面向错误见下面。", foreground="#444", wraplength=900
                  ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        items = [
            ("enabled", "启用战斗补救", "bool", ""),
            ("first_damage_s", "开打后多久没伤害算卡住(秒)", "float", "默认 4"),
            ("no_damage_s", "战斗中多久没伤害就补救(秒)", "float", "默认 2（看目标血条有没有变短/插件战斗日志）"),
            ("check_interval_s", "每次补救后等待(秒)", "float", "默认 1.5"),
            ("max_fails", "连续无效几次就放弃", "int", "默认 3"),
            ("turn_ms", "转视角按住(ms)", "int", "第 2 次补救"),
            ("back_ms", "后退按住(ms)", "int", "第 3 次补救"),
            ("skip_s", "放弃的怪跳过(秒)", "float", "这段时间不再点它附近的姓名板"),
        ]
        for i, (k, label, kind, tip) in enumerate(items, start=1):
            self._field(f, i, label, "recover", k, kind, tip=tip)
        r = len(items) + 1
        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
        self._field(f, r + 1, "危险时响提示音", "heal", "beep", "bool", tip="治疗后仍低于危险线 → 停止并响")
        ttk.Label(f, text="—— v8.17 面向错误 → 右键点目标姓名板（需要插件 v0.10：标记在姓名板中间）——"
                  ).grid(row=r + 2, column=0, columnspan=3, sticky="w", pady=(8, 2))
        fc = [
            ("enabled", "面向错误时右键点姓名板", "bool", "角色自动转向目标；找不到标记绝不点，改用原来的转身"),
            ("button", "点击用的键", "str", "right=右键（默认）"),
            ("cooldown_s", "两次点击最少间隔(秒)", "float", "v8.24 起还受“点击移动”页的“两次右键至少间隔”(默认 3 秒)限制"),
            ("in_melee", "近战距离内也右键点", "bool", "v8.24 默认关：近战内面向错误只转身（不右键，不按互动键）"),
            ("melee_back_ms", "贴脸面向错误：每次后退(ms)", "int", "默认 300（半步）；0=不后退（直接右键点）"),
            ("melee_backs", "贴脸标记在中间：先后退几次", "int", "v8.26 默认 2：不盲转，退 2 次还报错 → 右键点一次标记（守右键间隔）"),
            ("melee_cycles", "后退+右键最多循环(轮)", "int", "默认 3；还不行 → 放弃（怪在打我就不换目标，重新来）"),
            ("max_clicks", "连点几次没修好就转一下身", "int", "默认 2；转完身又先右键点；面向错误不再按互动键"),
            ("restore_mouse", "点完把鼠标移回原位", "bool", ""),
            ("offset_y", "点击位置往下偏移(像素)", "int", "默认 0（标记中心 = 姓名板中心）"),
        ]
        for i, (k, label, kind, tip) in enumerate(fc, start=r + 3):
            self._field(f, i, label, "face_click", k, kind, tip=tip)
        face = [
            ("face_interval_s", "转身间隔(秒)", "float", "默认 0.5"),
            ("face_settle_s", "转完等多久再看(秒)", "float", "默认 0.25"),
            ("face_center_deg", "标记在中间 ±(度)", "int", "这范围内报面向错误 → 按下面的度数逐次转（不再当误报）"),
            ("face_small_steps", "标记在中间仍报错 每次转(度)", "str", "逗号分隔，默认 15,25,40；之后扫一圈"),
            ("face_quiet_s", "转身后几秒没报错算修好(秒)", "float", "默认 5（不看怪掉血：荆棘术反伤也会掉血）"),
            ("no_damage_offcenter_s", "标记偏一边时没伤害(秒)", "float", "偏离 >25° 或贴边：默认 2.5 秒就转"),
            ("face_max_attempts", "转身最多(次)", "int", "还不行 → Tab 换目标"),
            ("no_damage_s", "多久没伤害算打不出(秒)", "float", "熊约 2.5 秒挥一下，至少 4（默认 4.5）"),
            ("use_marker", "用姓名板标记找怪方向", "bool", "插件 v0.7 在目标姓名板上方画 青|洋红 小标记"),
            ("face_ok_deg", "偏离多少度就转(度)", "int", "默认 60"),
            ("fov_deg", "游戏水平视角(度)", "int", "默认 90，用于把屏幕位置换算成角度"),
            ("turn_rate_dps", "转身速度(度/秒)", "int", "默认 180，运行时用插件朝向自动校准"),
        ]
        for k, label, kind, tip in face:      # v8.25：从“原地打怪”页搬来（点击模式也用），都在高级设置里
            self._field(f, 0, label, "stationary", k, kind, tip=tip)

    def _tab_safety(self, nb):
        f = self._page(nb, "安全")
        items = [
            ("delay_min_ms", "按键后随机间隔 最小(ms)", "int", ""),
            ("delay_max_ms", "按键后随机间隔 最大(ms)", "int", ""),
            ("hold_min_ms", "按键按住 最短(ms)", "int", ""),
            ("hold_max_ms", "按键按住 最长(ms)", "int", ""),
            ("tick_ms", "识别循环间隔(ms)", "int", "实际会再随机 ±"),
            ("stuck_timeout_s", "卡住提醒(秒)", "float", "目标血量这么久不下降 → 写提醒、截图，继续打（v8.43 起不停止）"),
            ("retarget_s", "换目标时间(秒)", "float", "选中的怪不掉血且未进战斗 → 换目标"),
            ("death_hp_seconds", "0 血持续(秒)视为死亡", "float", "v8.43：死了不停止（跑尸关着就等活过来）"),
        ]
        for i, (k, label, kind, tip) in enumerate(items):
            self._field(f, i, label, "safety", k, kind, tip=tip)
        r = len(items)
        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
        self._field(f, r + 1, "只在游戏前台时按键", "misc", "window_check", "bool")
        self._field(f, r + 2, "游戏窗口标题关键字", "misc", "window_keywords", tip="逗号分隔")
        self._field(f, r + 3, "开始/截图倒计时(秒)", "misc", "capture_delay_s", "int")
        self._field(f, r + 4, "调试预览窗口", "misc", "debug_preview", "bool", tip="会占一些性能，默认关")
        self._field(f, r + 5, "截图最多保留(张)", "log", "keep_shots", "int", tip="v8.25：logs/shots 只留最新的 N 张，默认 50")
        ttk.Separator(f).grid(row=r + 6, column=0, columnspan=3, sticky="ew", pady=6)
        self._field(f, r + 7, "断线自动重连（v8.44）", "reconnect", "enabled", "bool",
                    tip="截图认出断线弹窗/登录界面/选角色界面 → 按 Enter，直到回到游戏再继续打怪（默认开）")
        self._field(f, r + 8, "重连按 Enter 间隔(秒)", "reconnect", "interval_s", "float", tip="默认 10 秒按一次，不设放弃时间")

    # ================================================================ 底部
    def _bottom(self):
        bar = ttk.Frame(self, padding=(6, 0))
        self.btn_start = ttk.Button(bar, text="▶ 开始", command=self.start_bot)
        self.btn_start.pack(side="left", padx=2)
        ttk.Button(bar, text="■ 停止", command=self.stop_bot).pack(side="left", padx=2)
        ttk.Button(bar, text="测试识别", command=self.test_detect).pack(side="left", padx=8)
        ttk.Button(bar, text="保存设置", command=self.save).pack(side="left", padx=2)
        ttk.Button(bar, text="重新读取", command=self.reload).pack(side="left", padx=2)
        ttk.Button(bar, text="打开图片文件夹", command=lambda: self._open_dir(IMAGES_DIR)).pack(side="left", padx=8)
        ttk.Button(bar, text="打开日志文件夹", command=self._open_logs).pack(side="left", padx=2)

        st = ttk.Frame(self, padding=6)
        self.status_vars = {k: tk.StringVar(value="-") for k in ("state", "source", "player_hp", "target_hp", "combat", "kills", "area")}
        for i, (k, label) in enumerate([("state", "状态"), ("source", "来源"), ("player_hp", "自己血量"),
                                        ("target_hp", "目标血量"), ("combat", "战斗中"), ("kills", "击杀"), ("area", "范围")]):
            ttk.Label(st, text=label + "：").grid(row=0, column=2 * i, sticky="e")
            ttk.Label(st, textvariable=self.status_vars[k], width=18 if k == "area" else 10,
                      foreground="#0050a0").grid(row=0, column=2 * i + 1, sticky="w")
        self.logbox = ScrolledText(self, height=8, font=("Consolas", 9))
        # v8.16：从下往上排：日志框（固定 8 行）→ 状态栏 → 按钮栏；上面剩下的高度给设置页（可滚动）
        self.logbox.pack(side="bottom", fill="x", padx=6, pady=(0, 6))
        st.pack(side="bottom", fill="x")
        bar.pack(side="bottom", fill="x")

    def _show_status(self, st):
        sv = self.status_vars
        sv["state"].set(st.get("state", "-"))
        if "source" in st:
            sv["source"].set("插件" if st["source"] == "addon" else "截图")
        if "player_hp" in st:
            sv["player_hp"].set(f"{st['player_hp']:.0f}%")
            sv["target_hp"].set(f"{st['target_hp']:.0f}%" if st.get("target") else "无目标")
            c = st.get("combat")
            sv["combat"].set("未启用" if c is None else ("是" if c else "否"))
        sv["kills"].set(str(st.get("kills", 0)))
        if "area" in st:
            sv["area"].set(st["area"])

    # ================================================================ 截图 / 框选
    def get_screenshot(self, purpose: str):
        """返回 BGR 整屏图像或 None。"""
        ans = messagebox.askyesnocancel(
            "截图来源",
            f"{purpose}\n\n是：倒计时后截取当前屏幕（本窗口会先隐藏，请切到游戏）\n否：打开一张游戏截图文件（1920x1080）\n取消：放弃")
        if ans is None:
            return None
        if ans is False:
            path = filedialog.askopenfilename(title="选择游戏截图",
                                              filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("全部", "*.*")])
            if not path:
                return None
            img = imread(path)
            if img is None:
                messagebox.showerror("截图", "读不了这张图片")
            return img
        delay = max(0, int(float(self.fields[("misc", "capture_delay_s")][0].get() or 3)))
        self.withdraw()
        self.update()
        try:
            time.sleep(delay)
            from bot.capture import ScreenCapture
            cap = ScreenCapture(self.cfg["misc"].get("monitor", 1))
            img = cap.grab()
            cap.close()
        finally:
            self.deiconify()
            self.lift()
        return img

    def pick_region(self, key, label):
        img = self.get_screenshot(f"框选区域：{label}")
        if img is None:
            return
        rect = select_region(self, img, f"框选：{label}")
        if rect:
            self.region_vars[key].set(_fmt(rect))
            self.log(f"{label} 区域 = {rect}（记得点“保存设置”）")

    def capture_template(self, rel: str, label: str):
        img = self.get_screenshot(f"截取模板：{label}\n只框住图标/文字本身，越紧越好")
        if img is None:
            return
        rect = select_region(self, img, f"截取模板：{label}")
        if not rect:
            return
        path = image_path(rel)
        if imwrite(path, crop(img, rect)):
            self.log(f"已保存模板 {path}（{rect[2]}x{rect[3]}）")
        else:
            messagebox.showerror("模板", "保存失败：" + path)

    def auto_color(self, key):
        try:
            region = _parse_ints(self.region_vars[key].get(), 4)
        except ValueError as e:
            return messagebox.showerror("取色", str(e))
        if not region_set(region):
            return messagebox.showinfo("取色", "先框选血条区域")
        img = self.get_screenshot("自动取色：请在满血（或血条较满）时截图")
        if img is None:
            return
        from bot.detector import suggest_hsv_range
        r = suggest_hsv_range(crop(img, region))
        if not r:
            return messagebox.showerror("取色", "区域里没有明显的彩色像素")
        self.color_vars[key + "_lo"].set(_fmt(r[0]))
        self.color_vars[key + "_hi"].set(_fmt(r[1]))
        self.log(f"{key} 颜色范围 = {r[0]} ~ {r[1]}")

    # ================================================================ 测试识别
    def test_detect(self):
        try:
            cfg = copy.deepcopy(self.collect())
        except ValueError as e:
            return messagebox.showerror("设置有误", str(e))
        img = self.get_screenshot("测试识别：截一帧，显示识别到的数值")
        if img is None:
            return
        from bot.detector import Detector
        det = Detector(cfg)
        obs = det.observe(img)
        self.log("识别结果：" + obs.summary())
        if obs.details:
            self.log("  分数：" + ", ".join(f"{k}={v:.2f}" for k, v in obs.details.items()))
        dbg = det.draw_debug(img, obs)
        top = tk.Toplevel(self)
        top.title("识别结果（缩小一半显示）")
        photo = _photo_from_bgr(dbg).subsample(2, 2)
        lbl = ttk.Label(top, image=photo)
        lbl.image = photo
        lbl.pack()
        ttk.Label(top, text=obs.summary(), wraplength=900).pack(pady=4)

    # ================================================================ 运行
    def start_bot(self):
        if self.runner and self.runner.running:
            return self.log("已经在运行")
        try:
            cfg = self.collect()
        except ValueError as e:
            return messagebox.showerror("设置有误", str(e))
        miss = missing_required(cfg) + cat_missing(cfg)
        if miss:
            return messagebox.showerror("还没设置好", "请先设置：\n- " + "\n- ".join(miss))
        warn = []
        if cfg["detect"]["combat_mode"] == "template" and imread(image_path("combat.png")) is None:
            warn.append("images/combat.png 不存在（战斗识别会失效）")
        if region_set(cfg["regions"]["dead"]) and imread(image_path("dead.png")) is None:
            warn.append("images/dead.png 不存在（只能靠 0 血判断死亡）")
        if cfg["rotation"]["mode"] == "template":
            for s in cfg["rotation"]["skills"]:
                if s.get("enabled", True) and imread(image_path(s["image"])) is None:
                    warn.append(f"技能图标缺失：images/{s['image']}")
        click_mode = cfg["move"].get("mode") == "click"
        if click_mode and cfg["nameplate"]["enabled"] and not list_nameplate_templates("unselected") \
                and not cfg["nameplate"].get("use_enemy_markers", True):
            warn.append("没有“未选中姓名板”模板，也关了“用插件 v0.11 标记找怪”：程序只会一直转视角找怪")
        if click_mode and cfg["nameplate"]["enabled"] and not list_nameplate_templates("selected") \
                and not cfg["nameplate"].get("use_enemy_markers", True):
            warn.append("没有“当前目标姓名板”模板：无法右键当前目标/判断它在不在屏幕上")
        if warn and not messagebox.askyesno("提醒", "\n".join(warn) + "\n\n仍然开始？"):
            return
        save_config(cfg)
        self._start_hotkeys()
        from bot.runner import BotRunner
        self.runner = BotRunner(copy.deepcopy(cfg), self.log, self.status,
                                on_home=lambda x, y, m: self.msgq.put(("call", lambda: self._save_home(x, y, m))),
                                on_centers=lambda cs: self.msgq.put(("call", lambda: self._save_centers(cs))),
                                on_route=lambda rs: self.msgq.put(("call", lambda: self._save_route(rs))))
        self.runner.start()
        self.log(f"已启动。{cfg['keys']['hotkey_stop'].upper()}=紧急停止，{cfg['keys']['hotkey_toggle'].upper()}=暂停/继续，"
                 f"{str(cfg['keys'].get('hotkey_area_check') or '-').upper()}=检查范围，"
                 f"{str(cfg['keys'].get('hotkey_set_home') or '-').upper()}=设当前位置为中心，"
                 f"{str(cfg['keys'].get('hotkey_add_center') or '-').upper()}=添加中心（多中心 {len(cfg['area'].get('centers') or [])} 个），"
                 f"{str(cfg['keys'].get('hotkey_add_route') or '-').upper()}=添加跑尸路线点（{len(cfg['corpse'].get('route') or [])} 个）")

    def stop_bot(self):
        if self.runner and self.runner.running:
            self.runner.stop()
            self.log("正在停止…")

    def _hotkey_toggle(self):
        if self.runner and self.runner.running:
            paused = self.runner.toggle_pause()
            self.log("⏸ 已暂停" if paused else "▶ 继续")
        else:
            self.msgq.put(("call", self.start_bot))

    def _hotkey_stop(self):
        if self.runner and self.runner.running:
            self.runner.stop()
            self.log("⛔ 热键紧急停止")

    def _hotkey_area(self, cmd):
        """v8.17：F9 检查范围 / F8 设当前位置为中心。运行中交给状态机（下一帧处理）；没运行就自己截一帧读插件。"""
        g = self.runner.grinder if (self.runner and self.runner.running) else None
        if g is not None:
            g.request_area(cmd)
        else:
            self.msgq.put(("call", lambda: self._area_idle(cmd)))

    def _area_idle(self, cmd):
        from bot.addon_reader import AddonReader
        from bot.capture import ScreenCapture
        from bot.navigation import area_label, area_report
        cfg = self.cfg
        try:
            # v8.24：F9/F8/F7 用同一个读坐标（最多截 6 帧；以前 F7 只截 1 帧，偶尔读坏就失败）
            cap = ScreenCapture(cfg["misc"].get("monitor", 1))
            try:
                st, why = AddonReader(cfg).read_position(cap.grab)
            finally:
                cap.close()
        except Exception as e:  # noqa: BLE001
            return self.log(f"📍 截图/读插件出错：{e}")
        if not st.ok:
            return self.log("📍 读不到 BotStatus 插件（游戏窗口要在前台、插件色块没被挡住）：" + st.reason)
        if st.pos is not None:
            self._last_pos_read = (time.monotonic(), st.pos, st.map_id)
        elif cmd in ("add", "home", "route"):
            return self.log({"add": "🚩 添加中心", "home": "📍 设中心", "route": "🗺️ 添加跑尸路线点"}[cmd]
                            + f"失败：读不到坐标 —— {why}")
        a = cfg["area"]
        if cmd == "route":                                  # v8.39 跑尸路线点
            rs = [list(c) for c in (cfg["corpse"].get("route") or [])]
            p = [round(st.pos[0], 2), round(st.pos[1], 2), int(st.map_id or 0)]
            rs.append(p)
            return self._save_route(rs, f"🗺️ 已添加跑尸路线点 #{len(rs)}：{p[0]:.2f},{p[1]:.2f}（地图 {p[2]}），共 {len(rs)} 个，已保存"
                                        + ("（#1 应该是复活点/墓地）" if len(rs) == 1 else ""))
        if cmd == "add":
            if st.pos is None:
                return self.log("🚩 添加中心失败：读不到坐标（在副本里？）")
            cs = [list(c) for c in (a.get("centers") or [])]
            p = [round(st.pos[0], 2), round(st.pos[1], 2), int(st.map_id or 0)]
            note = ""
            if not cs and int(a.get("map_id", 0) or 0) == p[2] and p[2] and \
                    (abs(float(a["center_x"]) - p[0]) > 0.01 or abs(float(a["center_y"]) - p[1]) > 0.01):
                cs.append([float(a["center_x"]), float(a["center_y"]), p[2]])
                note = f"（原来保存的中心 {a['center_x']},{a['center_y']} 记为 #1）"
            cs.append(p)
            return self._save_centers(cs, f"🚩 已添加中心 #{len(cs)}：{p[0]:.2f},{p[1]:.2f}（地图 {p[2]}），共 {len(cs)} 个，已保存{note}")
        if cmd == "home":
            if st.pos is None:
                return self.log("📍 设中心失败：读不到坐标（在副本里？）")
            if a.get("centers"):
                self._save_centers([[round(st.pos[0], 2), round(st.pos[1], 2), int(st.map_id or 0)]],
                                   "🚩 多中心列表已清空，当前位置 = 中心 #1（按 F7 继续添加）")
            return self._save_home(st.pos[0], st.pos[1], int(st.map_id or 0))
        home = (float(a["center_x"]), float(a["center_y"]), int(a.get("map_id", 0) or 0))
        self.log(area_report(st.pos, st.facing, st.map_id, home, float(a["radius"]), bool(a.get("enabled")),
                             str(cfg["keys"].get("hotkey_set_home") or "f8"))
                 + "（没在打怪：用保存的中心" + ("；开始打怪时会改用当时的位置当中心" if a.get("home_on_start", True) else "") + "）")
        self.status_vars["area"].set(area_label(st.pos, st.map_id, home, float(a["radius"]), bool(a.get("enabled"))))

    def _save_home(self, x, y, map_id):
        """把范围中心写进设置页和 config.json（只改这三个值，别的没保存的修改不动）。"""
        for key, val in (("center_x", f"{x:.2f}"), ("center_y", f"{y:.2f}"), ("map_id", str(map_id))):
            if ("area", key) in self.fields:
                self.fields[("area", key)][0].set(val)
        try:
            cfg = load_config()
            cfg["area"].update(center_x=round(x, 2), center_y=round(y, 2), map_id=int(map_id))
            save_config(cfg)
            self.cfg["area"].update(center_x=round(x, 2), center_y=round(y, 2), map_id=int(map_id))
            self.log(f"📍 范围中心 = {x:.2f}, {y:.2f}（地图 {map_id}），已保存")
        except Exception as e:  # noqa: BLE001
            self.log(f"📍 保存范围中心失败：{e}")

    def _start_hotkeys(self):
        try:
            from bot.hotkeys import HotkeyListener
        except Exception:
            return
        if self.hotkeys:
            self.hotkeys.stop()
        k = self.cfg["keys"]
        try:
            binds = {k.get("hotkey_area_check") or "": lambda: self._hotkey_area("check"),
                     k.get("hotkey_set_home") or "": lambda: self._hotkey_area("home"),
                     k.get("hotkey_add_center") or "": lambda: self._hotkey_area("add"),
                     k.get("hotkey_add_route") or "": lambda: self._hotkey_area("route")}
            binds.pop("", None)
            binds.update({k["hotkey_stop"]: self._hotkey_stop, k["hotkey_toggle"]: self._hotkey_toggle})
            self.hotkeys = HotkeyListener(binds)
            self.hotkeys.start()
        except Exception as e:
            self.hotkeys = None
            self.log(f"全局热键启动失败：{e}")

    # ================================================================ 保存/读取
    def save(self):
        try:
            save_config(self.collect())
        except ValueError as e:
            return messagebox.showerror("设置有误", str(e))
        self._start_hotkeys()
        self.log("设置已保存")

    def reload(self):
        self.cfg = load_config()
        for (section, key), (var, kind) in self.fields.items():
            var.set(self.cfg[section][key])
        for key, var in self.region_vars.items():
            var.set(_fmt(self.cfg["regions"][key]))
        for key, var in self.color_vars.items():
            var.set(_fmt(self.cfg["colors"][key]))
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for sk in self.cfg["rotation"]["skills"]:
            self._insert_skill(sk)
        self._refresh_centers()
        self._refresh_route()
        self._start_hotkeys()
        self.refresh_nameplates()
        self.log("已重新读取 config.json")

    def _open_logs(self):
        from bot.logfile import LOG_DIR
        self._open_dir(LOG_DIR)

    def _open_dir(self, path):
        os.makedirs(path, exist_ok=True)
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except AttributeError:
            self.log(path)

    def _on_close(self):
        if self.runner and self.runner.running:
            self.runner.stop()
        if self.hotkeys:
            self.hotkeys.stop()
        self.destroy()


def run():
    App().mainloop()

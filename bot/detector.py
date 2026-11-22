"""识别逻辑：血条百分比（颜色）、模板匹配（战斗/死亡/技能图标）。

纯函数部分（bar_percent / match_best 等）不依赖屏幕，可单元测试。
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .addon_reader import AddonReader
from .config import image_path, list_nameplate_templates, region_set
from .imgio import crop, imread


# ---------------------------------------------------------------- 纯函数
def color_mask(img_bgr, hsv_lo, hsv_hi):
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    lo = np.array(hsv_lo, dtype=np.uint8)
    hi = np.array(hsv_hi, dtype=np.uint8)
    if lo[0] <= hi[0]:
        return cv2.inRange(hsv, lo, hi)
    # 色相跨越 0（例如红色 170→10）
    m1 = cv2.inRange(hsv, lo, np.array([179, hi[1], hi[2]], dtype=np.uint8))
    m2 = cv2.inRange(hsv, np.array([0, lo[1], lo[2]], dtype=np.uint8), hi)
    return cv2.bitwise_or(m1, m2)


def bar_percent(img_bgr, hsv_lo, hsv_hi, column_fill_ratio=0.4, mode="rightmost") -> float:
    """血条百分比 0-100。

    每一列中符合颜色的像素比例 >= column_fill_ratio 视为“已填充”。
    rightmost：最右侧填充列的位置 / 宽度（血条中间的数字不影响结果）。
    count：填充列数量 / 宽度。
    """
    if img_bgr is None or img_bgr.size == 0:
        return 0.0
    mask = color_mask(img_bgr, hsv_lo, hsv_hi)
    col_ratio = (mask > 0).mean(axis=0)
    filled = col_ratio >= column_fill_ratio
    w = filled.shape[0]
    if w == 0 or not filled.any():
        return 0.0
    if mode == "count":
        return float(filled.sum()) * 100.0 / w
    return float(np.nonzero(filled)[0][-1] + 1) * 100.0 / w


def color_ratio(img_bgr, hsv_lo, hsv_hi) -> float:
    if img_bgr is None or img_bgr.size == 0:
        return 0.0
    return float((color_mask(img_bgr, hsv_lo, hsv_hi) > 0).mean())


def match_best(haystack_bgr, template_bgr):
    """返回 (score, (x, y)) —— TM_CCOEFF_NORMED 的最高分及位置（灰度匹配）。"""
    if haystack_bgr is None or template_bgr is None:
        return 0.0, (0, 0)
    hh, hw = haystack_bgr.shape[:2]
    th, tw = template_bgr.shape[:2]
    if th > hh or tw > hw:
        return 0.0, (0, 0)
    g1 = cv2.cvtColor(haystack_bgr, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(g1, g2, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if not np.isfinite(max_val):
        return 0.0, (0, 0)
    return float(max_val), max_loc


def brightness(img_bgr) -> float:
    if img_bgr is None or img_bgr.size == 0:
        return 0.0
    return float(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)[:, :, 2].mean())


def suggest_hsv_range(img_bgr, h_pad=10, s_min=60, v_min=50):
    """“自动取色”：对满血状态的血条截图，取饱和像素的色相中位数，给出 HSV 范围。"""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    sat = hsv[(hsv[:, 1] >= s_min) & (hsv[:, 2] >= v_min)]
    if len(sat) == 0:
        return None
    h = int(np.median(sat[:, 0]))
    s_lo = int(max(0, np.percentile(sat[:, 1], 5) - 20))
    v_lo = int(max(0, np.percentile(sat[:, 2], 5) - 20))
    lo_h, hi_h = (h - h_pad) % 180, (h + h_pad) % 180
    return [lo_h, s_lo, v_lo], [hi_h, 255, 255]


def iou(a: dict, b: dict) -> float:
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1 = min(a["x"] + a["w"], b["x"] + b["w"])
    y1 = min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def nms(boxes: list[dict], iou_thr: float = 0.3, key: str = "adj") -> list[dict]:
    """非极大值抑制：分数从高到低，与已保留框重叠超过 iou_thr 的丢掉。"""
    kept: list[dict] = []
    for b in sorted(boxes, key=lambda d: d.get(key, d.get("score", 0)), reverse=True):
        if all(iou(b, k) < iou_thr for k in kept):
            kept.append(b)
    return kept


def parse_scales(text) -> list[float]:
    if isinstance(text, (list, tuple)):
        return [float(v) for v in text] or [1.0]
    vals = [float(p) for p in str(text).replace("，", ",").split(",") if p.strip()]
    return [v for v in vals if v > 0] or [1.0]


def find_all(hay, tpl, threshold: float, max_hits: int = 200) -> list[dict]:
    """一张模板在 hay 里所有 >= threshold 的位置（未做 NMS）。"""
    th, tw = tpl.shape[:2]
    hh, hw = hay.shape[:2]
    if th < 4 or tw < 4 or th > hh or tw > hw:
        return []
    res = cv2.matchTemplate(hay, tpl, cv2.TM_CCOEFF_NORMED)
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
    ys, xs = np.nonzero(res >= threshold)
    if len(xs) == 0:
        return []
    scores = res[ys, xs]
    order = np.argsort(-scores)[:max_hits]
    return [{"x": int(xs[i]), "y": int(ys[i]), "w": tw, "h": th, "score": float(scores[i])} for i in order]


def _gray_mean(img) -> float:
    if img is None or img.size == 0:
        return 0.0
    return float(img.mean()) if img.ndim == 2 else float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean())


def detect_nameplates(region_bgr, templates: dict, ncfg: dict) -> list[dict]:
    """在 region_bgr 里找姓名板。

    templates = {"selected": [img, ...], "unselected": [img, ...]}
    返回 [{x,y,w,h,score,adj,kind}]（region 内坐标，已经 NMS；最多一个 selected）。
    选中/未选中的模板形状很像，所以先都匹配，再用“分数 - 亮度差惩罚”决定重叠处属于哪一类。
    """
    if region_bgr is None or region_bgr.size == 0:
        return []
    ds = float(ncfg.get("downscale", 1.0) or 1.0)
    color = bool(ncfg.get("color_match", True))
    hay = region_bgr if ds == 1.0 else cv2.resize(region_bgr, None, fx=ds, fy=ds, interpolation=cv2.INTER_AREA)
    if not color:
        hay = cv2.cvtColor(hay, cv2.COLOR_BGR2GRAY)
    scales = parse_scales(ncfg.get("scales", "1.0")) if ncfg.get("multiscale", True) else [1.0]
    bw = float(ncfg.get("brightness_weight", 0.6))
    iou_thr = float(ncfg.get("nms_iou", 0.3))

    cands: list[dict] = []
    for kind in ("selected", "unselected"):
        thr = float(ncfg.get(f"threshold_{kind}", 0.8))
        for tpl in templates.get(kind, []):
            if tpl is None:
                continue
            t0 = tpl if color else cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
            for sc in scales:
                f = sc * ds
                h, w = t0.shape[:2]
                t = t0 if abs(f - 1) < 1e-6 else cv2.resize(
                    t0, (max(1, round(w * f)), max(1, round(h * f))),
                    interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
                tmean = _gray_mean(t)
                hits = nms(find_all(hay, t, thr), iou_thr, key="score")
                for d in hits:
                    patch = hay[d["y"]:d["y"] + d["h"], d["x"]:d["x"] + d["w"]]
                    d["adj"] = d["score"] - bw * abs(_gray_mean(patch) - tmean) / 255.0
                    d["kind"] = kind
                    cands.append(d)
    kept = nms(cands, iou_thr)
    # 同一时间只可能有一个选中目标：多余的“选中”改判为未选中
    sel = [d for d in kept if d["kind"] == "selected"]
    for d in sel[1:]:
        d["kind"] = "unselected"
    for d in kept:  # 还原到原始分辨率
        for k in ("x", "y", "w", "h"):
            d[k] = int(round(d[k] / ds))
    return kept


def nearest_to(points: list[dict], cx: float, cy: float):
    if not points:
        return None
    return min(points, key=lambda d: (d["x"] + d["w"] / 2 - cx) ** 2 + (d["y"] + d["h"] / 2 - cy) ** 2)


# ---------------------------------------------------------------- 观测结果
@dataclass
class Observation:
    player_hp: float = 0.0
    target_hp: float = 0.0
    target_exists: bool = False
    in_combat: bool | None = None   # None = 未启用战斗识别
    player_dead: bool = False       # 死亡模板（释放灵魂对话框）命中
    ready_skills: list = field(default_factory=list)  # [(skill_dict, score)] 已按优先级排序
    nameplates: list = field(default_factory=list)    # [{x,y,w,h,score,kind}] 屏幕坐标
    selected_plate: dict | None = None
    selected_checked: bool = False  # 有“选中”模板且启用 → selected_plate 的有无才有意义
    frame_size: tuple = (1920, 1080)
    # ---- 以下来自 BotStatus 插件（source == "addon" 时可靠）
    source: str = "screen"          # addon / screen
    addon_reason: str = ""          # 插件读不到的原因
    target_kind: str = "none"       # none/enemy/tapped/friendly/dead/unknown
    lootable: bool | None = None    # None = 不知道（截图模式）
    range: str = "unknown"          # melee/ranged/far/none/unknown
    error: str | None = None        # facing/range/no_target/los/moving/form/other
    form: str = "unknown"           # bear/caster/... unknown = 不知道
    casting: bool | None = None
    mana: float | None = None
    autoattack: bool | None = None
    pos: tuple | None = None        # 地图坐标 (x, y) 0-100（插件二进制块）
    facing: float | None = None     # 朝向弧度，0=北，逆时针
    map_id: int | None = None
    gamma_note: str = ""            # 插件校准灰偏离默认 → 提示
    glue: bool = False              # v8.44：截图认出断线弹窗/登录/选角色界面（约 1 秒查一次，不管插件读不读得到）
    glue_what: str = ""
    glue_info: str = ""             # v8.45：各项分数（日志/截图说明用）
    # ---- v0.5：普通攻击距离、独立报错、伤害
    attack_range: str = "unknown"   # in/out/unknown/none/no_slot
    attack_slot: bool | None = None # 动作条上有“攻击”
    err_facing: bool = False
    err_far: bool = False
    err_los: bool = False
    since_damage: float | None = None   # 插件：距上次打出伤害秒数（inf=还没打出；None=不可用）
    aggro: bool = False             # 目标的目标是我（怪在打我）
    hp_readable: bool = True        # False = 插件画不出自己血条（战斗中读不到血量）
    hp_source: str = "screen"       # v8：血量来源 bin=插件数值 / bar=插件进度条 / screen=截图血条
    # ---- v0.7：目标姓名板标记、面向错误计数
    marker_state: str = "unknown"   # shown/no_plate/error/none/unknown
    marker_center: bool = False     # v0.10 插件：标记在目标姓名板正中间
    marker: tuple | None = None     # 屏幕上找到的标记中心 (x, y)（frame 像素）
    facing_count: int | None = None # 面向错误计数 0-7 循环；None = 旧插件
    game_menu: bool = False         # v0.7.1：游戏菜单开着
    buff_thorns: str | None = None  # v0.8：yes/low/no/unlearned/unknown；None = 旧插件
    buff_motw: str | None = None
    combo: int | None = None        # v0.9 连击点；None = 读不到
    energy: float | None = None     # v0.9 能量%；None = 读不到
    # ---- v8.28：插件 v0.11 能打的怪的 黄|蓝 标记
    moving: bool = False            # v8.28：插件块 12（GetUnitSpeed > 0）角色在动
    plate_source: str = "template"  # marker = 姓名板来自插件标记 / template = 截图模板（插件旧/读不到时）
    plate_note: str = ""            # 为什么没用标记
    plate_count: int | None = None  # 插件报的屏幕上姓名板个数（最多 7）
    red_names: list = field(default_factory=list)   # 远处只有红色名字的怪 [{x,y,w,h,px}]
    # ---- v8.31 跑尸（插件 v0.12）
    death_info: bool = False        # 插件 v0.12：有死亡/尸体信息
    tinfo: bool = False             # v8.40 插件 v0.13：目标 玩家/精英/等级差 读得到
    eatinfo: bool = False           # v8.49 插件 v0.14：充分进食 / 正在进食 读得到
    well_fed: str | None = None     # yes/low/no/unknown
    eating: bool | None = None
    repinfo: bool = False           # v8.55 插件 v0.15：耐久 / 炉石冷却 / 商人窗口 读得到
    durability: float | None = None
    hearth_cd: int | None = None
    has_hearth: bool = False
    merchant_open: bool = False
    merchant_repair: bool = False
    t_player: bool | None = None
    t_elite: bool | None = None
    t_rare: bool = False
    t_level_diff: int | None = None
    t_skull: bool = False
    ghost: bool = False             # 灵魂状态
    release_popup: bool = False     # “释放灵魂”框开着
    res_popup: bool = False         # “复活”框开着（在尸体旁边）
    corpse_pos: tuple | None = None # 尸体坐标（和 pos 同一张地图）
    res_delay: float | None = None  # 复活还要等几秒
    np_dist: int | None = None      # 姓名板显示距离（码）
    timestamp: float = 0.0
    details: dict = field(default_factory=dict)

    def summary(self) -> str:
        combat = "未启用" if self.in_combat is None else ("是" if self.in_combat else "否")
        skills = ", ".join(f"{s['name']}({sc:.2f})" for s, sc in self.ready_skills) or "无"
        n_un = sum(1 for p in self.nameplates if p["kind"] == "unselected")
        plate = ("未启用" if not self.selected_checked else ("可见" if self.selected_plate else "不可见"))
        src = "插件" if self.source == "addon" else f"截图（{self.addon_reason or '插件未启用'}）"
        extra = ""
        if self.source == "addon":
            loc = ("坐标 读不到" if self.pos is None else
                   f"坐标 {self.pos[0]:.2f},{self.pos[1]:.2f} 朝向 "
                   f"{'?' if self.facing is None else f'{math.degrees(self.facing):.0f}°'} 地图 {self.map_id}")
            extra = (f" | {loc} | 目标类型 {self.target_kind} 可拾取 {self.lootable} 距离 {self.range} "
                     f"报错 {self.error} 形态 {self.form} 施法 {self.casting} 法力 "
                     f"{'?' if self.mana is None else f'{self.mana:.0f}%'}")
            extra += " | " + self.combat_summary()
        if self.gamma_note:
            extra += f" | 提示：{self.gamma_note}"
        return (f"来源：{src}{extra} | 姓名板：未选中 {n_un} 个 / 选中 {plate} | 自己血量 {self.player_hp:.0f}% | 目标 {'有' if self.target_exists else '无'} "
                f"{self.target_hp:.0f}% | 战斗中 {combat} | 死亡对话框 {'是' if self.player_dead else '否'} | "
                f"可用技能 {skills}")


    def combat_summary(self) -> str:
        rng = {"in": "够得到", "out": "太远", "unknown": "判断不了", "none": "无目标",
               "no_slot": "动作条上没有“普通攻击”，请拖到任意格子"}.get(self.attack_range, self.attack_range)
        errs = [n for n, v in (("面向错误", self.err_facing), ("太远", self.err_far), ("视线被挡", self.err_los)) if v]
        if self.since_damage is None:
            dmg = "插件读不到（战斗日志不可用，改看目标血条）"
        elif math.isinf(self.since_damage):
            dmg = "这个目标还没打出伤害"
        else:
            dmg = f"{self.since_damage:.0f} 秒前"
        aa = {True: "开", False: "关", None: "?"}[self.autoattack]
        hp = "" if self.hp_readable else " | ⚠️ 插件画不出自己血条，战斗中读不到血量"
        mk = {"shown": "已挂上", "no_plate": "目标没有姓名板（按 V 打开敌方姓名板）", "error": "被拦截",
              "none": "无目标", "unknown": "旧插件没有"}.get(self.marker_state, self.marker_state)
        if self.marker_state == "shown":
            mk += f"，屏幕位置 {tuple(int(v) for v in self.marker)}" if self.marker else "，但屏幕上没找到"
        fc = "" if self.facing_count is None else f" | 面向错误计数 {self.facing_count}"
        return (f"普通攻击距离 {rng} | 怪在打我 {'是' if self.aggro else '否'} | 报错 {'、'.join(errs) or '无'} | 自动攻击 {aa} | "
                f"上次伤害 {dmg} | 姓名板标记 {mk}{fc}{hp}")


class TemplateCache:
    """模板缓存在内存；文件修改后自动重新读取。"""

    def __init__(self):
        self._cache: dict[str, tuple[float, np.ndarray | None]] = {}

    def get(self, rel: str):
        if not rel:
            return None
        path = image_path(rel)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        hit = self._cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
        img = imread(path)
        self._cache[path] = (mtime, img)
        return img


class Detector:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.templates = TemplateCache()
        self.addon = AddonReader(cfg)

    def _bar(self, frame, region_key, color_key):
        region = self.cfg["regions"].get(region_key)
        if not region_set(region):
            return 0.0
        c, d = self.cfg["colors"], self.cfg["detect"]
        return bar_percent(crop(frame, region), c[color_key + "_lo"], c[color_key + "_hi"],
                           d["column_fill_ratio"], d["bar_mode"])

    def _template_in_region(self, frame, region_key, rel, threshold=None):
        region = self.cfg["regions"].get(region_key)
        tpl = self.templates.get(rel)
        if not region_set(region) or tpl is None:
            return None
        score, _ = match_best(crop(frame, region), tpl)
        thr = self.cfg["detect"]["template_threshold"] if threshold is None else threshold
        return score >= thr, score

    def nameplate_templates(self) -> dict:
        return {k: [t for t in (self.templates.get(p) for p in list_nameplate_templates(k)) if t is not None]
                for k in ("selected", "unselected")}

    def nameplates(self, frame):
        ncfg = self.cfg.get("nameplate", {})
        region = self.cfg["regions"].get("nameplates")
        tpls = self.nameplate_templates()
        if not ncfg.get("enabled", True) or not region_set(region) or not (tpls["selected"] or tpls["unselected"]):
            return [], False
        plates = detect_nameplates(crop(frame, region), tpls, ncfg)
        x0, y0 = max(0, region[0]), max(0, region[1])
        for p in plates:
            p["x"] += x0
            p["y"] += y0
        return plates, bool(tpls["selected"])

    def ready_skills(self, frame):
        rot = self.cfg["rotation"]
        skills = [s for s in rot["skills"] if s.get("enabled", True)]
        skills.sort(key=lambda s: s.get("priority", 99))
        if rot["mode"] != "template":
            return [(s, 1.0) for s in skills]
        region = self.cfg["regions"].get("action_bar")
        if not region_set(region):
            return []
        bar = crop(frame, region)
        out = []
        for s in skills:
            tpl = self.templates.get(s.get("image", ""))
            if tpl is None:
                continue
            score, (x, y) = match_best(bar, tpl)
            if score < float(s.get("threshold", 0.8)):
                continue
            th, tw = tpl.shape[:2]
            patch = bar[y:y + th, x:x + tw]
            # 冷却中/怒气不足时图标会变暗 —— 亮度明显低于模板则视为不可用
            if brightness(patch) < brightness(tpl) * float(rot.get("brightness_ratio", 0.75)):
                continue
            out.append((s, score))
        return out

    def observe(self, frame, want_nameplates: bool = True) -> Observation:
        cfg = self.cfg
        obs = Observation(timestamp=time.time())
        a = self.addon.read(frame)
        obs.addon_reason = a.reason
        if a.ok:
            self._fill_from_addon(obs, a)
        else:
            self._fill_from_screen(obs, frame)
        self._check_glue(obs, frame)
        obs.ready_skills = self.ready_skills(frame)
        obs.frame_size = (frame.shape[1], frame.shape[0])
        if obs.source == "addon" and obs.target_exists and obs.marker_state in ("shown", "unknown") \
                and cfg.get("stationary", {}).get("use_marker", True):
            obs.marker = self.find_target_marker(frame)
        use_marks, note = self._marks_usable(a, obs)
        obs.plate_source, obs.plate_note = ("marker" if use_marks else "template"), note
        if a.ok:
            obs.plate_count = a.plate_count
        if want_nameplates:  # 战斗中不需要，省时间
            if use_marks:
                obs.nameplates, obs.selected_checked = self.marker_plates(frame), True
                if cfg.get("nameplate", {}).get("far_names", True) and not obs.in_combat:
                    obs.red_names = self.red_names(frame, obs.nameplates, obs.marker)
            else:
                obs.nameplates, obs.selected_checked = self.nameplates(frame)
        obs.selected_plate = next((p for p in obs.nameplates if p["kind"] == "selected"), None)
        if obs.selected_plate is None and obs.marker is not None and obs.marker_center:
            # v8.17：v0.10 插件的标记在目标姓名板正中间（可能挡住“选中”模板）→ 用标记位置当选中姓名板
            mx, my = obs.marker
            obs.selected_plate = {"x": int(mx) - 60, "y": int(my) - 7, "w": 120, "h": 14, "score": 1.0, "adj": 1.0,
                                  "kind": "selected", "from_marker": True}
        if obs.selected_checked and obs.source == "screen":
            mode = cfg.get("nameplate", {}).get("target_confirm", "hp")
            if mode == "nameplate":
                obs.target_exists = obs.selected_plate is not None
            elif mode == "either":
                obs.target_exists = obs.target_exists or obs.selected_plate is not None
            obs.target_kind = "enemy" if obs.target_exists and obs.target_hp > cfg["detect"]["dead_pct"] else "none"
        return obs

    def _check_glue(self, obs, frame):
        """v8.44：看看是不是断线弹窗 / 登录界面 / 选角色界面（给自动重连用）。
        不能只在插件读不到时查：选角色界面左上角的 logo/天空会被插件读取器误读成“插件色块”（日志 00:41 实测）。
        一次约 50ms → 每 check_every_s 秒（默认 1）查一次，中间用上次的结果。"""
        rc = self.cfg.get("reconnect", {})
        if not rc.get("enabled", True):
            return
        now = time.monotonic()
        # v8.45：弹窗（黑底+红按钮）只在插件读不到时算数 —— 游戏世界里“释放灵魂”、Esc 菜单也长这样
        dialog_ok = obs.source != "addon"
        last = getattr(self, "_glue_last", None)
        if last is not None and last[1] == dialog_ok and now - last[0] < float(rc.get("check_every_s", 1.0)):
            obs.glue, obs.glue_what, obs.glue_info = last[2], last[3], last[4]
            return
        try:
            if getattr(self, "_glue", None) is None:
                from .glue import GlueDetector
                self._glue = GlueDetector()
            from .glue import score_text
            obs.glue, obs.glue_what, sc = self._glue.check(frame, dialog_ok=dialog_ok)
            obs.glue_info = score_text(sc)
        except Exception as e:  # noqa: BLE001
            obs.glue, obs.glue_what, obs.glue_info = False, "", f"出错 {type(e).__name__}"
        self._glue_last = (now, dialog_ok, obs.glue, obs.glue_what, obs.glue_info)

    def _marks_usable(self, a, obs):
        """v8.28：能不能用插件 v0.11 的 黄|蓝 标记代替模板。返回 (能用, 不能用的原因)。"""
        if not self.cfg.get("nameplate", {}).get("use_enemy_markers", True):
            return False, "设置里关了“用插件标记找怪”"
        if obs.source != "addon":
            return False, "读不到插件"
        if not a.enemy_marks:
            return False, "插件不是 v0.11（复制新插件后在游戏里 /reload）"
        if not a.enemy_api_ok:
            return False, "插件读不到姓名板（姓名板接口被拦）"
        if a.enemy_unknown:
            return False, "插件判断不了有的怪能不能打（机密值）"
        return True, ""

    def _status_exclude(self, blocks=42):   # v8.40：插件 v0.13 第 1 行 40 块；v8.49 / v0.14：42 块
        a = self.cfg.get("addon", {})
        x0, y0, bs = int(a.get("x", 0)), int(a.get("y", 0)), int(a.get("block", 10))
        return (x0, y0, blocks * bs + 10, 5 * bs + 10)

    def marker_plates(self, frame) -> list:
        """v8.28：插件 v0.11 画的 黄|蓝 标记 → 当成“未选中姓名板”（20x12 小框，中心 = 姓名板中心）。"""
        from .marker import find_enemy_markers
        try:
            pts = find_enemy_markers(frame, exclude=self._status_exclude())
        except Exception:  # noqa: BLE001
            return []
        return [{"x": int(round(mx)) - 10, "y": int(round(my)) - 6, "w": 20, "h": 12, "score": 1.0, "adj": 1.0,
                 "kind": "unselected", "from_marker": True} for mx, my in pts]

    def red_names(self, frame, plates=(), target_marker=None) -> list:
        """v8.28：远处只显示红色名字的怪。不找：左上角界面、自己角色周围、已经有标记的姓名板附近。
        v8.34：在整个游戏窗口里找（按实际截图大小，不再用旧的“姓名板区域”裁剪），另外排除 red_ui_exclude 里的界面区域。"""
        from .marker import find_red_names
        H, W = frame.shape[:2]
        st = self.cfg.get("stationary", {})
        mx, my = (st.get("plate_ui_margin") or [320, 140])[:2]
        ex = [(0, 0, int(mx), int(my)), self._status_exclude(),
              (int(W * 0.4), int(H * 0.34), int(W * 0.2), int(H * 0.28)),     # 自己角色（脚下光圈、头上名字）
              (0, int(H * 0.85), W, H)]                                        # 动作条
        for r in self.cfg.get("nameplate", {}).get("red_ui_exclude") or []:
            try:
                fx, fy, fw, fh = (float(v) for v in r[:4])
            except (TypeError, ValueError):
                continue
            ex.append((int(W * fx), int(H * fy), int(round(W * fw)), int(round(H * fh))))
        try:
            names = find_red_names(frame, None, exclude=ex)                    # v8.34：整个窗口
        except Exception:  # noqa: BLE001
            return []
        pts = [(p["x"] + p["w"] / 2, p["y"] + p["h"] / 2) for p in plates]
        if target_marker:
            pts.append(tuple(target_marker))
        out = []
        for n in names:
            cx, cy = n["x"] + n["w"] / 2, n["y"] + n["h"] / 2
            if any(abs(cx - px) < max(70, n["w"] / 2 + 20) and -60 < py - cy < 50 for px, py in pts):
                continue                  # 有标记的姓名板自带的名字
            out.append(n)
        return out

    def fresh_plates(self, frame):
        """v8.28 点之前再截一张图：返回 (未选中姓名板列表, 来源)。"""
        a = self.addon.read(frame)
        obs = Observation()
        obs.source = "addon" if a.ok else "screen"
        if self._marks_usable(a, obs)[0]:
            return self.marker_plates(frame), "marker"
        plates, _ = self.nameplates(frame)
        return [p for p in plates if p.get("kind") == "unselected"], "template"

    def find_target_marker(self, frame):
        from .marker import find_marker
        a = self.cfg.get("addon", {})
        x0, y0, bs = int(a.get("x", 0)), int(a.get("y", 0)), int(a.get("block", 10))
        try:
            return find_marker(frame, exclude=(x0, y0, 42 * bs + 10, 6 * bs + 10))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _fill_from_addon(obs: Observation, a):
        obs.source = "addon"
        obs.player_hp = a.player_hp
        obs.player_dead = a.player_dead
        obs.in_combat = a.in_combat
        obs.target_kind = a.target_kind
        obs.target_exists = a.target_kind != "none"
        obs.target_hp = a.target_hp
        if a.target_kind == "unknown" and a.target_hp <= 0:
            obs.target_hp = 100.0  # 读不到血量时别把活怪当成死的
        obs.lootable = a.lootable
        obs.range = a.range
        obs.error = a.error
        obs.form = a.form
        obs.casting = a.casting
        obs.mana = a.mana
        obs.autoattack = a.autoattack
        obs.pos, obs.facing, obs.map_id = a.pos, a.facing, a.map_id
        obs.gamma_note = a.gamma_note
        obs.attack_range, obs.attack_slot = a.attack_range, a.attack_slot
        obs.err_facing, obs.err_far, obs.err_los = a.err_facing, a.err_far, a.err_los
        obs.since_damage = a.since_damage
        obs.hp_readable = a.hp_bar_ok
        obs.aggro = a.aggro
        obs.hp_source = a.hp_source or "bar"
        obs.marker_state = a.marker_state
        obs.marker_center = a.marker_center
        obs.facing_count = a.facing_count
        obs.game_menu = a.game_menu
        obs.buff_thorns = a.buff_thorns
        obs.buff_motw = a.buff_motw
        obs.combo = a.combo
        obs.moving = bool(a.moving)
        obs.energy = a.energy
        obs.death_info, obs.ghost = a.death_info, a.ghost
        obs.release_popup, obs.res_popup = a.release_popup, a.res_popup
        obs.corpse_pos, obs.res_delay, obs.np_dist = a.corpse_pos, a.res_delay, a.np_dist
        obs.tinfo = getattr(a, "tinfo", False)                      # v8.40 / 插件 v0.13
        obs.t_player, obs.t_elite = getattr(a, "t_player", None), getattr(a, "t_elite", None)
        obs.t_rare, obs.t_skull = getattr(a, "t_rare", False), getattr(a, "t_skull", False)
        obs.t_level_diff = getattr(a, "t_level_diff", None)
        obs.eatinfo = getattr(a, "eatinfo", False)                  # v8.49 / 插件 v0.14
        obs.well_fed, obs.eating = getattr(a, "well_fed", None), getattr(a, "eating", None)
        obs.repinfo = getattr(a, "repinfo", False)                  # v8.55 / 插件 v0.15
        for k in ("durability", "hearth_cd", "has_hearth", "merchant_open", "merchant_repair"):
            setattr(obs, k, getattr(a, k, getattr(obs, k)))

    def _fill_from_screen(self, obs: Observation, frame):
        cfg = self.cfg
        obs.source = "screen"
        obs.player_hp = self._bar(frame, "player_hp", "player_hp")
        obs.target_hp = self._bar(frame, "target_hp", "target_hp")
        dead_pct = cfg["detect"]["dead_pct"]

        tf = self._template_in_region(frame, "target_frame", "target_frame.png")
        if tf is not None:
            obs.target_exists = tf[0]
            obs.details["target_frame_score"] = tf[1]
        else:
            obs.target_exists = obs.target_hp > dead_pct

        mode = cfg["detect"]["combat_mode"]
        if mode == "template":
            r = self._template_in_region(frame, "combat", "combat.png")
            obs.in_combat = None if r is None else r[0]
            if r is not None:
                obs.details["combat_score"] = r[1]
        elif mode == "color" and region_set(cfg["regions"].get("combat")):
            ratio = color_ratio(crop(frame, cfg["regions"]["combat"]),
                                cfg["colors"]["combat_lo"], cfg["colors"]["combat_hi"])
            obs.details["combat_color_ratio"] = ratio
            obs.in_combat = ratio >= cfg["detect"]["combat_color_ratio"]

        d = self._template_in_region(frame, "dead", "dead.png")
        if d is not None:
            obs.player_dead = d[0]
            obs.details["dead_score"] = d[1]
        obs.target_kind = "enemy" if obs.target_exists and obs.target_hp > dead_pct else "none"

    def draw_debug(self, frame, obs: Observation):
        img = frame.copy()
        colors = {"player_hp": (0, 255, 0), "target_hp": (0, 200, 255), "combat": (0, 0, 255),
                  "action_bar": (255, 200, 0), "dead": (255, 0, 255), "target_frame": (255, 255, 255),
                  "nameplates": (160, 160, 160)}
        for key, col in colors.items():
            r = self.cfg["regions"].get(key)
            if region_set(r):
                x, y, w, h = r
                cv2.rectangle(img, (x, y), (x + w, y + h), col, 2)
                cv2.putText(img, key, (x, max(12, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
        for p in obs.nameplates:
            sel = p["kind"] == "selected"
            col = (0, 255, 0) if sel else (0, 220, 255)
            cv2.rectangle(img, (p["x"], p["y"]), (p["x"] + p["w"], p["y"] + p["h"]), col, 3 if sel else 1)
            cv2.putText(img, f"{'SEL' if sel else 'np'} {p['score']:.2f}", (p["x"], p["y"] - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1)
        tgt = nearest_to([p for p in obs.nameplates if p["kind"] == "unselected"],
                         obs.frame_size[0] / 2, obs.frame_size[1] / 2)
        if tgt is not None:
            off = int(self.cfg.get("nameplate", {}).get("click_offset_y", 0))
            cx, cy = tgt["x"] + tgt["w"] // 2, tgt["y"] + tgt["h"] // 2 + off
            cv2.drawMarker(img, (cx, cy), (0, 0, 255), cv2.MARKER_CROSS, 18, 2)
        txt = (f"[{obs.source}] HP {obs.player_hp:.0f}%  T {obs.target_hp:.0f}% exists={obs.target_exists} "
               f"combat={obs.in_combat} dead={obs.player_dead} ready={len(obs.ready_skills)}")
        cv2.putText(img, txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        return img

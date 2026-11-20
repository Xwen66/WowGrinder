"""读取 BotStatus v0.7 插件画在屏幕左上角的色块（编码表见 addon/README_WA.md）。

v0.4 抗伽马/亮度：所有颜色的每个通道只用 0 或 255，读取时每个通道按 128 二值化。
  第 1 行 y=0..9      ：24 个状态块（8 种纯色；16-21 为 v0.5 新增；22 姓名板标记、23 面向错误计数为 v0.7 新增）
  进度条 y=10..25     ：4 条（自己血量/目标血量/怒气/法力），绿色像素数 = 百分比（战斗中用）
  二进制 A 行 y=30..39：坐标 X(7 块) Y(7 块) 朝向(7 块) 距上次伤害秒数(4 块)
  二进制 B 行 y=40..49：地图 ID(7) 血量%(4) 目标血量%(4) 怒气%(4) 法力%(4) 版本(1) 校准灰(1)
每块 3 位（R,G,B）。一个数值 = [有效位][数据位][4 位校验]，校验 = (数据各 4 位段之和 + 5) % 16。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

ADDON_VERSION = 7
ACCEPTED_VERSIONS = (5, 6, 7)
VERSION_COLOR = {"white": 7, "yellow": 6, "magenta": 5, "red": 4}
# 3 位 (R,G,B) → 颜色名
BITS_NAME = {(0, 0, 0): "black", (1, 1, 1): "white", (1, 0, 0): "red", (0, 1, 0): "green",
             (0, 0, 1): "blue", (1, 1, 0): "yellow", (0, 1, 1): "cyan", (1, 0, 1): "magenta"}
PALETTE = {v: tuple(255 * b for b in k) for k, v in BITS_NAME.items()}

LIFE_DEAD = ("red", "yellow")                       # 红=尸体，黄=灵魂
TARGET_KIND = {"black": "none", "red": "enemy", "yellow": "tapped", "blue": "friendly", "white": "dead"}
RANGE = {"green": "melee", "yellow": "ranged", "red": "far", "black": "none"}
ERRORS = {"black": None, "red": "facing", "yellow": "range", "blue": "no_target", "magenta": "los",
          "cyan": "moving", "green": "form", "white": "other"}
ATTACK_RANGE = {"green": "in", "red": "out", "magenta": "unknown", "blue": "no_slot", "black": "none"}
MARKER = {"green": "shown", "red": "no_plate", "magenta": "error", "black": "none"}
FORMS = {"red": "bear", "black": "caster", "yellow": "cat", "blue": "travel", "white": "other"}

# 二进制字段：(行, 起始块, 数据位数)
FIELDS = {
    "x": (0, 0, 16), "y": (0, 7, 16), "facing": (0, 14, 16), "since_damage": (0, 21, 7),
    "map_id": (1, 0, 16), "hp": (1, 7, 7), "target_hp": (1, 11, 7), "rage": (1, 15, 7), "mana": (1, 19, 7),
}
# v0.12 二进制 C 行：尸体坐标 / 复活还要等几秒 / 姓名板显示距离；C 行最后一块青 = v0.12 签名
FIELDS_C = {"corpse_x": (2, 0, 16), "corpse_y": (2, 7, 16), "res_delay": (2, 14, 7), "np_dist": (2, 18, 7)}
SIG_C_BLOCK = (2, 24)
SIG_C13_BLOCK = (2, 23)      # v0.13：C 行第 23 块洋红 = 块 36-39 目标信息有效
VERSION_BLOCK = (1, 23)
BUFF = {"green": "yes", "yellow": "low", "black": "no", "blue": "unlearned", "magenta": "unknown"}
CALIB_BLOCK = (1, 24)
BIN_TOP = 30


@dataclass
class AddonState:
    ok: bool = False
    reason: str = ""
    player_hp: float = 0.0
    player_dead: bool = False
    in_combat: bool = False
    target_kind: str = "none"      # none/enemy/tapped/friendly/dead/unknown
    target_hp: float = 0.0
    lootable: bool = False
    range: str = "unknown"         # melee/ranged/far/none/unknown
    error: str | None = None       # facing/range/no_target/los/moving/form/other
    form: str = "unknown"          # bear/caster/cat/travel/other/unknown
    casting: bool = False
    rage: float | None = None
    moving: bool = False
    aggro: bool = False
    autoattack: bool | None = None
    mana: float | None = None
    pos: tuple | None = None       # (x, y) 0-100，与游戏里显示的坐标一致；None = 读不到
    facing: float | None = None    # 弧度 0..2π，0=北，逆时针增加
    map_id: int | None = None
    # v0.5
    attack_range: str = "unknown"  # in=普通攻击够得到 / out=太远 / unknown / none=无目标 / no_slot=动作条没有“攻击”
    attack_slot: bool | None = None  # 动作条上找到“攻击”了吗
    err_facing: bool = False       # 最近 1.5 秒“面向错误”
    err_far: bool = False          # 最近 1.5 秒“太远”
    err_los: bool = False          # 最近 1.5 秒“视线被挡”
    since_damage: float | None = None  # 距上次对目标打出伤害的秒数；inf=这个目标还没打出过；None=战斗日志不可用
    hp_bar_ok: bool = True         # 插件能画出自己血条（战斗中血量就靠它）
    target_bar_ok: bool = True
    version: int = 0
    # v0.7
    # v0.12 跑尸
    death_info: bool = False       # 插件 v0.12（有下面这些）
    ghost: bool = False            # 灵魂状态（已释放）
    release_popup: bool = False    # “释放灵魂”框开着
    res_popup: bool = False        # “复活”框开着 = 在尸体旁边、能复活
    corpse_pos: tuple | None = None  # 尸体坐标（和 pos 同一张地图）
    res_delay: float | None = None   # 复活还要等几秒
    np_dist: int | None = None     # 姓名板显示距离（码）
    np_ok: bool = False
    ghost_marks: bool = False      # 灵魂状态下标记 = 会打我的怪
    # v0.13 目标信息（Tab 找怪用）
    tinfo: bool = False            # 插件 v0.13（块 36-39 有效）
    t_player: bool | None = None   # 目标是玩家；None = 读不到
    t_elite: bool | None = None    # 目标是精英（elite / rareelite / worldboss）；None = 读不到
    t_rare: bool = False           # 稀有（非精英）
    t_level_diff: int | None = None  # 目标等级 - 自己等级；None = 没目标/读不到
    t_skull: bool = False          # 骷髅（??）
    marker_center: bool = False    # v0.10：标记在目标姓名板正中间（块 28 白），可以右键点它
    marker_state: str = "unknown"  # shown=标记挂在目标姓名板上 / no_plate / error / none=没目标 / unknown=旧插件
    facing_count: int | None = None  # 面向错误计数 0-7（循环）；None = 旧插件
    hp_source: str = ""            # bin=二进制数值 / bar=进度条
    game_menu: bool = False        # v0.7.1：游戏菜单（Esc 菜单）开着
    buff_thorns: str | None = None # v0.8：yes/low/no/unlearned/unknown；None = 旧插件
    buff_motw: str | None = None
    combo: int | None = None       # v0.9：连击点 0-7；None = 读不到/旧插件
    energy: float | None = None    # v0.9：能量%（7 档，约 ±7%）；None = 读不到/旧插件
    enemy_marks: bool = False      # v0.11：插件给能打的怪的姓名板画了 黄|蓝 标记（块 28 绿）
    enemy_api_ok: bool | None = None  # v0.11 块 32 G：姓名板接口能用
    enemy_unknown: bool = False    # v0.11 块 32 B：有怪的状态读不到（机密值）→ 标记不全，Python 改用模板
    plate_count: int | None = None # v0.11 块 33：屏幕上姓名板个数（最多 7）
    calib: int | None = None       # 校准灰实际读到的值（默认伽马 ≈ 128）
    gamma_note: str = ""           # 非空 = 伽马/亮度不是默认（仅提示）


# ------------------------------------------------------------------ 编解码（纯函数）
def checksum(v: int) -> int:
    cs = 5
    while v > 0:
        cs += v % 16
        v //= 16
    return cs % 16


def encode_bits(value, nbits: int) -> list[int]:
    """与 Lua Bits() 一致。value=None → 有效位 0。"""
    valid = value is not None
    v = 0
    if valid:
        v = max(0, min(2 ** nbits - 1, int(math.floor(value + 0.5))))
    bits = [1 if valid else 0] + [(v >> k) & 1 for k in range(nbits - 1, -1, -1)]
    cs = checksum(v)
    bits += [(cs >> k) & 1 for k in range(3, -1, -1)]
    while len(bits) % 3:
        bits.append(0)
    return bits


def decode_bits(bits: list[int], nbits: int):
    """有效位为 0 或校验不对 → None。"""
    if len(bits) < 1 + nbits + 4 or bits[0] != 1:
        return None
    v = 0
    for b in bits[1:1 + nbits]:
        v = v * 2 + b
    cs = 0
    for b in bits[1 + nbits:1 + nbits + 4]:
        cs = cs * 2 + b
    return v if cs == checksum(v) else None


def n_blocks(nbits: int) -> int:
    return math.ceil((1 + nbits + 4) / 3)


def to_bits(rgb, threshold: int = 128):
    return tuple(1 if c >= threshold else 0 for c in rgb)


def classify(rgb, threshold: int = 128) -> str:
    """每个通道按阈值二值化 → 8 种纯色之一（伽马/亮度改变不影响）。"""
    return BITS_NAME[to_bits(rgb, threshold)]


def rgb_at(frame_bgr, x: int, y: int):
    b, g, r = frame_bgr[y, x][:3]
    return int(r), int(g), int(b)


# ------------------------------------------------------------------ 读取
class AddonReader:
    def __init__(self, cfg: dict, clock=time.monotonic):
        self.cfg = cfg
        self.clock = clock
        self._last_beat = None
        self._last_change = None

    def _a(self):
        return self.cfg.get("addon", {})

    def _geom(self):
        a = self._a()
        return int(a.get("x", 0)), int(a.get("y", 0)), int(a.get("block", 10))

    def _block(self, frame, i):
        x0, y0, bs = self._geom()
        return rgb_at(frame, x0 + i * bs + bs // 2, y0 + bs // 2)

    def _bin_block(self, frame, row, i):
        x0, y0, bs = self._geom()
        return rgb_at(frame, x0 + i * bs + bs // 2, y0 + BIN_TOP + row * bs + bs // 2)

    def _bar(self, frame, n):
        """第 n 条进度条（0=自己血量 1=目标血量 2=怒气 3=法力），返回 0-100。"""
        x0, y0, bs = self._geom()
        y = y0 + bs + n * 4 + 1
        row = frame[y, x0:x0 + 100]
        g, r, b = row[:, 1].astype(int), row[:, 2].astype(int), row[:, 0].astype(int)
        return float(((g >= 128) & (r < 128) & (b < 128)).sum())

    def field(self, frame, name):
        row, start, nbits = FIELDS[name] if name in FIELDS else FIELDS_C[name]
        bits: list[int] = []
        for j in range(n_blocks(nbits)):
            bits += to_bits(self._bin_block(frame, row, start + j))
        return decode_bits(bits, nbits)

    def field_status(self, frame, name) -> str:
        """v8.24：'ok' / 'nil'（插件说没有这个值，比如副本里没有坐标）/ 'bad'（色块校验不对：画面撕裂/被挡）。"""
        row, start, nbits = FIELDS[name]
        bits: list[int] = []
        for j in range(n_blocks(nbits)):
            bits += to_bits(self._bin_block(frame, row, start + j))
        if not bits or bits[0] != 1:
            return "nil"
        return "ok" if decode_bits(bits, nbits) is not None else "bad"

    def read_position(self, grab, tries: int = 6, delay: float = 0.12, sleep=None):
        """v8.24：F7/F8/F9 共用的读坐标：最多截 tries 帧，读到坐标就返回。返回 (AddonState, 失败原因或 "")。"""
        import time as _t
        sleep = sleep or _t.sleep
        st, why = None, ""
        for i in range(max(1, tries)):
            frame = grab()
            st = self.read(frame)
            if st.ok and st.pos is not None:
                return st, ""
            if not st.ok:
                why = st.reason
            else:
                fs = {n: self.field_status(frame, n) for n in ("x", "y")}
                why = ("插件说现在没有坐标（副本/战场里，或刚加载）" if "nil" in fs.values()
                       else f"坐标色块校验不对（x={fs['x']} y={fs['y']}：画面撕裂或被挡住）")
            if i + 1 < tries:
                sleep(delay)
        return st, why

    def read(self, frame) -> AddonState:
        st = AddonState()
        a = self._a()
        if not a.get("enabled", True):
            st.reason = "插件读取已关闭"
            return st
        x0, y0, bs = self._geom()
        if frame is None or frame.shape[0] < y0 + BIN_TOP + 2 * bs or frame.shape[1] < x0 + 25 * bs:
            st.reason = "画面太小"
            return st
        now = self.clock()
        r, g, b = self._block(frame, 0)
        hb = to_bits((r, g, b))
        if hb[0] and not hb[1] and not hb[2]:
            st.reason = "插件报错（心跳块纯红），看游戏聊天框"
            return st
        if not (hb[0] and hb[1]):
            self._last_beat = self._last_change = None
            st.reason = "没检测到心跳块（插件没开/被遮挡/位置不对）"
            return st
        st.version = VERSION_COLOR.get(classify(self._bin_block(frame, *VERSION_BLOCK)), 0)
        if st.version not in ACCEPTED_VERSIONS:
            old = f"（读到 v0.{st.version}）" if st.version else ""
            st.reason = f"插件版本不对{old}：需要 BotStatus v0.7，复制新插件后在游戏里 /reload"
            return st
        if self._last_beat is None or b != self._last_beat:
            self._last_beat, self._last_change = b, now
        if now - self._last_change > float(a.get("stale_s", 3.0)):
            st.reason = "心跳停止（游戏卡住或插件停了）"
            return st

        c = lambda i: classify(self._block(frame, i))  # noqa: E731
        st.ok = True
        st.player_dead = c(2) in LIFE_DEAD
        st.in_combat = c(3) == "green"
        st.target_kind = TARGET_KIND.get(c(4), "unknown")
        st.lootable = c(6) == "green"
        st.range = RANGE.get(c(7), "unknown")
        st.error = ERRORS.get(c(8))
        st.form = FORMS.get(c(9), "unknown")
        st.casting = c(10) in ("green", "blue")
        st.moving = c(12) == "green"
        st.aggro = c(13) == "green"
        aa = c(14)
        st.autoattack = True if aa == "green" else (False if aa == "black" else None)

        st.attack_range = ATTACK_RANGE.get(c(16), "unknown")
        st.err_facing = c(17) == "red"
        st.err_far = c(18) == "yellow"
        st.err_los = c(19) == "magenta"
        st.attack_slot = {"green": True, "blue": False}.get(c(20))
        bs21 = c(21)
        st.hp_bar_ok = bs21 != "red"
        st.target_bar_ok = bs21 not in ("red", "yellow")
        if st.version >= 7:
            st.marker_state = MARKER.get(c(22), "unknown")
            fb = to_bits(self._block(frame, 23))
            st.facing_count = fb[0] * 4 + fb[1] * 2 + fb[2]
            st.game_menu = c(24) == "white"
            if frame.shape[1] >= x0 + 29 * bs and c(27) == "cyan" and c(28) in ("yellow", "white", "green"):   # v0.8 签名（v0.10 白，v0.11 绿）
                st.marker_center = c(28) in ("white", "green")
                if c(28) == "green" and frame.shape[1] >= x0 + 34 * bs:
                    eb = to_bits(self._block(frame, 32))
                    if eb[0]:                                              # v0.11 签名位
                        st.enemy_marks = True
                        st.enemy_api_ok = bool(eb[1])
                        st.enemy_unknown = bool(eb[2])
                        nb = to_bits(self._block(frame, 33))
                        st.plate_count = nb[0] * 4 + nb[1] * 2 + nb[2]
                st.buff_thorns = BUFF.get(c(25), "unknown")
                st.buff_motw = BUFF.get(c(26), "unknown")
                if frame.shape[1] >= x0 + 32 * bs:
                    vb = to_bits(self._block(frame, 31))
                    if vb[2] == 1:                                         # v0.9 签名
                        if vb[0]:
                            b = to_bits(self._block(frame, 29))
                            st.combo = b[0] * 4 + b[1] * 2 + b[2]
                        if vb[1]:
                            b = to_bits(self._block(frame, 30))
                            st.energy = round((b[0] * 4 + b[1] * 2 + b[2]) / 7 * 100, 1)
        if frame.shape[1] >= x0 + 36 * bs and frame.shape[0] >= y0 + BIN_TOP + 3 * bs \
                and to_bits(self._block(frame, 35))[2] == 1 and classify(self._bin_block(frame, *SIG_C_BLOCK)) == "cyan":
            st.death_info = True                                           # v0.12
            st.ghost = c(2) == "yellow"
            db = to_bits(self._block(frame, 34))
            st.release_popup, st.res_popup = bool(db[0]), bool(db[1])
            sb = to_bits(self._block(frame, 35))
            st.np_ok, st.ghost_marks = bool(sb[0]), bool(sb[1])
            cx, cy = self.field(frame, "corpse_x"), self.field(frame, "corpse_y")
            if db[2] and cx is not None and cy is not None:
                st.corpse_pos = (cx * 100.0 / 65535, cy * 100.0 / 65535)
            rd = self.field(frame, "res_delay")
            st.res_delay = None if rd is None else float(rd)
            st.np_dist = self.field(frame, "np_dist")
            if frame.shape[1] >= x0 + 40 * bs and to_bits(self._block(frame, 37))[2] == 1 \
                    and classify(self._bin_block(frame, *SIG_C13_BLOCK)) == "magenta":
                st.tinfo = True                                            # v0.13
                tb, vb = to_bits(self._block(frame, 36)), to_bits(self._block(frame, 37))
                st.t_player = bool(tb[0]) if vb[0] else None
                st.t_elite = bool(tb[1]) if vb[1] else None
                st.t_rare = bool(tb[2]) and bool(vb[1])
                hb, lb = to_bits(self._block(frame, 38)), to_bits(self._block(frame, 39))
                code = hb[0] * 32 + hb[1] * 16 + hb[2] * 8 + lb[0] * 4 + lb[1] * 2 + lb[2]
                st.t_skull = code == 63
                st.t_level_diff = None if code == 0 else (99 if code == 63 else code - 32)
        sd = self.field(frame, "since_damage")
        st.since_damage = None if sd is None else (math.inf if sd >= 127 else float(sd))

        # 百分比：战斗中一律读进度条（机密值，二进制无效）；脱战时二进制更精确，无效再读进度条
        src = {}

        def pct(name, bar_n):
            if not st.in_combat:
                v = self.field(frame, name)
                if v is not None and v <= 100:
                    src[name] = "bin"
                    return float(v)
            src[name] = "bar"
            return self._bar(frame, bar_n)

        st.player_hp = pct("hp", 0)
        st.hp_source = src["hp"]
        st.target_hp = 0.0 if st.target_kind == "none" else pct("target_hp", 1)
        st.rage = pct("rage", 2)
        st.mana = pct("mana", 3)

        vx, vy, vf = self.field(frame, "x"), self.field(frame, "y"), self.field(frame, "facing")
        if vx is not None and vy is not None:
            st.pos = (vx * 100.0 / 65535, vy * 100.0 / 65535)
        if vf is not None:
            st.facing = vf * 2 * math.pi / 65535
        st.map_id = self.field(frame, "map_id")

        cal = self._bin_block(frame, *CALIB_BLOCK)
        st.calib = int(round(sum(cal) / 3))
        if abs(st.calib - 128) > int(a.get("calib_tolerance", 12)):
            st.gamma_note = (f"游戏伽马/亮度不是默认值（校准灰读到 {st.calib}，默认约 128）。"
                             "v0.4 已抗伽马，仅提示")
        return st


# ------------------------------------------------------------------ 测试用：画一帧
def apply_gamma(img, gamma: float = 1.2, brightness: int = 0):
    """模拟游戏伽马：v -> 255*(v/255)**(1/gamma)，再加亮度偏移。"""
    f = 255.0 * (img.astype(np.float64) / 255.0) ** (1.0 / gamma) + brightness
    return np.clip(np.round(f), 0, 255).astype(np.uint8)


def paint_test_frame(state: dict, size=(200, 400), block=10, beat=0, pos=None, facing=None, map_id=None,
                     bin_pct=None, version=7, since_damage=None, marker="black", facing_count=0, v08=False,
                     combo=None, energy_lvl=None, marker_center=False, enemy=None, death=None, tinfo=None):
    """按 v0.7 编码表画一帧（BGR）。

    state: {块号: 颜色名 或 ('pct', 数值)（画到 R 通道，旧式）, "bars": (血, 目标, 怒气, 法力)}
    块 20（找到攻击格子）、21（进度条正常）默认绿。version: 5 / 4 / None(不画)。
    since_damage: 秒数 / math.inf(还没打出伤害) / None(战斗日志不可用)
    bin_pct: {"hp": 数值|None, "target_hp":..., "rage":..., "mana":...}（二进制百分比，None = 无效）
    """
    img = np.zeros((size[0], size[1], 3), np.uint8)
    if tinfo is not None and death is None:
        death = {}
    if death is not None and enemy is None:
        enemy = (True, False, 0)

    def put(x, y, rgb):
        r, g, b = rgb
        img[y:y + block, x:x + block] = (b, g, r)

    put(0, 0, (255, 255, beat))
    v09 = combo is not None or energy_lvl is not None
    if enemy is not None:          # v0.11：enemy = (接口能用, 有读不到的, 姓名板个数)
        v09 = True
        marker_center = True
    if v09:
        v08 = True
    for i in range(1, (34 if enemy is not None else 32) if v09 else (29 if v08 else 25)):
        if i == 22:
            dflt = marker
        elif i == 23:
            dflt = BITS_NAME[((facing_count >> 2) & 1, (facing_count >> 1) & 1, facing_count & 1)]
        elif i == 29:
            n = combo or 0
            dflt = BITS_NAME[((n >> 2) & 1, (n >> 1) & 1, n & 1)]
        elif i == 30:
            n = energy_lvl or 0
            dflt = BITS_NAME[((n >> 2) & 1, (n >> 1) & 1, n & 1)]
        elif i == 31:
            dflt = BITS_NAME[(1 if combo is not None and combo >= 0 else 0,
                              1 if energy_lvl is not None and energy_lvl >= 0 else 0, 1)]
        elif i == 27:
            dflt = "cyan"
        elif i == 28:
            dflt = "green" if enemy is not None else ("white" if marker_center else "yellow")
        elif i == 32:
            dflt = BITS_NAME[(1, 1 if enemy[0] else 0, 1 if enemy[1] else 0)]
        elif i == 33:
            n = min(7, enemy[2] or 0)
            dflt = BITS_NAME[((n >> 2) & 1, (n >> 1) & 1, n & 1)]
        else:
            dflt = "green" if i in (20, 21) else "black"
        v = state.get(i, dflt)
        if isinstance(v, tuple):
            put(i * block, 0, (int(round(v[1] * 2.55)), 0, 0))
        else:
            put(i * block, 0, PALETTE[v])
    for n, pct in enumerate(state.get("bars", (0, 0, 0, 0))):
        y = block + n * 4
        img[y:y + 4, 0:int(pct)] = (0, 255, 0)

    values = {"x": None if pos is None else pos[0] / 100 * 65535,
              "y": None if pos is None else pos[1] / 100 * 65535,
              "facing": None if facing is None else (facing % (2 * math.pi)) / (2 * math.pi) * 65535,
              "map_id": map_id,
              "since_damage": None if since_damage is None else min(127, since_damage)}
    values.update(bin_pct or {})
    for name, (row, start, nbits) in FIELDS.items():
        bits = encode_bits(values.get(name), nbits)
        for j in range(len(bits) // 3):
            put((start + j) * block, BIN_TOP + row * block, tuple(255 * b for b in bits[3 * j:3 * j + 3]))
    if version:
        rgb = {7: (255, 255, 255), 6: (255, 255, 0), 5: (255, 0, 255)}.get(version, (255, 0, 0))
        put(VERSION_BLOCK[1] * block, BIN_TOP + VERSION_BLOCK[0] * block, rgb)
    put(CALIB_BLOCK[1] * block, BIN_TOP + CALIB_BLOCK[0] * block, (128, 128, 128))
    if death is not None:
        # v0.12：death = {"ghost", "release", "res", "corpse": (x,y)|None, "delay", "np", "ghost_marks"}
        d = death
        if d.get("ghost"):
            put(2 * block, 0, PALETTE["yellow"])
        elif d.get("dead"):
            put(2 * block, 0, PALETTE["red"])
        corpse = d.get("corpse")
        put(34 * block, 0, tuple(255 * b for b in (1 if d.get("release") else 0, 1 if d.get("res") else 0,
                                                    1 if corpse else 0)))
        put(35 * block, 0, tuple(255 * b for b in (1 if d.get("np") else 0, 1 if d.get("ghost_marks") else 0, 1)))
        vals = {"corpse_x": None if not corpse else corpse[0] / 100 * 65535,
                "corpse_y": None if not corpse else corpse[1] / 100 * 65535,
                "res_delay": d.get("delay"), "np_dist": d.get("np")}
        for name, (row, start, nbits) in FIELDS_C.items():
            bits = encode_bits(vals.get(name), nbits)
            for j in range(len(bits) // 3):
                put((start + j) * block, BIN_TOP + row * block, tuple(255 * b for b in bits[3 * j:3 * j + 3]))
        put(SIG_C_BLOCK[1] * block, BIN_TOP + SIG_C_BLOCK[0] * block, PALETTE["cyan"])
    if tinfo is not None:
        # v0.13：tinfo = {"player": bool|None, "elite": bool|None, "rare": bool, "diff": int|None, "skull": bool}
        pl, el = tinfo.get("player", False), tinfo.get("elite", False)
        put(36 * block, 0, tuple(255 * b for b in (1 if pl else 0, 1 if el else 0, 1 if tinfo.get("rare") else 0)))
        put(37 * block, 0, tuple(255 * b for b in (0 if pl is None else 1, 0 if el is None else 1, 1)))
        d = tinfo.get("diff")
        code = 63 if tinfo.get("skull") else (0 if d is None else max(1, min(62, int(d) + 32)))
        put(38 * block, 0, tuple(255 * ((code >> k) & 1) for k in (5, 4, 3)))
        put(39 * block, 0, tuple(255 * ((code >> k) & 1) for k in (2, 1, 0)))
        put(SIG_C13_BLOCK[1] * block, BIN_TOP + SIG_C13_BLOCK[0] * block, PALETTE["magenta"])
    return img

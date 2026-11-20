"""区域刷怪用的几何计算（纯函数，可单元测试）。

坐标：游戏地图坐标 0-100，x 向东增大，y 向南增大（和游戏里显示的一样）。
朝向：GetPlayerFacing() 弧度，0 = 北，逆时针增大（π/2 = 西，π = 南，3π/2 = 东）。
"""
from __future__ import annotations

import math

TWO_PI = 2 * math.pi


def distance(a, b) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def bearing_to(src, dst) -> float:
    """从 src 看向 dst 应该面对的 WoW 朝向（弧度 0..2π）。

    朝向 θ 的前进方向（地图坐标）：dx = -sin θ，dy = -cos θ（y 向南为正）。
    所以 θ = atan2(-dx, -dy)。
    """
    dx, dy = dst[0] - src[0], dst[1] - src[1]
    return math.atan2(-dx, -dy) % TWO_PI


def angle_diff(target: float, current: float) -> float:
    """target - current，归一化到 (-π, π]。正数 = 需要向左转（逆时针）。"""
    d = (target - current) % TWO_PI
    return d - TWO_PI if d > math.pi else d


def forward_step(pos, facing: float, dist: float):
    """沿朝向前进 dist（地图单位）后的位置。测试/模拟用。"""
    return pos[0] - math.sin(facing) * dist, pos[1] - math.cos(facing) * dist


def turn_command(delta: float, rate_dps: float, max_ms: float = 1500):
    """要转 delta 弧度：返回 ('left'|'right', 按住毫秒)。"""
    rate = math.radians(max(1e-3, rate_dps))
    ms = min(abs(delta) / rate * 1000.0, max_ms)
    return ("left" if delta > 0 else "right"), ms


def area_status(pos, map_id, home, radius: float) -> tuple[str, float | None]:
    """v8.17：返回 (状态, 距离)。状态：in / out / nopos / nohome / othermap。
    home = (x, y, map_id)；map_id 为 0/None 时不比地图。"""
    if pos is None:
        return "nopos", None
    if home is None:
        return "nohome", None
    hm = int(home[2] or 0) if len(home) > 2 else 0
    if hm and map_id is not None and int(map_id) != hm:
        return "othermap", None
    d = distance(pos, (home[0], home[1]))
    return ("in" if d <= radius else "out"), d


def area_report(pos, facing, map_id, home, radius: float, enabled: bool, set_key: str = "F8") -> str:
    """v8.17：F9 范围检查的一行说明（日志用）。"""
    st, d = area_status(pos, map_id, home, radius)
    sw = "开" if enabled else "关（只检查，不会走回去）"
    face = "读不到" if facing is None else f"{math.degrees(facing):.0f}°"
    where = "读不到坐标" if pos is None else f"{pos[0]:.2f},{pos[1]:.2f} 地图 {map_id}"
    hm = "未设置" if home is None else f"{home[0]:.2f},{home[1]:.2f} 地图 {home[2] if len(home) > 2 else 0}"
    if st == "in":
        head = f"✅ 在范围内（离中心 {d:.2f} / 半径 {radius:.2f}）"
    elif st == "out":
        head = f"⚠️ 在范围外（离中心 {d:.2f} / 半径 {radius:.2f}）"
    elif st == "nopos":
        head = "❓ 读不到坐标（插件没读到 / 在副本里），判断不了"
    elif st == "nohome":
        head = f"❓ 还没有中心点，按 {set_key.upper()} 把当前位置设为中心"
    else:
        head = f"⚠️ 不在中心所在的地图（换了区域？按 {set_key.upper()} 把当前位置设为新中心）"
    return f"📍 范围检查：{head} | 当前 {where} 朝向 {face} | 中心 {hm} | 区域刷怪={sw}"


def area_label(pos, map_id, home, radius: float, enabled: bool) -> str:
    """v8.17：状态栏短文字。"""
    st, d = area_status(pos, map_id, home, radius)
    if st in ("in", "out"):
        s = f"{'内' if st == 'in' else '外'} {d:.2f}/{radius:g}"
    else:
        s = {"nopos": "无坐标", "nohome": "没中心", "othermap": "地图不同"}[st]
    return s if enabled else s + "(关)"

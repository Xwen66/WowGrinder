"""v8.60（t231u）：把 刷怪中心 + 半径、跑尸路线、修理路线 + 修理商 写成 BotStatus 插件能读的 MapData.lua。

游戏里的插件读不到 config.json，所以每次在 WowGrinder 里保存设置 / 录完点（都会走 save_config）就写一次
<AddOns>\\BotStatus\\MapData.lua；你在游戏里 /reload 后，插件 v0.17 在大地图上画出来。
只写这一个文件（先写 .tmp 再替换），不碰 WTF / CVar / 别的文件。找不到插件文件夹就什么都不做。
"""
from __future__ import annotations

import os
import time

CANDIDATES = (
    r"C:\Program Files (x86)\World of Warcraft\_forever_\Interface\AddOns",
    r"C:\Program Files\World of Warcraft\_forever_\Interface\AddOns",
    r"D:\World of Warcraft\_forever_\Interface\AddOns",
    r"D:\Program Files (x86)\World of Warcraft\_forever_\Interface\AddOns",
)

LAST = {"path": None, "error": None, "counts": None}


def find_addon_dir(cfg: dict) -> str | None:
    """BotStatus 插件文件夹：设置里填了就用（填 AddOns 或 BotStatus 都行），没填就在常见位置找。"""
    want = str((cfg.get("addon") or {}).get("addons_dir") or "").strip().strip('"')
    cands = [want] if want else list(CANDIDATES)
    for c in cands:
        if not c:
            continue
        for d in (c, os.path.join(c, "BotStatus")):
            if os.path.isfile(os.path.join(d, "BotStatus.toc")):
                return d
    return None


def _num(v) -> str:
    return repr(round(float(v), 4))


def _pts(pts) -> str:
    out = []
    for p in pts or []:
        try:
            x, y = float(p[0]), float(p[1])
            m = int(p[2]) if len(p) > 2 and p[2] else 0
        except (TypeError, ValueError, IndexError):
            continue
        out.append("{" + f"{_num(x)}, {_num(y)}, {m}" + "}")
    return "{" + ", ".join(out) + "}"


def build_lua(cfg: dict, now: float | None = None) -> str:
    a, co, rp = cfg.get("area") or {}, cfg.get("corpse") or {}, cfg.get("repair") or {}
    vi = int(rp.get("vendor_i", -1) if rp.get("vendor_i") is not None else -1)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now if now is not None else time.time()))
    return ("-- 这个文件由 WowGrinder 自动生成（v8.60），每次在 WowGrinder 里保存/录点时覆盖；不要手改。\n"
            "-- 改完要在游戏里 /reload 才会更新大地图。\n"
            "BotStatusMapData = {\n"
            "  version = 1,\n"
            f"  written = \"{stamp}\",\n"
            f"  radius = {_num(a.get('radius', 3.0) or 0)},\n"
            f"  centers = {_pts(a.get('centers'))},\n"
            f"  corpse = {_pts(co.get('route'))},\n"
            f"  repair = {_pts(rp.get('route'))},\n"
            f"  vendor = {vi + 1 if vi >= 0 else 0},\n"
            "}\n")


def write_map_data(cfg: dict, now: float | None = None) -> str | None:
    """写 MapData.lua。成功 → 返回文件路径；找不到插件文件夹/写失败 → None（不抛异常，不影响保存设置）。"""
    LAST.update(path=None, error=None, counts=None)
    try:
        d = find_addon_dir(cfg)
        if not d:
            LAST["error"] = "找不到 BotStatus 插件文件夹（可以在“插件文件夹”里填 AddOns 的路径）"
            return None
        path = os.path.join(d, "MapData.lua")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(build_lua(cfg, now))
        os.replace(tmp, path)
        a, co, rp = cfg.get("area") or {}, cfg.get("corpse") or {}, cfg.get("repair") or {}
        LAST.update(path=path, counts=(len(a.get("centers") or []), len(co.get("route") or []),
                                       len(rp.get("route") or [])))
        return path
    except Exception as e:  # noqa: BLE001
        LAST["error"] = f"写地图数据失败：{e}"
        return None


def status_msg() -> str:
    if LAST["path"]:
        c = LAST["counts"] or (0, 0, 0)
        return (f"🗺️ 大地图数据已写入（中心 {c[0]} 个、跑尸路线 {c[1]} 个点、修理路线 {c[2]} 个点）"
                "→ 游戏里 /reload 后在大地图上看")
    return f"🗺️ 没写大地图数据：{LAST['error']}" if LAST["error"] else ""

"""v8：每次运行写一个日志文件到 logs/（超过大小换新文件；只保留最近 N 个）。"""
from __future__ import annotations

import os
import threading
import time

from .config import BASE_DIR

LOG_DIR = os.path.join(BASE_DIR, "logs")


class SessionLog:
    def __init__(self, directory: str = LOG_DIR, keep: int = 20, max_mb: float = 5.0, clock=time.time):
        self.dir = directory
        self.keep = max(1, int(keep))
        self.max_bytes = int(float(max_mb) * 1024 * 1024)
        self.clock = clock
        self.lock = threading.Lock()
        self.part = 0
        self.f = None
        self.path = ""
        self.stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(clock()))
        try:
            os.makedirs(self.dir, exist_ok=True)
            self._open()
        except OSError:
            self.f = None

    def _open(self):
        suffix = "" if self.part == 0 else f"_{self.part}"
        self.path = os.path.join(self.dir, f"wowgrinder_{self.stamp}{suffix}.log")
        self.f = open(self.path, "a", encoding="utf-8")
        self._prune()

    def _prune(self):
        files = sorted(f for f in os.listdir(self.dir) if f.startswith("wowgrinder_") and f.endswith(".log"))
        for old in files[:-self.keep]:
            try:
                os.remove(os.path.join(self.dir, old))
            except OSError:
                pass

    def write(self, msg: str, tag: str = ""):
        if self.f is None:
            return
        t = self.clock()
        line = time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d} {tag}{msg}\n"
        with self.lock:
            try:
                self.f.write(line)
                self.f.flush()
                if self.f.tell() > self.max_bytes:
                    self.f.close()
                    self.part += 1
                    self._open()
            except (OSError, ValueError):
                pass

    def close(self):
        with self.lock:
            if self.f is not None:
                try:
                    self.f.close()
                except OSError:
                    pass
                self.f = None


def obs_snapshot(obs) -> str:
    """把一帧观测压成一行（只在变化时写日志）。标记位置按 20 像素取整，免得每帧都变。"""
    def pct(v):
        return "?" if v is None else f"{v:.0f}"
    mk = "-"
    if getattr(obs, "marker", None):
        mk = f"({int(obs.marker[0]) // 20 * 20},{int(obs.marker[1]) // 20 * 20})"
    errs = "".join(c for c, v in (("面", obs.err_facing), ("远", obs.err_far), ("视", obs.err_los)) if v) or "-"
    return (f"来源 {obs.source} | 血 {pct(obs.player_hp)}%({obs.hp_source}{'' if obs.hp_readable else ',读不到'}) "
            f"目标血 {pct(obs.target_hp)}% 怒 {pct(getattr(obs, 'rage', None))} 蓝 {pct(obs.mana)} | "
            f"战斗 {obs.in_combat} | 目标 {obs.target_kind} 可拾取 {obs.lootable} 打我 {obs.aggro} 自动攻击 {obs.autoattack} | "
            f"距离 {obs.range}/{obs.attack_range} | 报错 {obs.error or '-'} 灯 {errs} 面向计数 {obs.facing_count} | "
            f"形态 {obs.form} 施法 {obs.casting} | 标记 {obs.marker_state} {mk}"
            + (f" | 姓名板来源 {'插件标记' if getattr(obs, 'plate_source', '') == 'marker' else '模板'}"
               f"{'' if getattr(obs, 'plate_count', None) is None else f' 插件看到 {obs.plate_count} 个'}"
               if obs.source == "addon" else "")
            + (" | ⚠️游戏菜单开着" if getattr(obs, "game_menu", False) else "")
            + (f" | buff 荆棘 {obs.buff_thorns} 印记 {obs.buff_motw}" if getattr(obs, "buff_thorns", None) is not None else "")
            + (f" | 连击点 {obs.combo if obs.combo is not None else '?'} 能量 {obs.energy if obs.energy is not None else '?'}"
               if getattr(obs, "form", "") == "cat" or getattr(obs, "combo", None) is not None else ""))


class ShotSaver:
    """关键事件截图：左上角色块区域（放大 3 倍）+ 整屏（缩到 960 宽）。存到 logs/shots/，只留最新的 N 张（log.keep_shots，默认 50）。"""

    def __init__(self, directory: str = os.path.join(LOG_DIR, "shots"), keep: int = 50):
        self.dir = directory
        self.keep = keep

    def save(self, frame, kind: str, addon_xy=(0, 0), block=10) -> list[str]:
        if frame is None:
            return []
        try:
            import cv2
            os.makedirs(self.dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
            x0, y0 = int(addon_xy[0]), int(addon_xy[1])
            top = frame[y0:y0 + 6 * block, x0:x0 + 26 * block, :3]
            out = []
            if top.size:
                big = cv2.resize(top, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
                out.append(self._write(f"{stamp}_{kind}_blocks.png", ".png", big))
            h, w = frame.shape[:2]
            scale = 960.0 / max(1, w)
            small = cv2.resize(frame[:, :, :3], (960, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
            out.append(self._write(f"{stamp}_{kind}_screen.jpg", ".jpg", small))
            self._prune()
            return [p for p in out if p]
        except Exception:  # noqa: BLE001
            return []

    def _write(self, name, ext, img):
        import cv2
        ok, buf = cv2.imencode(ext, img)
        if not ok:
            return ""
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:      # 不用 cv2.imwrite：Windows 中文路径会失败
            f.write(buf.tobytes())
        return path

    def prune(self):
        try:
            self._prune()
        except OSError:
            pass

    def _prune(self):
        if not os.path.isdir(self.dir):
            return
        files = sorted(os.listdir(self.dir))
        for old in files[:-max(1, int(self.keep))]:
            try:
                os.remove(os.path.join(self.dir, old))
            except OSError:
                pass

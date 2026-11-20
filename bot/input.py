"""键盘/鼠标输入：pyautogui 发送按键，带随机按住时长与随机间隔，可被急停打断。"""
from __future__ import annotations

import random
import threading
import time

SPECIAL = {"rightclick", "leftclick"}


def _norm(key: str) -> str:
    from .config import norm_key        # v8.27：· → `（VK_OEM_3），全角 → 半角
    return norm_key(key or "")


class StopRequested(Exception):
    """急停：中断正在进行的动作序列。"""


class InputController:
    def __init__(self, cfg: dict, stop_event: threading.Event, log=print, backend=None):
        self.cfg = cfg
        self.stop_event = stop_event
        self.log = log
        self._pg = backend  # 测试时可注入假的后端
        self.last_press: dict[str, float] = {}
        self.held: set[str] = set()
        self.history: list[str] = []
        self.context = ""        # v8：当前按键原因（写日志用）
        self.on_key = None       # v8：回调 on_key(文字)，每次按键写日志

    @property
    def pg(self):
        if self._pg is None:
            import pyautogui
            pyautogui.PAUSE = 0
            pyautogui.FAILSAFE = False      # v8.43：去掉“鼠标甩到左上角急停”（只用 F12 / 停止按钮 / 关窗口停）
            self._pg = pyautogui
        return self._pg

    # -------------------------------------------------- 等待
    def sleep(self, seconds: float):
        """可被急停打断的 sleep。"""
        if self.stop_event.wait(max(0.0, seconds)):
            raise StopRequested()

    def random_delay(self):
        s = self.cfg["safety"]
        lo, hi = sorted((s["delay_min_ms"], s["delay_max_ms"]))
        self.sleep(random.uniform(lo, hi) / 1000.0)

    # -------------------------------------------------- 按键
    def press(self, key: str, hold_ms: float | None = None, delay: bool = True):
        if not key:
            return
        if self.stop_event.is_set():
            raise StopRequested()
        key = _norm(key).strip()
        s = self.cfg["safety"]
        hold = hold_ms if hold_ms is not None else random.uniform(s["hold_min_ms"], s["hold_max_ms"])
        pg = self.pg
        if key in SPECIAL:
            pg.click(button="right" if key == "rightclick" else "left")
        else:
            parts = [p.strip() for p in key.split("+") if p.strip()]
            try:
                for p in parts:
                    pg.keyDown(p)
                time.sleep(hold / 1000.0)
            finally:
                for p in reversed(parts):
                    pg.keyUp(p)
        self.last_press[key] = time.monotonic()
        self.history.append(key)
        self._note(f"按键 {key}（按住 {hold:.0f}ms）")
        if delay:
            self.random_delay()

    def _note(self, what):
        if self.on_key:
            try:
                self.on_key(f"{what} ← {self.context or '?'}")
            except Exception:  # noqa: BLE001
                pass

    def click_at(self, x: int, y: int, button: str = "left", delay: bool = True, restore: bool = False):
        """移动鼠标到 (x, y)（屏幕绝对坐标）并点击，移动时间随机。restore=True：点完把鼠标移回原位。"""
        if self.stop_event.is_set():
            raise StopRequested()
        pg = self.pg
        old = None
        if restore and hasattr(pg, "position"):
            try:
                old = tuple(pg.position())
            except Exception:
                old = None
        pg.moveTo(int(x), int(y), duration=random.uniform(0.06, 0.16))
        pg.click(button=button)
        if old is not None:
            pg.moveTo(int(old[0]), int(old[1]), duration=random.uniform(0.05, 0.12))
        self.last_press["click"] = time.monotonic()
        self.history.append(f"click({int(x)},{int(y)})")
        self._note(f"鼠标{button}键点击 ({int(x)},{int(y)})")
        if delay:
            self.random_delay()

    def key_down(self, key: str):
        """按住不放（例如前进 W），直到 key_up / release_all。"""
        key = _norm(key).strip()
        if not key or key in self.held:
            return
        if self.stop_event.is_set():
            raise StopRequested()
        self.pg.keyDown(key)
        self.held.add(key)
        self.history.append(f"down:{key}")
        self._note(f"按住 {key}")

    def key_up(self, key: str):
        key = _norm(key).strip()
        if key in self.held:
            self.held.discard(key)
            self.pg.keyUp(key)
            self.history.append(f"up:{key}")
            self._note(f"松开 {key}")

    def since_last(self, key: str) -> float:
        t = self.last_press.get((key or "").strip().lower())
        return float("inf") if t is None else time.monotonic() - t

    def release_all(self):
        """停止时松开可能卡住的键。"""
        if self._pg is None:
            return
        for k in list(self.held):
            try:
                self._pg.keyUp(k)
            except Exception:
                pass
        self.held.clear()
        for k in set(self.cfg["keys"].values()):
            for p in (k or "").split("+"):
                p = p.strip().lower()
                if p and p not in SPECIAL:
                    try:
                        self._pg.keyUp(p)
                    except Exception:
                        pass

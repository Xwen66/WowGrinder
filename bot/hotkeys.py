"""全局热键（pynput）：在游戏里按 F12 急停、F10 开始/暂停。"""
from __future__ import annotations


def _key_name(key) -> str:
    name = getattr(key, "name", None)
    if name:
        return name.lower()
    ch = getattr(key, "char", None)
    return (ch or "").lower()


class HotkeyListener:
    def __init__(self, bindings: dict):
        """bindings: {"f12": callback, ...}，回调在 pynput 线程里执行。"""
        self.bindings = {k.lower(): v for k, v in bindings.items() if k}
        self._listener = None

    def start(self):
        from pynput import keyboard
        self.stop()
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.daemon = True
        self._listener.start()

    def _on_press(self, key):
        cb = self.bindings.get(_key_name(key))
        if cb:
            try:
                cb()
            except Exception:
                pass

    def stop(self):
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

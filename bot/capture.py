"""屏幕截图：复用同一个 mss 实例（mss 实例不能跨线程，所以在使用它的线程里创建）。"""
from __future__ import annotations

import threading

import numpy as np


class ScreenCapture:
    def __init__(self, monitor_index: int = 1):
        self.monitor_index = monitor_index
        self._local = threading.local()

    def _sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            import mss  # 延迟导入，方便在无显示器环境测试其它模块
            sct = mss.mss()
            self._local.sct = sct
        return sct

    @property
    def monitor(self) -> dict:
        mons = self._sct().monitors
        idx = self.monitor_index if 0 < self.monitor_index < len(mons) else 1
        return mons[idx]

    def grab(self) -> np.ndarray:
        """抓取整个显示器，返回 BGR 图像（坐标原点 = 显示器左上角）。"""
        shot = self._sct().grab(self.monitor)
        return np.asarray(shot)[:, :, :3].copy()

    def close(self):
        sct = getattr(self._local, "sct", None)
        if sct is not None:
            sct.close()
            self._local.sct = None

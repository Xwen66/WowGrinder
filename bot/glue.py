"""v8.44：截图认出“不在游戏世界里”的界面（断线弹窗 / 登录界面 / 选角色界面），给自动重连用。

模板来自 Simon 电脑上真实的选角色界面截图（2160×1350，国服“魔兽世界 无限”客户端）：
- logo.png：左上角“魔兽世界 无限”logo（登录界面、选角色界面都有；游戏世界里没有）
- enter_world.png：选角色界面下方红色“进入魔兽世界”按钮
- top_bar.png：选角色界面顶上“商城 / 选项菜单 / 规则集”一排按钮
任何一个匹配上（灰度归一化相关 ≥ 各自阈值）就算“不在游戏世界里”。只在插件色块读不到时才检查（游戏世界里插件一直在）。
界面大小跟着窗口高度缩放：模板按 帧高/1350 缩放，再在 ±20% 里找。"""
from __future__ import annotations

import os

import cv2
import numpy as np

from .config import IMAGES_DIR

REF_H = 1350.0
# 名字, 文件, 搜索区域（相对帧：x0, y0, x1, y1）
# 阈值：在 25 张游戏世界里的截图（Simon 电脑上 logs/shots）上最高分 logo 0.47 / 按钮 0.28 / 顶栏 0.74
TEMPLATES = (
    ("logo", "logo.png", (0.0, 0.0, 0.25, 0.25), 0.75),
    ("enter_world", "enter_world.png", (0.25, 0.75, 0.75, 1.0), 0.70),
    ("top_bar", "top_bar.png", (0.2, 0.0, 0.8, 0.12), 0.88),
)
SCALES = (0.8, 0.9, 1.0, 1.1, 1.2)
CN = {"logo": "左上角魔兽世界 logo", "enter_world": "“进入魔兽世界”按钮", "top_bar": "选角色界面顶栏"}


class GlueDetector:
    def __init__(self, folder: str | None = None):
        self.folder = folder or os.path.join(IMAGES_DIR, "glue")
        self.tpls = []
        self.thr = {}
        for name, fn, roi, thr in TEMPLATES:
            img = cv2.imread(os.path.join(self.folder, fn), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                self.tpls.append((name, img, roi))
                self.thr[name] = thr

    @property
    def ready(self) -> bool:
        return bool(self.tpls)

    def scores(self, frame) -> dict:
        if frame is None or not self.tpls:
            return {}
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        H, W = gray.shape[:2]
        base = H / REF_H
        out = {}
        for name, tpl, (x0, y0, x1, y1) in self.tpls:
            roi = gray[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)]
            best = -1.0
            for s in SCALES:
                f = base * s
                t = cv2.resize(tpl, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
                if t.shape[0] < 8 or t.shape[1] < 8 or t.shape[0] > roi.shape[0] or t.shape[1] > roi.shape[1]:
                    continue
                if float(t.std()) < 1.0:
                    continue
                r = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
                v = float(np.nanmax(r)) if r.size else -1.0
                best = max(best, v)
            out[name] = best
        return out

    def check(self, frame):
        """→ (是不是登录/选角色/断线界面, 认出来的是什么, 分数)。"""
        sc = self.scores(frame)
        hits = [n for n, v in sc.items() if v >= self.thr.get(n, 0.8)]
        return bool(hits), "、".join(CN.get(n, n) for n in hits), sc

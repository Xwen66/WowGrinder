"""v8.44：截图认出“不在游戏世界里”的界面（断线弹窗 / 登录界面 / 选角色界面），给自动重连用。

模板来自 Simon 电脑上真实的选角色界面截图（2160×1350，国服“魔兽世界 无限”客户端）：
- logo.png：左上角“魔兽世界 无限”logo（登录界面、选角色界面都有；游戏世界里没有）
- enter_world.png：选角色界面下方红色“进入魔兽世界”按钮
- top_bar.png：选角色界面顶上“商城 / 选项菜单 / 规则集”一排按钮
任何一个匹配上（灰度归一化相关 ≥ 各自阈值）就算“不在游戏世界里”。
界面大小跟着窗口高度缩放：模板按 帧高/1350 缩放，再在 ±20% 里找。

v8.45（t182u）：“已与服务器断开连接”弹窗（登录界面上，背景可能没有 logo）v8.44 认不出来 → 加 find_dialog：
屏幕中间一个黑底、棕/金色边框的对话框，里面有红色按钮（暴雪对话框统一样式：游戏里的“释放灵魂”、
“BotStatus 已被禁用”弹窗、选角色界面的按钮都是这样）。游戏世界里也有这种弹窗，所以这一项
只在插件色块读不到时才算数（由 check(dialog_ok=...) 控制）。"""
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
CN = {"logo": "左上角魔兽世界 logo", "enter_world": "“进入魔兽世界”按钮", "top_bar": "选角色界面顶栏",
      "dialog": "屏幕中间的弹窗（黑底+红按钮，如“已与服务器断开连接”）"}


def find_dialog(frame) -> list:
    """v8.45：找屏幕中间“黑底 + 棕/金边框 + 红按钮”的暴雪对话框。→ [(x, y, w, h)]（缩到 600 高的坐标）。
    按钮：深红色块，高 5~30px、宽高比 2.5~12、填满 60% 以上，中心在屏幕中间 60%×80% 里；
    按钮正下方是暗的（对话框底）、正上方 2 个按钮高也是暗的；再往上 14 个按钮高以内有一条棕/金色边框横线，
    边框和按钮之间大部分是暗的（对话框里的字是亮的，所以只要 60%）。"""
    if frame is None or frame.ndim != 3:
        return []
    H0 = frame.shape[0]
    if H0 < 100:
        return []
    f = cv2.resize(frame, (max(1, int(round(frame.shape[1] * 600.0 / H0))), 600),
                   interpolation=cv2.INTER_AREA) if H0 != 600 else frame
    H, W = f.shape[:2]
    b, g, r = (f[:, :, i].astype(np.int16) for i in range(3))
    red = ((r > 80) & (r > 2 * g + 10) & (r > 2 * b + 10)).astype(np.uint8)
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((3, 5), np.uint8))   # 补上按钮上的金色字
    n, _lab, st, _cen = cv2.connectedComponentsWithStats(red, 8)
    v = f.max(axis=2).astype(np.int16)
    hits = []
    for i in range(1, n):
        x, y, w, h, a = (int(t) for t in st[i])
        if not (5 <= h <= 30 and 2.5 <= w / h <= 12 and a >= 0.6 * w * h):
            continue
        cx, cy = x + w / 2.0, y + h / 2.0
        if not (0.2 * W < cx < 0.8 * W and 0.1 * H < cy < 0.9 * H):
            continue
        below = v[min(H - 1, y + h + 1):min(H, y + h + 1 + max(2, h // 2)), x:x + w]
        above = v[max(0, y - 2 * h):max(0, y - 1), x:x + w]
        if below.size == 0 or above.size == 0:
            continue
        if (below < 60).mean() <= 0.6 or (above < 60).mean() <= 0.5:
            continue
        border = None
        for yy in range(y - 2 * h, max(0, y - 14 * h), -1):
            row = f[yy, max(0, x - h):min(W, x + w + h)].astype(np.int16)
            bb, gg, rr = row[:, 0], row[:, 1], row[:, 2]
            if ((rr >= gg) & (gg >= bb - 5) & (rr > 60) & (rr - bb > 15)).mean() > 0.7:
                border = yy
                break
        if border is None:
            continue
        mid = v[border + 2:y - 1, x:x + w]
        if mid.size and (mid < 70).mean() > 0.6:
            hits.append((x, y, w, h))
    return hits


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

    def check(self, frame, dialog_ok: bool = True):
        """→ (是不是登录/选角色/断线界面, 认出来的是什么, 分数)。
        dialog_ok=False：弹窗不算（插件读得到 = 在游戏世界里，那里的弹窗是“释放灵魂”之类）。分数里 dialog = 找到几个红按钮。"""
        sc = self.scores(frame)
        hits = [n for n, v in sc.items() if v >= self.thr.get(n, 0.8)]
        try:
            sc["dialog"] = float(len(find_dialog(frame)))
        except Exception:  # noqa: BLE001
            sc["dialog"] = 0.0
        if dialog_ok and sc["dialog"] > 0:
            hits.append("dialog")
        return bool(hits), "、".join(CN.get(n, n) for n in hits), sc


def score_text(sc: dict) -> str:
    """日志用：logo 0.31 / 进入按钮 0.20 / 顶栏 0.55 / 弹窗 0。"""
    short = {"logo": "logo", "enter_world": "进入按钮", "top_bar": "顶栏", "dialog": "弹窗"}
    return " / ".join(f"{short.get(k, k)} {v:.2f}" if k != "dialog" else f"{short[k]} {int(v)}"
                      for k, v in sc.items())

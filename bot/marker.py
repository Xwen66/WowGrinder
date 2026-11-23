"""v8：在屏幕上找 BotStatus v0.7 画在目标姓名板上方的 青|洋红 标记，算出怪在左/右/身后多少度。

标记：黑底 20x12，左 8x8 青 (0,255,255)、右 8x8 洋红 (255,0,255)（物理像素，抗伽马：只用 0/255）。
游戏设置 nameplateTargetRadialPosition=1 时，目标不在屏幕内，姓名板会被钉在屏幕边上：
  钉在下边 → 怪在身后（转约 180°）；钉在左/右边 → 怪在那一侧 ≥ 半个视角（先转 90° 再看）。
"""
from __future__ import annotations

import math

import numpy as np

try:
    import cv2
except Exception:  # noqa: BLE001
    cv2 = None


def _masks(img_bgr):
    if cv2 is not None:
        img = np.ascontiguousarray(img_bgr[:, :, :3])
        cyan = cv2.inRange(img, (170, 170, 0), (255, 255, 89)) > 0
        mag = cv2.inRange(img, (170, 0, 170), (255, 89, 255)) > 0
        return cyan, mag
    b = img_bgr[:, :, 0].astype(np.int16)
    g = img_bgr[:, :, 1].astype(np.int16)
    r = img_bgr[:, :, 2].astype(np.int16)
    cyan = (r < 90) & (g >= 170) & (b >= 170)
    mag = (r >= 170) & (g < 90) & (b >= 170)
    return cyan, mag


def find_marker(frame_bgr, exclude=(0, 0, 260, 60), shift=6, min_area=12):
    """返回标记中心 (x, y)（frame 像素）或 None。exclude = 左上角状态色块区域 (x, y, w, h)，不在里面找。

    做法：找“这个像素是青，右边 shift 像素处是洋红”的点（标记左右两块交界），再取最大的一团。
    """
    if frame_bgr is None or frame_bgr.ndim != 3 or frame_bgr.shape[1] <= shift + 1:
        return None
    cyan, mag = _masks(frame_bgr)
    ex, ey, ew, eh = exclude
    cyan[max(0, ey):ey + eh, max(0, ex):ex + ew] = False
    cand = np.zeros_like(cyan)
    cand[:, :-shift] = cyan[:, :-shift] & mag[:, shift:]
    if not cand.any():
        return None
    if cv2 is not None:
        n, _, stats, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), connectivity=8)
        comps = [tuple(int(v) for v in stats[i][:5]) for i in range(1, n)]
    else:
        ys, xs = np.nonzero(cand)
        comps = [(int(xs.min()), int(ys.min()), int(np.ptp(xs)) + 1, int(np.ptp(ys)) + 1, len(xs))]
    best = None
    for x, y, w, h, area in comps:
        if area < min_area or h < 3 or w > 40 or h > 40:
            continue
        if best is None or area > best[4]:
            best = (x, y, w, h, area)
    if best is None:
        return None
    x, y, w, h, _ = best
    # 交界：最右一列候选点 + 1 再往右到洋红开始的地方（≈ x + w + (8 - shift)）
    xr = x + w
    row = cyan[y + h // 2, xr:xr + 12]
    off = int(np.argmin(row)) if row.size and not row.all() else 0
    return (xr + off, y + h / 2.0)


def marker_bearing(mx, my, width, height, fov_deg=90.0, edge_px=30, bottom_frac=0.86):
    """把标记位置换成“需要转多少度”。正数 = 怪在右边（向右转），负数 = 左边。

    返回 (角度, 说明)。说明：front / side_edge / behind。
    """
    cx = width / 2.0
    dx = mx - cx
    side = 1.0 if dx >= 0 else -1.0
    if my >= height * bottom_frac:
        # 钉在下边：在身后。靠角落的偏一点（约 120-150°），中间 180°
        frac = min(1.0, abs(dx) / cx)
        return side * (180.0 - 50.0 * frac), "behind"
    if mx <= edge_px or mx >= width - edge_px:
        return side * max(90.0, fov_deg / 2.0), "side_edge"
    half = math.radians(max(10.0, min(170.0, fov_deg)) / 2.0)
    ang = math.degrees(math.atan((dx / cx) * math.tan(half)))
    return ang, "front"


def plate_bearing(plate: dict, width, height, fov_deg=90.0):
    """选中姓名板模板（备用）：用姓名板中心 x 算角度。"""
    return marker_bearing(plate["x"] + plate["w"] / 2.0, plate["y"] + plate["h"] / 2.0, width, height,
                          fov_deg, edge_px=-1, bottom_frac=2.0)


# ---------------------------------------------------------------- v8.28：插件 v0.11 画在每个能打的怪姓名板中间的 黄|蓝 标记
def _yb_masks(img_bgr):
    b = img_bgr[:, :, 0].astype(np.int16)
    g = img_bgr[:, :, 1].astype(np.int16)
    r = img_bgr[:, :, 2].astype(np.int16)
    yellow = (r >= 170) & (g >= 170) & (b < 90)
    blue = (r < 90) & (g < 90) & (b >= 170)
    return yellow, blue


def find_enemy_markers(frame_bgr, exclude=(0, 0, 350, 60), shift=6, min_area=12):
    """返回所有 黄|蓝 标记的中心 [(x, y), ...]（frame 像素）。exclude = 左上角状态色块区域。

    标记：黑底 20x12，左 8x8 黄 (255,255,0)、右 8x8 蓝 (0,0,255)。和目标标记同样的找法：
    “这个像素黄、右边 shift 像素处蓝”的点连成一团，一团 = 一个标记。地面/界面很少有纯黄紧挨纯蓝。
    """
    if frame_bgr is None or frame_bgr.ndim != 3 or frame_bgr.shape[1] <= shift + 1:
        return []
    yellow, blue = _yb_masks(np.ascontiguousarray(frame_bgr[:, :, :3]))
    ex, ey, ew, eh = exclude
    yellow[max(0, ey):ey + eh, max(0, ex):ex + ew] = False
    cand = np.zeros_like(yellow)
    cand[:, :-shift] = yellow[:, :-shift] & blue[:, shift:]
    if not cand.any():
        return []
    if cv2 is not None:
        n, _, stats, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), connectivity=8)
        comps = [tuple(int(v) for v in stats[i][:5]) for i in range(1, n)]
    else:
        comps = _components(cand)
    out = []
    for x, y, w, h, area in comps:
        if area < min_area or h < 3 or w > 40 or h > 40:
            continue
        xr = x + w
        row = yellow[y + h // 2, xr:xr + 12]
        off = int(np.argmin(row)) if row.size and not row.all() else 0
        out.append((float(xr + off), y + h / 2.0))
    return out


def _components(mask):
    """没有 cv2 时的简单 4 连通分量（测试用）。返回 [(x, y, w, h, area)]。"""
    seen = np.zeros_like(mask, dtype=bool)
    H, W = mask.shape
    comps = []
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        stack = [(y0, x0)]
        seen[y0, x0] = True
        xs, ys = [], []
        while stack:
            y, x = stack.pop()
            xs.append(x)
            ys.append(y)
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        comps.append((int(min(xs)), int(min(ys)), int(max(xs) - min(xs) + 1), int(max(ys) - min(ys) + 1), len(xs)))
    return comps


# ---------------------------------------------------------------- v8.28：远处的怪只显示红色名字（没有血条/标记）
def find_red_names(frame_bgr, region=None, min_r=80, ratio=1.5, min_diff=35, min_w=14, max_w=260,
                   min_h=5, max_h=26, min_px=40, exclude=(), strict=None):
    """在世界区域里找敌对 NPC 的红色名字。返回 [{x, y, w, h, px}]（frame 像素，x/y 为左上角），按像素多到少排序。

    红字：R 明显大于 G、B（R ≥ min_r，R ≥ ratio × G/B，R - max(G,B) ≥ min_diff）。远处名字会变淡，所以门槛不高；
    再把横向挨着的字连成一行（膨胀），只留“一行字”形状：高 min_h..max_h、宽 min_w..max_w、宽 ≥ 1.5 × 高。
    选中光圈、红色血条、大块红色地面都不是这个形状。exclude = [(x, y, w, h), ...] 不找的地方（界面）。

    v8.66 strict = dict（None = 旧做法）：更严格的“这是一行红字名字”判断（在朝它走之前就筛掉）：
      min_purity  红字像素平均 (R - max(G,B)) ≥ 这个（名字 102-126；千针石林的红土/红岩 36-60；顶部红字报错 72-84）
      min_mean_r  红字像素平均 R ≥ 这个（名字 160-224；红土 100-170）
      min_h / max_h  一行字的高（按 1350 高的画面算，自动按画面高缩放；名字 17-26）
      min_w       最窄（2 个字的名字 ≈ 40）
      min_strokes 每 20 像素宽平均有几次“红/不红”切换（字有笔画 ≈ 1.8-2.8；血条/横条 < 0.5）
    返回的每个名字多带 pur / mr / strokes 三个值（日志/测试用）。
    """
    if frame_bgr is None or frame_bgr.ndim != 3:
        return []
    H, W = frame_bgr.shape[:2]
    x0, y0, x1, y1 = 0, 0, W, H
    if region and len(region) >= 4 and region[2] > 0 and region[3] > 0:
        x0, y0 = max(0, int(region[0])), max(0, int(region[1]))
        x1, y1 = min(W, x0 + int(region[2])), min(H, y0 + int(region[3]))
    img = frame_bgr[y0:y1, x0:x1, :3]
    if img.size == 0:
        return []
    b = img[:, :, 0].astype(np.int16)
    g = img[:, :, 1].astype(np.int16)
    r = img[:, :, 2].astype(np.int16)
    mx = np.maximum(g, b)
    mask = (r >= min_r) & (r * 10 >= mx * int(ratio * 10)) & (r - mx >= min_diff)
    for ex, ey, ew, eh in exclude:
        mask[max(0, ey - y0):max(0, ey - y0 + eh), max(0, ex - x0):max(0, ex - x0 + ew)] = False
    if mask.sum() < min_px:
        return []
    m8 = mask.astype(np.uint8)
    if cv2 is not None:
        line = cv2.dilate(m8, np.ones((3, 11), np.uint8))
        n, lab, stats, _ = cv2.connectedComponentsWithStats(line, connectivity=8)
        comps = []
        for i in range(1, n):
            x, y, w, h, _ = (int(v) for v in stats[i][:5])
            px = int(mask[y:y + h, x:x + w][lab[y:y + h, x:x + w] == i].sum())
            comps.append((x, y, w, h, px))
    else:
        comps = []
        for x, y, w, h, _ in _components(mask):
            comps.append((x, y, w, h, int(mask[y:y + h, x:x + w].sum())))
    out = []
    sc = H / 1350.0
    for x, y, w, h, px in comps:
        if strict is not None:
            lo_h, hi_h = float(strict.get("min_h", 13)) * sc, float(strict.get("max_h", 28)) * sc
            if px < min_px or not (lo_h <= h <= hi_h) or not (float(strict.get("min_w", 30)) * sc <= w <= max_w) \
                    or w < 1.5 * h:
                continue
        elif px < min_px or not (min_h <= h <= max_h) or not (min_w <= w <= max_w) or w < 1.5 * h:
            continue
        fill = px / float(w * h)
        if fill > 0.75:                   # 实心红块（血条/按钮），不是字
            continue
        d = {"x": x + x0, "y": y + y0, "w": w, "h": h, "px": px}
        if strict is not None:
            m = mask[y:y + h, x:x + w]
            rr, mm = r[y:y + h, x:x + w], mx[y:y + h, x:x + w]
            pur = float((rr - mm)[m].mean()) if m.any() else 0.0
            mr = float(rr[m].mean()) if m.any() else 0.0
            tr = float(np.abs(np.diff(m.astype(np.int8), axis=1)).sum(1).mean()) if w > 1 else 0.0
            strokes = tr / max(1.0, w / (20.0 * sc))
            d.update(pur=round(pur, 1), mr=round(mr, 1), strokes=round(strokes, 2))
            if pur < float(strict.get("min_purity", 90)) or mr < float(strict.get("min_mean_r", 150)) \
                    or strokes < float(strict.get("min_strokes", 1.0)):
                continue
        out.append(d)
    out.sort(key=lambda d: -d["px"])
    return out

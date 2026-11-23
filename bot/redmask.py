"""v8.66（t249u，Simon 选的第 1 项）：远处“红名”的固定界面遮罩。

做法：人每转身 ≥ turn_deg 度取样一次（不在战斗中），记下这一帧所有“红名”盖住的屏幕格子（cell×cell 像素）。
真怪的名字在 3D 画面里，转 15° 会横着挪几百像素，不会总落在同一格；界面上的红色（buff 图标、红字报错、头像框……）
不管怎么转都钉在同一格。某一格在 ≥ frac 比例的取样里都有“红名”、且至少出现 min_hits 次 → 这一格是界面，永久不看。

另外，转身/走路判定（v8.50/v8.53）认定成永久界面的位置也记进来（add_point）。
学到的格子和位置存到 red_ui_mask.json（WowGrinder 文件夹里），下次启动还记得；画面大小变了就作废重学。
删掉这个文件（或界面里点“清空学到的界面遮罩”）= 从头学。
"""
from __future__ import annotations

import json
import math
import os


class RedUIMask:
    VERSION = 1

    def __init__(self, cell: int = 16):
        self.cell = int(cell)
        self.size = None            # (W, H)
        self.hits = {}              # 格子 -> 加权次数（慢慢衰减）
        self.nhits = {}             # 格子 -> 真实次数
        self.samples = 0.0          # 加权取样数
        self.learned = set()        # 认定是界面的格子 (cx, cy)
        self.points = []            # 认定是界面的位置 [(x, y, r)]
        self.last_fac = None
        self.path = None
        self.dirty = False

    # ------------------------------------------------------------ 存取
    def attach(self, path: str) -> str:
        """绑定文件并读取。返回一句说明（日志用）。"""
        self.path = path
        if not path or not os.path.exists(path):
            return "还没有学到的界面遮罩（从头学）"
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if int(d.get("version", 0)) != self.VERSION or int(d.get("cell", 0)) != self.cell:
                return "界面遮罩文件版本不对，从头学"
            self.size = tuple(int(v) for v in d.get("size") or ()) or None
            self.learned = {(int(a), int(b)) for a, b in d.get("learned") or []}
            self.points = [(float(x), float(y), float(r)) for x, y, r in d.get("points") or []]
            return f"读取学到的界面遮罩：{len(self.learned)} 格 + {len(self.points)} 个位置（画面 {self.size}）"
        except Exception as e:  # noqa: BLE001
            return f"界面遮罩文件读不了（{type(e).__name__}），从头学"

    def save(self, force: bool = False) -> bool:
        if not self.path or not (self.dirty or force):
            return False
        d = {"version": self.VERSION, "cell": self.cell, "size": list(self.size) if self.size else None,
             "learned": sorted([list(c) for c in self.learned]),
             "points": [[round(x, 1), round(y, 1), round(r, 1)] for x, y, r in self.points]}
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.dirty = False
            return True
        except Exception:  # noqa: BLE001
            return False

    def clear(self):
        self.hits, self.nhits, self.samples = {}, {}, 0.0
        self.learned, self.points = set(), []
        self.last_fac = None
        self.dirty = True

    # ------------------------------------------------------------ 学习
    def _check_size(self, frame_size):
        fs = tuple(int(v) for v in frame_size[:2])
        if self.size != fs:
            if self.size is not None:
                self.clear()
            self.size = fs
            self.dirty = True

    def _cells(self, box):
        x, y, w, h = box
        c = self.cell
        return {(cx, cy) for cx in range(int(x) // c, int(x + w - 1) // c + 1)
                for cy in range(int(y) // c, int(y + h - 1) // c + 1)}

    def observe(self, names, facing, frame_size, turn_deg=15.0, frac=0.4, min_hits=5, decay=0.98) -> list:
        """每帧调用（不在战斗中）。只在朝向比上次取样变了 ≥ turn_deg 时取样。返回这次新认定的格子中心 [(x, y)]。"""
        if facing is None or not frame_size:
            return []
        self._check_size(frame_size)
        if self.last_fac is None:
            self.last_fac = facing
            return []
        d = abs((facing - self.last_fac + math.pi) % (2 * math.pi) - math.pi)
        if math.degrees(d) < float(turn_deg):
            return []
        self.last_fac = facing
        cells = set()
        for n in names or []:
            cells |= self._cells((n["x"], n["y"], n["w"], n["h"]))
        self.samples = self.samples * decay + 1.0
        for k in list(self.hits):
            self.hits[k] *= decay
            if self.hits[k] < 0.05 and k not in cells:
                del self.hits[k]
                self.nhits.pop(k, None)
        new = []
        for k in cells:
            self.hits[k] = self.hits.get(k, 0.0) + 1.0
            self.nhits[k] = self.nhits.get(k, 0) + 1
            if k not in self.learned and self.nhits[k] >= int(min_hits) and self.hits[k] / self.samples >= float(frac):
                self.learned.add(k)
                self.dirty = True
                new.append(((k[0] + 0.5) * self.cell, (k[1] + 0.5) * self.cell))
        return new

    def add_point(self, x, y, r, frame_size=None):
        if frame_size:
            self._check_size(frame_size)
        if any(math.hypot(x - px, y - py) < 4 for px, py, _ in self.points):
            return
        self.points.append((float(x), float(y), float(r)))
        self.dirty = True

    # ------------------------------------------------------------ 查询
    def is_ui(self, cx, cy, frame_size=None) -> bool:
        if frame_size and self.size and tuple(int(v) for v in frame_size[:2]) != self.size:
            return False
        if (int(cx) // self.cell, int(cy) // self.cell) in self.learned:
            return True
        return any(math.hypot(cx - px, cy - py) < r for px, py, r in self.points)

    def summary(self) -> str:
        return f"{len(self.learned)} 格 + {len(self.points)} 个位置"

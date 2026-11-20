"""框选工具：全屏显示一张截图，用鼠标拖出矩形。回车/松开确认，Esc 取消。"""
from __future__ import annotations

import base64
import math
import tkinter as tk

import cv2


def _photo_from_bgr(img_bgr) -> tk.PhotoImage:
    ok, buf = cv2.imencode(".png", img_bgr)
    return tk.PhotoImage(data=base64.b64encode(buf.tobytes()).decode("ascii"))


def select_region(master: tk.Misc, image_bgr, title: str = "拖动鼠标框选区域"):
    """返回 [left, top, w, h]（图片原始像素坐标）或 None。"""
    sw, sh = master.winfo_screenwidth(), master.winfo_screenheight()
    ih, iw = image_bgr.shape[:2]
    scale = max(1, math.ceil(max(iw / sw, ih / sh)))  # 图片比屏幕大时整数缩小
    shown = image_bgr if scale == 1 else cv2.resize(image_bgr, (iw // scale, ih // scale),
                                                     interpolation=cv2.INTER_AREA)

    top = tk.Toplevel(master)
    top.title(title)
    top.overrideredirect(True)
    top.geometry(f"{sw}x{sh}+0+0")
    top.attributes("-topmost", True)
    canvas = tk.Canvas(top, width=sw, height=sh, highlightthickness=0, cursor="crosshair", bg="black")
    canvas.pack(fill="both", expand=True)
    photo = _photo_from_bgr(shown)
    canvas.create_image(0, 0, image=photo, anchor="nw")
    canvas.image = photo
    canvas.create_rectangle(0, 0, sw, 34, fill="black", outline="")
    canvas.create_text(10, 17, anchor="w", fill="yellow", font=("Microsoft YaHei", 14),
                       text=f"{title}   （拖动框选，松开鼠标确认；Esc 取消；需要精细可放大后在设置里手动改数字）")

    result = {"rect": None}
    state = {"x0": 0, "y0": 0, "id": None}

    def on_down(e):
        state["x0"], state["y0"] = e.x, e.y
        if state["id"]:
            canvas.delete(state["id"])
        state["id"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=2)

    def on_drag(e):
        if state["id"]:
            canvas.coords(state["id"], state["x0"], state["y0"], e.x, e.y)

    def on_up(e):
        x0, x1 = sorted((state["x0"], e.x))
        y0, y1 = sorted((state["y0"], e.y))
        if x1 - x0 >= 3 and y1 - y0 >= 3:
            result["rect"] = [x0 * scale, y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale]
            top.destroy()

    canvas.bind("<ButtonPress-1>", on_down)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_up)
    top.bind("<Escape>", lambda e: top.destroy())
    top.focus_force()
    top.grab_set()
    master.wait_window(top)
    return result["rect"]

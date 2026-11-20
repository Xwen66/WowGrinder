"""前台窗口检查（仅 Windows）：游戏不在前台时不按键，避免误操作其它程序。"""
from __future__ import annotations

import sys


def foreground_title() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return None


GAME_EXES = ("wow.exe", "wowclassic.exe", "wowb.exe", "wowt.exe", "wow-64.exe", "wowclassict.exe", "wowclassicb.exe")


def foreground_info():
    """(进程 exe 名小写, 进程 id, 标题)；取不到返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        h = k32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    exe = buf.value.replace("/", "\\").split("\\")[-1].lower()
            finally:
                k32.CloseHandle(h)
        return exe, int(pid.value), foreground_title() or ""
    except Exception:
        return None


def game_in_foreground(keywords: str, exes: str = "") -> bool:
    """v8.12：先按进程判断（Wow.exe 等）。以前只按标题关键字，“WoW” 会匹配到本程序窗口
    “WowGrinder 熊德自动打怪”、标题含“魔兽世界”的浏览器网页 → 误以为游戏在前台，截到的是别的窗口。"""
    import os
    info = foreground_info()
    if info is not None:
        exe, pid, title = info
        if pid == os.getpid():
            return False                      # 本程序（WowGrinder 界面）在前台
        names = [e.strip().lower() for e in (exes or "").split(",") if e.strip()] or list(GAME_EXES)
        if exe:
            return exe in names
        t = title.lower()
        if "wowgrinder" in t or "google chrome" in t or "edge" in t or "firefox" in t:
            return False
    title = foreground_title()
    if title is None:  # 非 Windows 或取不到 → 不拦截
        return True
    if "wowgrinder" in title.lower():
        return False
    words = [w.strip().lower() for w in (keywords or "").split(",") if w.strip()]
    return not words or any(w in title.lower() for w in words)


def enable_dpi_awareness():
    """让 Tk / mss / pyautogui 使用相同的物理像素坐标（Windows 缩放 125%/150% 时很重要）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

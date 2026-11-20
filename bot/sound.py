"""提示音：Windows 用 winsound，其他系统打印响铃符。"""
from __future__ import annotations


def alarm(times: int = 3):
    try:
        import winsound
        for _ in range(times):
            winsound.Beep(1200, 250)
            winsound.Beep(800, 250)
    except Exception:  # noqa: BLE001 - 非 Windows / 没有声卡
        try:
            winsound.MessageBeep()  # type: ignore[name-defined]
        except Exception:  # noqa: BLE001
            print("\a" * times, end="", flush=True)

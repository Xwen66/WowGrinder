"""WowGrinder 入口：python main.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.window import enable_dpi_awareness  # noqa: E402

enable_dpi_awareness()  # 必须在创建 Tk 窗口之前

from gui.app import run  # noqa: E402

if __name__ == "__main__":
    run()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
终端输出工具库：ANSI 彩色支持 + 表格排版 + 状态提示
"""
import sys
import os
import io
import threading
from typing import Dict, List, Optional
from contextlib import redirect_stdout

# 全局输出锁：capture_print 重定向 sys.stdout 期间，其他线程的 print 必须等待
output_lock = threading.Lock()


class Colors:
    """ANSI 颜色代码 (兼容 Win10+/Linux/macOS)"""
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    ORANGE = "\033[38;5;208m"
    DARK_GRAY = "\033[90m"
    RESET = "\033[0m"


def enable_win_colors():
    """Windows 终端启用 ANSI 支持"""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            STD_OUTPUT_HANDLE = -11
            ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        except Exception:
            pass
        try:
            from colorama import init
            init()
        except ImportError:
            pass

def c(text, color_code):
    """包裹彩色文本"""
    return f"{color_code}{text}{Colors.RESET}"


def truncate(text, max_len=30):
    """安全截断长文本"""
    if not text: return ""
    return text if len(text) <= max_len else text[:max_len - 3] + "..."


def print_lecture_table(title=None, chairs=None, category="available", custom_title=None):
    enable_win_colors()
    count = len(chairs) if chairs else 0

    category_config = {
        "available": (Colors.GREEN, f"🟢 可报名讲座 ({count})"),
        "not_started": (Colors.YELLOW, f"🟡 未到时间 ({count})"),
        "full": (Colors.RED, f"🔴 已满讲座 ({count})"),
        "conflict": (Colors.ORANGE, f"🟠 时间冲突讲座 ({count})"),
        "nanjing": (Colors.MAGENTA, f"🟣 南京校区讲座 ({count})"),
        "hefei": (Colors.CYAN, f"🔵 合肥校区讲座 ({count})"),
        "enrolled": (Colors.BLUE, f"✅ 已报名讲座 ({count})"),
    }

    base_color, default_title = category_config.get(category, (Colors.RESET, f"{category} ({count})"))
    display_title = custom_title if custom_title else default_title

    print(f"\n{'=' * 75}")
    print(c(display_title, base_color))

    if not chairs:
        print(c("   (无数据)", Colors.BLUE))
        return

    header = f"{'序号':<3}|   {'报名时间':<12} |{'剩余':>3} | {'讲座时间':<15} | {'地点':<20} | {'标题':<28} | {'主讲人'}"
    print(c(header, Colors.CYAN))
    print("-" * 95)

    row_fmt = "{:<5}|   {:<13} | {:>4} | {:<19} | {:<20} | {:<28} | {:<10}"
    for i, item in enumerate(chairs, 1):
        title_text = truncate(item["title"], 28)
        row = row_fmt.format(
            i, item["applyBeginTime"], item["leave"],
            item["time"], item.get("address") or "-", title_text, item["teacher"]
        )
        print(c(row, base_color))
    print(f"{'=' * 95}\n")


def print_all_categories(categorized_result: Dict[str, List[Dict]]):
    enable_win_colors()

    category_order = [
        ("not_started", "未到时间", "🟡"),
        ("available", "可报名", "🟢"),
        # ("full", "已满", "🔴"),
        ("conflict", "时间冲突", "🟠"),
        # ("nanjing", "南京校区", "🟣"),
        # ("hefei", "合肥校区", "🔵"),   # 屏蔽校区不整表输出，过滤统计行里有数量
        ("enrolled", "已报名", "✅"),
    ]

    has_data = False
    for cat_key, cat_name, icon in category_order:
        chairs = categorized_result.get(cat_key, [])
        if not chairs:
            continue

        has_data = True
        count = len(chairs)
        print_lecture_table(chairs=chairs, category=cat_key)

    if not has_data:
        print(f"\n{'=' * 75}")
        print(c("   (无数据)", Colors.BLUE))
        print(f"{'=' * 75}\n")


class AccountOutputBuffer:
    def __init__(self, account_name: str):
        self.account_name = account_name
        self._lines: List[str] = []
        self._lock = threading.Lock()

    def add(self, text: str = ""):
        if text is None:
            return
        with self._lock:
            self._lines.append(str(text))

    def add_status(self, success: bool, msg: str):
        prefix = "✅" if success else "❌"
        self.add(f"{prefix} {msg}")

    def capture_print(self, func, *args, **kwargs):
        buf = io.StringIO()
        # redirect_stdout 是全局替换：多账号并发时必须持锁，否则主线程的
        # print 会落进某个工作线程的 StringIO 里凭空消失
        with output_lock, redirect_stdout(buf):
            func(*args, **kwargs)
        content = buf.getvalue().rstrip()
        if content:
            self.add(content)

    def render(self, summary: Optional[dict] = None) -> str:
        header = (
            f"\n{'=' * 72}\n"
            f"📦 账号：{self.account_name}\n"
            f"{'=' * 72}"
        )
        body = "\n".join(self._lines).rstrip()
        footer = ""
        if summary:
            footer = (
                f"\n--- 汇总 ---\n"
                f"成功: {summary.get('success', 0)} | "
                f"失败: {summary.get('fail', 0)}"
                f"{' | ' + summary['error'] if summary.get('error') else ''}"
            )
        return f"{header}\n{body}{footer}\n"

"""
alert.py - tkinter 置顶弹窗提醒模块

功能：
- 静默超时提醒弹窗（15 秒静音）
- 手动关麦提醒弹窗（物理/系统关闭麦克风）
- 弹窗置顶常驻，用户关闭后继续后台监听
- 防止重复弹窗：同时只允许一个弹窗存在
"""

import threading
import tkinter as tk
from tkinter import font as tkfont
from typing import Optional

# 弹窗文案常量
MSG_SILENT  = "已连续静音 15 秒，\n请检查麦克风状态，避免错过发言"
MSG_MIC_OFF = "麦克风已被手动关闭，\n请留意发言"

# 弹窗样式
BG_COLOR      = "#1e1e2e"
FG_COLOR      = "#cdd6f4"
ACCENT_COLOR  = "#f38ba8"
BTN_BG        = "#313244"
BTN_HOVER_BG  = "#45475a"
WIN_WIDTH     = 360
WIN_HEIGHT    = 160


class AlertWindow:
    """置顶提醒弹窗（单例模式：同时只允许一个存在）"""

    _lock = threading.Lock()
    _instance: Optional["AlertWindow"] = None

    @classmethod
    def show(cls, alert_type: str = "silent") -> None:
        """
        在主线程安全地弹出提醒窗口。
        :param alert_type: 'silent' | 'mic_off'
        """
        with cls._lock:
            if cls._instance is not None:
                # 已有弹窗存在，不重复弹出
                return
        # 在独立线程中运行 tkinter 事件循环，避免阻塞监听主线程
        t = threading.Thread(
            target=cls._run_window,
            args=(alert_type,),
            daemon=True,
        )
        t.start()

    @classmethod
    def _run_window(cls, alert_type: str) -> None:
        """在独立线程中创建并运行 tkinter 弹窗"""
        with cls._lock:
            cls._instance = object()  # 占位标记，防止并发重复创建

        root = tk.Tk()
        root.withdraw()  # 先隐藏，配置完成后再显示

        msg = MSG_MIC_OFF if alert_type == "mic_off" else MSG_SILENT

        # ---- 窗口基础配置 ----
        root.title("智能防误关麦助手 - 提醒")
        root.configure(bg=BG_COLOR)
        root.resizable(False, False)
        root.attributes("-topmost", True)    # 置顶
        root.attributes("-alpha", 0.97)      # 轻微透明

        # ---- 居中显示 ----
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = (sw - WIN_WIDTH) // 2
        y = (sh - WIN_HEIGHT) // 2
        root.geometry(f"{WIN_WIDTH}x{WIN_HEIGHT}+{x}+{y}")

        # ---- 图标文字（根据类型切换颜色） ----
        icon_color = ACCENT_COLOR if alert_type == "mic_off" else "#fab387"
        icon_label = tk.Label(
            root, text="🎤", bg=BG_COLOR, fg=icon_color,
            font=("Segoe UI Emoji", 22)
        )
        icon_label.pack(pady=(16, 0))

        # ---- 提醒文案 ----
        msg_label = tk.Label(
            root, text=msg, bg=BG_COLOR, fg=FG_COLOR,
            font=("Microsoft YaHei", 11),
            justify="center", wraplength=320
        )
        msg_label.pack(pady=(6, 0))

        # ---- 关闭按钮 ----
        def on_close():
            root.destroy()
            with cls._lock:
                cls._instance = None

        btn = tk.Button(
            root, text="我知道了",
            bg=BTN_BG, fg=FG_COLOR,
            activebackground=BTN_HOVER_BG, activeforeground=FG_COLOR,
            relief="flat", bd=0, padx=18, pady=5,
            font=("Microsoft YaHei", 10),
            cursor="hand2",
            command=on_close,
        )
        btn.pack(pady=(10, 0))

        # ---- 键盘 Enter / Escape 也可关闭 ----
        root.bind("<Return>", lambda _: on_close())
        root.bind("<Escape>", lambda _: on_close())

        # 处理窗口 X 按钮
        root.protocol("WM_DELETE_WINDOW", on_close)

        root.deiconify()  # 显示窗口
        root.mainloop()

        # mainloop 退出后清除实例标记
        with cls._lock:
            cls._instance = None


def show_silent_alert() -> None:
    """弹出静默超时提醒"""
    AlertWindow.show(alert_type="silent")


def show_mic_off_alert() -> None:
    """弹出手动关麦提醒"""
    AlertWindow.show(alert_type="mic_off")
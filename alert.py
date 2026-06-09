"""
alert.py - tkinter 置顶弹窗提醒模块（重构版）

功能：
- 静默超时提醒弹窗（15 秒静音）
- 手动关麦提醒弹窗（物理/系统关闭麦克风）
- 弹窗置顶常驻，用户关闭后继续后台监听
- 单例复用：关闭时隐藏窗口而非销毁实例，彻底修复重复创建 BUG

架构说明：
  AlertWindow 在模块首次调用时创建唯一 tkinter 实例，之后通过
  withdraw/deiconify 切换可见性，不再重复 destroy/创建。
  tkinter mainloop 运行在独立守护线程中，与监听主循环完全解耦。
"""

import threading
import tkinter as tk
from typing import Optional

# Windows 提示音（弹窗时播放短提示音）
try:
    import winsound
    _HAS_WINSOUND = True
except ImportError:
    _HAS_WINSOUND = False


def _play_alert_sound() -> None:
    """播放弹窗提示音（Windows 使用 winsound，其他平台静默跳过）"""
    if not _HAS_WINSOUND:
        return
    try:
        # 播放系统默认提示音（异步，不阻塞 GUI 线程）
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass

# 弹窗文案常量 — 核心聚焦「提醒关麦」
MSG_SILENT  = "麦克风仍开启中\n建议关闭麦克风避免背景音干扰"
MSG_MIC_OFF = "麦克风已关闭\n会议期间如需发言请手动开启"

# 弹窗样式
BG_COLOR     = "#1e1e2e"
FG_COLOR     = "#cdd6f4"
ACCENT_COLOR = "#f38ba8"
BTN_BG       = "#313244"
BTN_HOVER_BG = "#45475a"
WIN_WIDTH    = 360
WIN_HEIGHT   = 170


class AlertWindow:
    """
    置顶提醒弹窗（进程级单例，复用窗口实例）

    设计要点：
    - 首次调用 show() 时在守护线程中创建 Tk 根窗口并启动 mainloop。
    - 后续调用仅更新文案并调用 deiconify() 重新显示，不重复创建。
    - 用户点击关闭时调用 withdraw() 隐藏窗口，不调用 destroy()，
      保留窗口对象供下次复用。
    - 所有对 tkinter 控件的操作通过 after(0, ...) 调度到 mainloop
      线程，确保线程安全。
    """

    _lock     = threading.Lock()
    _instance: Optional["AlertWindow"] = None  # 进程级唯一实例

    def __init__(self):
        self._visible: bool = False
        self._root: Optional[tk.Tk] = None
        self._msg_var: Optional[tk.StringVar] = None
        self._icon_label: Optional[tk.Label] = None
        self._ready = threading.Event()  # mainloop 就绪信号

        # 在守护线程中创建 tkinter 窗口并启动 mainloop
        t = threading.Thread(target=self._create_window, daemon=True)
        t.start()
        self._ready.wait(timeout=5.0)  # 等待窗口初始化完成

    # ------------------------------------------------------------------
    # 类方法入口（外部调用此方法）
    # ------------------------------------------------------------------
    @classmethod
    def show(cls, alert_type: str = "silent", *, message: str = "") -> None:
        """
        显示提醒弹窗（线程安全）。
        :param alert_type: 'silent' | 'mic_off'
        :param message: 可选自定义弹窗文案，空字符串则使用默认文案
        """
        with cls._lock:
            if cls._instance is None:
                cls._instance = AlertWindow()
        cls._instance._show(alert_type, message=message)

    # ------------------------------------------------------------------
    # 窗口创建（在守护线程中运行）
    # ------------------------------------------------------------------
    def _create_window(self) -> None:
        """初始化 tkinter 根窗口（仅调用一次）"""
        self._root = tk.Tk()
        self._root.withdraw()  # 初始隐藏

        # 基础配置
        self._root.title("智能防误关麦助手 - 提醒")
        self._root.configure(bg=BG_COLOR)
        self._root.resizable(False, False)
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", 0.97)

        # 居中定位
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        x  = (sw - WIN_WIDTH)  // 2
        y  = (sh - WIN_HEIGHT) // 2
        self._root.geometry(f"{WIN_WIDTH}x{WIN_HEIGHT}+{x}+{y}")

        # 图标
        self._icon_label = tk.Label(
            self._root, text="🎤", bg=BG_COLOR, fg=ACCENT_COLOR,
            font=("Segoe UI Emoji", 22)
        )
        self._icon_label.pack(pady=(16, 0))

        # 可变文案（通过 StringVar 动态更新，无需重建 Label）
        self._msg_var = tk.StringVar(value=MSG_SILENT)
        msg_label = tk.Label(
            self._root,
            textvariable=self._msg_var,
            bg=BG_COLOR, fg=FG_COLOR,
            font=("Microsoft YaHei", 11),
            justify="center", wraplength=320,
        )
        msg_label.pack(pady=(6, 0))

        # 关闭按钮（隐藏而非销毁）
        btn = tk.Button(
            self._root, text="我知道了",
            bg=BTN_BG, fg=FG_COLOR,
            activebackground=BTN_HOVER_BG, activeforeground=FG_COLOR,
            relief="flat", bd=0, padx=18, pady=5,
            font=("Microsoft YaHei", 10),
            cursor="hand2",
            command=self._hide,
        )
        btn.pack(pady=(10, 0))

        # 键盘快捷键
        self._root.bind("<Return>",  lambda _: self._hide())
        self._root.bind("<Escape>",  lambda _: self._hide())

        # 窗口 X 按钮：隐藏而非关闭
        self._root.protocol("WM_DELETE_WINDOW", self._hide)

        # 通知 show() 等待方：窗口已就绪
        self._ready.set()

        # 启动 mainloop（阻塞，直到程序退出）
        self._root.mainloop()

    # ------------------------------------------------------------------
    # 显示 / 隐藏（线程安全：通过 after 调度到 mainloop 线程）
    # ------------------------------------------------------------------
    def _show(self, alert_type: str, *, message: str = "") -> None:
        """更新文案并显示窗口（调度到 mainloop 线程）"""
        if self._root is None:
            return

        def _do_show():
            # 自定义文案优先，否则用默认文案
            if message:
                msg = message
            else:
                msg = MSG_MIC_OFF if alert_type == "mic_off" else MSG_SILENT
            icon_color = ACCENT_COLOR if alert_type == "mic_off" else "#fab387"
            if self._msg_var:
                self._msg_var.set(msg)
            if self._icon_label:
                self._icon_label.configure(fg=icon_color)
            self._root.deiconify()
            self._root.lift()
            self._root.focus_force()
            self._visible = True
            # 播放提示音（异步，不阻塞 GUI）
            _play_alert_sound()

        self._root.after(0, _do_show)

    def _hide(self) -> None:
        """隐藏窗口（保留实例供下次复用）"""
        if self._root:
            self._root.withdraw()
            self._visible = False

    @property
    def visible(self) -> bool:
        return self._visible


# ------------------------------------------------------------------
# 公共接口函数
# ------------------------------------------------------------------

def show_silent_alert(message: str = "") -> None:
    """弹出静默/闲聊提醒（可传入自定义文案）"""
    AlertWindow.show(alert_type="silent", message=message)


def show_mic_off_alert(message: str = "") -> None:
    """弹出手动关麦提醒（可传入自定义文案）"""
    AlertWindow.show(alert_type="mic_off", message=message)

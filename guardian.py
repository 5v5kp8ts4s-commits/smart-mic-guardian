"""
guardian.py - 防误关麦守护主控模块

功能：
- 整合 VAD 人声检测、静默计时、15 秒延时等待、弹窗提醒
- 区分「环境静音」与「手动关麦」两种场景
- 后台线程持续监听，用户点击弹窗后自动继续监听
"""

import sys
import time
import threading
from typing import Optional

if sys.platform != "win32":
    print("[Guardian] 本模块仅支持 Windows 平台。")
    sys.exit(1)

from vad import VADDetector
from alert import show_silent_alert, show_mic_off_alert


# ========== 配置常量 ==========
SILENT_THRESHOLD_S = 15.0   # 静默计时阈值（秒）
DELAY_WAIT_S       = 15.0   # 延时等待窗口（秒）


class MicGuardian:
    """
    麦克风防误关守护器

    核心状态机：
      监听中 → 静音帧累加 silent_time
            → 人声帧 → silent_time = 0，wait_flag = False，delay_cnt = 0
            → silent_time >= 15 → 进入延时等待（wait_flag = True, delay_cnt = 15）
            → delay_cnt <= 0 → 弹窗提醒
            → mic_off 帧 → 立即弹窗（不走计时）
    """

    def __init__(self):
        self._vad = VADDetector()
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # 计时器状态（由 _listen_loop 内部维护）
        self.silent_time: float = 0.0      # 累计静音时长
        self.wait_flag: bool = False       # 是否处于延时等待中
        self.delay_cnt: float = 0.0        # 延时倒计时
        self._mic_off_flag: bool = False   # 是否触发过关麦弹窗

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动守护监听（阻塞式校准 + 后台线程监听）"""
        if self._running:
            print("[Guardian] 守护线程已在运行")
            return

        # 校准底噪（必须在主线程或同一线程中完成）
        self._vad.calibrate(duration=3.0)

        self._running = True
        self._thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
        )
        self._thread.start()
        print("[Guardian] 守护线程已启动，后台监听中...")

    def stop(self) -> None:
        """停止守护监听并释放资源"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._vad.close()
        print("[Guardian] 守护线程已停止")

    # ------------------------------------------------------------------
    # 主监听循环
    # ------------------------------------------------------------------
    def _listen_loop(self) -> None:
        """后台监听主循环"""
        self._vad.open()
        frame_duration = self._vad.frame_duration  # 单帧耗时（秒）

        while self._running:
            try:
                state, energy = self._vad.detect_frame()
            except RuntimeError as e:
                print(f"[Guardian] 检测异常: {e}")
                break
            except Exception as e:
                print(f"[Guardian] 未预期异常: {e}")
                break

            # ---- 手动关麦：即时弹窗，不走计时 ----
            if state == "mic_off":
                if not self._mic_off_flag:
                    print("[Guardian] 检测到麦克风被手动关闭，即时弹窗提醒")
                    show_mic_off_alert()
                    self._mic_off_flag = True
                    # 清空调试计时器，回归待机
                    self.silent_time = 0.0
                    self.wait_flag = False
                    self.delay_cnt = 0.0
                continue
            else:
                # 关麦状态恢复后，重置标记
                if self._mic_off_flag:
                    print("[Guardian] 麦克风已恢复")
                    self._mic_off_flag = False

            # ---- 检测到人声：重置所有计时器 ----
            if state == "voice":
                if self.silent_time > 0 or self.wait_flag:
                    print(f"[Guardian] 检测到人声（能量={energy:.1f}），计时器重置")
                self.silent_time = 0.0
                self.wait_flag = False
                self.delay_cnt = 0.0
                continue

            # ---- 静音帧：累加静音时间 ----
            # state == "silence"
            self.silent_time += frame_duration

            # 延时等待阶段
            if self.wait_flag:
                self.delay_cnt -= frame_duration
                if self.delay_cnt <= 0:
                    print("[Guardian] 延时结束，弹窗提醒")
                    show_silent_alert()
                    # 弹窗后继续监听，但本次静默周期结束
                    # 重置计时器，避免反复弹窗
                    self.silent_time = 0.0
                    self.wait_flag = False
                    self.delay_cnt = 0.0
                continue

            # 静默达到阈值，进入延时等待
            if self.silent_time >= SILENT_THRESHOLD_S:
                print(f"[Guardian] 连续静音 {self.silent_time:.1f}s，进入 {DELAY_WAIT_S:.0f}s 延时等待")
                self.wait_flag = True
                self.delay_cnt = DELAY_WAIT_S

    # ------------------------------------------------------------------
    # 状态查询（供外部 UI / CLI 展示）
    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def threshold(self) -> float:
        return self._vad.threshold

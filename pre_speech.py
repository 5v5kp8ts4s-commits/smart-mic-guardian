"""
pre_speech.py - 发言预测模块（v4.3）

功能：
1. 音频滑动窗口（近 2s）实时提取预发声特征
   - 能量介于「底噪阈值 ~ 人声阈值」区间 → 判定预发声
   - 窗口后半段能量必须明显高于前半段（上升斜率，过滤静止噪音）
2. 语义历史预判

v4.3 修复：添加能量上升斜率检查，解决安静环境下静止噪音持续误触发问题。
"""

import struct
import time
from collections import deque
from typing import Optional

# 统一从 config 引入参数
from config import (
    PRE_SPEECH_BUFFER_SECS as WINDOW_SECS,
    PRE_VOICE_LOWER_RATIO as PRE_VOICE_LOWER,
    PRE_VOICE_UPPER_RATIO as PRE_VOICE_UPPER,
    PRE_VOICE_MIN_FILL_RATIO,
    PRE_VOICE_HIT_RATIO,
    PRE_VOICE_RISING_RATIO,
    PRE_SPEECH_COOLDOWN_S,
    FRAME_DURATION as _DEFAULT_FRAME_DURATION,
)

class PreSpeechDetector:
    """
    发言预测检测器

    维护近 2s 音频能量滑动窗口，
    判断用户是否处于「即将开口」状态。

    状态属性：
      - is_pre_voice: 音频特征判定为预发声
    """

    def __init__(self, noise_threshold: float, voice_threshold: float,
                 frame_duration: float = 0.03):
        """
        :param noise_threshold: VAD 底噪能量阈值（校准值）
        :param voice_threshold: VAD 人声能量阈值（= noise_threshold * NOISE_FACTOR，通常 * 3）
        :param frame_duration: 单帧时长（秒）
        """
        self._noise_threshold = noise_threshold
        self._voice_threshold = voice_threshold
        self._frame_duration = frame_duration

        # 滑动窗口：最多保存 WINDOW_SECS / frame_duration 帧的能量值
        max_frames = int(WINDOW_SECS / frame_duration) + 1
        self._energy_window: deque[float] = deque(maxlen=max_frames)

        # 语义历史标记：记录上次 mark_ready 命中的时间戳（time.monotonic）

        # 对外暴露的状态
        self._is_pre_voice = False

        # 发言预测触发后的冷却计时（避免连续误触发）
        self._last_trigger_at: Optional[float] = None

    # ------------------------------------------------------------------
    # 更新接口（每帧调用）
    # ------------------------------------------------------------------
    def update_audio(self, energy: float) -> None:
        """
        提供本帧音频能量，更新滑动窗口并计算预发声状态。
        :param energy: 当前帧短时能量（由 VADDetector 提供）
        """
        self._energy_window.append(energy)
        self._is_pre_voice = self._check_pre_voice()

    # ------------------------------------------------------------------
    # 周期性刷新语义状态（可在主循环每帧调用）
    # ------------------------------------------------------------------
    def tick(self) -> None:
        """刷新语义有效期判断，超时自动失效。"""
        pass

    # ------------------------------------------------------------------
    # 状态属性
    # ------------------------------------------------------------------
    @property
    def is_pre_voice(self) -> bool:
        """音频特征命中预发声区间（含冷却期检查）"""
        if not self._is_pre_voice:
            return False
        # 冷却期：触发后 8 秒内即使条件满足也返回 False，避免连续弹窗
        if self._last_trigger_at is not None:
            elapsed = time.monotonic() - self._last_trigger_at
            if elapsed < PRE_SPEECH_COOLDOWN_S:
                return False
        return self._is_pre_voice

    @property

    @property
    def should_reset(self) -> bool:
        """任一预测条件命中 → 建议清零静音计时器"""
        return self._is_pre_voice

    @property
    def status_str(self) -> str:
        """可读状态字符串（用于日志/CLI）"""
        parts = []
        if self._is_pre_voice:
            parts.append("预发声音频")
        return "、".join(parts) if parts else "无"

    def reset(self) -> None:
        """完全重置：清空能量窗口、语义标记和所有状态（麦克风休眠后恢复时调用）"""
        self._energy_window.clear()
        self._is_pre_voice = False
        self._last_trigger_at = None

    # ------------------------------------------------------------------
    # 内部计算
    # ------------------------------------------------------------------
    def _check_pre_voice(self) -> bool:
        """
        判断滑动窗口内是否有足够比例的帧落入预发声区间，且能量呈上升趋势。

        算法演进：
        - v4.0：看窗口内平均能量是否在区间 → 底噪轻微抬高就误触发
        - v4.2：统计单帧命中区间占比 + 窗口填充率检查 → 仍被静止噪音命中
        - v4.3：新增「能量上升斜率」检查，真正的 pre-voice 是气息渐强，
                静止噪音（风扇/空调）能量分布均匀，不会持续上升。

        需同时满足：
        1. 窗口已填充 >= PRE_VOICE_MIN_FILL_RATIO
        2. 落在 [lower, upper) 区间的帧占比 >= PRE_VOICE_HIT_RATIO
        3. 窗口后半段平均能量 >= 前半段 × PRE_VOICE_RISING_RATIO（上升趋势）
        """
        if not self._energy_window:
            return False

        max_frames = self._energy_window.maxlen or 1
        fill_ratio = len(self._energy_window) / max_frames

        # 条件 1：窗口填充率
        if fill_ratio < PRE_VOICE_MIN_FILL_RATIO:
            return False

        lower = self._noise_threshold * PRE_VOICE_LOWER
        upper = self._noise_threshold * PRE_VOICE_UPPER

        # 条件 2：预发声区间命中占比
        hit_frames = sum(1 for e in self._energy_window if lower < e < upper)
        hit_ratio = hit_frames / len(self._energy_window)
        if hit_ratio < PRE_VOICE_HIT_RATIO:
            return False

        # 条件 3：能量上升斜率（核心改进：过滤静止噪音）
        # 将窗口分为前半段和后半段，比较平均能量
        window = list(self._energy_window)
        mid = len(window) // 2
        if mid == 0:
            return False
        first_half_avg = sum(window[:mid]) / mid
        second_half_avg = sum(window[mid:]) / (len(window) - mid)
        # 后半段必须明显高于前半段（气息渐强特征）
        if second_half_avg < first_half_avg * PRE_VOICE_RISING_RATIO:
            return False

        return True


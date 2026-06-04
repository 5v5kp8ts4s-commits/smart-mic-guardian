"""
pre_speech.py - 发言预测模块

功能：
1. 音频滑动窗口（近 2s）实时提取预发声特征
   - 能量介于「底噪阈值 ~ 人声阈值」区间 → 判定预发声
2. 语义历史预判
   - 上一轮 NLP 命中铺垫/提问类意图（action=mark_ready）→ 标记即将发言
3. 任一条件命中 → 建议调用方清零静音计时器
"""

import struct
import time
from collections import deque
from typing import Optional


# ========== 预测配置 ==========
WINDOW_SECS     = 2.0   # 滑动窗口时长（秒）
# 预发声能量倍率区间（相对底噪阈值）
# 能量 ∈ (noise_threshold * LOWER_RATIO, noise_threshold * UPPER_RATIO)
PRE_VOICE_LOWER = 1.05  # 略高于底噪
PRE_VOICE_UPPER = 2.5   # 但低于正常人声（实际人声通常 > 3x 底噪）

# NLP 铺垫类意图的 action 值
FORESHADOW_ACTION = "mark_ready"

# 语义历史有效期（秒）：铺垫意图识别后，多久内视为"即将发言"状态
SEMANTIC_VALID_SECS = 30.0


class PreSpeechDetector:
    """
    发言预测检测器

    维护近 2s 音频能量滑动窗口 + NLP 语义历史标记，
    判断用户是否处于「即将开口」状态。

    状态属性：
      - is_pre_voice: 音频特征判定为预发声
      - is_semantic_ready: NLP 历史语义判定即将发言
      - should_reset: 任一条件命中，建议清零计时器
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
        self._semantic_marked_at: Optional[float] = None

        # 对外暴露的状态
        self._is_pre_voice = False
        self._is_semantic_ready = False

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

    def update_nlp(self, intent_result: Optional[dict]) -> None:
        """
        提供最新 NLP 意图匹配结果，更新语义历史标记。
        :param intent_result: IntentMatcher.match() 的返回值
        """
        if intent_result and intent_result.get("action") == FORESHADOW_ACTION:
            import time
            self._semantic_marked_at = time.monotonic()
            print(f"[PreSpeech] NLP 铺垫意图命中 "
                  f"({intent_result.get('matched_keyword')})，标记即将发言状态")
        self._refresh_semantic_ready()

    # ------------------------------------------------------------------
    # 周期性刷新语义状态（可在主循环每帧调用）
    # ------------------------------------------------------------------
    def tick(self) -> None:
        """刷新语义有效期判断，超时自动失效。"""
        self._refresh_semantic_ready()

    # ------------------------------------------------------------------
    # 状态属性
    # ------------------------------------------------------------------
    @property
    def is_pre_voice(self) -> bool:
        """音频特征命中预发声区间"""
        return self._is_pre_voice

    @property
    def is_semantic_ready(self) -> bool:
        """NLP 语义历史标记为即将发言"""
        return self._is_semantic_ready

    @property
    def should_reset(self) -> bool:
        """任一预测条件命中 → 建议清零静音计时器"""
        return self._is_pre_voice or self._is_semantic_ready

    @property
    def status_str(self) -> str:
        """可读状态字符串（用于日志/CLI）"""
        parts = []
        if self._is_pre_voice:
            parts.append("预发声音频")
        if self._is_semantic_ready:
            parts.append("语义历史铺垫")
        return "、".join(parts) if parts else "无"

    def reset_semantic(self) -> None:
        """手动清除语义历史标记（重置计时器后调用，避免重复触发）"""
        self._semantic_marked_at = None
        self._is_semantic_ready = False

    # ------------------------------------------------------------------
    # 内部计算
    # ------------------------------------------------------------------
    def _check_pre_voice(self) -> bool:
        """
        判断滑动窗口内平均能量是否落入预发声区间：
        底噪阈值 * LOWER_RATIO < 均值 < 底噪阈值 * UPPER_RATIO
        （高于底噪但低于正常人声，对应吸气、轻微唇噪等）
        """
        if not self._energy_window:
            return False
        avg_energy = sum(self._energy_window) / len(self._energy_window)
        lower = self._noise_threshold * PRE_VOICE_LOWER
        upper = self._noise_threshold * PRE_VOICE_UPPER
        return lower < avg_energy < upper

    def _refresh_semantic_ready(self) -> None:
        """检查语义标记是否仍在有效期内"""
        if self._semantic_marked_at is None:
            self._is_semantic_ready = False
            return
        elapsed = time.monotonic() - self._semantic_marked_at
        self._is_semantic_ready = elapsed <= SEMANTIC_VALID_SECS

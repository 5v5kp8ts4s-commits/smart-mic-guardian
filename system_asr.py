"""
system_asr.py - 系统音频 ASR + 会议状态检测模块 (v5.3)

功能：
- 对系统捕获的音频（会议中其他人的声音）进行 ASR 识别
- 关键词检测：识别"轮到你了""请回答""你怎么看"等提问信号
- 会议状态机：
    LISTENING: 其他人在说话（用户在听）
    MY_TURN:   检测到主持人提问用户 → 轮到用户发言
    IDLE:      系统音频安静，会议可能结束/等待中
"""

import time
import re
from enum import Enum
from typing import Optional, List

from config import (
    ASR_CAPTURE_SECS,
    SAMPLE_RATE,
)
from asr import OfflineASR


class MeetingState(Enum):
    """会议状态枚举"""
    LISTENING = "listening"   # 系统音频中检测到人在说话（用户在听）
    MY_TURN = "my_turn"       # 检测到提问用户的关键词
    IDLE = "idle"             # 系统音频安静，无人说话
    UNKNOWN = "unknown"       # 尚未开始检测


# ======================================================
# 提问用户关键词库（主持人/他人提问用户的信号词）
# ======================================================
MY_TURN_KEYWORDS: List[str] = [
    # 直接点名
    "你怎么看", "你怎么想", "你说说", "你说一下",
    "请你说", "请你回答", "你来讲", "你来说说",
    "你怎么看这个问题", "你有什么看法", "你有什么想法",
    "你觉得呢", "你觉得怎么样", "你认为呢",
    # 轮流发言
    "轮到你了", "轮到你", "下一位", "下一个",
    "请继续", "接着说", "继续讲",
    # 征求意见
    "征求一下你的意见", "听听你的看法", "听听你的想法",
    "想听听你的", "想听一下你的",
    # 补充发言
    "补充一下", "补充几点", "还有什么补充",
    # 确认/追问
    "对吗", "是不是", "对吧",
]

# 会议结束/切换信号词
MEETING_END_KEYWORDS: List[str] = [
    "会议结束", "散会", "今天就到这里", "到此为止",
    "下次再讨论", "下次再说", "先这样吧",
    "休息一下", "休会", "中场休息",
]


class SystemASRProcessor:
    """
    系统音频 ASR 处理器

    对系统音频进行周期性 ASR 识别，
    根据识别结果判断当前会议状态。
    """

    def __init__(self, asr: Optional[OfflineASR] = None):
        self._asr = asr if asr is not None else OfflineASR()
        self._state = MeetingState.UNKNOWN
        self._last_voice_time: float = 0.0      # 上次检测到系统音频人声的时间
        self._my_turn_until: float = 0.0         # "轮到用户"状态的过期时间戳
        self._my_turn_duration_secs: float = 30.0  # MY_TURN 状态持续时间（30秒）
        self._idle_threshold_secs: float = 5.0   # 系统音频安静多久判定为 IDLE
        self._last_text: str = ""               # 最近一次识别的文本
        self._last_matched_keyword: str = ""    # 最近一次命中的关键词

    # ------------------------------------------------------------------
    # 核心处理
    # ------------------------------------------------------------------
    def process(self, pcm_bytes: bytes) -> MeetingState:
        """
        处理一段系统音频 PCM 数据。
        :param pcm_bytes: 系统音频 PCM 数据
        :return: 当前会议状态
        """
        now = time.time()

        if not pcm_bytes or len(pcm_bytes) < 1000:
            # 无音频数据 → 检查是否 IDLE
            if now - self._last_voice_time > self._idle_threshold_secs:
                self._state = MeetingState.IDLE
            return self._state

        # 尝试 ASR 识别
        if self._asr.available:
            text = self._asr.recognize_pcm(pcm_bytes)
        else:
            text = ""

        if text:
            self._last_voice_time = now
            self._last_text = text

            # 1. 检查是否会议结束
            if self._match_keywords(text, MEETING_END_KEYWORDS):
                self._state = MeetingState.IDLE
                return self._state

            # 2. 检查是否轮到用户发言
            matched = self._match_keywords(text, MY_TURN_KEYWORDS)
            if matched:
                self._last_matched_keyword = matched
                self._state = MeetingState.MY_TURN
                self._my_turn_until = now + self._my_turn_duration_secs
                print(f"[SystemASR] 检测到提问信号 '{matched}' → 轮到用户发言"
                      f"（持续 {self._my_turn_duration_secs}s）")
                return self._state

            # 3. 其他人在说话 → LISTENING
            self._state = MeetingState.LISTENING
        else:
            # ASR 无结果 → 可能是安静/噪音
            if now - self._last_voice_time > self._idle_threshold_secs:
                self._state = MeetingState.IDLE

        # 检查 MY_TURN 是否过期
        if self._state == MeetingState.MY_TURN and now > self._my_turn_until:
            self._state = MeetingState.IDLE
            print("[SystemASR] 轮到用户状态已过期 → 恢复 IDLE")

        return self._state

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _match_keywords(self, text: str, keywords: List[str]) -> str:
        """
        在文本中匹配关键词，返回第一个命中的关键词或空字符串。
        """
        for kw in keywords:
            if kw in text:
                return kw
        return ""

    def is_my_turn(self) -> bool:
        """当前是否轮到用户发言"""
        return self._state == MeetingState.MY_TURN

    def is_listening(self) -> bool:
        """用户是否正在听其他人说话"""
        return self._state == MeetingState.LISTENING

    def is_idle(self) -> bool:
        """系统音频是否安静（会议可能结束）"""
        return self._state == MeetingState.IDLE

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    @property
    def state(self) -> MeetingState:
        return self._state

    @property
    def status(self) -> dict:
        """当前状态字典（供 CLI/UI 展示）"""
        now = time.time()
        my_turn_remaining = max(0.0, self._my_turn_until - now) if self._my_turn_until > 0 else 0.0
        return {
            "state": self._state.value,
            "is_my_turn": self.is_my_turn(),
            "is_listening": self.is_listening(),
            "is_idle": self.is_idle(),
            "my_turn_remaining": round(my_turn_remaining, 1),
            "last_text": self._last_text,
            "last_matched_keyword": self._last_matched_keyword,
            "asr_available": self._asr.available,
        }

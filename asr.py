"""
asr.py - 离线语音识别模块

功能：
- 基于 vosk 离线轻量化 ASR，不依赖网络
- 接收原始 PCM 字节流，输出中文文本
- 模型文件需用户自行下载（首次运行提示）
"""

import os
import sys
import json
import wave
import io
from pathlib import Path
from typing import Optional

# vosk 可选导入，未安装/无模型时不影响主程序运行
try:
    from vosk import Model, KaldiRecognizer
except ImportError:
    Model = None
    KaldiRecognizer = None

# 统一从 config 引入路径和采样率参数
from config import VOSK_MODEL_PATH, MODEL_DIR, SAMPLE_RATE as _DEFAULT_SAMPLE_RATE


def _ensure_model() -> Optional[str]:
    """
    检查模型目录是否存在。
    :return: 模型目录路径 或 None（未找到）
    """
    if Model is None:
        print("[ASR] vosk 未安装，请执行: pip install vosk")
        return None

    model_path = Path(VOSK_MODEL_PATH)
    if not model_path.exists():
        print(f"[ASR] 模型目录不存在: {model_path}")
        print("[ASR] 请下载中文 vosk 模型并解压到该目录")
        print("[ASR] 下载地址: https://alphacephei.com/vosk/models")
        print("[ASR] 推荐模型: vosk-model-small-cn-0.22 (约 50MB)")
        return None

    return str(model_path)


class OfflineASR:
    """
    离线语音识别器（基于 vosk）
    """

    def __init__(self, sample_rate: int = _DEFAULT_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._model_path: Optional[str] = _ensure_model()
        self._recognizer = None

        if self._model_path and KaldiRecognizer is not None:
            try:
                model = Model(self._model_path)
                self._recognizer = KaldiRecognizer(model, sample_rate)
                print(f"[ASR] 离线识别器初始化成功，模型路径: {self._model_path}")
            except Exception as e:
                print(f"[ASR] 识别器初始化失败: {e}")
                self._recognizer = None

    @property
    def available(self) -> bool:
        """识别器是否可用"""
        return self._recognizer is not None

    # ------------------------------------------------------------------
    # 核心识别接口
    # ------------------------------------------------------------------
    def recognize_pcm(self, pcm_bytes: bytes) -> str:
        """
        识别原始 16-bit 单声道 PCM 字节流。
        :param pcm_bytes: 原始 PCM 数据
        :return: 识别出的中文文本（空字符串表示无结果）
        """
        if not self.available:
            return ""

        try:
            self._recognizer.AcceptWaveform(pcm_bytes)
            result = json.loads(self._recognizer.FinalResult())
            text = result.get("text", "")
            # 重置 recognizer 以便下次使用
            self._reset_recognizer()
            return text.strip()
        except Exception as e:
            print(f"[ASR] 识别异常: {e}")
            return ""

    def recognize_wav_file(self, wav_path: str) -> str:
        """
        识别 WAV 文件（16-bit PCM, 单声道）。
        :param wav_path: WAV 文件路径
        :return: 识别出的中文文本
        """
        if not self.available:
            return ""

        try:
            with wave.open(wav_path, "rb") as wf:
                # 校验格式
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                    print("[ASR] 仅支持 16-bit 单声道 WAV")
                    return ""
                if wf.getframerate() != self.sample_rate:
                    print(f"[ASR] 采样率不匹配: {wf.getframerate()} ≠ {self.sample_rate}")
                    return ""

                text_parts = []
                while True:
                    data = wf.readframes(4000)
                    if len(data) == 0:
                        break
                    self._recognizer.AcceptWaveform(data)

                result = json.loads(self._recognizer.FinalResult())
                text = result.get("text", "").strip()
                self._reset_recognizer()
                return text
        except Exception as e:
            print(f"[ASR] 文件识别异常: {e}")
            return ""

    # ------------------------------------------------------------------
    # 快速语音验证（用于噪音过滤：咳嗽/键盘/走动不触发重置）
    # ------------------------------------------------------------------
    def verify_speech(self, pcm_bytes: bytes, min_text_len: int = 1) -> bool:
        """
        快速验证一段音频是否包含可识别的语音（自然语言）。
        与 recognize_pcm 的区别：只返回 True/False，不做完整文本输出。
        :param pcm_bytes: 原始 PCM 数据
        :param min_text_len: 识别文本最小有效长度
        :return: True 确认为人声（识别出有效文本），False 为噪音/无语音
        """
        if not self.available:
            # ASR 不可用时回退到能量判定（无法做噪音过滤）
            return True

        try:
            self._recognizer.AcceptWaveform(pcm_bytes)
            result = json.loads(self._recognizer.FinalResult())
            text = result.get("text", "").strip()
            self._reset_recognizer()
            return len(text) >= min_text_len
        except Exception as e:
            print(f"[ASR] 语音验证异常: {e}")
            return True  # 异常时保守处理，视为人声

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _reset_recognizer(self) -> None:
        """重置识别器状态，用于连续识别"""
        if self._model_path and KaldiRecognizer is not None and Model is not None:
            try:
                model = Model(self._model_path)
                self._recognizer = KaldiRecognizer(model, self.sample_rate)
            except Exception:
                self._recognizer = None

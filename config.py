"""
config.py - 全项目统一参数配置

所有模块必须从此文件引入参数，禁止在各模块内部硬编码。
统一管理音频参数、阈值、路径、超时时间等全局配置。
"""

import os
from pathlib import Path

# ==============================================================
# 音频采集参数（各模块必须使用完全一致的值）
# ==============================================================
SAMPLE_RATE: int    = 16000    # 采样率（Hz）
FRAME_DURATION: float = 0.03   # 单帧时长（秒）
FRAME_SIZE: int     = int(SAMPLE_RATE * FRAME_DURATION)  # 单帧采样点数（480）
CHANNELS: int       = 1        # 声道数（单声道）
SAMPLE_WIDTH: int   = 2        # 采样位深字节数（16-bit = 2 bytes）

# ==============================================================
# VAD 阈值参数
# ==============================================================
CALIBRATE_SECS: float   = 3.0   # 底噪校准时长（秒）
NOISE_FACTOR: float     = 3.0   # 动态阈值 = 底噪均值 × NOISE_FACTOR
DEFAULT_ENERGY: float   = 500.0 # 校准失败时的默认能量阈值
MIC_OFF_FRAMES: int     = 10    # 连续全零帧数判定为物理关麦

# ==============================================================
# 音频缓冲区
# ==============================================================
AUDIO_BUFFER_SECS: float   = 5.0  # VAD 滚动缓冲区时长（秒，需 >= ASR 截取需求）
ASR_CAPTURE_SECS: float    = 3.0  # ASR 截取静音前音频时长（秒）
PRE_SPEECH_BUFFER_SECS: float = 2.0  # 发言预测滑动窗口时长（秒）

# ==============================================================
# 静默计时与延时等待
# ==============================================================
SILENT_THRESHOLD_S: float  = 15.0  # 静默计时阈值（秒）
DELAY_WAIT_S: float        = 15.0  # 触发弹窗前的延时等待窗口（秒）

# ==============================================================
# 发言预测模块参数
# ==============================================================
PRE_VOICE_LOWER_RATIO: float = 1.05  # 预发声能量下限倍率（相对底噪阈值）
PRE_VOICE_UPPER_RATIO: float = 2.5   # 预发声能量上限倍率
SEMANTIC_VALID_SECS: float   = 30.0  # NLP 铺垫意图有效期（秒）

# ==============================================================
# NLP 模糊匹配
# ==============================================================
FUZZY_THRESHOLD: float = 0.65  # 模糊匹配最低相似度

# ==============================================================
# 文件路径
# ==============================================================
PROJECT_DIR: Path  = Path(__file__).parent
RULES_PATH: Path   = PROJECT_DIR / "intent_rules.json"
MODEL_DIR: Path    = PROJECT_DIR / "model"

# 支持环境变量覆盖 vosk 模型路径
VOSK_MODEL_PATH: str = os.environ.get("VOSK_MODEL_PATH", str(MODEL_DIR))

# ==============================================================
# 稳定性测试参数
# ==============================================================
STRESS_LOG_INTERVAL_S: float  = 300.0   # 压测日志写入间隔（秒）
MEMORY_WARN_MB: float         = 200.0   # 内存占用警告阈值（MB）
MEMORY_CLEAR_MB: float        = 300.0   # 内存占用触发清理阈值（MB）

# ==============================================================
# 异常处理参数
# ==============================================================
MAX_FRAME_ERRORS: int   = 20   # 连续帧错误上限，超出则重置音频流
DEVICE_RETRY_SECS: float = 5.0  # 设备异常后重试间隔（秒）


def validate() -> bool:
    """
    全局参数一致性校验。
    验证 FRAME_SIZE == SAMPLE_RATE * FRAME_DURATION，
    以及各缓冲区时长满足业务需求。
    :return: True 校验通过，False 有不一致
    """
    ok = True
    expected_frame_size = int(SAMPLE_RATE * FRAME_DURATION)
    if FRAME_SIZE != expected_frame_size:
        print(f"[Config 错误] FRAME_SIZE={FRAME_SIZE} 与 "
              f"SAMPLE_RATE*FRAME_DURATION={expected_frame_size} 不一致")
        ok = False

    if AUDIO_BUFFER_SECS < ASR_CAPTURE_SECS:
        print(f"[Config 错误] AUDIO_BUFFER_SECS={AUDIO_BUFFER_SECS} "
              f"< ASR_CAPTURE_SECS={ASR_CAPTURE_SECS}，缓冲区不足")
        ok = False

    if AUDIO_BUFFER_SECS < PRE_SPEECH_BUFFER_SECS:
        print(f"[Config 错误] AUDIO_BUFFER_SECS={AUDIO_BUFFER_SECS} "
              f"< PRE_SPEECH_BUFFER_SECS={PRE_SPEECH_BUFFER_SECS}，缓冲区不足")
        ok = False

    if PRE_VOICE_LOWER_RATIO >= PRE_VOICE_UPPER_RATIO:
        print(f"[Config 错误] PRE_VOICE_LOWER_RATIO >= PRE_VOICE_UPPER_RATIO")
        ok = False

    if ok:
        print(f"[Config] 全局参数校验通过：采样率={SAMPLE_RATE}Hz，"
              f"帧长={FRAME_DURATION*1000:.0f}ms（{FRAME_SIZE}采样点）")
    return ok

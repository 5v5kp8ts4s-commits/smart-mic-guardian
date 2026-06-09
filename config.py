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
# ASR 人声验证窗口（解决咳嗽/键盘/走动噪音误触发问题）
# ==============================================================
VOICE_VERIFY_SECS: float = 1.0   # 检测到能量超限后，收集音频送入 ASR 验证的窗口时长（秒）
MIN_VOICE_TEXT_LEN: int  = 1     # ASR 返回文本的最小有效长度（字符），低于此值视为噪音
VOICE_VERIFY_ENABLED: bool = True  # 是否启用 ASR 语音验证过滤噪音（False 则回退到纯能量检测）

# ==============================================================
# 发言预测模块参数（v4.3 修复误触发）
# ==============================================================
# 预发声区间 = [noise_threshold * LOWER, noise_threshold * UPPER)
# 这个区间必须在底噪之上、正常 VAD 人声之下。
# 原值 1.05 太低（几乎等于底噪），导致频繁误触发 → 修复为 1.5 → 再提高为 2.0
PRE_VOICE_LOWER_RATIO: float = 2.0   # 预发声能量下限倍率
PRE_VOICE_UPPER_RATIO: float = 2.8   # 预发声能量上限倍率（接近 VAD threshold=3.0，缩小区间）
PRE_VOICE_MIN_FILL_RATIO: float = 0.75  # 窗口至少填满 75% 才开始判定（避免冷启动误判）
PRE_VOICE_HIT_RATIO: float = 0.6     # 窗口中 >= 60% 帧落在区间才判定命中（提高门槛）
PRE_VOICE_RISING_RATIO: float = 1.2  # 窗口后半段均值 >= 前半段 × 1.2（能量上升趋势，过滤静止噪音）
PRE_SPEECH_COOLDOWN_S: float = 8.0   # 发言预测触发后的冷却时间（秒），避免连续误触发

# ==============================================================
# ==============================================================

# ==============================================================
# 会议白名单话题提取（v5.0 新增）
# ==============================================================
TOPIC_CALIBRATE_SECS: float = 180.0   # 开场白名单采集时长（秒），默认 3 分钟
TOPIC_MIN_WORD_LEN: int     = 2       # 参与白名单提取的最小词长度（过滤单字）
TOPIC_MIN_FREQ: int         = 2       # 词语出现次数下限，低于此值不进入白名单
TOPIC_TOP_N: int            = 50      # 白名单保留的高频词数量上限
TOPIC_MATCH_MIN_LEN: int    = 2       # ASR 文本中命中白名单的最小匹配长度
CHITCHAT_ALERT_SECS: float  = 3.0     # 连续闲聊内容达到此秒数才触发告警（避免偶发插话）
CHITCHAT_RESET_SECS: float  = 5.0     # 连续非闲聊（命中白名单/静默）持续此秒数则重置闲聊计时
SILENT_ALERT_SECS: float    = 15.0    # 麦克风开启但无有效输入，达到此秒数弹窗提醒关麦
MEETING_MODE_DURATION_SECS: float = 300.0  # 检测到会议发言后，会议模式持续时长（5 分钟）

# ==============================================================
# 系统音频捕获（v5.3 新增）
# 使用 WASAPI Loopback 捕获会议中其他人的语音
# ==============================================================
SYSTEM_AUDIO_ENABLED: bool = True      # 是否启用系统音频捕获
SYSTEM_AUDIO_BUFFER_SECS: float = 5.0  # 系统音频缓冲区时长
SYSTEM_ASR_INTERVAL_SECS: float = 3.0   # 系统音频 ASR 处理间隔（秒）
SYSTEM_IDLE_THRESHOLD_SECS: float = 5.0  # 系统音频安静多久判定为 IDLE（秒）
MY_TURN_DURATION_SECS: float = 30.0     # 检测到提问后 "轮到用户" 持续时间（秒）

# ==============================================================
# 声学校准基线（v5.0 新增）
# 用于区分「近距离开麦发言」vs「扭头小声闲聊」
# ==============================================================
ACOUSTIC_CALIBRATE_FRAMES: int = 200   # 开场校准采集帧数（≈ 6s @ 30ms/帧）
ACOUSTIC_NEAR_ENERGY_MIN: float = 1.0  # 近距离开麦能量下限倍率（相对校准基线）
ACOUSTIC_NEAR_STABLE_RATIO: float = 0.3 # 近距离开麦时帧间能量波动上限（标准差/均值）
ACOUSTIC_CHIT_VOLATILE_RATIO: float = 0.6  # 扭头闲聊时能量波动下限（忽高忽低）

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
MEMORY_WARN_MB: float         = 500.0   # 内存占用警告阈值（MB），提高至 500MB 避免频繁误报
MEMORY_CLEAR_MB: float        = 800.0   # 内存占用触发清理阈值（MB）

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

    if PRE_VOICE_MIN_FILL_RATIO < 0.3:
        print(f"[Config 警告] PRE_VOICE_MIN_FILL_RATIO={PRE_VOICE_MIN_FILL_RATIO}，"
              f"窗口填充率过低可能导致冷启动时误判")

    if PRE_VOICE_HIT_RATIO < 0.3:
        print(f"[Config 警告] PRE_VOICE_HIT_RATIO={PRE_VOICE_HIT_RATIO}，"
              f"命中比率过低可能频繁误触发")

    if PRE_VOICE_HIT_RATIO < PRE_VOICE_MIN_FILL_RATIO:
        print(f"[Config 错误] PRE_VOICE_HIT_RATIO < PRE_VOICE_MIN_FILL_RATIO，"
              f"逻辑上不可能满足")

    if PRE_VOICE_LOWER_RATIO < 1.8:
        print(f"[Config 警告] PRE_VOICE_LOWER_RATIO={PRE_VOICE_LOWER_RATIO}，"
              f"过低会导致预发声区间与底噪重叠，建议 >= 2.0")

    if PRE_VOICE_RISING_RATIO < 1.05:
        print(f"[Config 警告] PRE_VOICE_RISING_RATIO={PRE_VOICE_RISING_RATIO}，"
              f"上升斜率门槛过低，无法有效过滤静止噪音")

    if VOICE_VERIFY_SECS > SILENT_THRESHOLD_S:
        print(f"[Config 警告] VOICE_VERIFY_SECS={VOICE_VERIFY_SECS}s 大于 SILENT_THRESHOLD_S，"
              f"建议 VOICE_VERIFY_SECS <= 1.5s 以避免响应延迟")

    if TOPIC_CALIBRATE_SECS < 30 or TOPIC_CALIBRATE_SECS > 600:
        print(f"[Config 警告] TOPIC_CALIBRATE_SECS={TOPIC_CALIBRATE_SECS}s，"
              f"建议 30~600 秒之间")

    if CHITCHAT_ALERT_SECS < 1.0:
        print(f"[Config 警告] CHITCHAT_ALERT_SECS={CHITCHAT_ALERT_SECS}s，"
              f"过短会导致偶发插话误报")

    if ok:
        mode = "ASR语音验证" if VOICE_VERIFY_ENABLED else "纯能量检测"
        print(f"[Config] 全局参数校验通过：采样率={SAMPLE_RATE}Hz，"
              f"帧长={FRAME_DURATION*1000:.0f}ms（{FRAME_SIZE}采样点），"
              f"人声检测模式={mode}，话题校准={TOPIC_CALIBRATE_SECS}s")
    return ok

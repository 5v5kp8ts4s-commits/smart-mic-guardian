"""
vad.py - 人声检测模块（VAD 短时能量算法）

功能：
- PyAudio 流式读取麦克风采样数据
- 程序启动前 3 秒采集环境底噪，自动校准动态阈值
- 逐帧计算短时能量，区分人声 / 环境静音 / 手动关麦三种状态
"""

import sys
import struct
from collections import deque

# 平台检查
if sys.platform != "win32":
    print("[VAD] 本模块仅支持 Windows 平台。")
    sys.exit(1)

try:
    import pyaudio
except ImportError:
    print("[VAD 错误] 缺少 PyAudio，请执行: pip install pyaudio")
    sys.exit(1)

# 统一从 config 引入所有音频参数
from config import (
    SAMPLE_RATE, FRAME_DURATION, FRAME_SIZE,
    CALIBRATE_SECS, NOISE_FACTOR, DEFAULT_ENERGY,
    MIC_OFF_FRAMES, AUDIO_BUFFER_SECS, ASR_CAPTURE_SECS,
)


def _compute_energy(frame_bytes: bytes) -> float:
    """
    计算单帧短时能量（振幅平方和）
    :param frame_bytes: 16-bit PCM 字节数据
    :return: 短时能量值
    """
    count = len(frame_bytes) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"{count}h", frame_bytes)
    return sum(s * s for s in samples) / count


def _is_all_zero(frame_bytes: bytes) -> bool:
    """判断一帧是否全部采样点为 0（硬件静音/物理关麦特征）"""
    return all(b == 0 for b in frame_bytes)


class VADDetector:
    """
    麦克风人声检测器

    三种状态：
      - 'voice'   : 检测到人声
      - 'silence' : 麦克风开着但环境安静
      - 'mic_off' : 麦克风被手动关闭（物理/系统静音）
    """

    def __init__(self):
        self._pa: pyaudio.PyAudio = pyaudio.PyAudio()
        self._stream = None
        self._threshold: float = DEFAULT_ENERGY
        self._mic_off_count: int = 0  # 连续全零帧计数
        self.calibrated: bool = False

        # 音频滚动缓冲区：保存最近 AUDIO_BUFFER_SECS 秒的原始 PCM 帧
        max_frames = int(AUDIO_BUFFER_SECS / FRAME_DURATION)
        self._audio_buffer: deque[bytes] = deque(maxlen=max_frames)

    # ------------------------------------------------------------------
    # 底噪校准
    # ------------------------------------------------------------------
    def calibrate(self, duration: float = CALIBRATE_SECS) -> float:
        """
        采集指定时长的环境底噪，计算并设置动态阈值。
        :param duration: 校准时长（秒），默认 3 秒
        :return: 校准后的动态阈值
        """
        print(f"[VAD] 开始底噪校准，请保持安静（{duration:.0f} 秒）...")
        stream = self._open_stream()
        energies = []
        frames_needed = int(duration / FRAME_DURATION)

        for _ in range(frames_needed):
            try:
                data = stream.read(FRAME_SIZE, exception_on_overflow=False)
                if not _is_all_zero(data):
                    energies.append(_compute_energy(data))
            except OSError:
                break

        stream.stop_stream()
        stream.close()

        if energies:
            avg_energy = sum(energies) / len(energies)
            self._threshold = avg_energy * NOISE_FACTOR
            self.calibrated = True
            print(f"[VAD] 校准完成：底噪均值={avg_energy:.1f}，动态阈值={self._threshold:.1f}")
        else:
            self._threshold = DEFAULT_ENERGY
            print(f"[VAD] 校准数据不足，使用默认阈值 {DEFAULT_ENERGY}")

        return self._threshold

    # ------------------------------------------------------------------
    # 开启/关闭音频流
    # ------------------------------------------------------------------
    def open(self) -> None:
        """打开麦克风音频流"""
        if self._stream is None or not self._stream.is_active():
            self._stream = self._open_stream()

    def close(self) -> None:
        """关闭麦克风音频流并释放资源"""
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._pa.terminate()

    # ------------------------------------------------------------------
    # 逐帧检测
    # ------------------------------------------------------------------
    def detect_frame(self) -> tuple[str, float]:
        """
        读取一帧音频并返回检测结果。
        :return: (状态字符串, 帧能量值)
          - 状态: 'voice' | 'silence' | 'mic_off'
          - 能量: 当前帧短时能量
        """
        if self._stream is None:
            raise RuntimeError("[VAD] 音频流未打开，请先调用 open()")

        try:
            data = self._stream.read(FRAME_SIZE, exception_on_overflow=False)
        except OSError as e:
            print(f"[VAD 警告] 读取音频帧失败: {e}")
            return "silence", 0.0

        # 写入滚动缓冲区
        self._audio_buffer.append(data)

        # --- 手动关麦检测：连续 MIC_OFF_FRAMES 帧全零 ---
        if _is_all_zero(data):
            self._mic_off_count += 1
            if self._mic_off_count >= MIC_OFF_FRAMES:
                return "mic_off", 0.0
            # 不足判定帧数时先视为静音
            return "silence", 0.0
        else:
            self._mic_off_count = 0  # 有效帧，重置关麦计数

        # --- 短时能量计算与人声判定 ---
        energy = _compute_energy(data)
        if energy > self._threshold:
            return "voice", energy
        return "silence", energy

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------
    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def frame_duration(self) -> float:
        """单帧时长（秒），用于外部累加计时"""
        return FRAME_DURATION

    def get_recent_pcm(self, duration_secs: float = 3.0) -> bytes:
        """
        从滚动缓冲区截取最近 duration_secs 秒的 PCM 数据。
        :param duration_secs: 截取时长，默认 3 秒
        :return: 原始 PCM 字节流
        """
        frames_needed = int(duration_secs / FRAME_DURATION)
        recent = list(self._audio_buffer)[-frames_needed:]
        return b"".join(recent)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _get_input_device_index(self) -> int:
        """
        获取可用输入设备的索引。
        先尝试默认输入设备，失败则遍历所有设备找第一个有输入通道的设备。
        :return: 设备索引号
        :raises RuntimeError: 找不到任何可用输入设备
        """
        # 尝试获取默认输入设备
        try:
            info = self._pa.get_default_input_device_info()
            idx = info.get("index")
            if idx is not None:
                return int(idx)
        except Exception:
            pass

        # 默认失败，遍历所有设备
        device_count = self._pa.get_device_count()
        for i in range(device_count):
            try:
                info = self._pa.get_device_info_by_index(i)
                max_input = info.get("maxInputChannels", 0)
                if max_input and int(max_input) > 0:
                    print(f"[VAD] 使用音频输入设备: {info.get('name', 'Unknown')} (index={i})")
                    return i
            except Exception:
                continue

        raise RuntimeError(
            "[VAD 错误] 未找到可用音频输入设备。\n"
            "  可能原因：\n"
            "  1. 未连接麦克风（请插入 USB/耳机麦克风）\n"
            "  2. 麦克风被其他程序独占（请关闭 Teams/Zoom/腾讯会议等）\n"
            "  3. 系统隐私设置未允许应用访问麦克风\n"
            "  4. Anaconda 环境 PyAudio 与系统音频驱动不兼容（尝试 pip uninstall pyaudio 后 pip install pipwin && pipwin install pyaudio）"
        )

    def _open_stream(self) -> pyaudio.Stream:
        """打开麦克风音频流，自动检测可用设备"""
        device_index = self._get_input_device_index()
        return self._pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=FRAME_SIZE,
        )

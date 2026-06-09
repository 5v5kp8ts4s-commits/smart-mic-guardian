"""
system_audio.py - 系统输出音频捕获模块 (v5.3)

功能：
- 使用 WASAPI Loopback 捕获系统扬声器/耳机输出音频
- 用于监听会议中其他人的语音（主持人提问、他人发言等）
- 需以管理员权限运行（WASAPI Loopback 要求）

Windows 平台专用。
"""

import sys
import time
import threading
import collections
from typing import Optional, List

if sys.platform != "win32":
    print("[SystemAudio] 本模块仅支持 Windows 平台。")

# sounddevice 可选导入，未安装时不影响主程序
try:
    import sounddevice as sd
except ImportError:
    sd = None

from config import SAMPLE_RATE as _DEFAULT_SAMPLE_RATE, FRAME_DURATION


class SystemAudioCapturer:
    """
    系统输出音频捕获器（WASAPI Loopback）

    持续捕获系统扬声器/耳机输出的音频，
    供 ASR 模块识别会议中其他人的语音内容。
    """

    def __init__(self, sample_rate: int = _DEFAULT_SAMPLE_RATE,
                 buffer_secs: float = 5.0):
        self.sample_rate = sample_rate
        self.buffer_secs = buffer_secs
        self.frame_size = int(sample_rate * FRAME_DURATION)
        self._max_buffer_frames = int(buffer_secs / FRAME_DURATION)

        # 音频环形缓冲区（线程安全）
        self._buffer: collections.deque = collections.deque(
            maxlen=self._max_buffer_frames
        )
        self._buffer_lock = threading.Lock()

        # 运行状态
        self._running = False
        self._stream = None
        self._device_id: Optional[int] = None
        self._error_count = 0
        self._total_frames = 0

        # 查找 Loopback 设备
        self._find_loopback_device()

    # ------------------------------------------------------------------
    # 设备查找（强制 WASAPI）
    # ------------------------------------------------------------------
    def _find_loopback_device(self) -> None:
        """强制查找 WASAPI 主机 API 下的 Loopback 或默认输出设备"""
        if sd is None:
            print("[SystemAudio] sounddevice 未安装，系统音频捕获不可用")
            print("[SystemAudio] 安装命令: pip install sounddevice")
            return

        try:
            # 第一步：枚举所有 Host API，找到 WASAPI
            hostapis = sd.query_hostapis()
            wasapi_idx = None
            for i, api in enumerate(hostapis):
                api_name = api.get("name", "").lower()
                if "wasapi" in api_name:
                    wasapi_idx = i
                    print(f"[SystemAudio] 找到 WASAPI 主机 API (index={i}): {api['name']}")
                    break

            if wasapi_idx is None:
                print("[SystemAudio] 未找到 WASAPI 主机 API，系统音频捕获不可用")
                print("[SystemAudio] 可能原因: sounddevice 依赖的 PortAudio 未启用 WASAPI 支持")
                print("[SystemAudio] 解决方法: 卸载重装 sounddevice → pip uninstall sounddevice -y && pip install sounddevice --no-binary :all:")
                self._device_id = None
                return

            # 第二步：在 WASAPI 设备中查找 Loopback
            devices = sd.query_devices()
            wasapi_devices = [d for d in devices if d.get("hostapi") == wasapi_idx]
            print(f"[SystemAudio] WASAPI 设备列表 ({len(wasapi_devices)} 个):")
            for d in wasapi_devices:
                print(f"  [{d['index']}] {d['name']} (in={d['max_input_channels']}, out={d['max_output_channels']})")

            # 优先找明确标记为 Loopback 的设备
            for d in wasapi_devices:
                name = d.get("name", "").lower()
                if "loopback" in name or "立体声混音" in name or "stereo mix" in name:
                    self._device_id = d["index"]
                    print(f"[SystemAudio] 选中 WASAPI Loopback 设备: [{self._device_id}] {d['name']}")
                    return

            # 没有 Loopback 时，找 WASAPI 下的默认输出设备
            default_out = None
            try:
                default_out = sd.query_devices(kind="output")
            except Exception:
                pass

            if default_out and default_out.get("hostapi") == wasapi_idx:
                self._device_id = default_out["index"]
                print(f"[SystemAudio] 未找到 Loopback，使用 WASAPI 默认输出设备: [{self._device_id}] {default_out['name']}")
                print("[SystemAudio] 提示: 以管理员权限运行后，WASAPI 输出设备的 Loopback 功能会自动启用")
                return

            # 连默认输出设备都不在 WASAPI 下 → 随便选一个 WASAPI 输出设备
            for d in wasapi_devices:
                if d["max_output_channels"] > 0:
                    self._device_id = d["index"]
                    print(f"[SystemAudio] 使用 WASAPI 输出设备: [{self._device_id}] {d['name']}")
                    return

            print("[SystemAudio] WASAPI 下无可用输出设备，系统音频捕获不可用")
            self._device_id = None

        except Exception as e:
            print(f"[SystemAudio] 设备查找失败: {e}")
            self._device_id = None

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """启动系统音频捕获（强制使用 WASAPI Loopback）"""
        if self._running:
            return True
        if sd is None:
            print("[SystemAudio] sounddevice 未安装，无法启动")
            return False
        if self._device_id is None:
            print("[SystemAudio] 未找到可用 WASAPI 音频设备，无法启动")
            print("[SystemAudio] 可能原因: 未以管理员权限运行 / PortAudio 未启用 WASAPI 支持")
            return False

        self._running = True
        self._error_count = 0
        self._total_frames = 0

        try:
            # 获取当前设备信息
            dev_info = sd.query_devices(self._device_id)
            hostapis = sd.query_hostapis()
            hostapi_name = hostapis[dev_info.get("hostapi", 0)].get("name", "Unknown")
            input_ch = dev_info.get("max_input_channels", 0)
            output_ch = dev_info.get("max_output_channels", 0)
            print(f"[SystemAudio] 启动设备: [{self._device_id}] {dev_info['name']} "
                  f"(Host API: {hostapi_name}, in={input_ch}, out={output_ch})")

            # 判断设备类型，选择正确的启动方式
            if input_ch > 0:
                # 设备有输入通道 → 直接作为输入设备使用
                print("[SystemAudio] 设备有输入通道，直接启动 RawInputStream")
                self._stream = sd.RawInputStream(
                    samplerate=self.sample_rate,
                    channels=min(1, input_ch),
                    dtype="int16",
                    blocksize=self.frame_size,
                    device=self._device_id,
                    callback=self._audio_callback,
                )
            else:
                # 设备只有输出通道 → 使用 WASAPI Loopback 模式
                print("[SystemAudio] 设备为输出设备，启用 WASAPI Loopback 模式")
                try:
                    extra = sd.WasapiSettings(loopback=True)
                except AttributeError:
                    # 旧版 sounddevice 不支持 WasapiSettings
                    print("[SystemAudio] 当前 sounddevice 版本不支持 WasapiSettings")
                    print("[SystemAudio] 请升级: pip install --upgrade sounddevice")
                    self._running = False
                    return False

                self._stream = sd.RawInputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=self.frame_size,
                    device=self._device_id,
                    callback=self._audio_callback,
                    extra_settings=extra,
                )

            self._stream.start()
            print(f"[SystemAudio] 系统音频捕获已启动 (device={self._device_id}, "
                  f"sr={self.sample_rate}Hz, hostapi={hostapi_name})")
            return True

        except Exception as e:
            self._running = False
            self._stream = None
            err_str = str(e)
            print(f"[SystemAudio] 启动失败: {e}")

            if "Access is denied" in err_str or "-9993" in err_str:
                print("[SystemAudio] 错误原因: 未以管理员权限运行")
                print("[SystemAudio] 解决方法: 右键点击 PowerShell / CMD → '以管理员身份运行'")
            elif "loopback" in err_str.lower():
                print("[SystemAudio] 错误原因: 当前设备不支持 Loopback 模式")
                print("[SystemAudio] 解决方法: 升级 sounddevice 或检查声卡驱动")
            elif "wasapi" in err_str.lower():
                print("[SystemAudio] 错误原因: WASAPI 初始化失败")
                print("[SystemAudio] 解决方法: pip uninstall sounddevice -y && pip install --upgrade sounddevice")
            return False

    def stop(self) -> None:
        """停止系统音频捕获"""
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._buffer_lock:
            self._buffer.clear()
        print(f"[SystemAudio] 系统音频捕获已停止（共采集 {self._total_frames} 帧）")

    # ------------------------------------------------------------------
    # 音频回调
    # ------------------------------------------------------------------
    def _audio_callback(self, indata, frames, time_info, status):
        """sounddevice 音频回调（在独立线程中运行）"""
        if status:
            self._error_count += 1
            if self._error_count < 5:
                print(f"[SystemAudio] 音频回调状态: {status}")

        if not self._running:
            return

        # 将 float32 转为 int16 bytes
        import numpy as np
        pcm_bytes = (indata[:, 0] * 32767).astype(np.int16).tobytes()

        with self._buffer_lock:
            self._buffer.append(pcm_bytes)
        self._total_frames += 1

    # ------------------------------------------------------------------
    # 数据获取
    # ------------------------------------------------------------------
    def get_recent_pcm(self, duration_secs: float = 3.0) -> bytes:
        """
        获取最近指定时长的系统音频 PCM 数据。
        :param duration_secs: 截取时长（秒）
        :return: 拼接后的 PCM 字节流
        """
        frame_count = int(duration_secs / FRAME_DURATION)
        with self._buffer_lock:
            frames = list(self._buffer)[-frame_count:]
        return b"".join(frames)

    def get_recent_energy(self, frame_count: int = 10) -> List[float]:
        """
        获取最近若干帧的能量值。
        :param frame_count: 帧数
        :return: 能量列表
        """
        import struct
        import math

        with self._buffer_lock:
            frames = list(self._buffer)[-frame_count:]

        energies = []
        for pcm in frames:
            if len(pcm) < 4:
                energies.append(0.0)
                continue
            samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
            mean_sq = sum(s * s for s in samples) / len(samples)
            energies.append(math.sqrt(mean_sq))
        return energies

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def available(self) -> bool:
        """系统音频捕获是否可用"""
        return sd is not None and self._device_id is not None

    @property
    def device_name(self) -> str:
        """当前使用的设备名称"""
        if sd is None or self._device_id is None:
            return "不可用"
        try:
            dev = sd.query_devices(self._device_id)
            return dev.get("name", "未知")
        except Exception:
            return "未知"

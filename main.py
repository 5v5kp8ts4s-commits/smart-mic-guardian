"""
智能防误关麦助手 (Smart Mic Guardian)
适用于 Windows 10 / Windows 11

功能说明：
- 实时读取系统麦克风静音状态
- 提供静音 / 开麦的基础控制接口
- VAD 短时能量算法人声检测（PyAudio）
- 连续静音 15s + 15s 延时等待后弹窗提醒
- 手动关麦即时检测并弹窗提醒
- 通过桌面通知提示当前麦克风状态变化

依赖库：
- pycaw: Windows Core Audio 控制
- pyaudio: 实时音频流式采集（VAD 算法）
- speech_recognition: 语音活动检测（备用）
- plyer: 系统桌面通知
"""

import sys
import time
import threading
from typing import Optional

# 检查是否在 Windows 平台运行
if sys.platform != "win32":
    print("[错误] 本脚本仅支持 Windows 平台。")
    sys.exit(1)

# Windows 音频控制库
try:
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, IAudioMeterInformation
    from comtypes import CLSCTX_ALL
except ImportError as e:
    print(f"[错误] 缺少依赖库 pycaw/comtypes: {e}")
    print("[提示] 请执行: pip install -r requirements.txt")
    sys.exit(1)

# 桌面通知库
try:
    from plyer import notification
except ImportError as e:
    print(f"[警告] 缺少通知库 plyer: {e}")
    notification = None

# 语音识别库（备用）
try:
    import speech_recognition as sr
except ImportError as e:
    print(f"[警告] 缺少语音识别库 SpeechRecognition: {e}")
    sr = None

# 防误关麦守护主控模块（VAD + 延时等待 + 弹窗 + ASR）
try:
    from guardian import MicGuardian
except ImportError as e:
    print(f"[警告] 无法加载 guardian 模块: {e}")
    MicGuardian = None



class MicController:
    """麦克风控制器：封装 Windows Core Audio 接口操作"""

    def __init__(self):
        self._volume = None
        self._meter = None
        self._init_audio()

    def _init_audio(self) -> None:
        """初始化音频端点，获取默认麦克风音量控制接口与实时电平计量接口"""
        try:
            devices = AudioUtilities.GetMicrophone()
            interface = devices.Activate(
                IAudioEndpointVolume._iid_, CLSCTX_ALL, None
            )
            self._volume = interface.QueryInterface(IAudioEndpointVolume)

            # 尝试获取实时音频电平计量接口（用于读取输入峰值）
            try:
                meter_interface = devices.Activate(
                    IAudioMeterInformation._iid_, CLSCTX_ALL, None
                )
                self._meter = meter_interface.QueryInterface(IAudioMeterInformation)
            except Exception:
                self._meter = None
        except Exception as e:
            print(f"[错误] 初始化音频接口失败: {e}")
            raise

    def is_muted(self) -> bool:
        """
        获取当前麦克风是否处于静音状态
        :return: True 表示已静音，False 表示未静音（开麦）
        """
        if self._volume is None:
            return False
        return self._volume.GetMute()

    def mute(self) -> bool:
        """
        将麦克风设为静音
        :return: 操作是否成功
        """
        try:
            self._volume.SetMute(1, None)
            print("[状态] 麦克风已静音")
            self._notify("麦克风已静音", "系统麦克风已被静音")
            return True
        except Exception as e:
            print(f"[错误] 静音操作失败: {e}")
            return False

    def unmute(self) -> bool:
        """
        解除麦克风静音（开麦）
        :return: 操作是否成功
        """
        try:
            self._volume.SetMute(0, None)
            print("[状态] 麦克风已开启")
            self._notify("麦克风已开启", "系统麦克风已恢复收音")
            return True
        except Exception as e:
            print(f"[错误] 开麦操作失败: {e}")
            return False

    def toggle(self) -> bool:
        """
        切换麦克风静音/开麦状态
        :return: 切换后是否处于静音状态
        """
        if self.is_muted():
            self.unmute()
            return False
        else:
            self.mute()
            return True

    def get_volume_percent(self) -> float:
        """
        获取麦克风当前音量百分比（0.0 ~ 100.0）
        :return: 音量百分比
        """
        if self._volume is None:
            return 0.0
        # pycaw 返回的是 0.0 ~ 1.0 的标量值
        scalar = self._volume.GetMasterVolumeLevelScalar()
        return round(scalar * 100.0, 2)

    def get_peak_level(self) -> float:
        """
        获取麦克风实时输入峰值电平（0.0 ~ 1.0）
        返回值越大表示当前输入音量越高，可用于后续人声检测阈值判断
        :return: 实时峰值电平，-1.0 表示计量接口不可用
        """
        if self._meter is None:
            return -1.0
        try:
            return self._meter.GetPeakValue()
        except Exception:
            return -1.0

    def set_volume_percent(self, percent: float) -> bool:
        """
        设置麦克风音量百分比
        :param percent: 0.0 ~ 100.0
        :return: 操作是否成功
        """
        try:
            clamped = max(0.0, min(100.0, percent))
            self._volume.SetMasterVolumeLevelScalar(clamped / 100.0, None)
            print(f"[状态] 麦克风音量已设置为 {clamped}%")
            return True
        except Exception as e:
            print(f"[错误] 设置音量失败: {e}")
            return False

    def _notify(self, title: str, message: str) -> None:
        """发送桌面通知（如果可用）"""
        if notification is None:
            return
        try:
            notification.notify(
                title=title,
                message=message,
                app_name="智能防误关麦助手",
                timeout=3,
            )
        except Exception as e:
            print(f"[警告] 通知发送失败: {e}")


class SpeechGuardian:
    """
    语音守护器：检测用户是否正在说话，用于防误关麦逻辑
    """

    def __init__(self, mic_controller: MicController):
        self.mic = mic_controller
        self._recognizer: Optional[sr.Recognizer] = None
        self._running = False
        self._guard_thread: Optional[threading.Thread] = None

        if sr is not None:
            self._recognizer = sr.Recognizer()
            # 调整能量阈值，降低误触发
            self._recognizer.energy_threshold = 400
            self._recognizer.dynamic_energy_threshold = True

    def is_speaking(self, timeout: int = 1) -> bool:
        """
        检测用户是否正在说话（通过麦克风读取环境音频能量）
        :param timeout: 监听超时秒数
        :return: True 表示检测到语音活动
        """
        if self._recognizer is None or sr is None:
            return False

        try:
            with sr.Microphone() as source:
                # 校准环境噪音
                self._recognizer.adjust_for_ambient_noise(source, duration=0.5)
                # 非阻塞方式读取音频
                audio = self._recognizer.listen(source, timeout=timeout, phrase_time_limit=1)
                # 如果能获取到音频数据，说明有语音活动
                return audio is not None and len(audio.frame_data) > 0
        except sr.WaitTimeoutError:
            return False
        except Exception as e:
            print(f"[警告] 语音检测异常: {e}")
            return False

    def start_guard(self, check_interval: float = 2.0) -> None:
        """
        启动防误关麦守护线程：当检测到用户正在说话而麦克风被静音时，自动提醒或自动开麦
        :param check_interval: 检测间隔秒数
        """
        if self._running:
            print("[提示] 守护线程已在运行")
            return

        self._running = True
        self._guard_thread = threading.Thread(
            target=self._guard_loop,
            args=(check_interval,),
            daemon=True,
        )
        self._guard_thread.start()
        print("[启动] 防误关麦守护线程已启动")

    def stop_guard(self) -> None:
        """停止守护线程"""
        self._running = False
        if self._guard_thread:
            self._guard_thread.join(timeout=2.0)
        print("[停止] 防误关麦守护线程已停止")

    def _guard_loop(self, interval: float) -> None:
        """守护线程主循环"""
        while self._running:
            try:
                # 如果麦克风当前处于静音状态
                if self.mic.is_muted():
                    # 检测用户是否正在说话
                    if self.is_speaking(timeout=1):
                        print("[提醒] 检测到您正在说话，但麦克风处于静音状态！")
                        print("[建议] 如需自动开麦，请调用 mic.unmute()")
                        # 可选：取消下面注释实现自动开麦
                        # self.mic.unmute()
                time.sleep(interval)
            except Exception as e:
                print(f"[错误] 守护循环异常: {e}")
                time.sleep(interval)


def print_help() -> None:
    """打印使用说明"""
    print("\n" + "=" * 56)
    print("  智能防误关麦助手 - 交互命令说明")
    print("=" * 56)
    print("  --- 麦克风基础控制 ---")
    print("  s / status   - 查看当前麦克风静音状态和音量")
    print("  m / mute     - 将麦克风设为静音")
    print("  u / unmute   - 解除静音（开麦）")
    print("  t / toggle   - 切换静音/开麦状态")
    print("  v <数值>     - 设置麦克风音量，如: v 80 (0-100)")
    print("")
    print("  --- 音频检测 ---")
    print("  l / listen   - 实时监听麦克风音量（按 Enter 停止）")
    print("                用途：检查麦克风是否正常工作、查看环境底噪大小")
    print("")
    print("  --- 防误关麦守护（核心功能）---")
    print("  g / guard    - 启动/停止 VAD 守护线程")
    print("                工作流程：")
    print("                ① 3s 底噪校准 → ② 实时检测人声 → ③ 静音计时")
    print("                ④ 延时缓冲 15s → ⑤ 弹窗提醒")
    print("                用途：开会时开着麦但忘记关，长时间静默后提醒")
    print("")
    print("  --- 高级功能（需守护线程运行中）---")
    print("  a / asr      - 手动触发语音识别（截取最近 3 秒音频转文字）")
    print("  p / predict  - 查看发言预测 / 系统音频 / 话题状态")
    print("                用途：了解预发声检测、系统音频捕获的会议状态")
    print("  mm / meeting - 查看系统音频捕获状态（会议中其他人的语音）")
    print("                用途：了解是否检测到主持人提问、是否轮到您发言")
    print("")
    print("  --- 其他 ---")
    print("  h / help     - 显示帮助信息")
    print("  q / quit     - 退出程序")
    print("=" * 56 + "\n")


def main() -> None:
    """主入口函数"""
    print("\n╔══════════════════════════════════════════╗")
    print("║      智能防误关麦助手 (Smart Mic Guardian)  ║")
    print("║           支持 Windows 10 / 11            ║")
    print("╚══════════════════════════════════════════╝\n")

    # 初始化控制器
    try:
        mic = MicController()
    except Exception:
        sys.exit(1)

    # 初始化 VAD 防误关麦守护器
    vad_guardian: Optional[MicGuardian] = None
    if MicGuardian is not None:
        try:
            vad_guardian = MicGuardian()
        except Exception as e:
            print(f"[警告] VAD 守护器初始化失败: {e}")

    # 显示初始状态
    status_text = "静音" if mic.is_muted() else "开麦"
    print(f"[初始化] 麦克风当前状态: {status_text}")
    print(f"[初始化] 麦克风当前音量: {mic.get_volume_percent()}%")
    if vad_guardian is not None:
        from asr import OfflineASR as _ASR
        _asr_check = _ASR()
        asr_status = "就绪" if _asr_check.available else "不可用（未安装vosk或缺少模型，仍可弹窗）"
        print(f"[初始化] VAD 守护器就绪（输入 g 启动）")
        print(f"[初始化] 离线 ASR 状态: {asr_status}")
    print_help()

    # 交互式命令循环
    while True:
        try:
            cmd = input("请输入命令 > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n[退出] 接收到中断信号，程序即将退出...")
            break

        if not cmd:
            continue

        parts = cmd.split()
        action = parts[0]

        if action in ("q", "quit", "exit"):
            print("[退出] 程序已退出")
            break

        elif action in ("h", "help", "?"):
            print_help()

        elif action in ("s", "status"):
            muted = mic.is_muted()
            vol = mic.get_volume_percent()
            state = "🔇 静音" if muted else "🎤 开麦"
            print(f"[状态] {state} | 音量: {vol}%")

        elif action in ("m", "mute"):
            mic.mute()

        elif action in ("u", "unmute"):
            mic.unmute()

        elif action in ("t", "toggle"):
            mic.toggle()

        elif action in ("v", "vol", "volume"):
            if len(parts) >= 2:
                try:
                    val = float(parts[1])
                    mic.set_volume_percent(val)
                except ValueError:
                    print("[错误] 音量值必须是数字，例如: v 80")
            else:
                print("[提示] 用法: v <0-100>")

        elif action in ("l", "listen"):
            """
            使用 PyAudio callback 模式实时读取麦克风，计算 RMS 峰值。
            callback 模式非阻塞，避免 stream.read() 卡住导致无法停止的问题。
            同时检测设备是否可用，不可用则给出友好提示。
            """
            try:
                import pyaudio
                import struct
                import math
            except ImportError:
                print("[错误] 缺少 PyAudio 库，无法启动实时监听")
                print("[提示] pip install PyAudio")
                continue

            try:
                pa = pyaudio.PyAudio()
                info = pa.get_default_input_device_info()
                print(f"[监听] 检测到音频输入设备: {info['name']}")
            except Exception as e:
                print(f"[错误] 无法获取默认音频输入设备: {e}")
                print("[提示] 请检查麦克风是否已连接并被系统识别")
                continue

            # 共享数据：callback 线程写入，主线程读取打印
            _latest_rms = [0.0]
            _latest_normalized = [0.0]
            _frame_count = [0]
            _stop_flag = threading.Event()

            def _audio_callback(in_data, frame_count, time_info, status_flags):
                """PyAudio callback：在非阻塞线程中接收音频帧并计算 RMS"""
                count = frame_count
                data_len = len(in_data)
                expected = count * 2  # 16-bit = 2 bytes per sample
                if data_len < expected:
                    # 数据不足，填充 0
                    in_data = in_data + b'\x00' * (expected - data_len)
                fmt = "<" + "h" * count
                try:
                    samples = struct.unpack(fmt, in_data[:expected])
                except struct.error:
                    return (in_data, pyaudio.paContinue)
                # 计算 RMS
                sum_sq = sum(s * s for s in samples)
                rms = math.sqrt(sum_sq / count) if count > 0 else 0.0
                _latest_rms[0] = rms
                _latest_normalized[0] = min(1.0, rms / 32767.0)
                _frame_count[0] += 1
                return (in_data, pyaudio.paContinue)

            try:
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    input=True,
                    frames_per_buffer=480,  # 30ms，与 VAD 帧长一致
                    stream_callback=_audio_callback,
                )
            except Exception as e:
                print(f"[错误] 无法打开麦克风流: {e}")
                print("[提示] 麦克风可能被其他程序占用")
                pa.terminate()
                continue

            print("[监听] 启动实时音量监听，按 Enter 停止...")
            _stop_flag.clear()
            _frame_count[0] = 0

            def _print_loop():
                """每 100ms 刷新一次显示"""
                while not _stop_flag.is_set():
                    rms = _latest_rms[0]
                    normalized = _latest_normalized[0]
                    bar_len = max(0, min(40, int(normalized * 40)))
                    bar = "█" * bar_len + "░" * (40 - bar_len)
                    line = f"\r[监听] 峰值: {rms:7.1f} | {bar} | {normalized * 100:5.1f}%"
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    time.sleep(0.1)

            print_t = threading.Thread(target=_print_loop, daemon=True)
            print_t.start()

            try:
                input()  # 主线程等待用户按 Enter
            except KeyboardInterrupt:
                pass
            finally:
                _stop_flag.set()
                print_t.join(timeout=1.5)
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
                pa.terminate()
                sys.stdout.write("\r" + " " * 80 + "\r")
                sys.stdout.flush()
                total_frames = _frame_count[0]
                if total_frames == 0:
                    print("[监听] 已停止（警告：未收到任何音频数据，请检查麦克风是否正常工作）")
                else:
                    print(f"[监听] 已停止（共采集 {total_frames} 帧）")

        elif action in ("g", "guard"):
            if vad_guardian is None:
                print("[错误] VAD 守护模块不可用，无法启动")
                continue
            if vad_guardian.is_running:
                vad_guardian.stop()
            else:
                # 在新线程中启动（底噪校准会阻塞几秒，避免卡住主循环）
                t = threading.Thread(target=vad_guardian.start, daemon=True)
                t.start()

        elif action in ("a", "asr"):
            if vad_guardian is None or not vad_guardian.is_running:
                print("[错误] 守护线程未运行，无法截取音频。请先输入 g 启动")
                continue
            print("[ASR] 手动触发近 3 秒音频识别...")

        elif action in ("p", "predict"):
            if vad_guardian is None or not vad_guardian.is_running:
                print("[错误] 守护线程未运行。请先输入 g 启动")
                continue
            ps = vad_guardian.pre_speech_status
            bs = vad_guardian.block_status
            sa = vad_guardian.system_audio_status
            ts = vad_guardian.topic_status
            cs = vad_guardian.chitchat_status
            print(f"\n[发言预测] 当前状态:")
            print(f"  预发声音频: {ps['pre_voice']}")
            print(f"  综合判定:   {'建议清零计时器' if ps['should_reset'] else '未命中'}")
            print(f"  详情:       {ps['status_str']}")
            print(f"\n[系统音频] 双通道监听状态:")
            print(f"  可用:       {'是' if sa['available'] else '否'}")
            if sa['available']:
                print(f"  捕获设备:   {sa['device_name']}")
                print(f"  会议状态:   {sa['state']}")
                print(f"  轮到您发言: {'是' if sa['is_my_turn'] else '否'}")
                print(f"  其他人在说: {'是' if sa['is_listening'] else '否'}")
                print(f"  系统安静:   {'是' if sa['is_idle'] else '否'}")
                if sa['is_my_turn']:
                    print(f"  发言剩余:   {sa['my_turn_remaining']:.0f}s")
                print(f"  最近识别:   {sa['last_text'][:40] if sa['last_text'] else '（无）'}")
            else:
                print(f"  提示:       以管理员权限运行可启用双通道监听")
            print(f"\n[话题分类] 当前状态:")
            print(f"  会议发言:   {ts['meeting_count']} / 闲聊: {ts['chitchat_count']} / 中性: {ts['neutral_count']}")
            print(f"  闲聊计时:   {cs['timer']:.1f}s / {cs['alert_threshold']}s (阈值)")
            print(f"\n[关麦屏蔽] 当前状态:")
            print(f"  主动下线:   {bs['stop_remind']}")
            print(f"  屏蔽弹窗:   {bs['block_alert']}")
            print(f"  关麦标记:   {bs['mic_off_flag']}")
            print(f"  此前人声:   {bs['had_voice_before_off']}")
            print()

        elif action in ("mm", "meeting"):
            if vad_guardian is None or not vad_guardian.is_running:
                print("[错误] 守护线程未运行。请先输入 g 启动")
                continue
            sa = vad_guardian.system_audio_status
            print(f"\n[系统音频] 会议状态详情:")
            print(f"  系统音频:   {'可用' if sa['available'] else '不可用'}")
            if sa['available']:
                print(f"  捕获设备:   {sa['device_name']}")
                print(f"  当前状态:   {sa['state']}")
                print(f"  轮到您发言: {'是' if sa['is_my_turn'] else '否'}")
                print(f"  其他人在说: {'是' if sa['is_listening'] else '否'}")
                print(f"  系统安静:   {'是' if sa['is_idle'] else '否'}")
                print(f"  剩余发言:   {sa['my_turn_remaining']:.0f}s")
                print(f"  最近识别:   {sa['last_text'][:50] if sa['last_text'] else '（无）'}")
                print(f"  命中关键词: {sa['last_matched_keyword'] if sa['last_matched_keyword'] else '（无）'}")
                print(f"  ASR 可用:   {'是' if sa['asr_available'] else '否'}")
            else:
                print(f"  设备名称:   {sa['device_name']}")
                print(f"  提示:       以管理员权限运行 + 安装 sounddevice 可启用")
            print()

        else:
            print(f"[未知命令] '{cmd}'，输入 h 查看帮助")

    # 清理资源
    if vad_guardian is not None and vad_guardian.is_running:
        vad_guardian.stop()
    print("[结束] 感谢使用智能防误关麦助手！")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能防误关麦助手 (Smart Mic Guardian)
适用于 Windows 10 / Windows 11

功能说明：
- 实时读取系统麦克风静音状态
- 提供静音 / 开麦的基础控制接口
- 通过语音识别检测用户是否正在说话，防止误关麦克风
- 通过桌面通知提示当前麦克风状态变化

依赖库：
- pycaw: Windows Core Audio 控制
- speech_recognition: 语音活动检测
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

# 语音识别库
try:
    import speech_recognition as sr
except ImportError as e:
    print(f"[警告] 缺少语音识别库 SpeechRecognition: {e}")
    sr = None


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
    print("\n" + "=" * 50)
    print("  智能防误关麦助手 - 交互命令说明")
    print("=" * 50)
    print("  s / status   - 查看当前麦克风状态")
    print("  m / mute     - 静音麦克风")
    print("  u / unmute   - 开启麦克风")
    print("  t / toggle   - 切换静音/开麦状态")
    print("  v <数值>     - 设置麦克风音量 (0-100)")
    print("  l / listen   - 实时监听麦克风音量峰值")
    print("  g / guard    - 启动/停止防误关麦守护")
    print("  h / help     - 显示帮助信息")
    print("  q / quit     - 退出程序")
    print("=" * 50 + "\n")


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

    # 初始化语音守护器
    guardian = SpeechGuardian(mic)

    # 显示初始状态
    status_text = "静音" if mic.is_muted() else "开麦"
    print(f"[初始化] 麦克风当前状态: {status_text}")
    print(f"[初始化] 麦克风当前音量: {mic.get_volume_percent()}%")
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
            peak = mic.get_peak_level()
            if peak < 0:
                print("[错误] 实时音量计量接口不可用，无法监听")
                continue
            print("[监听] 启动实时音量监听，按 Ctrl+C 停止...")
            try:
                while True:
                    peak = mic.get_peak_level()
                    bar = "█" * max(0, int(peak * 30))
                    line = f"\r[监听] 峰值: {peak:.4f} {bar:<30}"
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    # Windows 下将单次 sleep 拆成多段短 sleep，确保 Ctrl+C 能及时中断
                    for _ in range(10):
                        time.sleep(0.02)
            except KeyboardInterrupt:
                pass
            # 清除当前行内容，避免残留字符
            sys.stdout.write("\r" + " " * 60 + "\r")
            sys.stdout.flush()
            print("[监听] 已停止")

        elif action in ("g", "guard"):
            if guardian._running:
                guardian.stop_guard()
            else:
                guardian.start_guard()

        else:
            print(f"[未知命令] '{cmd}'，输入 h 查看帮助")

    # 清理资源
    guardian.stop_guard()
    print("[结束] 感谢使用智能防误关麦助手！")


if __name__ == "__main__":
    main()

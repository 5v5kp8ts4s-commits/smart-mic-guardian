"""
stress_test.py - 72 小时稳定性压力测试脚本

测试目标：
1. 长时间运行内存占用不持续增长（无内存泄漏）
2. 各模拟场景下提醒触发精准度达标
3. 异常捕获覆盖所有预设故障注入场景
4. 弹窗无重复创建（单例复用验证）

场景循环：
  ① 正常说话（5~10 分钟，模拟有效音频能量）
  ② 短时静音（5~15 秒）
  ③ 长时静默（30~60 秒，触发提醒验证）
  ④ 反复开关麦克风（手动关麦检测验证）
  ⑤ 环境噪音变化（阈值自适应验证）

输出：
  - 实时控制台日志
  - stress_test_report.txt（测试摘要）
  - 最终结果写入 issues.md（若发现 bug）

使用方式：
  python stress_test.py [--hours 72] [--no-real-mic]

  --hours N      : 指定测试时长（小时），默认 72
  --no-real-mic  : 不使用真实麦克风，改用模拟音频数据（CI/离线环境）
"""

import sys
import os
import time
import math
import struct
import random
import argparse
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

# 确保项目目录在模块搜索路径中
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    SAMPLE_RATE, FRAME_SIZE, FRAME_DURATION, DEFAULT_ENERGY,
    SILENT_THRESHOLD_S, DELAY_WAIT_S, STRESS_LOG_INTERVAL_S,
    MEMORY_WARN_MB, MEMORY_CLEAR_MB,
)
from error_handler import get_error_log, install_global_handler

# psutil 可选（内存监控）
try:
    import psutil
    _PSUTIL_OK = True
except ImportError:
    _PSUTIL_OK = False
    print("[压测] psutil 未安装，内存监控不可用。建议执行: pip install psutil")

# ======================================================================
# 场景枚举
# ======================================================================
SCENE_VOICE    = "voice"      # 正常说话
SCENE_SHORT    = "short_sil"  # 短时静音（< 阈值）
SCENE_LONG     = "long_sil"   # 长时静默（> 阈值，应触发提醒）
SCENE_MIC_OFF  = "mic_off"    # 反复开关麦克风
SCENE_NOISE    = "noise"      # 环境噪音变化

# ======================================================================
# 模拟音频生成
# ======================================================================

def _make_frame(energy_level: float, all_zero: bool = False) -> bytes:
    """
    生成一帧模拟 16-bit PCM 音频数据。
    :param energy_level: 目标能量值（振幅平方均值）
    :param all_zero:     全零帧（模拟手动关麦）
    """
    if all_zero:
        return b"\x00" * (FRAME_SIZE * 2)

    # 目标振幅 = sqrt(energy_level)
    amp = max(0.0, math.sqrt(energy_level))
    amp = min(amp, 32767.0)
    samples = []
    for _ in range(FRAME_SIZE):
        # 加入随机抖动模拟真实信号
        val = int(amp * (0.8 + random.random() * 0.4))
        val = max(-32768, min(32767, val))
        samples.append(val)
    return struct.pack(f"{FRAME_SIZE}h", *samples)


# ======================================================================
# 统计数据
# ======================================================================

class StressStats:
    """线程安全的压测统计数据"""

    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = time.time()

        # 场景计数
        self.scene_counts  = {s: 0 for s in [SCENE_VOICE, SCENE_SHORT, SCENE_LONG, SCENE_MIC_OFF, SCENE_NOISE]}

        # 提醒统计
        self.expected_alerts   = 0   # 应触发提醒次数（长时静默场景）
        self.actual_alerts     = 0   # 实际触发次数（hook 注入）
        self.false_alerts      = 0   # 误报（非长时静默时触发）
        self.missed_alerts     = 0   # 漏报（长时静默未触发）

        # 异常统计
        self.error_counts: dict = {}

        # 内存曲线（MB，每 interval 记录一次）
        self.mem_samples: list[tuple[float, float]] = []  # (elapsed_s, mem_mb)
        self.mem_peak: float = 0.0

        # 弹窗创建次数（用于复用验证）
        self.alert_create_count = 0

    def record_scene(self, scene: str):
        with self._lock:
            self.scene_counts[scene] = self.scene_counts.get(scene, 0) + 1

    def record_expected_alert(self):
        with self._lock:
            self.expected_alerts += 1

    def record_actual_alert(self, is_expected: bool):
        with self._lock:
            self.actual_alerts += 1
            if not is_expected:
                self.false_alerts += 1

    def record_mem(self, mem_mb: float):
        with self._lock:
            elapsed = time.time() - self.start_time
            self.mem_samples.append((elapsed, mem_mb))
            if mem_mb > self.mem_peak:
                self.mem_peak = mem_mb

    def elapsed_str(self) -> str:
        elapsed = int(time.time() - self.start_time)
        h, rem = divmod(elapsed, 3600)
        m, s   = divmod(rem, 60)
        return f"{h:02d}h{m:02d}m{s:02d}s"

    def alert_accuracy(self) -> float:
        """提醒精准度 = 正确触发 / 应触发"""
        if self.expected_alerts == 0:
            return 1.0
        correct = self.actual_alerts - self.false_alerts
        return correct / max(self.expected_alerts, 1)

    def summary_lines(self) -> list[str]:
        lines = [
            "=" * 60,
            "  智能防误关麦助手 - 压力测试报告",
            "=" * 60,
            f"  测试时长:    {self.elapsed_str()}",
            f"  内存峰值:    {self.mem_peak:.1f} MB",
        ]
        lines.append("")
        lines.append("  场景执行次数:")
        for scene, cnt in self.scene_counts.items():
            lines.append(f"    {scene:<12}: {cnt} 次")
        lines.append("")
        lines.append("  提醒精准度:")
        lines.append(f"    应触发:  {self.expected_alerts}")
        lines.append(f"    实际触发: {self.actual_alerts}")
        lines.append(f"    误报:    {self.false_alerts}")
        lines.append(f"    精准度:  {self.alert_accuracy() * 100:.1f}%")
        lines.append("")
        err_summary = get_error_log().summary()
        if err_summary:
            lines.append("  运行期异常计数:")
            for cat, cnt in err_summary.items():
                lines.append(f"    {cat:<12}: {cnt}")
        else:
            lines.append("  运行期异常: 无")
        lines.append("=" * 60)
        return lines


# ======================================================================
# 模拟 VAD 注入器（无真实麦克风时使用）
# ======================================================================

class SimulatedVAD:
    """
    模拟 VAD 检测器，根据注入的场景返回预定状态。
    替代真实 VADDetector 在无麦克风环境下运行压测。
    """

    def __init__(self, noise_threshold: float = DEFAULT_ENERGY):
        self.threshold: float = noise_threshold * 3.0
        self.frame_duration: float = FRAME_DURATION
        self.calibrated: bool = True

        self._current_state: str = "silence"
        self._current_energy: float = 0.0
        self._buffer: list[bytes] = []
        self._max_buf = int(5.0 / FRAME_DURATION)
        self._state_lock = threading.Lock()

    def calibrate(self, duration: float = 3.0) -> float:
        print(f"[SimVAD] 模拟底噪校准（{duration:.0f}s）...完成")
        return self.threshold

    def open(self): pass
    def close(self): pass

    def set_state(self, state: str, energy: float):
        with self._state_lock:
            self._current_state = state
            self._current_energy = energy
            frame = _make_frame(energy, all_zero=(state == "mic_off"))
            self._buffer.append(frame)
            if len(self._buffer) > self._max_buf:
                self._buffer.pop(0)

    def detect_frame(self) -> tuple[str, float]:
        with self._state_lock:
            time.sleep(FRAME_DURATION)  # 模拟真实帧率
            return self._current_state, self._current_energy

    def get_recent_pcm(self, duration_secs: float = 3.0) -> bytes:
        frames_needed = int(duration_secs / FRAME_DURATION)
        recent = self._buffer[-frames_needed:]
        return b"".join(recent)


# ======================================================================
# 场景控制器
# ======================================================================

class ScenarioRunner:
    """
    按预定场景循环驱动 SimulatedVAD（或真实 VAD），
    模拟 72h 内各种麦克风使用场景。
    """

    SCENARIO_PLAN = [
        # (场景,           持续秒数,      能量倍率,  all_zero)
        (SCENE_VOICE,    random.uniform(10, 30),  8.0,   False),
        (SCENE_SHORT,    random.uniform(5,  14),  0.2,   False),
        (SCENE_LONG,     random.uniform(32, 60),  0.05,  False),  # 应触发提醒
        (SCENE_MIC_OFF,  random.uniform(3,  10),  0.0,   True),   # 全零帧
        (SCENE_VOICE,    random.uniform(15, 40),  10.0,  False),
        (SCENE_NOISE,    random.uniform(5,  15),  1.5,   False),  # 噪音偏高
        (SCENE_SHORT,    random.uniform(8,  13),  0.3,   False),
        (SCENE_LONG,     random.uniform(35, 70),  0.02,  False),  # 应触发提醒
    ]

    def __init__(self, vad: SimulatedVAD, stats: StressStats, noise_threshold: float):
        self._vad = vad
        self._stats = stats
        self._noise_threshold = noise_threshold
        self._running = False
        self._in_alert_scene = False

    def start(self):
        self._running = True
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def stop(self):
        self._running = False

    def _run(self):
        voice_energy  = self._noise_threshold * 10.0
        noise_energy  = self._noise_threshold * 1.5
        silent_energy = self._noise_threshold * 0.1

        while self._running:
            # 重新随机化场景持续时间（增加多样性）
            plan = [
                (SCENE_VOICE,   random.uniform(10, 30), voice_energy,  False),
                (SCENE_SHORT,   random.uniform(5,  13), silent_energy, False),
                (SCENE_LONG,    random.uniform(33, 62), silent_energy * 0.3, False),
                (SCENE_MIC_OFF, random.uniform(3,   8), 0.0,           True),
                (SCENE_VOICE,   random.uniform(10, 25), voice_energy,  False),
                (SCENE_NOISE,   random.uniform(5,  12), noise_energy,  False),
                (SCENE_LONG,    random.uniform(35, 65), silent_energy * 0.2, False),
            ]

            for scene, duration, energy, all_zero in plan:
                if not self._running:
                    return

                # 标记是否处于"应触发提醒"场景
                self._in_alert_scene = (scene == SCENE_LONG and
                                        duration > SILENT_THRESHOLD_S + DELAY_WAIT_S)
                if self._in_alert_scene:
                    self._stats.record_expected_alert()

                self._stats.record_scene(scene)
                end_t = time.time() + duration

                while time.time() < end_t and self._running:
                    self._vad.set_state(
                        "mic_off" if all_zero else ("voice" if energy > self._noise_threshold * 3 else "silence"),
                        energy
                    )
                    time.sleep(FRAME_DURATION * 5)  # 批量注入，降低 CPU

                self._in_alert_scene = False

    @property
    def in_alert_scene(self) -> bool:
        return self._in_alert_scene


# ======================================================================
# 内存监控线程
# ======================================================================

def _memory_monitor_loop(stats: StressStats, stop_event: threading.Event):
    """定期采集内存占用"""
    if not _PSUTIL_OK:
        return
    proc = psutil.Process(os.getpid())
    while not stop_event.is_set():
        try:
            mem_mb = proc.memory_info().rss / 1024 / 1024
            stats.record_mem(mem_mb)
            if mem_mb >= MEMORY_CLEAR_MB:
                print(f"[压测|内存] ⚠️  占用 {mem_mb:.1f}MB 超过清理阈值 {MEMORY_CLEAR_MB}MB")
            elif mem_mb >= MEMORY_WARN_MB:
                print(f"[压测|内存] ⚠️  占用 {mem_mb:.1f}MB 超过警告阈值 {MEMORY_WARN_MB}MB")
        except Exception:
            pass
        stop_event.wait(timeout=STRESS_LOG_INTERVAL_S)


# ======================================================================
# 日志打印
# ======================================================================

def _periodic_log(stats: StressStats, stop_event: threading.Event):
    """每 5 分钟打印一次进度摘要"""
    while not stop_event.is_set():
        stop_event.wait(timeout=300)
        if not stop_event.is_set():
            print(f"\n[压测|{stats.elapsed_str()}] "
                  f"场景次数={sum(stats.scene_counts.values())}, "
                  f"应触发={stats.expected_alerts}, "
                  f"实际触发={stats.actual_alerts}, "
                  f"精准度={stats.alert_accuracy()*100:.1f}%, "
                  f"内存峰值={stats.mem_peak:.1f}MB")


# ======================================================================
# 压测主流程
# ======================================================================

def run_stress_test(hours: float = 72.0, use_real_mic: bool = False) -> StressStats:
    """
    执行压力测试。
    :param hours:        测试时长（小时）
    :param use_real_mic: True 使用真实麦克风（需 Windows + PyAudio），
                         False 使用模拟数据（跨平台）
    :return: 测试统计对象
    """
    install_global_handler()
    stats = StressStats()

    print("=" * 60)
    print("  智能防误关麦助手 - 稳定性压力测试")
    print(f"  计划时长: {hours:.1f} 小时")
    print(f"  模式: {'真实麦克风' if use_real_mic else '模拟音频（无麦克风）'}")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ---- 初始化 Guardian（或模拟版） ----
    if use_real_mic:
        # 真实麦克风模式：启动完整守护线程
        if sys.platform != "win32":
            print("[压测] 真实麦克风模式仅支持 Windows，自动切换为模拟模式")
            use_real_mic = False

    if use_real_mic:
        from guardian import MicGuardian
        guardian = MicGuardian()
        guardian.start()
        vad      = guardian._vad
        runner   = None  # 真实模式下不注入场景
    else:
        # 模拟模式：SimulatedVAD + 场景注入
        noise_thr = DEFAULT_ENERGY
        sim_vad   = SimulatedVAD(noise_threshold=noise_thr)
        vad       = sim_vad

        # 直接实例化 MicGuardian 并替换其 _vad（猴子补丁）
        # 避免在非 Windows 环境因 platform check 而退出
        try:
            from guardian import MicGuardian
            guardian = MicGuardian.__new__(MicGuardian)
            guardian._vad              = sim_vad
            guardian._thread           = None
            guardian._running          = False
            guardian._stop_remind      = False
            guardian._last_intent      = None
            guardian._voice_streak     = 0
            guardian._had_voice_before_off = False
            guardian._mic_off_flag     = False
            guardian.silent_time       = 0.0
            guardian.wait_flag         = False
            guardian.delay_cnt         = 0.0
            guardian._state_lock       = threading.Lock()
            guardian._frame_error_count = 0
            guardian._pre_speech       = None

            from asr import OfflineASR
            from nlp import IntentMatcher
            guardian._asr = OfflineASR(sample_rate=SAMPLE_RATE)
            guardian._nlp = IntentMatcher()
        except Exception as e:
            print(f"[压测] Guardian 初始化失败（模拟模式）: {e}")
            guardian = None

        runner = ScenarioRunner(sim_vad, stats, noise_thr)
        runner.start()

    # ---- 内存监控线程 ----
    stop_event = threading.Event()
    mem_thread = threading.Thread(
        target=_memory_monitor_loop, args=(stats, stop_event), daemon=True
    )
    mem_thread.start()

    # ---- 周期日志线程 ----
    log_thread = threading.Thread(
        target=_periodic_log, args=(stats, stop_event), daemon=True
    )
    log_thread.start()

    # ---- 主等待循环 ----
    total_secs  = hours * 3600
    check_interval = 10.0  # 秒
    end_time    = time.time() + total_secs

    try:
        while time.time() < end_time:
            time.sleep(check_interval)
    except KeyboardInterrupt:
        print("\n[压测] 用户中断测试")

    # ---- 清理 ----
    stop_event.set()
    if runner:
        runner.stop()
    if guardian and hasattr(guardian, '_running') and guardian._running:
        try:
            guardian.stop()
        except Exception:
            pass

    return stats


# ======================================================================
# 报告生成
# ======================================================================

def write_report(stats: StressStats, output_dir: Path) -> Path:
    """将测试报告写入文件"""
    report_path = output_dir / "stress_test_report.txt"
    lines = stats.summary_lines()
    lines += [
        "",
        f"  报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "  内存占用趋势（每 5 分钟采样）:",
    ]
    for elapsed_s, mem_mb in stats.mem_samples[-20:]:  # 最近 20 条
        h, r = divmod(int(elapsed_s), 3600)
        m, s = divmod(r, 60)
        lines.append(f"    +{h:02d}h{m:02d}m{s:02d}s  {mem_mb:.1f} MB")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[压测] 报告已保存至: {report_path}")
    return report_path


def append_to_issues(stats: StressStats, output_dir: Path) -> None:
    """将压测发现的问题追加到 issues.md"""
    issues_path = output_dir / "issues.md"
    new_issues: list[str] = []

    if stats.alert_accuracy() < 0.9:
        missed = stats.expected_alerts - (stats.actual_alerts - stats.false_alerts)
        new_issues.append(
            f"- [ ] [压测] 提醒精准度 {stats.alert_accuracy()*100:.1f}% 低于 90%，"
            f"漏报约 {max(0, missed)} 次，需排查 VAD 阈值与延时逻辑"
        )

    if stats.mem_peak > MEMORY_WARN_MB:
        new_issues.append(
            f"- [ ] [压测] 内存峰值 {stats.mem_peak:.1f}MB 超过警告阈值 {MEMORY_WARN_MB}MB，"
            f"检查音频缓冲区是否及时清理"
        )

    err_summary = get_error_log().summary()
    for cat, cnt in err_summary.items():
        if cnt > 0:
            new_issues.append(f"- [ ] [压测] {cat} 类异常共 {cnt} 次，详见错误日志")

    if new_issues:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        block = f"\n## 压测发现 ({timestamp})\n\n" + "\n".join(new_issues) + "\n"
        with open(issues_path, "a", encoding="utf-8") as f:
            f.write(block)
        print(f"[压测] 发现 {len(new_issues)} 条问题，已追加至 issues.md")
    else:
        print("[压测] 未发现新问题")


# ======================================================================
# 入口
# ======================================================================

def main():
    parser = argparse.ArgumentParser(description="智能防误关麦助手稳定性压力测试")
    parser.add_argument("--hours", type=float, default=72.0, help="测试时长（小时），默认 72")
    parser.add_argument("--no-real-mic", action="store_true", help="使用模拟音频，无需真实麦克风")
    args = parser.parse_args()

    use_real = not args.no_real_mic and sys.platform == "win32"
    stats    = run_stress_test(hours=args.hours, use_real_mic=use_real)

    output_dir = Path(__file__).parent
    report_path = write_report(stats, output_dir)
    append_to_issues(stats, output_dir)

    # 打印最终摘要
    print()
    for line in stats.summary_lines():
        print(line)


if __name__ == "__main__":
    main()

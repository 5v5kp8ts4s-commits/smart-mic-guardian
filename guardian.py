"""
guardian.py - 防误关麦守护主控模块

功能：
- 整合 VAD 人声检测、静默计时、15 秒延时等待、弹窗提醒
- 区分「环境静音」与「手动关麦」两种场景
- 弹窗触发后：截取静音前 3s 音频 → 离线 ASR → NLP 意图匹配 → 智能调整提醒策略
- 全局状态变量通过线程锁保护，支持多线程安全访问
- 集成全链路异常捕获，确保长时间稳定运行
"""

import sys
import time
import threading
from typing import Optional

if sys.platform != "win32":
    print("[Guardian] 本模块仅支持 Windows 平台。")
    sys.exit(1)

from vad import VADDetector
from alert import show_silent_alert, show_mic_off_alert
from asr import OfflineASR
from nlp import IntentMatcher
from pre_speech import PreSpeechDetector
from error_handler import (
    handle_audio_frame_error, handle_asr_error,
    check_memory_usage, get_error_log, install_global_handler,
)
from config import (
    SILENT_THRESHOLD_S, DELAY_WAIT_S, ASR_CAPTURE_SECS,
    SAMPLE_RATE, STRESS_LOG_INTERVAL_S,MAX_FRAME_ERRORS,
)


class MicGuardian:
    """
    麦克风防误关守护器

    核心状态机：
      监听中 → 静音帧累加 silent_time
            → 人声帧 → silent_time = 0，wait_flag = False，delay_cnt = 0
            → silent_time >= 15 → 进入延时等待（wait_flag = True, delay_cnt = 15）
            → delay_cnt <= 0 → 弹窗提醒
            → mic_off 帧 → 立即弹窗（不走计时）
    """

    def __init__(self):
        self._vad = VADDetector()
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # ASR / NLP 组件
        self._asr = OfflineASR(sample_rate=self._vad.SAMPLE_RATE if hasattr(self._vad, 'SAMPLE_RATE') else 16000)
        self._nlp = IntentMatcher()

        # 发言预测模块（在校准后初始化）
        self._pre_speech: Optional[PreSpeechDetector] = None

        # 计时器状态（由 _listen_loop 内部维护）
        self.silent_time: float = 0.0      # 累计静音时长
        self.wait_flag: bool = False       # 是否处于延时等待中
        self.delay_cnt: float = 0.0        # 延时倒计时
        self._mic_off_flag: bool = False   # 是否触发过关麦弹窗
        self._stop_remind: bool = False    # NLP 判定用户主动下线，停止弹窗
        self._last_intent: Optional[dict] = None  # 上次识别的意图结果

        # 人声历史追踪（用于区分误关麦 vs 主动关麦）
        # 连续检测到 voice 的帧数（关麦前如有语音记录 → 误关麦）
        self._voice_streak: int = 0
        # 麦克风关闭前是否有显著人声记录
        self._had_voice_before_off: bool = False

        # 线程锁：保护全局状态变量的并发读写
        self._state_lock = threading.Lock()

        # 帧错误计数：连续出错超过 MAX_FRAME_ERRORS 则重置音频流
        self._frame_error_count: int = 0

        # 安装全局未捕获异常处理器
        install_global_handler()

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动守护监听（阻塞式校准 + 后台线程监听）"""
        if self._running:
            print("[Guardian] 守护线程已在运行")
            return

        # 重置状态标记（线程锁保护）
        with self._state_lock:
            self._stop_remind = False
            self._last_intent = None
            self._voice_streak = 0
            self._had_voice_before_off = False
            self._frame_error_count = 0

        # 校准底噪（必须在主线程或同一线程中完成）
        self._vad.calibrate(duration=3.0)

        # 初始化发言预测器（用校准后的阈值）
        threshold = self._vad.threshold
        self._pre_speech = PreSpeechDetector(
            noise_threshold=threshold / 3.0,  # 底噪阈值 = 人声阈值 / 3
            voice_threshold=threshold,
            frame_duration=self._vad.frame_duration,
        )

        self._running = True
        self._thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
        )
        self._thread.start()
        print("[Guardian] 守护线程已启动，后台监听中...")

    def stop(self) -> None:
        """停止守护监听并释放资源"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._vad.close()
        print("[Guardian] 守护线程已停止")

    # ------------------------------------------------------------------
    # 主监听循环
    # ------------------------------------------------------------------
    def _listen_loop(self) -> None:
        """后台监听主循环（含全链路异常捕获 + 内存监控）"""
        self._vad.open()
        frame_duration = self._vad.frame_duration  # 单帧耗时（秒）
        _mem_check_timer: float = 0.0  # 内存检测定时器

        while self._running:
            try:
                state, energy = self._vad.detect_frame()
            except OSError as e:
                # 硬件异常（设备占用/拔出）
                from error_handler import handle_hardware_error
                handle_hardware_error(e)
                state, energy = "silence", 0.0
                self._frame_error_count += 1
                if self._frame_error_count >= MAX_FRAME_ERRORS:
                    print("[Guardian] 连续帧错误过多，尝试重置音频流")
                    self._restart_vad_stream()
                    self._frame_error_count = 0
                continue
            except Exception as e:
                state, energy = handle_audio_frame_error(e)
                self._frame_error_count += 1
                if self._frame_error_count >= MAX_FRAME_ERRORS:
                    print("[Guardian] 连续帧错误过多，尝试重置音频流")
                    self._restart_vad_stream()
                    self._frame_error_count = 0
                continue
            else:
                self._frame_error_count = 0  # 成功读帧，重置错误计数

            # 定时检查内存占用（每 STRESS_LOG_INTERVAL_S 秒一次）
            _mem_check_timer += frame_duration
            if _mem_check_timer >= STRESS_LOG_INTERVAL_S:
                _mem_check_timer = 0.0
                check_memory_usage(cleanup_callback=self._clear_audio_cache)

            # ---- 步骤 1：发言预测更新（每帧） ----
            if self._pre_speech is not None:
                self._pre_speech.update_audio(energy)
                self._pre_speech.tick()
                if self._pre_speech.should_reset:
                    status = self._pre_speech.status_str
                    print(f"[Guardian] 发言预测命中（{status}），提前清零计时器")
                    self._reset_cycle()
                    self._pre_speech.reset_semantic()
                    continue

            # ---- 步骤 2：手动关麦处理（动态屏蔽逻辑） ----
            if state == "mic_off":
                if not self._mic_off_flag:
                    # 判断：误关麦 vs 主动闭麦
                    if self._had_voice_before_off and not self._should_block_mic_off_alert():
                        # 此前连续有人声 → 误关麦 → 即时弹窗
                        print("[Guardian] 检测到麦克风被手动关闭（此前连续人声 → 误关麦）")
                        show_mic_off_alert()
                    else:
                        # 主动闭麦 → 动态屏蔽弹窗
                        block_reason = "主动闭麦（NLP 休息意图）" if self._should_block_mic_off_alert() else "主动闭麦（无人声前兆）"
                        print(f"[Guardian] 检测到麦克风被手动关闭（{block_reason}），已屏蔽弹窗")
                    self._mic_off_flag = True
                    self._reset_cycle()
                continue
            else:
                # 关麦状态恢复 → 解除屏蔽，回归常规
                if self._mic_off_flag:
                    print("[Guardian] 麦克风已恢复，解除屏蔽状态，回归常规监听")
                    self._mic_off_flag = False
                    self._had_voice_before_off = False

            # ---- 步骤 3：检测到人声 → 重置计时 + 更新语音历史 ----
            if state == "voice":
                self._voice_streak += 1
                # 连续若干帧有人声，标记"此前有人声记录"
                if self._voice_streak >= 3:
                    self._had_voice_before_off = True
                if self.silent_time > 0 or self.wait_flag:
                    print(f"[Guardian] 检测到人声（能量={energy:.1f}），计时器重置")
                self._reset_cycle()
                continue
            else:
                # 非 voice 帧 → 中断连续人声记录
                self._voice_streak = 0

            # ---- 步骤 4：静音帧 → 累加计时 ----
            # state == "silence"
            self.silent_time += frame_duration

            # 延时等待阶段
            if self.wait_flag:
                self.delay_cnt -= frame_duration
                if self.delay_cnt <= 0:
                    print("[Guardian] 延时结束，触发 ASR+NLP 流程...")
                    self._run_asr_nlp_pipeline()
                continue

            # 静默达到阈值，进入延时等待（若已判定主动下线则跳过）
            if self.silent_time >= SILENT_THRESHOLD_S:
                if self._stop_remind:
                    self.silent_time = 0.0
                    continue
                print(f"[Guardian] 连续静音 {self.silent_time:.1f}s，进入 {DELAY_WAIT_S:.0f}s 延时等待")
                self.wait_flag = True
                self.delay_cnt = DELAY_WAIT_S

    # ------------------------------------------------------------------
    # ASR + NLP 联动管道
    # ------------------------------------------------------------------
    def _run_asr_nlp_pipeline(self) -> None:
        """
        弹窗触发后执行：截取音频 → ASR → NLP → 根据意图调整提醒策略
        """
        # 1. 弹窗提醒
        show_silent_alert()

        # 2. 截取静音前 3s 音频
        pcm_data = self._vad.get_recent_pcm(duration_secs=ASR_CAPTURE_SECS)
        if not pcm_data:
            print("[Guardian] 音频缓冲区为空，跳过 ASR")
            self._reset_cycle()
            return

        # 3. 离线 ASR 识别（含异常捕获）
        recognized_text = ""
        if self._asr.available:
            print("[Guardian] 正在进行离线语音识别...")
            try:
                recognized_text = self._asr.recognize_pcm(pcm_data)
            except Exception as e:
                recognized_text = handle_asr_error(e, context="recognize_pcm")
            if recognized_text:
                print(f'[Guardian] ASR 结果: "{recognized_text}"')
            else:
                print("[Guardian] ASR 未识别到有效语音")
        else:
            print("[Guardian] ASR 不可用，跳过语音识别")

        # 4. NLP 意图匹配
        if recognized_text:
            intent_result = self._nlp.match(recognized_text)
            if intent_result:
                self._last_intent = intent_result
                intent = intent_result["intent"]
                action = intent_result["action"]
                score = intent_result["score"]
                keyword = intent_result["matched_keyword"]
                print(f"[Guardian] NLP 命中意图: {intent}（匹配关键词: {keyword}, 相似度: {score}）")

                # 4.1 发言预测：将意图结果传递给 pre_speech
                if self._pre_speech is not None:
                    self._pre_speech.update_nlp(intent_result)

                if action == "stop_remind":
                    print("[Guardian] 判定用户主动下线，后续不再弹窗提醒")
                    self._stop_remind = True
                elif action == "reset_timer":
                    print("[Guardian] 判定用户准备发言，重置计时器")
                    self._reset_cycle()
                    return
                elif action == "mark_ready":
                    print("[Guardian] 判定用户铺垫/提问，标记即将发言状态")
                elif action == "block_alert":
                    print("[Guardian] 判定用户主动休息，后续关麦将屏蔽弹窗")
            else:
                print("[Guardian] NLP 未命中任何意图，按默认逻辑处理")
        else:
            print("[Guardian] 无识别文本，按默认逻辑处理")

        # 5. 无论结果如何，重置本次静默周期计时器
        self._reset_cycle()

    def _restart_vad_stream(self) -> None:
        """重置 VAD 音频流（用于连续帧错误后的恢复）"""
        try:
            self._vad.close()
            time.sleep(0.2)
            self._vad.open()
            print("[Guardian] VAD 音频流已重置")
        except Exception as e:
            from error_handler import handle_hardware_error
            handle_hardware_error(e)

    def _clear_audio_cache(self) -> None:
        """清理 VAD 音频滚动缓冲区（内存超限时调用）"""
        try:
            self._vad._audio_buffer.clear()
            print("[Guardian] 音频滚动缓冲区已清理（内存压力释放）")
        except Exception:
            pass

    def _should_block_mic_off_alert(self) -> bool:
        """
        判断当前是否应该屏蔽麦克风关闭弹窗。
        条件：NLP 最近命中离场/休息类意图（block_alert）。
        """
        if self._last_intent is None:
            return False
        return self._last_intent.get("action") == "block_alert"

    def _reset_cycle(self) -> None:
        """重置当前静默周期计时器（线程安全）"""
        with self._state_lock:
            self.silent_time = 0.0
            self.wait_flag = False
            self.delay_cnt = 0.0

    # ------------------------------------------------------------------
    # 状态查询（供外部 UI / CLI 展示）
    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def threshold(self) -> float:
        return self._vad.threshold

    @property
    def last_intent(self) -> Optional[dict]:
        """上次识别的 NLP 意图结果"""
        return self._last_intent

    @property
    def pre_speech_status(self) -> dict:
        """发言预测模块当前状态（供 CLI/UI 展示）"""
        if self._pre_speech is None:
            return {
                "pre_voice": False,
                "semantic_ready": False,
                "should_reset": False,
                "status_str": "未初始化",
            }
        return {
            "pre_voice": self._pre_speech.is_pre_voice,
            "semantic_ready": self._pre_speech.is_semantic_ready,
            "should_reset": self._pre_speech.should_reset,
            "status_str": self._pre_speech.status_str,
        }

    @property
    def block_status(self) -> dict:
        """关麦屏蔽动态状态"""
        return {
            "stop_remind": self._stop_remind,
            "mic_off_flag": self._mic_off_flag,
            "had_voice_before_off": self._had_voice_before_off,
            "block_alert": self._should_block_mic_off_alert(),
        }

    @property
    def error_summary(self) -> dict:
        """各类异常计数摘要（供 CLI/压测报告使用）"""
        return get_error_log().summary()

    @property
    def recent_errors(self) -> list:
        """最近 10 条错误记录"""
        return get_error_log().recent(10)

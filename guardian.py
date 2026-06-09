"""
guardian.py - 防误关麦守护主控模块 (v5.0)

功能（v5.0 重构）：
- 核心逻辑从「检测静默提醒关麦」改为「检测非会议闲聊提醒关麦」
- 开场 3 分钟自动构建会议话题白名单（高频专业词）
- 实时 ASR 语音识别 → 话题分类（MEETING / CHITCHAT / NEUTRAL）
- 声学特征辅助：区分「近距离开麦发言」vs「扭头小声闲聊」
- 连续 3 秒闲聊才弹窗提醒，偶发插话不触发
- 全链路异常捕获 + 线程锁保护
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
from pre_speech import PreSpeechDetector
from error_handler import (
    handle_audio_frame_error, handle_asr_error,
    get_error_log, install_global_handler,
)
from config import (
    SILENT_THRESHOLD_S, DELAY_WAIT_S, ASR_CAPTURE_SECS,
    SAMPLE_RATE, FRAME_DURATION, MAX_FRAME_ERRORS,
    VOICE_VERIFY_SECS, MIN_VOICE_TEXT_LEN, VOICE_VERIFY_ENABLED,
    CHITCHAT_ALERT_SECS, CHITCHAT_RESET_SECS, SILENT_ALERT_SECS,
    TOPIC_CALIBRATE_SECS, ACOUSTIC_CALIBRATE_FRAMES,
    MEETING_MODE_DURATION_SECS,
    SYSTEM_AUDIO_ENABLED, SYSTEM_ASR_INTERVAL_SECS,
    SYSTEM_AUDIO_BUFFER_SECS,
)
from session_topic import SessionTopicManager, ContentType
from session_calibrator import SessionCalibrator
from system_audio import SystemAudioCapturer
from system_asr import SystemASRProcessor, MeetingState


class MicGuardian:
    """
    麦克风防误关守护器 (v5.0)

    核心状态机（v5.0 重构）：
      ① 开场校准（3 分钟白名单采集 + 声学基线，两者并行）
      ② 实时监听 → VAD 检测到 voice → ASR 语音验证 → topic 分类
      ③ 分类结果：
         MEETING   → 重置闲聊计时，不提醒
         CHITCHAT  → 累加闲聊计时，>= CHITCHAT_ALERT_SECS 弹窗提醒
         NEUTRAL   → 不重置也不累加（在听别人说话）
      ④ 声学辅助：近麦稳定发言 vs 偏移闲聊的 CV 评估
      ⑤ 连续非闲聊 >= CHITCHAT_RESET_SECS → 重置闲聊计时
      ⑥ mic_off 帧 → 即时弹窗（不走闲聊计时）
    """

    def __init__(self):
        self._vad = VADDetector()
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self._asr = OfflineASR(sample_rate=16000)

        # v5.0 新增：话题白名单管理器 + 声学校准器
        self._topic_mgr = SessionTopicManager()
        self._calibrator = SessionCalibrator()

        # 发言预测模块（在底噪校准后初始化）
        self._pre_speech: Optional[PreSpeechDetector] = None

        # ---- v4.x 兼容：旧静默计时状态 ----
        self.silent_time: float = 0.0
        self.wait_flag: bool = False
        self.delay_cnt: float = 0.0

        # ---- v5.0 新增：闲聊计时器 ----
        self._chitchat_timer: float = 0.0      # 连续闲聊累计秒数
        self._chitchat_alerted: bool = False   # 是否已触发弹窗（防重复）
        self._last_chitchat_text: str = ""     # 最近一次命中的闲聊文本
        self._non_chitchat_timer: float = 0.0  # 连续非闲聊秒数

        # ---- v5.2 新增：安静计时器（麦克风开启但无有效输入） ----
        self._silent_timer: float = 0.0        # 安静累计秒数（NEUTRAL / silence 累加）
        self._silent_alerted: bool = False     # 是否已触发安静弹窗

        # ---- v5.3 新增：系统音频捕获 + 会议状态检测 ----
        self._sys_audio: Optional[SystemAudioCapturer] = None
        self._sys_asr: Optional[SystemASRProcessor] = None
        self._sys_asr_last_time: float = 0.0   # 上次系统音频 ASR 处理时间戳
        self._sys_audio_enabled: bool = SYSTEM_AUDIO_ENABLED

        # ---- 最近一次话题分类结果（供 UI 查询）----
        self._last_content_type: Optional[ContentType] = None
        self._last_classify_detail: Optional[dict] = None

        # ---- 弹窗与屏蔽 ----
        self._mic_off_flag: bool = False
        self._stop_remind: bool = False

        # 人声历史追踪（区分误关麦 vs 主动关麦）
        self._voice_streak: int = 0
        self._had_voice_before_off: bool = False

        # ---- ASR 语音验证窗口（噪音过滤） ----
        self._voice_verify_enabled: bool = VOICE_VERIFY_ENABLED
        self._verify_buffer = []
        self._verify_frames_needed: int = max(1, int(VOICE_VERIFY_SECS / FRAME_DURATION))
        self._in_verify_window: bool = False
        self._voice_confirmed: bool = False

        # 线程锁
        self._state_lock = threading.Lock()
        self._frame_error_count: int = 0

        install_global_handler()

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动守护监听"""
        if self._running:
            print("[Guardian] 守护线程已在运行")
            return

        with self._state_lock:
            self._stop_remind = False
            self._voice_streak = 0
            self._had_voice_before_off = False
            self._frame_error_count = 0
            self._chitchat_timer = 0.0
            self._chitchat_alerted = False
            self._non_chitchat_timer = 0.0
            self._silent_timer = 0.0
            self._silent_alerted = False
            self._last_content_type = None
            self._sys_asr_last_time = 0.0

        try:
            # ① VAD 底噪校准（3s）
            self._vad.calibrate(duration=3.0)
        except RuntimeError as e:
            # 麦克风设备不可用（PyAudio -9996 等）
            print(f"\n{str(e)}")
            print("[Guardian] 守护启动失败：音频输入设备不可用，请检查麦克风后重试。")
            self._vad = VADDetector()  # 重建 VAD 以便下次重试
            return
        except OSError as e:
            print(f"\n[Guardian 错误] 音频设备异常: {e}")
            print("[Guardian] 守护启动失败，请检查麦克风连接和隐私设置后重试。")
            self._vad = VADDetector()
            return

        # ② 初始化发言预测器
        threshold = self._vad.threshold
        self._pre_speech = PreSpeechDetector(
            noise_threshold=threshold / 3.0,
            voice_threshold=threshold,
            frame_duration=self._vad.frame_duration,
        )

        # ③ 启动话题白名单采集（并行，前 3 分钟）
        self._topic_mgr.start_calibration()

        # ④ 启动声学基线采集（并行）
        self._calibrator.start_calibration()

        # ⑤ 初始化并启动系统音频捕获（v5.3 双通道监听）
        if self._sys_audio_enabled:
            self._sys_audio = SystemAudioCapturer(
                sample_rate=SAMPLE_RATE,
                buffer_secs=SYSTEM_AUDIO_BUFFER_SECS,
            )
            if self._sys_audio.available:
                ok = self._sys_audio.start()
                if ok:
                    self._sys_asr = SystemASRProcessor(asr=self._asr)
                    print("[Guardian] 系统音频双通道监听已启动（需管理员权限）")
                else:
                    print("[Guardian] 系统音频启动失败，回退到单麦克风模式")
                    self._sys_audio = None
                    self._sys_asr = None
            else:
                print("[Guardian] 系统音频设备不可用，回退到单麦克风模式")
                print("[Guardian] 提示: 安装 sounddevice 并以管理员权限运行可启用双通道监听")
                self._sys_audio = None
                self._sys_asr = None

        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        print(f"[Guardian] 守护线程已启动，开场 {TOPIC_CALIBRATE_SECS:.0f}s 采集会议话题白名单中...")

    def stop(self) -> None:
        """停止守护监听并释放资源"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._vad.close()
        # 停止系统音频捕获
        if self._sys_audio is not None:
            self._sys_audio.stop()
            self._sys_audio = None
            self._sys_asr = None
        print("[Guardian] 守护线程已停止")

    # ------------------------------------------------------------------
    # 主监听循环
    # ------------------------------------------------------------------
    def _listen_loop(self) -> None:
        """后台监听主循环（v5.0：闲聊检测 + 话题分类）"""
        self._vad.open()
        frame_duration = self._vad.frame_duration
        # 连续语音帧累积（用于 ASR 分段）
        _voice_segment_frames: int = 0
        _voice_segment_start: float = 0.0

        while self._running:
            try:
                state, energy = self._vad.detect_frame()
            except OSError as e:
                from error_handler import handle_hardware_error
                handle_hardware_error(e)
                state, energy = "silence", 0.0
                self._frame_error_count += 1
                if self._frame_error_count >= MAX_FRAME_ERRORS:
                    self._restart_vad_stream()
                    self._frame_error_count = 0
                continue
            except Exception as e:
                state, energy = handle_audio_frame_error(e)
                self._frame_error_count += 1
                if self._frame_error_count >= MAX_FRAME_ERRORS:
                    self._restart_vad_stream()
                    self._frame_error_count = 0
                continue
            else:
                self._frame_error_count = 0

            # ---- 步骤 1：麦克风关闭状态（休眠模式） ----
            if state == "mic_off":
                if not self._mic_off_flag:
                    self._mic_off_flag = True
                    print("[Guardian] 麦克风已关闭，守护进入休眠模式")
                    # 重置所有计时器和检测状态
                    self._reset_chitchat_timer()
                    self._reset_silent_timer()
                    self.silent_time = 0.0
                    self.wait_flag = False
                    self.delay_cnt = 0.0
                    if self._pre_speech is not None:
                        self._pre_speech.reset()
                # 休眠期间：不更新声学特征、不更新发言预测、不做任何检测
                continue
            else:
                # 麦克风从关闭恢复到开启
                if self._mic_off_flag:
                    self._mic_off_flag = False
                    print("[Guardian] 麦克风已开启，守护恢复监听")
                    self._had_voice_before_off = False
                    if self._pre_speech is not None:
                        self._pre_speech.reset()
                    self._reset_silent_timer()

            # ---- 步骤 2：发言预测更新（每帧） ----
            if self._pre_speech is not None:
                self._pre_speech.update_audio(energy)
                self._pre_speech.tick()

            # ---- 步骤 3：声学特征每帧更新（供 UI 展示） ----
            # 校准期间只传递 voice 帧，避免静音帧污染「发言基线」
            self._calibrator.feed_frame(energy, is_voice=(state == "voice"))

            # ---- 步骤 3：检测到人声帧 ----
            if state == "voice":
                self._voice_streak += 1
                if self._voice_streak >= 3:
                    self._had_voice_before_off = True
                # 有声音输入，重置安静计时器
                self._reset_silent_timer()

                # ASR 语音验证（防噪音误判）
                if self._voice_verify_enabled:
                    if not self._in_verify_window:
                        self._in_verify_window = True
                        self._verify_buffer = [
                            self._vad._audio_buffer[-1] if self._vad._audio_buffer else b""
                        ]
                        self._voice_confirmed = False
                        continue
                    else:
                        self._verify_buffer.append(
                            self._vad._audio_buffer[-1] if self._vad._audio_buffer else b""
                        )
                        if len(self._verify_buffer) >= self._verify_frames_needed:
                            pcm = b"".join(self._verify_buffer)
                            is_speech = self._asr.verify_speech(pcm, min_text_len=MIN_VOICE_TEXT_LEN)
                            self._in_verify_window = False
                            self._verify_buffer = []

                            if is_speech:
                                self._voice_confirmed = True
                                # ---- 步骤 4：ASR 转写 + 话题分类 ----
                                self._run_topic_classification()
                            else:
                                self._voice_confirmed = False
                        continue

                else:
                    # 关闭验证：直接 ASR
                    self._run_topic_classification()
                    continue

            else:
                # 能量回落（非 voice / silence）
                self._voice_streak = 0
                if self._in_verify_window:
                    self._in_verify_window = False
                    self._verify_buffer = []
                    self._voice_confirmed = False

                # 静默/NEUTRAL：累加非闲聊计时
                self._non_chitchat_timer += frame_duration
                if self._non_chitchat_timer >= CHITCHAT_RESET_SECS and self._chitchat_timer > 0:
                    print(f"[Guardian] 连续非闲聊 {self._non_chitchat_timer:.1f}s，重置闲聊计时")
                    self._reset_chitchat_timer()

                # ---- 步骤 X：系统音频 ASR 处理（v5.3 双通道监听） ----
                self._process_system_audio()

                # 累加安静计时器（麦克风开启但无有效输入）
                self._silent_timer += frame_duration
                # 安静达到阈值 → 弹窗提醒关麦（系统音频会议状态下不弹窗）
                if self._silent_timer >= SILENT_ALERT_SECS and not self._silent_alerted:
                    sys_state = self._get_system_meeting_state()
                    if sys_state == MeetingState.MY_TURN:
                        print(f"[Guardian] 安静 {self._silent_timer:.1f}s，但轮到用户发言 → 跳过弹窗")
                        self._reset_silent_timer()
                    elif sys_state == MeetingState.LISTENING:
                        print(f"[Guardian] 安静 {self._silent_timer:.1f}s，但其他人在说话 → 跳过弹窗")
                        self._reset_silent_timer()
                    elif not self._stop_remind:
                        print(f"[Guardian] 安静 {self._silent_timer:.1f}s 无有效输入 → 弹窗提醒关麦")
                        show_silent_alert(
                            message=f"提醒：麦克风已开启 {self._silent_timer:.0f} 秒\n未检测到发言，建议关闭麦克风"
                        )
                        self._silent_alerted = True

    # ------------------------------------------------------------------
    # 话题分类流程（ASR → topic → 计时器更新）
    # ------------------------------------------------------------------
    def _run_topic_classification(self) -> None:
        """
        截取近 ASR_CAPTURE_SECS 音频 → ASR → 话题分类（MEETING/CHITCHAT/NEUTRAL）
        根据分类结果更新闲聊计时器，达到阈值则弹窗提醒。
        """
        pcm_data = self._vad.get_recent_pcm(duration_secs=ASR_CAPTURE_SECS)
        if not pcm_data:
            return

        recognized_text = ""
        if self._asr.available:
            try:
                recognized_text = self._asr.recognize_pcm(pcm_data)
            except Exception as e:
                recognized_text = handle_asr_error(e, context="classify_pcm")
        else:
            return  # ASR 不可用则无法分类

        if not recognized_text:
            return

        # 校准阶段：喂给话题管理器，不分类
        if self._topic_mgr.is_calibrating:
            self._topic_mgr.feed_text(recognized_text)
            print(f'[Guardian][校准] 采集话题文本: "{recognized_text}"')
            remaining = self._topic_mgr.calibrate_remaining
            if remaining <= 0:
                self._topic_mgr.force_end_calibration()
            return

        # 正式阶段：三重校验分类
        detail = self._topic_mgr.classify_with_reason(recognized_text)
        content_type: ContentType = detail["type"]

        with self._state_lock:
            self._last_content_type = content_type
            self._last_classify_detail = detail

        # 声学辅助评分
        acoustic_score, acoustic_detail = self._calibrator.evaluate()

        if content_type == ContentType.MEETING:
            # 命中会议白名单 → 正常发言，重置闲聊计时和安静计时
            wl_hits = detail.get("matched_whitelist", [])
            print(f'[Guardian] 会议发言（命中白名单: {wl_hits[:3]}）: "{recognized_text}"')
            self._reset_chitchat_timer()
            self._reset_silent_timer()

        elif content_type == ContentType.CHITCHAT:
            # 命中闲聊词库 → 累加闲聊计时
            cc_hits = detail.get("matched_chitchat", [])
            segment_dur = min(ASR_CAPTURE_SECS, len(pcm_data) / (SAMPLE_RATE * 2))
            self._chitchat_timer += segment_dur
            self._non_chitchat_timer = 0.0  # 重置非闲聊计时
            self._reset_silent_timer()       # 有声音输入，重置安静计时

            acou_note = f" 声学:{acoustic_detail.get('score','?')}" if acoustic_detail else ""
            print(f'[Guardian] 闲聊内容（{self._chitchat_timer:.1f}s/{CHITCHAT_ALERT_SECS}s'
                  f'，命中词: {cc_hits[:3]}{acou_note}）: "{recognized_text}"')

            # 达到阈值 → 弹窗提醒
            if self._chitchat_timer >= CHITCHAT_ALERT_SECS and not self._chitchat_alerted:
                if not self._stop_remind:
                    print(f"[Guardian] 连续闲聊 {self._chitchat_timer:.1f}s → 弹窗提醒关麦")
                    show_silent_alert(
                        message=f"提醒：您已闲聊 {self._chitchat_timer:.0f} 秒\n建议关闭麦克风，避免打扰会议"
                    )
                    self._chitchat_alerted = True
                    self._last_chitchat_text = recognized_text

        else:
            # NEUTRAL：没有检测到有效发言（安静或听别人说话）
            print(f'[Guardian] 中性内容（NEUTRAL）: "{recognized_text}"')
            # 轻微累加非闲聊计时
            self._non_chitchat_timer += min(ASR_CAPTURE_SECS, 1.0)
            # 重置闲聊计时器（安静时不算闲聊）
            self._chitchat_timer = 0.0
            self._chitchat_alerted = False

    # ------------------------------------------------------------------
    # ASR 联动管道（旧版弹窗流程，保留兼容 CLI 的 a/asr 命令）
    # ------------------------------------------------------------------
    def _run_asr_nlp_pipeline(self) -> None:
        """CLI 手动触发：截取音频 → ASR → 展示结果"""
        show_silent_alert()
        pcm_data = self._vad.get_recent_pcm(duration_secs=ASR_CAPTURE_SECS)
        if not pcm_data:
            print("[Guardian] 音频缓冲区为空，跳过 ASR")
            return
        recognized_text = ""
        if self._asr.available:
            try:
                recognized_text = self._asr.recognize_pcm(pcm_data)
            except Exception as e:
                recognized_text = handle_asr_error(e, context="manual_asr")
            if recognized_text:
                print(f'[Guardian] ASR 结果: "{recognized_text}"')
        if recognized_text:
            detail = self._topic_mgr.classify_with_reason(recognized_text)
            print(f"[Guardian] 话题分类: {detail['type'].value}，"
                  f"白名单命中: {detail.get('matched_whitelist', [])}，"
                  f"闲聊命中: {detail.get('matched_chitchat', [])}")

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _restart_vad_stream(self) -> None:
        try:
            self._vad.close()
            time.sleep(0.2)
            self._vad.open()
            print("[Guardian] VAD 音频流已重置")
        except Exception as e:
            from error_handler import handle_hardware_error
            handle_hardware_error(e)

    def _clear_audio_cache(self) -> None:
        try:
            self._vad._audio_buffer.clear()
            print("[Guardian] 音频滚动缓冲区已清理")
        except Exception:
            pass

    def _reset_chitchat_timer(self) -> None:
        """重置闲聊计时器和弹窗状态"""
        with self._state_lock:
            self._chitchat_timer = 0.0
            self._chitchat_alerted = False
            self._non_chitchat_timer = 0.0

    def _reset_silent_timer(self) -> None:
        """重置安静计时器和弹窗状态（检测到有效声音输入时调用）"""
        with self._state_lock:
            self._silent_timer = 0.0
            self._silent_alerted = False

    # ------------------------------------------------------------------
    # 系统音频处理（v5.3 双通道监听）
    # ------------------------------------------------------------------
    def _process_system_audio(self) -> None:
        """
        定期处理系统音频：ASR 识别 + 会议状态判断。
        在 _listen_loop 的每帧 silence/NEUTRAL 时调用。
        """
        if self._sys_audio is None or self._sys_asr is None:
            return
        if not self._sys_audio.is_running:
            return

        now = time.time()
        if now - self._sys_asr_last_time < SYSTEM_ASR_INTERVAL_SECS:
            return  # 未到处理间隔

        self._sys_asr_last_time = now
        pcm = self._sys_audio.get_recent_pcm(duration_secs=ASR_CAPTURE_SECS)
        if pcm and len(pcm) > 1000:
            state = self._sys_asr.process(pcm)
            if state == MeetingState.MY_TURN:
                # 轮到用户发言 → 重置安静计时器
                self._reset_silent_timer()
            elif state == MeetingState.LISTENING:
                # 其他人在说话 → 用户在听 → 也重置安静计时器
                self._reset_silent_timer()

    def _get_system_meeting_state(self) -> MeetingState:
        """
        获取当前系统音频检测到的会议状态。
        :return: MeetingState
        """
        if self._sys_asr is None:
            return MeetingState.UNKNOWN
        return self._sys_asr.state

    def _reset_cycle(self) -> None:
        """旧版计时器重置（兼容 CLI 查询）"""
        with self._state_lock:
            self.silent_time = 0.0
            self.wait_flag = False
            self.delay_cnt = 0.0

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def threshold(self) -> float:
        return self._vad.threshold

    @property
    def pre_speech_status(self) -> dict:
        if self._pre_speech is None:
            return {"pre_voice": False, "semantic_ready": False,
                    "should_reset": False, "status_str": "未初始化"}
        return {
            "pre_voice": self._pre_speech.is_pre_voice,
            "semantic_ready": self._pre_speech.is_semantic_ready,
            "should_reset": self._pre_speech.should_reset,
            "status_str": self._pre_speech.status_str,
        }

    @property
    def block_status(self) -> dict:
        return {
            "stop_remind": self._stop_remind,
            "mic_off_flag": self._mic_off_flag,
            "had_voice_before_off": self._had_voice_before_off,
            "block_alert": False,
        }

    @property
    def chitchat_status(self) -> dict:
        """闲聊检测当前状态（供 CLI/UI 展示）"""
        return {
            "timer": round(self._chitchat_timer, 1),
            "alert_threshold": CHITCHAT_ALERT_SECS,
            "alerted": self._chitchat_alerted,
            "non_chitchat_timer": round(self._non_chitchat_timer, 1),
            "reset_threshold": CHITCHAT_RESET_SECS,
            "last_text": self._last_chitchat_text,
            "last_content_type": (
                self._last_content_type.value if self._last_content_type else "无"
            ),
        }

    @property
    def topic_status(self) -> dict:
        """话题白名单状态（供 CLI/UI 展示）"""
        stats = self._topic_mgr.stats
        return {
            "is_calibrating": stats["is_calibrating"],
            "is_calibrated": stats["is_calibrated"],
            "calibrate_remaining": round(stats["calibrate_remaining"], 1),
            "whitelist_size": stats["whitelist_size"],
            "top_words": self._topic_mgr.whitelist_words[:10],
            "meeting_count": stats["meeting"],
            "chitchat_count": stats["chitchat"],
            "neutral_count": stats["neutral"],
        }

    @property
    def acoustic_status(self) -> dict:
        """声学特征当前状态（供 CLI/UI 展示）"""
        score, detail = self._calibrator.evaluate()
        return {
            "is_calibrated": self._calibrator.is_calibrated,
            "calibrate_progress": round(self._calibrator.calibrate_progress * 100, 1),
            "baseline_mean": round(self._calibrator.baseline_mean, 1),
            "baseline_cv": round(self._calibrator.baseline_cv, 3),
            "current_score": detail.get("score", "mid"),
            "current_cv": detail.get("cv", 0.0),
            "current_mean": detail.get("mean", 0.0),
            "reason": detail.get("reason", ""),
        }

    @property
    def error_summary(self) -> str:
        return get_error_log().summary()

    @property
    def recent_errors(self) -> list:
        return get_error_log().recent(10)

    @property
    def system_audio_status(self) -> dict:
        """系统音频状态（供 CLI/UI 展示）"""
        if self._sys_asr is None:
            return {
                "available": False,
                "device_name": self._sys_audio.device_name if self._sys_audio else "未初始化",
                "state": "unknown",
                "is_my_turn": False,
                "is_listening": False,
                "is_idle": False,
                "last_text": "",
                "last_matched_keyword": "",
            }
        s = self._sys_asr.status
        return {
            "available": True,
            "device_name": self._sys_audio.device_name if self._sys_audio else "未知",
            "state": s["state"],
            "is_my_turn": s["is_my_turn"],
            "is_listening": s["is_listening"],
            "is_idle": s["is_idle"],
            "my_turn_remaining": s["my_turn_remaining"],
            "last_text": s["last_text"],
            "last_matched_keyword": s["last_matched_keyword"],
            "asr_available": s["asr_available"],
        }


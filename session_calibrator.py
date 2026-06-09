"""
session_calibrator.py - 声学特征基线校准器 (v5.0)

功能：
1. 开场采集本人正常「开麦发言」时的声学特征基线
   - 近距离对麦发言：能量较高、帧间波动小（稳定）
   - 扭头和旁边人小声闲聊：能量忽高忽低、波动大

2. 实时评估当前音频帧的声学特征
   - 返回「近距离开麦」vs「偏移闲聊」的概率评分
   - 与 NLP 分类结果联合决策，提高区分准确率

声学指标：
  - RMS 均值：平均音量水平
  - 变异系数（CV = σ/μ）：帧间能量波动程度
    CV 小 → 发声稳定（正对麦克风发言）
    CV 大 → 发声不稳定（扭头、声源偏移、小声悄悄说话）

判定规则（声学辅助，不单独触发告警）：
  - 声学得分 HIGH（≥0.7）：当前音频接近「近麦发言」基线 → 倾向 MEETING
  - 声学得分 LOW（≤0.3）：能量忽高忽低 → 辅助支持 CHITCHAT 判定
  - 声学得分 MID（0.3~0.7）：无定论，以语义结果为准
"""

import time
import math
from collections import deque
from typing import Optional, Tuple

from config import (
    ACOUSTIC_CALIBRATE_FRAMES,
    ACOUSTIC_NEAR_ENERGY_MIN,
    ACOUSTIC_NEAR_STABLE_RATIO,
    ACOUSTIC_CHIT_VOLATILE_RATIO,
    FRAME_DURATION,
)


# ======================================================
# 声学评分等级
# ======================================================
class AcousticScore:
    HIGH = "high"     # 近麦发言特征（稳定高能量）
    MID  = "mid"      # 中性（无法判定）
    LOW  = "low"      # 声源偏移/小声闲聊特征（波动大）


class SessionCalibrator:
    """
    声学特征基线校准器

    使用方式：
        cal = SessionCalibrator()
        cal.start_calibration()

        # 每帧调用（传入该帧 RMS 能量值）
        cal.feed_frame(energy)

        # 校准完成后，实时评估
        score, detail = cal.evaluate(energy_window)
    """

    # 有效 voice 样本最小数量（低于此值视为校准失败，使用默认基线）
    MIN_VOICE_SAMPLES: int = 30

    def __init__(self) -> None:
        self._calibrating: bool = False
        self._is_calibrated: bool = False
        self._calibrate_start: Optional[float] = None

        # 校准阶段采集的能量样本（仅 voice 帧）
        self._calibrate_samples: deque = deque(maxlen=ACOUSTIC_CALIBRATE_FRAMES)
        self._calibrate_silence_count: int = 0  # 校准期间遇到的 silence 帧数（仅用于日志）

        # 基线统计
        self._baseline_mean: float = 0.0    # 校准期均值
        self._baseline_cv: float   = 0.0    # 校准期变异系数
        self._baseline_std: float  = 0.0    # 校准期标准差（绝对值，不受均值大小影响）

        # 实时滑动窗口（最近 1s 能量）
        _window_size = max(1, int(1.0 / FRAME_DURATION))
        self._energy_window: deque = deque(maxlen=_window_size)

        # 评估历史（供 UI 展示趋势）
        self._score_history: deque = deque(maxlen=50)

        # 运行时基线自动更新标记
        self._auto_update_pending: bool = False

    # ----------------------------------------------------------
    # 校准控制
    # ----------------------------------------------------------
    def start_calibration(self) -> None:
        """开始声学基线校准"""
        self._calibrating = True
        self._is_calibrated = False
        self._auto_update_pending = False
        self._calibrate_start = time.monotonic()
        self._calibrate_samples.clear()
        self._calibrate_silence_count = 0
        print(f"[Calibrator] 开始声学基线采集，目标 {ACOUSTIC_CALIBRATE_FRAMES} 帧有效发言...")

    @property
    def is_calibrating(self) -> bool:
        return self._calibrating

    @property
    def is_calibrated(self) -> bool:
        return self._is_calibrated

    @property
    def calibrate_progress(self) -> float:
        """校准进度 0.0~1.0（供 UI 进度条）"""
        if not self._calibrating:
            return 1.0 if self._is_calibrated else 0.0
        return min(1.0, len(self._calibrate_samples) / ACOUSTIC_CALIBRATE_FRAMES)

    @property
    def baseline_mean(self) -> float:
        return self._baseline_mean

    @property
    def baseline_cv(self) -> float:
        return self._baseline_cv

    # ----------------------------------------------------------
    # 帧数据输入
    # ----------------------------------------------------------
    def feed_frame(self, energy: float, is_voice: bool = False) -> None:
        """
        每帧音频的 RMS 能量输入。
        校准阶段：仅采集 voice 帧作为「发言基线」。
        正常阶段：更新实时窗口，若基线未校准且检测到 voice，自动触发重校准。

        :param energy: 当前帧 RMS 能量（由 VAD 提供）
        :param is_voice: 当前帧是否被 VAD 判定为人声（voice 状态）
        """
        if self._calibrating:
            if is_voice:
                self._calibrate_samples.append(energy)
                if len(self._calibrate_samples) >= ACOUSTIC_CALIBRATE_FRAMES:
                    self._finish_calibration()
            else:
                self._calibrate_silence_count += 1
        else:
            self._energy_window.append(energy)
            # 运行时自动重校准：若基线未校准或待更新，积累 voice 帧
            if is_voice and (not self._is_calibrated or self._auto_update_pending):
                self._calibrate_samples.append(energy)
                if len(self._calibrate_samples) >= ACOUSTIC_CALIBRATE_FRAMES:
                    self._finish_calibration()

    # ----------------------------------------------------------
    # 实时评估
    # ----------------------------------------------------------
    def evaluate(self, energy: Optional[float] = None) -> Tuple[str, dict]:
        """
        评估当前声学特征，返回评分等级和详细指标。

        :param energy: 当前帧能量（可选，不传则仅用窗口数据）
        :return: (AcousticScore.HIGH/MID/LOW, detail_dict)
        """
        if energy is not None:
            self._energy_window.append(energy)

        if len(self._energy_window) < 3:
            return AcousticScore.MID, {"reason": "数据不足", "cv": 0.0, "mean": 0.0}

        mean_val, cv_val = self._compute_stats(list(self._energy_window))

        # 若未校准，使用默认规则判定
        if not self._is_calibrated:
            score = self._rule_based_score(mean_val, cv_val, baseline_mean=None)
        else:
            score = self._rule_based_score(mean_val, cv_val, baseline_mean=self._baseline_mean)

        detail = {
            "mean": round(mean_val, 2),
            "cv": round(cv_val, 3),
            "baseline_mean": round(self._baseline_mean, 2),
            "baseline_cv": round(self._baseline_cv, 3),
            "score": score,
            "reason": self._score_reason(score, mean_val, cv_val),
        }

        self._score_history.append(score)
        return score, detail

    @property
    def recent_scores(self) -> list:
        """最近评分历史（供 UI 展示趋势）"""
        return list(self._score_history)

    @property
    def dominant_recent_score(self) -> str:
        """最近 50 个评分中的主导结果"""
        if not self._score_history:
            return AcousticScore.MID
        from collections import Counter
        cnt = Counter(self._score_history)
        return cnt.most_common(1)[0][0]

    # ----------------------------------------------------------
    # 内部：校准完成
    # ----------------------------------------------------------
    def _finish_calibration(self) -> None:
        samples = list(self._calibrate_samples)
        self._calibrate_samples.clear()
        self._calibrating = False

        # ---- 样本量检查 ----
        if len(samples) < self.MIN_VOICE_SAMPLES:
            print(f"[Calibrator] 有效发言样本不足（仅 {len(samples)} 帧，"
                  f"需 ≥{self.MIN_VOICE_SAMPLES} 帧），使用默认基线")
            self._set_default_baseline()
            print("[Calibrator] 提示：声学基线用于区分「近麦发言」与「偏移闲聊」，"
                  "请在正常对麦克风说话时启动守护，系统将自动采集您的发言特征。")
            return

        # ---- 异常值过滤（3σ 规则，去除偶发电磁脉冲/噪音） ----
        filtered = self._filter_outliers(samples)
        if len(filtered) < self.MIN_VOICE_SAMPLES:
            print(f"[Calibrator] 过滤异常值后样本不足（{len(filtered)} 帧），使用默认基线")
            self._set_default_baseline()
            return

        # ---- 计算基线统计 ----
        self._baseline_mean, self._baseline_cv, self._baseline_std = self._compute_stats(filtered)

        # ---- 基线合理性检查 ----
        # CV ≥ 1.0 或均值异常 → 说明采集到的不是正常人类发言（可能是噪音误判）
        # 直接回退默认基线，避免用错误数据干扰后续判断
        if self._baseline_cv >= 1.0 or self._baseline_mean > 2000 or self._baseline_mean < 100:
            print(f"[Calibrator] 未检测到稳定发言特征（样本均值={self._baseline_mean:.1f}，"
                  f"变异系数={self._baseline_cv:.3f}），使用默认基线")
            print("[Calibrator] 提示：系统未识别到您的正常发言，"
                  "声学辅助暂时使用默认参数。检测到您的发言后会自动校准。")
            self._set_default_baseline()
            return

        self._is_calibrated = True
        self._auto_update_pending = False

        print(f"[Calibrator] 声学基线校准完成："
              f"均值={self._baseline_mean:.1f}，变异系数={self._baseline_cv:.3f}，"
              f"标准差={self._baseline_std:.1f}（有效样本 {len(filtered)}/{len(samples)} 帧）")

        # ---- 基线质量评估与提示 ----
        if self._baseline_cv < 0.15:
            print("[Calibrator] 基线稳定（CV < 0.15），声学辅助已启用")
        elif self._baseline_cv < 0.4:
            print("[Calibrator] 基线一般（CV 0.15~0.4），声学辅助可用")
        else:
            print("[Calibrator] 基线波动稍大（CV 0.4~1.0），声学辅助精度可能受限，"
                  "建议您在坐姿端正、正对麦克风的状态下使用")

    def _set_default_baseline(self) -> None:
        """使用默认基线参数（未采集到有效发言时使用）"""
        self._baseline_mean = 500.0
        self._baseline_cv = 0.3
        self._baseline_std = 150.0
        self._is_calibrated = False
        self._auto_update_pending = True

    # ----------------------------------------------------------
    # 内部：统计计算
    # ----------------------------------------------------------
    @staticmethod
    def _compute_stats(samples: list) -> Tuple[float, float, float]:
        """
        计算均值、变异系数（CV = σ / μ）和标准差（σ）。
        当均值过小时，CV 容易虚高，此时应结合标准差绝对值判断。
        """
        n = len(samples)
        if n == 0:
            return 0.0, 0.0, 0.0
        mean = sum(samples) / n
        variance = sum((x - mean) ** 2 for x in samples) / n
        std = math.sqrt(variance)
        if mean < 1e-6:
            cv = 0.0
        else:
            cv = std / mean
        return mean, cv, std

    @staticmethod
    def _filter_outliers(samples: list, sigma: float = 3.0) -> list:
        """
        使用 3σ 规则过滤异常值（去除偶发的电磁脉冲、USB 电流声等）。
        :param samples: 原始样本列表
        :param sigma: 几倍标准差视为异常，默认 3σ
        :return: 过滤后的样本列表
        """
        n = len(samples)
        if n < 10:
            return samples
        mean = sum(samples) / n
        variance = sum((x - mean) ** 2 for x in samples) / n
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std == 0:
            return samples
        lower = mean - sigma * std
        upper = mean + sigma * std
        return [x for x in samples if lower <= x <= upper]

    # ----------------------------------------------------------
    # 内部：评分规则
    # ----------------------------------------------------------
    def _rule_based_score(self, mean: float, cv: float,
                          baseline_mean: Optional[float]) -> str:
        """
        综合能量均值和变异系数判定声学等级。

        HIGH（近麦发言）：
          - 能量 >= 基线均值 × NEAR_ENERGY_MIN
          - 变异系数 <= NEAR_STABLE_RATIO（发声稳定）

        LOW（声源偏移/小声闲聊）：
          - 变异系数 >= CHIT_VOLATILE_RATIO（忽高忽低）

        MID：介于两者之间
        """
        # 能量是否达到「近麦」水平
        if baseline_mean and baseline_mean > 0:
            energy_near = mean >= baseline_mean * ACOUSTIC_NEAR_ENERGY_MIN
        else:
            energy_near = mean >= 200.0  # 无基线时用绝对值兜底

        # 变异系数判定
        is_stable   = cv <= ACOUSTIC_NEAR_STABLE_RATIO
        is_volatile = cv >= ACOUSTIC_CHIT_VOLATILE_RATIO

        if energy_near and is_stable:
            return AcousticScore.HIGH
        elif is_volatile:
            return AcousticScore.LOW
        else:
            return AcousticScore.MID

    @staticmethod
    def _score_reason(score: str, mean: float, cv: float) -> str:
        if score == AcousticScore.HIGH:
            return f"能量充足({mean:.0f})且稳定(CV={cv:.2f})，近距离对麦发言"
        elif score == AcousticScore.LOW:
            return f"能量波动大(CV={cv:.2f})，声源可能偏移或小声闲聊"
        else:
            return f"中性(均值={mean:.0f}, CV={cv:.2f})，依语义结果判定"


# ======================================================
# 便捷工厂函数
# ======================================================
def create_calibrator() -> SessionCalibrator:
    return SessionCalibrator()

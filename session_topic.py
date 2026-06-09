"""
session_topic.py - 会议话题白名单管理器 (v5.0)

功能：
1. 开场采集阶段（前 TOPIC_CALIBRATE_SECS 秒）
   - 持续收集 ASR 识别的会议文本
   - 提取高频专业词、项目名词，构建「本场白名单」

2. 实时匹配阶段
   - 判断每段 ASR 文本是否命中白名单（= 正常会议发言）
   - 判断是否命中闲聊词库（= 与会议无关的私下闲聊）

3. 三重判定结果
   - MEETING  : 命中白名单 → 正常会议沟通，不触发任何提醒
   - CHITCHAT : 命中闲聊词库且未命中白名单 → 弹窗提醒关麦
   - NEUTRAL  : 两边都未命中 → 中性，不触发提醒（可能是沉默/听讲）
"""

import time
import re
from collections import Counter
from typing import List, Optional
from enum import Enum

from config import (
    TOPIC_CALIBRATE_SECS,
    TOPIC_MIN_WORD_LEN,
    TOPIC_MIN_FREQ,
    TOPIC_TOP_N,
    TOPIC_MATCH_MIN_LEN,
)

# ======================================================
# 判定结果枚举
# ======================================================
class ContentType(Enum):
    MEETING   = "meeting"   # 命中会议白名单 → 正常会议发言
    CHITCHAT  = "chitchat"  # 命中闲聊词库 → 私下闲聊
    NEUTRAL   = "neutral"   # 两者均未命中 → 中性（静默/听讲）


# ======================================================
# 内置闲聊词库（生活化词汇）
# 只要文本中出现这些词且不在白名单内 → 判定为闲聊
# ======================================================
CHITCHAT_KEYWORDS: List[str] = [
    # 饮食
    "吃饭", "午饭", "晚饭", "早饭", "外卖", "点餐", "饿了", "好吃", "吃啥", "炒菜",
    "火锅", "奶茶", "咖啡", "零食", "喝水", "饮料", "甜点", "蛋糕", "宵夜",
    # 购物 / 网购
    "快递", "淘宝", "京东", "下单", "退货", "发货", "包裹", "拼多多", "网购",
    "优惠券", "双十一", "618", "打折", "便宜",
    # 娱乐 / 追剧
    "追剧", "刷剧", "电视剧", "电影", "综艺", "短视频", "抖音", "B站", "直播",
    "游戏", "打游戏", "王者", "英雄联盟", "剧情", "主角", "哈哈", "笑死",
    # 亲友 / 生活闲聊
    "老妈", "老爸", "妈妈", "爸爸", "男朋友", "女朋友", "对象", "室友", "邻居",
    "同学", "发小", "周末", "假期", "休息", "睡觉", "起床", "困了", "累了",
    # 天气 / 交通
    "下雨", "天气", "堵车", "地铁", "公交", "迟到", "上班", "下班",
    # 其他明显离题词
    "没事", "聊天", "闲聊", "扯淡", "废话", "算了", "随便",
]


# ======================================================
# 停用词（不参与白名单提取）
# ======================================================
_STOP_WORDS: set = {
    "我", "你", "他", "她", "我们", "你们", "他们", "的", "了", "是", "有",
    "在", "和", "也", "就", "都", "很", "这", "那", "来", "去", "说",
    "会", "要", "能", "可以", "一个", "还是", "然后", "因为", "所以",
    "但是", "如果", "不是", "没有", "什么", "怎么", "为什么", "好的", "嗯",
    "对", "啊", "吧", "呢", "哦", "哈", "嗯嗯", "好", "行",
}


class SessionTopicManager:
    """
    会议话题白名单管理器

    使用方式：
        mgr = SessionTopicManager()
        mgr.start_calibration()        # 开场校准开始

        # 校准阶段，每次 ASR 有结果时调用：
        mgr.feed_text(text)

        # 校准结束后自动构建白名单
        if mgr.is_calibrated:
            result = mgr.classify(text)  # ContentType.MEETING / CHITCHAT / NEUTRAL
    """

    def __init__(self) -> None:
        self._calibrating: bool = False
        self._calibrate_start: Optional[float] = None
        self._is_calibrated: bool = False

        # 校准阶段收集的原始文本词语
        self._calibration_word_counter: Counter = Counter()

        # 构建完成的白名单词集合（字符串集合）
        self._whitelist: set = set()

        # 对外可查的白名单词列表（按频率排序）
        self._whitelist_words: List[str] = []

        # 校准阶段剩余秒数（供UI展示）
        self._calibrate_remaining: float = TOPIC_CALIBRATE_SECS

        # 历史统计（调试用）
        self._total_texts: int = 0
        self._meeting_count: int = 0
        self._chitchat_count: int = 0

    # ----------------------------------------------------------
    # 开场校准控制
    # ----------------------------------------------------------
    def start_calibration(self) -> None:
        """开始校准阶段，重置所有状态"""
        self._calibrating = True
        self._calibrate_start = time.monotonic()
        self._is_calibrated = False
        self._calibration_word_counter = Counter()
        self._whitelist = set()
        self._whitelist_words = []
        self._calibrate_remaining = TOPIC_CALIBRATE_SECS
        print(f"[TopicMgr] 开始校准，采集 {TOPIC_CALIBRATE_SECS:.0f}s 会议内容构建话题白名单...")

    @property
    def is_calibrating(self) -> bool:
        """当前是否在校准阶段"""
        return self._calibrating

    @property
    def is_calibrated(self) -> bool:
        """白名单是否已构建完成"""
        return self._is_calibrated

    @property
    def calibrate_remaining(self) -> float:
        """校准阶段剩余秒数（用于 UI 倒计时）"""
        if not self._calibrating or self._calibrate_start is None:
            return 0.0
        elapsed = time.monotonic() - self._calibrate_start
        remaining = max(0.0, TOPIC_CALIBRATE_SECS - elapsed)
        self._calibrate_remaining = remaining
        return remaining

    @property
    def whitelist_words(self) -> List[str]:
        """当前白名单词列表（按频率降序）"""
        return self._whitelist_words

    @property
    def whitelist_size(self) -> int:
        """白名单词数"""
        return len(self._whitelist)

    @property
    def stats(self) -> dict:
        """实时统计摘要（供 UI 展示）"""
        return {
            "total": self._total_texts,
            "meeting": self._meeting_count,
            "chitchat": self._chitchat_count,
            "neutral": self._total_texts - self._meeting_count - self._chitchat_count,
            "whitelist_size": len(self._whitelist),
            "is_calibrated": self._is_calibrated,
            "is_calibrating": self._calibrating,
            "calibrate_remaining": self.calibrate_remaining if self._calibrating else 0.0,
        }

    # ----------------------------------------------------------
    # 文本输入
    # ----------------------------------------------------------
    def feed_text(self, text: str) -> Optional["ContentType"]:
        """
        输入一段 ASR 识别文本。

        - 校准阶段：提取词语纳入频率统计，自动检测校准是否结束
        - 正常阶段：返回分类结果 ContentType

        :param text: ASR 识别的原始中文文本
        :return: 校准阶段返回 None；正常阶段返回 ContentType
        """
        if not text or not text.strip():
            return None

        # 检查校准是否到期
        if self._calibrating:
            self._process_calibration(text)
            if self._should_end_calibration():
                self._build_whitelist()
            return None  # 校准阶段不分类

        # 正常阶段：分类
        return self.classify(text)

    # ----------------------------------------------------------
    # 分类判定
    # ----------------------------------------------------------
    def classify(self, text: str) -> "ContentType":
        """
        三重校验分类：
        1. 命中白名单 → MEETING（即使同时含有闲聊词）
        2. 未命中白名单 + 命中闲聊词 → CHITCHAT
        3. 两者均未命中 → NEUTRAL

        :param text: ASR 原始文本
        :return: ContentType
        """
        self._total_texts += 1

        if self._hit_whitelist(text):
            self._meeting_count += 1
            return ContentType.MEETING

        if self._hit_chitchat(text):
            self._chitchat_count += 1
            return ContentType.CHITCHAT

        return ContentType.NEUTRAL

    def classify_with_reason(self, text: str) -> dict:
        """
        分类并返回命中依据（供日志/UI详细展示）

        :param text: ASR 原始文本
        :return: { "type": ContentType, "matched_whitelist": [...], "matched_chitchat": [...] }
        """
        matched_wl = self._find_whitelist_hits(text)
        matched_cc = self._find_chitchat_hits(text)

        if matched_wl:
            content_type = ContentType.MEETING
        elif matched_cc:
            content_type = ContentType.CHITCHAT
        else:
            content_type = ContentType.NEUTRAL

        return {
            "type": content_type,
            "matched_whitelist": matched_wl,
            "matched_chitchat": matched_cc,
            "text": text,
        }

    # ----------------------------------------------------------
    # 白名单动态维护（校准结束后也可追加）
    # ----------------------------------------------------------
    def add_keyword(self, word: str) -> None:
        """手动向白名单追加关键词（用于用户自定义补充）"""
        if len(word) >= TOPIC_MIN_WORD_LEN:
            self._whitelist.add(word)
            if word not in self._whitelist_words:
                self._whitelist_words.insert(0, word)
            print(f"[TopicMgr] 手动添加白名单词: {word}")

    def remove_keyword(self, word: str) -> None:
        """从白名单删除词"""
        self._whitelist.discard(word)
        if word in self._whitelist_words:
            self._whitelist_words.remove(word)

    def force_end_calibration(self) -> None:
        """强制结束校准（用于会议开始前提前完成采集）"""
        if self._calibrating:
            print("[TopicMgr] 手动结束校准阶段")
            self._build_whitelist()

    # ----------------------------------------------------------
    # 内部：校准阶段处理
    # ----------------------------------------------------------
    def _process_calibration(self, text: str) -> None:
        """校准阶段：分词并更新词频"""
        words = self._segment(text)
        for w in words:
            if len(w) >= TOPIC_MIN_WORD_LEN and w not in _STOP_WORDS:
                self._calibration_word_counter[w] += 1

    def _should_end_calibration(self) -> bool:
        """判断校准时间是否到期"""
        if self._calibrate_start is None:
            return False
        return (time.monotonic() - self._calibrate_start) >= TOPIC_CALIBRATE_SECS

    def _build_whitelist(self) -> None:
        """从词频统计中构建白名单"""
        self._calibrating = False
        self._is_calibrated = True

        # 过滤：出现次数 >= TOPIC_MIN_FREQ，取频率最高的 TOPIC_TOP_N 个
        candidates = [
            (word, freq)
            for word, freq in self._calibration_word_counter.most_common()
            if freq >= TOPIC_MIN_FREQ
               and len(word) >= TOPIC_MIN_WORD_LEN
               and word not in _STOP_WORDS
               # 不将纯数字（如"123"）纳入白名单
               and not word.isdigit()
        ][:TOPIC_TOP_N]

        self._whitelist = {word for word, _ in candidates}
        self._whitelist_words = [word for word, _ in candidates]

        print(f"[TopicMgr] 白名单构建完成，共 {len(self._whitelist)} 个关键词")
        if self._whitelist_words:
            print(f"[TopicMgr] 高频词 Top-10: {self._whitelist_words[:10]}")
        else:
            print("[TopicMgr] 警告：白名单为空，校准阶段音频可能不足，建议手动添加关键词")

    # ----------------------------------------------------------
    # 内部：匹配检查
    # ----------------------------------------------------------
    def _hit_whitelist(self, text: str) -> bool:
        """文本中是否包含任意白名单词（子串匹配）"""
        if not self._whitelist:
            return False
        for word in self._whitelist:
            if len(word) >= TOPIC_MATCH_MIN_LEN and word in text:
                return True
        return False

    def _find_whitelist_hits(self, text: str) -> List[str]:
        """返回文本中命中的白名单词列表"""
        hits = []
        for word in self._whitelist:
            if len(word) >= TOPIC_MATCH_MIN_LEN and word in text:
                hits.append(word)
        return hits

    def _hit_chitchat(self, text: str) -> bool:
        """文本中是否包含任意闲聊词"""
        for kw in CHITCHAT_KEYWORDS:
            if kw in text:
                return True
        return False

    def _find_chitchat_hits(self, text: str) -> List[str]:
        """返回文本中命中的闲聊词列表"""
        return [kw for kw in CHITCHAT_KEYWORDS if kw in text]

    # ----------------------------------------------------------
    # 内部：简易中文分词
    # 优先使用 jieba，不可用则退化为字/双字 n-gram 切分
    # ----------------------------------------------------------
    @staticmethod
    def _segment(text: str) -> List[str]:
        """
        将中文文本分词，返回词语列表。
        尝试使用 jieba 精确模式，若未安装则用 bigram（双字切分）兜底。
        """
        # 去掉标点、空格，只保留中文、英文、数字
        clean = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text)
        if not clean:
            return []

        try:
            import jieba
            return list(jieba.cut(clean, cut_all=False))
        except ImportError:
            # 兜底：bigram（双字）+ unigram（单字）
            words = []
            for i in range(len(clean)):
                words.append(clean[i])           # 单字
                if i + 2 <= len(clean):
                    words.append(clean[i:i + 2]) # 双字
                if i + 3 <= len(clean):
                    words.append(clean[i:i + 3]) # 三字
            return words


# ======================================================
# 便捷工厂函数
# ======================================================
def create_topic_manager() -> SessionTopicManager:
    """创建并返回一个新的会议白名单管理器实例"""
    return SessionTopicManager()


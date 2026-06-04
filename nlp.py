"""
nlp.py - NLP 意图匹配引擎

功能：
- 读取 intent_rules.json 关键词规则库
- 文本预处理：统一大小写、去除标点符号
- 字符串模糊匹配（基于 difflib.SequenceMatcher）
- 返回意图分类与对应动作
"""

import json
import re
import string
from pathlib import Path
from typing import Optional
from difflib import SequenceMatcher

# 规则文件路径
RULES_PATH = Path(__file__).parent / "intent_rules.json"

# 模糊匹配阈值（0.0~1.0），达到该相似度即视为命中
FUZZY_THRESHOLD = 0.65


def _preprocess(text: str) -> str:
    """
    文本预处理：
    1. 统一转小写
    2. 去除标点符号
    3. 去除多余空白
    """
    text = text.lower().strip()
    # 去除中文标点 + 英文标点
    # 中文标点范围参考常见 CJK 标点 Unicode 区段
    text = re.sub(r"[\u3000-\u303f\uff00-\uffef]", "", text)
    # 去除英文标点
    text = text.translate(str.maketrans("", "", string.punctuation))
    # 去除多余空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fuzzy_match(text: str, keyword: str) -> float:
    """
    计算两个字符串的模糊相似度。
    :return: 0.0 ~ 1.0 的相似度分数
    """
    return SequenceMatcher(None, text, keyword).ratio()


class IntentMatcher:
    """
    NLP 意图匹配器

    加载规则库后，对输入文本进行关键词模糊匹配，返回命中的意图。
    """

    def __init__(self, rules_path: Optional[Path] = None):
        self._rules_path = rules_path or RULES_PATH
        self._rules: list[dict] = []
        self._load_rules()

    def _load_rules(self) -> None:
        """加载关键词规则配置文件"""
        if not self._rules_path.exists():
            print(f"[NLP 警告] 规则文件不存在: {self._rules_path}，使用内置默认规则")
            self._rules = _default_rules()
            return

        try:
            with open(self._rules_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._rules = data.get("rules", [])
            print(f"[NLP] 规则库加载成功，共 {len(self._rules)} 类意图")
        except Exception as e:
            print(f"[NLP 警告] 规则文件解析失败: {e}，使用内置默认规则")
            self._rules = _default_rules()

    # ------------------------------------------------------------------
    # 核心匹配接口
    # ------------------------------------------------------------------
    def match(self, text: str) -> Optional[dict]:
        """
        对输入文本进行意图匹配。
        :param text: ASR 识别出的原始文本
        :return: 命中的规则字典（含 intent/label/action/keyword）
                未命中则返回 None
        """
        if not text or not self._rules:
            return None

        processed = _preprocess(text)
        if not processed:
            return None

        best_match: Optional[dict] = None
        best_score = 0.0
        matched_keyword = ""

        for rule in self._rules:
            for keyword in rule.get("keywords", []):
                # 预处理后的关键词
                proc_kw = _preprocess(keyword)
                if not proc_kw:
                    continue

                # 1. 子串包含匹配（精确命中优先）
                if proc_kw in processed or processed in proc_kw:
                    score = 1.0
                else:
                    # 2. 模糊相似度匹配
                    score = _fuzzy_match(processed, proc_kw)

                if score >= FUZZY_THRESHOLD and score > best_score:
                    best_score = score
                    best_match = rule
                    matched_keyword = keyword

        if best_match:
            return {
                "intent": best_match["intent"],
                "label": best_match.get("label", ""),
                "action": best_match.get("action", ""),
                "matched_keyword": matched_keyword,
                "score": round(best_score, 3),
                "raw_text": text,
            }

        return None

    def get_intent_by_name(self, intent_name: str) -> Optional[dict]:
        """
        根据意图名称获取规则定义（用于查询铺垫/休息类规则详情）。
        """
        for rule in self._rules:
            if rule.get("intent") == intent_name:
                return rule
        return None

    @property
    def rules(self) -> list[dict]:
        return self._rules


# ------------------------------------------------------------------
# 内置默认规则（配置文件缺失时兜底）
# ------------------------------------------------------------------
def _default_rules() -> list[dict]:
    return [
        {
            "intent": "leave",
            "label": "请假/退出",
            "action": "stop_remind",
            "keywords": ["不好意思", "先下线", "退出会议", "有事离开"],
        },
        {
            "intent": "inquiry",
            "label": "询问",
            "action": "reset_timer",
            "keywords": ["听得到吗", "能听见吗", "在吗"],
        },
        {
            "intent": "speak",
            "label": "发言",
            "action": "reset_timer",
            "keywords": ["我说一下", "补充一点"],
        },
        {
            "intent": "foreshadow",
            "label": "铺垫/提问",
            "action": "mark_ready",
            "keywords": ["接下来", "顺带说下", "顺便提一下", "还有一件事"],
        },
        {
            "intent": "rest",
            "label": "离场/休息",
            "action": "block_alert",
            "keywords": ["我先静音", "暂时不说", "休息一下", "静音片刻"],
        },
    ]

"""普通话分词与语义槽提取"""

import jieba
import jieba.posseg as pseg
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SemanticSlot:
    """一个语义元素"""
    word: str
    pos: str                # 词性标签（jieba posseg）
    role: str               # "core_image" / "action" / "modifier" / "function_word"
    weight: str             # "must_keep" / "prefer_keep" / "sacrificeable"


@dataclass
class BarSemantics:
    """一个小节的语义信息"""
    bar_index: int
    raw_text: str
    slots: List[SemanticSlot] = field(default_factory=list)
    is_empty: bool = False

    def core_images(self) -> List[str]:
        return [s.word for s in self.slots if s.role == "core_image"]

    def actions(self) -> List[str]:
        return [s.word for s in self.slots if s.role == "action"]

    def to_prompt_text(self) -> str:
        """生成供 GLM prompt 使用的语义描述"""
        if self.is_empty:
            return ""
        parts = []
        for s in self.slots:
            parts.append(f"{s.word}({s.role},{s.weight})")
        return "; ".join(parts)


# 词性 → 角色映射
_POS_TO_ROLE = {
    'n': 'core_image',    # 名词
    'nr': 'core_image',   # 人名
    'ns': 'core_image',   # 地名
    'nt': 'core_image',   # 机构名
    'nz': 'core_image',   # 其他专名
    'ng': 'core_image',   # 名语素
    'v': 'action',        # 动词
    'vd': 'action',       # 副动词
    'vn': 'core_image',   # 名动词
    'a': 'modifier',      # 形容词
    'ad': 'modifier',     # 副形词
    'an': 'modifier',     # 名形词
    'd': 'modifier',      # 副词
    'r': 'function_word', # 代词
    'p': 'function_word', # 介词
    'c': 'function_word', # 连词
    'u': 'function_word', # 助词
    'x': 'function_word', # 非语素字
    'm': 'modifier',      # 数词
    'q': 'function_word', # 量词
    'f': 'modifier',      # 方位词
    't': 'modifier',      # 时间词
    'eng': 'function_word', # 英文
}

_ROLE_TO_WEIGHT = {
    'core_image': 'must_keep',
    'action': 'prefer_keep',
    'modifier': 'prefer_keep',
    'function_word': 'sacrificeable',
}


def segment_bar(text: str, bar_index: int) -> BarSemantics:
    """对单个小节的普通话文本进行分词和语义槽提取"""
    text = text.strip()
    if not text:
        return BarSemantics(bar_index=bar_index, raw_text="", is_empty=True)

    words = pseg.cut(text)
    slots = []
    for word, pos in words:
        word = word.strip()
        if not word:
            continue
        role = _POS_TO_ROLE.get(pos, 'modifier')
        weight = _ROLE_TO_WEIGHT.get(role, 'prefer_keep')
        slots.append(SemanticSlot(word=word, pos=pos, role=role, weight=weight))

    return BarSemantics(
        bar_index=bar_index,
        raw_text=text,
        slots=slots,
        is_empty=False,
    )


def segment_all_bars(mandarin_seed: str) -> List[BarSemantics]:
    """对整首歌的普通话语义种子进行分词"""
    bar_texts = mandarin_seed.split('|')
    results = []
    for i, text in enumerate(bar_texts):
        results.append(segment_bar(text, i))
    return results

"""协音/语义/自然度/韵脚评分器"""

import os
import yaml
from typing import List, Optional, Dict

from src.dictionary.cantonese_db import char_to_0243, char_jyutping, char_tone


# 加载评分权重配置
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'settings.yaml')
with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
    _CONFIG = yaml.safe_load(f)

_SCORING = _CONFIG['scoring']
TONE_WEIGHT = _SCORING['tone_weight']
SEMANTIC_WEIGHT = _SCORING['semantic_weight']
NATURALNESS_WEIGHT = _SCORING['naturalness_weight']
PHRASING_WEIGHT = _SCORING['phrasing_weight']
RHYME_STYLE_WEIGHT = _SCORING['rhyme_style_weight']

# 0243 类别之间的"距离"——值越小越近
_0243_DISTANCE = {
    (0, 0): 0, (0, 2): 1, (0, 4): 2, (0, 3): 3,
    (2, 0): 1, (2, 2): 0, (2, 4): 1, (2, 3): 2,
    (4, 0): 2, (4, 2): 1, (4, 4): 0, (4, 3): 1,
    (3, 0): 3, (3, 2): 2, (3, 4): 1, (3, 3): 0,
}


def _tone_score(lyric_chars: List[str], target_0243: List[int],
                beat_strengths: Optional[List[str]] = None) -> float:
    """协音子评分

    逐字比较实际 0243 与目标 0243：
    - 完全匹配: 1.0
    - 相邻类别: 0.6
    - 差两级: 0.3
    - 差三级: 0.0
    强拍位置不匹配的惩罚加倍。
    """
    if not lyric_chars or not target_0243:
        return 0.5  # 无法评分时返回中间值

    n = min(len(lyric_chars), len(target_0243))
    total = 0.0
    weight_sum = 0.0

    for i in range(n):
        actual = char_to_0243(lyric_chars[i])
        target = target_0243[i]

        # 确定该位置的权重
        beat_w = 1.0
        if beat_strengths and i < len(beat_strengths):
            if beat_strengths[i] == "strong":
                beat_w = 2.0
            elif beat_strengths[i] == "medium":
                beat_w = 1.5

        if actual is None:
            # 查不到声调，给中等分
            total += 0.5 * beat_w
        else:
            dist = _0243_DISTANCE.get((actual, target), 3)
            score_map = {0: 1.0, 1: 0.6, 2: 0.3, 3: 0.0}
            total += score_map[dist] * beat_w

        weight_sum += beat_w

    return total / weight_sum if weight_sum > 0 else 0.5


def _semantic_score(lyric: str, core_images: List[str],
                    must_keep: List[str]) -> float:
    """语义保持评分：检查核心意象词是否在歌词中有对应

    由于是粤语改写，不要求完全匹配原词，
    而是检查原词中的关键字是否出现在歌词中（字级匹配）。
    """
    if not core_images and not must_keep:
        return 0.8  # 无约束时给高分

    score = 0.0
    total = 0

    for word in must_keep:
        total += 2  # 必须保留词权重更高
        if word in lyric:
            score += 2
        else:
            # 字级部分匹配：check if any character from the word appears
            char_hits = sum(1 for c in word if c in lyric and c not in '的了在是有')
            if char_hits > 0:
                score += min(2, char_hits * 0.8)

    for word in core_images:
        total += 1
        if word in lyric:
            score += 1
        else:
            char_hits = sum(1 for c in word if c in lyric and c not in '的了在是有')
            if char_hits > 0:
                score += min(1, char_hits * 0.5)

    if total == 0:
        return 0.8

    return min(1.0, score / total)


def _naturalness_score(lyric: str) -> float:
    """口语自然度评分（简化版：基于基本规则）"""
    score = 1.0

    # 检测明显不自然的模式
    # 连续重复字
    for i in range(len(lyric) - 2):
        if lyric[i] == lyric[i+1] == lyric[i+2]:
            score -= 0.2

    # 过短（单字小节可以接受）
    cjk_chars = [c for c in lyric if '\u4e00' <= c <= '\u9fff']
    if len(cjk_chars) == 0:
        return 0.0

    return max(0.0, min(1.0, score))


def _phrasing_score(char_count: int, target_count: int) -> float:
    """分句匹配评分：字数是否匹配"""
    if target_count == 0:
        return 1.0 if char_count == 0 else 0.0
    if char_count == target_count:
        return 1.0
    diff = abs(char_count - target_count)
    if diff == 1:
        return 0.5
    return 0.0


def score_candidate(
    lyric: str,
    target_0243: List[int],
    beat_strengths: Optional[List[str]] = None,
    core_images: Optional[List[str]] = None,
    must_keep: Optional[List[str]] = None,
    target_char_count: Optional[int] = None,
) -> Dict:
    """综合评分

    Returns:
        包含总分和各子项分数的字典
    """
    cjk_chars = [c for c in lyric if '\u4e00' <= c <= '\u9fff']

    tone = _tone_score(cjk_chars, target_0243, beat_strengths)
    semantic = _semantic_score(lyric, core_images or [], must_keep or [])
    naturalness = _naturalness_score(lyric)

    phrasing = 1.0
    if target_char_count is not None:
        phrasing = _phrasing_score(len(cjk_chars), target_char_count)

    # 韵脚/风格暂给默认分
    rhyme_style = 0.6

    total = (
        TONE_WEIGHT * tone +
        SEMANTIC_WEIGHT * semantic +
        NATURALNESS_WEIGHT * naturalness +
        PHRASING_WEIGHT * phrasing +
        RHYME_STYLE_WEIGHT * rhyme_style
    )

    return {
        "total": round(total, 4),
        "tone": round(tone, 4),
        "semantic": round(semantic, 4),
        "naturalness": round(naturalness, 4),
        "phrasing": round(phrasing, 4),
        "rhyme_style": round(rhyme_style, 4),
        "char_count": len(cjk_chars),
        "target_count": target_char_count,
    }

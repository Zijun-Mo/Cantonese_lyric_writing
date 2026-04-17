"""粤语候选库：读音、声调、0243 类别查询"""

import re
from typing import Optional, List, Dict, Tuple
from functools import lru_cache

import pycantonese
import yaml
import os


# 加载配置
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'settings.yaml')
with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
    _CONFIG = yaml.safe_load(f)

TONE_MAPPING = _CONFIG['tone_mapping']  # 粤语九声 → 0243


def _extract_tone(jyutping: str) -> Optional[int]:
    """从粤拼字符串提取声调数字（1-9）"""
    if not jyutping:
        return None
    m = re.search(r'(\d)$', jyutping)
    if m:
        return int(m.group(1))
    return None


def _split_jyutping_compound(compound: str) -> List[str]:
    """将多字粤拼拆分成单字粤拼列表
    例如 'fung1ceoi1gwo3' → ['fung1', 'ceoi1', 'gwo3']
    """
    return re.findall(r'[a-z]+\d', compound)


@lru_cache(maxsize=8192)
def char_jyutping(char: str) -> Optional[str]:
    """查询单个汉字的粤拼"""
    try:
        result = pycantonese.characters_to_jyutping(char)
        if result and result[0][1]:
            jp_str = result[0][1]
            # 如果返回的是多音节（不太可能对单字），取第一个
            parts = _split_jyutping_compound(jp_str)
            if parts:
                return parts[0]
            return jp_str
        return None
    except Exception:
        return None


def char_tone(char: str) -> Optional[int]:
    """查询单个汉字的粤语声调（1-9）"""
    jp = char_jyutping(char)
    if jp:
        return _extract_tone(jp)
    return None


def char_to_0243(char: str) -> Optional[int]:
    """查询单个汉字的 0243 类别"""
    tone = char_tone(char)
    if tone is not None and tone in TONE_MAPPING:
        return TONE_MAPPING[tone]
    return None


def text_to_jyutping_list(text: str) -> List[Tuple[str, Optional[str]]]:
    """将一段文字转为 [(字, 粤拼), ...] 列表"""
    result = []
    for char in text:
        if '\u4e00' <= char <= '\u9fff':  # CJK 范围
            jp = char_jyutping(char)
            result.append((char, jp))
        else:
            result.append((char, None))
    return result


def text_to_tone_list(text: str) -> List[Tuple[str, Optional[int]]]:
    """将一段文字转为 [(字, 声调), ...] 列表"""
    result = []
    for char in text:
        if '\u4e00' <= char <= '\u9fff':
            tone = char_tone(char)
            result.append((char, tone))
        else:
            result.append((char, None))
    return result


def text_to_0243_list(text: str) -> List[Tuple[str, Optional[int]]]:
    """将一段文字转为 [(字, 0243类别), ...] 列表"""
    result = []
    for char in text:
        if '\u4e00' <= char <= '\u9fff':
            t = char_to_0243(char)
            result.append((char, t))
        else:
            result.append((char, None))
    return result


def validate_lyric_tones(lyric: str, target_0243: List[int]) -> List[dict]:
    """验证一段歌词每个字的 0243 是否匹配目标

    Returns:
        每个字位的匹配信息列表
    """
    chars = [c for c in lyric if '\u4e00' <= c <= '\u9fff']
    results = []
    for i, (char, target) in enumerate(zip(chars, target_0243)):
        actual = char_to_0243(char)
        jp = char_jyutping(char)
        tone = char_tone(char)
        results.append({
            "index": i,
            "char": char,
            "jyutping": jp,
            "tone": tone,
            "actual_0243": actual,
            "target_0243": target,
            "match": actual == target if actual is not None else False,
        })
    return results

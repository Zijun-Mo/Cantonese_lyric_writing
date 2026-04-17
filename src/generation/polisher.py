"""句级润色器：对 Top-N 候选进行有限润色"""

import logging
from typing import List, Dict, Optional

from src.generation.glm_client import GLMClient

logger = logging.getLogger(__name__)


def _build_polish_prompt(
    candidates: List[Dict],
    slot_count: int,
    target_0243: List[int],
    prev_lyric: str = "",
    theme_tags: List[str] = None,
) -> str:
    """构建润色 prompt"""
    cand_text = "\n".join(
        f"  {i+1}. {c['lyric']} — {c.get('reasoning', '')}"
        for i, c in enumerate(candidates)
    )

    return f"""以下是为一个 {slot_count} 字小节生成的粤语歌词候选：

{cand_text}

目标声调模板（0243体系）：{target_0243}
{f"前文歌词：{prev_lyric}" if prev_lyric else ""}
{f"主题：{', '.join(theme_tags)}" if theme_tags else ""}

请对以上候选进行有限润色，改善粤语口语自然度和搭配，但必须遵守：
1. 不改变字数（仍为 {slot_count} 字）
2. 尽量保持声调匹配
3. 保留原有语义
4. 允许替换同义近义词、调整虚词

请输出JSON格式：
{{"polished": [{{"lyric": "...", "reasoning": "..."}}]}}

只需输出最好的 3-5 个润色结果。lyric 必须恰好 {slot_count} 个汉字。"""


def polish_candidates(
    client: GLMClient,
    candidates: List[Dict],
    slot_count: int,
    target_0243: List[int],
    prev_lyric: str = "",
    theme_tags: List[str] = None,
) -> List[Dict]:
    """对候选列表进行润色

    Returns:
        润色后的候选列表
    """
    if not candidates:
        return []

    # 最多取前 5 个候选去润色
    top = candidates[:5]

    system_msg = "你是粤语填词润色专家。在保证协音和字数的前提下，优化歌词的口语自然度和文学性。输出严格JSON格式。"

    user_msg = _build_polish_prompt(
        top, slot_count, target_0243, prev_lyric, theme_tags
    )

    result = client.chat_json(
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.7,
        max_tokens=1024,
    )

    if result is None:
        logger.warning("润色请求失败，返回原始候选")
        return candidates

    polished = result.get("polished", [])
    # 验证字数
    valid = []
    for c in polished:
        lyric = c.get("lyric", "")
        cjk_count = sum(1 for ch in lyric if '\u4e00' <= ch <= '\u9fff')
        if cjk_count == slot_count:
            valid.append(c)

    if valid:
        return valid

    logger.warning("润色结果字数不匹配，返回原始候选")
    return candidates

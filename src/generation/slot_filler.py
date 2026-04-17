"""槽位填充器：按乐句调用 GLM 生成粤语候选"""

import json
import logging
from typing import List, Optional, Dict

from src.generation.glm_client import GLMClient
from src.preprocess.jianpu_parser import Bar, get_singable_pitches_for_bar
from src.preprocess.mandarin_segmenter import BarSemantics
from src.rules.tone_template import melody_to_0243_template, melody_to_contour

logger = logging.getLogger(__name__)

# 0243 类别描述（用于 prompt）
_0243_DESC = {
    0: "低调(0)：阳平/阳上/阳入，如「人、你、食」",
    2: "中低调(2)：阳去，如「大、後、夜」",
    4: "中高调(4)：阴上/阴去/下阴入，如「好、世、百」",
    3: "高调(3)：阴平/上阴入，如「风、天、一」",
}


def _build_system_prompt() -> str:
    return """你是一位精通粤语填词的作词家。你的任务是根据旋律音高和普通话语义种子，创作协音的粤语歌词。

关键规则：
1. 字数必须严格等于指定的字位数，每个字位对应一个汉字
2. 每个字位有一个目标声调类别（0243体系），你应尽量让每个字的粤语声调匹配目标
3. 0243体系说明：
   - 0（低调）：粤语阳平(4声)、阳上(5声)、阳入(9声)，声调低沉
   - 2（中低调）：粤语阳去(6声)，声调中偏低
   - 4（中高调）：粤语阴上(2声)、阴去(3声)、下阴入(8声)，声调中偏高
   - 3（高调）：粤语阴平(1声)、上阴入(7声)，声调高
4. 强拍位置（标记为★）的声调匹配最重要，弱拍可适度放宽
5. 粤语表达要自然流畅，适合演唱，避免生硬书面语
6. 保留普通话种子的核心意象和情感

你必须输出严格的JSON格式。"""


def _build_bar_prompt(
    bar: Bar,
    semantics: BarSemantics,
    template_0243: List[int],
    contour: List[str],
    theme_tags: List[str],
    style_tags: List[str],
    prev_lyric: str = "",
    num_candidates: int = 10,
) -> str:
    """构建单小节的填词 prompt"""
    slot_count = bar.slot_count
    pitches = get_singable_pitches_for_bar(bar)

    # 构建字位描述
    slot_desc = []
    for i, (p, t) in enumerate(zip(pitches, template_0243)):
        strength = p['beat_strength']
        mark = "★" if strength == "strong" else ("☆" if strength == "medium" else "·")
        slot_desc.append(f"  字位{i+1} {mark}: 目标声调类={t}, 音高={p['abs_pitch']}")

    slots_text = "\n".join(slot_desc)

    # 走向描述
    contour_text = " → ".join(contour) if contour else "（单字/无走向）"

    # 语义信息
    semantic_text = semantics.raw_text if not semantics.is_empty else "（纯伴奏/过渡）"
    semantic_detail = semantics.to_prompt_text() if not semantics.is_empty else ""

    prompt = f"""请为以下小节填写 {num_candidates} 个粤语歌词候选。

【小节信息】
- 需要字数：{slot_count} 个字
- 旋律走向：{contour_text}
- 字位详情：
{slots_text}

【语义种子（普通话）】
{semantic_text}
{f"语义分析：{semantic_detail}" if semantic_detail else ""}

【主题】{', '.join(theme_tags) if theme_tags else '自由'}
【风格】{', '.join(style_tags) if style_tags else '抒情'}
{f"【前文歌词】{prev_lyric}" if prev_lyric else ""}

请输出JSON格式，包含 candidates 数组，每个元素含：
- lyric: 粤语歌词（恰好 {slot_count} 个汉字，不含标点空格）
- reasoning: 简要说明声调匹配和选词理由

示例格式：
{{"candidates": [{{"lyric": "...", "reasoning": "..."}}]}}

注意：lyric 必须恰好 {slot_count} 个汉字！"""

    return prompt


def fill_bar(
    client: GLMClient,
    bar: Bar,
    semantics: BarSemantics,
    theme_tags: List[str],
    style_tags: List[str],
    prev_lyric: str = "",
    num_candidates: int = 10,
    max_retries: int = 3,
) -> List[Dict]:
    """为单个小节生成粤语候选

    Returns:
        候选列表，每个元素包含 lyric 和 reasoning
    """
    if bar.is_rest_bar or bar.slot_count == 0:
        return []

    template = melody_to_0243_template(bar)
    contour = melody_to_contour(bar)

    system_msg = _build_system_prompt()
    user_msg = _build_bar_prompt(
        bar, semantics, template, contour,
        theme_tags, style_tags, prev_lyric, num_candidates,
    )

    for attempt in range(max_retries):
        result = client.chat_json(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.9,
            max_tokens=2048,
        )

        if result is None:
            logger.warning(f"小节 {bar.index} 第 {attempt+1} 次调用返回空")
            continue

        candidates = result.get("candidates", [])
        if not candidates:
            logger.warning(f"小节 {bar.index} 返回无候选")
            continue

        # 验证字数
        valid = []
        for c in candidates:
            lyric = c.get("lyric", "")
            cjk_count = sum(1 for ch in lyric if '\u4e00' <= ch <= '\u9fff')
            if cjk_count == bar.slot_count:
                valid.append(c)
            else:
                logger.debug(
                    f"小节 {bar.index} 候选 '{lyric}' 字数 {cjk_count} != {bar.slot_count}，丢弃"
                )

        if valid:
            return valid

        logger.warning(
            f"小节 {bar.index} 第 {attempt+1} 次所有候选字数不匹配，重试"
        )

    logger.error(f"小节 {bar.index} 经 {max_retries} 次重试仍无有效候选")
    return []

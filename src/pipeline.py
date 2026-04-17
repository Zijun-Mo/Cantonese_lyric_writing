"""总流程编排：串联全流程生成粤语歌词

采用乐句级（phrase-level）批量生成策略，将连续非休止小节合并为乐句，
每个乐句一次 API 调用，显著减少请求总数。
"""

import json
import logging
import sys
import os
import time
from typing import List, Dict, Optional, Tuple

from src.input.schema import LyricInput
from src.preprocess.jianpu_parser import parse_jianpu, ParsedScore, Bar
from src.preprocess.mandarin_segmenter import segment_all_bars, BarSemantics
from src.rules.tone_template import melody_to_0243_template, score_to_0243_templates
from src.rules.scorer import score_candidate
from src.generation.glm_client import GLMClient
from src.dictionary.cantonese_db import text_to_0243_list

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
logger = logging.getLogger(__name__)


def _group_into_phrases(
    score: ParsedScore,
    semantics_list: List[BarSemantics],
    templates: List[List[int]],
    max_bars_per_phrase: int = 4,
    max_slots_per_phrase: int = 30,
) -> List[Dict]:
    """将连续非空小节合并为乐句组

    受 max_bars_per_phrase 和 max_slots_per_phrase 限制，
    超出时自动拆分为新乐句。

    Returns:
        乐句列表，每个含 bar_indices, slot_counts, combined_seed 等
    """

    def _new_phrase():
        return {
            "bar_indices": [],
            "bars": [],
            "semantics": [],
            "templates": [],
            "combined_seed": "",
            "slot_counts": [],
        }

    def _flush(current, phrases):
        if current["bar_indices"]:
            phrases.append(current)
        return _new_phrase()

    phrases = []
    current = _new_phrase()

    for i, bar in enumerate(score.bars):
        sem = semantics_list[i] if i < len(semantics_list) else BarSemantics(
            bar_index=i, raw_text="", is_empty=True
        )
        tmpl = templates[i] if i < len(templates) else []

        if bar.is_rest_bar or bar.slot_count == 0:
            current = _flush(current, phrases)
            continue

        # 检查是否需要拆分
        current_total = sum(current["slot_counts"])
        if (len(current["bar_indices"]) >= max_bars_per_phrase or
                current_total + bar.slot_count > max_slots_per_phrase):
            current = _flush(current, phrases)

        current["bar_indices"].append(i)
        current["bars"].append(bar)
        current["semantics"].append(sem)
        current["templates"].append(tmpl)
        current["slot_counts"].append(bar.slot_count)
        if not sem.is_empty:
            current["combined_seed"] += sem.raw_text

    _flush(current, phrases)

    return phrases


def _split_count(n: int) -> List[int]:
    """将较长字数拆分为3-5字的子短语长度列表"""
    if n <= 5:
        return [n]
    if n <= 10:
        half = n // 2
        return [half, n - half]
    parts = []
    remaining = n
    while remaining > 0:
        if remaining <= 5:
            parts.append(remaining)
            break
        if remaining <= 10:
            half = remaining // 2
            parts.extend([half, remaining - half])
            break
        chunk = min(5, remaining)
        parts.append(chunk)
        remaining -= chunk
    return parts


def _segment_sentences(
    score: ParsedScore,
    semantics: List[BarSemantics],
    char_threshold: int = 10,
) -> Dict[int, Dict]:
    """将连续非休止小节按字数阈值分组为"句子"

    返回 bar_index -> {"sentence_bars": [bar_indices], "sentence_seed": str}
    的映射，同一句内的所有bar共享相同的 sentence_bars 和 sentence_seed。
    """
    # 先按段落（rest bar 分隔）分组
    paragraphs: List[List[tuple]] = []
    current_para: List[tuple] = []
    for i, bar in enumerate(score.bars):
        if bar.is_rest_bar or bar.slot_count == 0:
            if current_para:
                paragraphs.append(current_para)
            current_para = []
            continue
        sem = semantics[i] if i < len(semantics) else None
        seed = sem.raw_text.strip() if (sem and not sem.is_empty and sem.raw_text.strip()) else ""
        current_para.append((i, bar.slot_count, seed))
    if current_para:
        paragraphs.append(current_para)

    # 段落内按字数阈值分句
    result: Dict[int, Dict] = {}
    for para in paragraphs:
        sentences: List[List[tuple]] = []
        current_sent: List[tuple] = []
        current_chars = 0
        for bar_idx, slot_count, seed in para:
            # 如果当前句子已够长，且当前bar不是短尾巴(<=3字)，开新句
            if current_sent and current_chars >= char_threshold and slot_count > 3:
                sentences.append(current_sent)
                current_sent = []
                current_chars = 0
            current_sent.append((bar_idx, seed))
            current_chars += slot_count
        if current_sent:
            sentences.append(current_sent)

        # 映射到每个bar
        for sent in sentences:
            bar_indices = [idx for idx, _ in sent]
            sentence_seed = "，".join(s for _, s in sent if s)
            for bar_idx, _ in sent:
                result[bar_idx] = {
                    "sentence_bars": bar_indices,
                    "sentence_seed": sentence_seed,
                }
    return result


def _has_repetitive_chars(text: str, max_repeat: int = 2) -> bool:
    """检查是否有连续重复字超过 max_repeat 次"""
    if len(text) < max_repeat + 1:
        return False
    for i in range(len(text) - max_repeat):
        if len(set(text[i:i + max_repeat + 1])) == 1:
            return True
    return False


def _build_bar_prompt(
    slot_count: int,
    seed_text: str,
    target_0243: List[int],
    theme_tags: List[str],
    prev_lyric: str = "",
    num_candidates: int = 10,
    avoid_words: List[str] = None,
    sentence_context: str = "",
    sentence_seed: str = "",
) -> Tuple[str, str]:
    """构建单小节填词 prompt"""
    system = "你是粤语作词人。严格按要求字数输出。用词要有变化，避免重复。"

    # 0243 提示（只对短小节提供）
    tone_str = ""
    if slot_count <= 7 and target_0243:
        tone_hints = []
        for i, t in enumerate(target_0243):
            desc = {0: "低", 2: "中低", 4: "中高", 3: "高"}.get(t, "")
            tone_hints.append(f"第{i+1}字:{desc}")
        tone_str = f"\n声调建议：{'、'.join(tone_hints)}"

    # 避免重复词汇
    avoid_str = ""
    if avoid_words:
        avoid_str = f"\n避免使用这些词：{'、'.join(avoid_words[:10])}"

    # 句内上下文
    context_str = ""
    if sentence_context and sentence_seed:
        # 有句内上下文时，重构prompt使上下文成为主要指令
        context_str += f"\n这是整句歌词的一部分。整句原词：{sentence_seed}"
        context_str += f"\n前面已填：{sentence_context}"
        context_str += f"\n请紧接「{sentence_context}」续写恰好{slot_count}个字（对应原词「{seed_text}」这部分），使整句语意通顺连贯。"
    elif sentence_seed:
        context_str += f"\n整句原词：{sentence_seed}"

    # 示例JSON
    example_placeholder = "字" * slot_count
    examples_json = ", ".join(f'"{example_placeholder}"' for _ in range(3))

    user = f"""将以下普通话歌词改写为地道粤语，保持原意，恰好{slot_count}个字。

原词：{seed_text}{context_str}{tone_str}{avoid_str}
{f"主题：{', '.join(theme_tags)}" if theme_tags else ""}
{f"上句：{prev_lyric}" if prev_lyric else ""}

注意：每个候选必须恰好{slot_count}个汉字，不多不少。纯汉字无标点。

请给{num_candidates}个候选，JSON格式：
{{"lyrics": [{examples_json}]}}

现在请你写（每个恰好{slot_count}个汉字）："""

    return system, user


def _fill_single_bar(
    client: GLMClient,
    slot_count: int,
    seed_text: str,
    target_0243: List[int],
    theme_tags: List[str],
    prev_lyric: str = "",
    num_candidates: int = 5,
    max_retries: int = 3,
    avoid_words: List[str] = None,
    hard_banned: set = None,
    sentence_context: str = "",
    sentence_seed: str = "",
) -> List[str]:
    """为单个小节生成候选歌词列表"""

    if slot_count <= 7:
        # 短小节：直接生成，模型能可靠输出 1-7 字
        return _fill_short_bar(
            client, slot_count, seed_text, target_0243,
            theme_tags, prev_lyric, num_candidates, max_retries,
            avoid_words=avoid_words,
            sentence_context=sentence_context,
            sentence_seed=sentence_seed,
        )
    else:
        # 长小节：生成长文本后截取
        return _fill_long_bar(
            client, slot_count, seed_text, target_0243,
            theme_tags, prev_lyric, num_candidates, max_retries,
            avoid_words=avoid_words,
            hard_banned=hard_banned,
            sentence_context=sentence_context,
            sentence_seed=sentence_seed,
        )


def _fill_short_bar(
    client: GLMClient,
    slot_count: int,
    seed_text: str,
    target_0243: List[int],
    theme_tags: List[str],
    prev_lyric: str = "",
    num_candidates: int = 5,
    max_retries: int = 3,
    avoid_words: List[str] = None,
    sentence_context: str = "",
    sentence_seed: str = "",
) -> List[str]:
    """短小节（≤7字）直接生成"""
    ask_count = max(num_candidates * 2, 10)
    system, user = _build_bar_prompt(
        slot_count, seed_text, target_0243, theme_tags, prev_lyric, ask_count,
        avoid_words=avoid_words,
        sentence_context=sentence_context,
        sentence_seed=sentence_seed,
    )

    all_valid = []
    all_candidates = []  # 所有候选（含不精确的）
    for attempt in range(max_retries):
        result = client.chat_json(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.85 + attempt * 0.05,
            max_tokens=1024,
        )
        if result is None:
            continue

        lyrics = _extract_lyrics_from_result(result)
        for lyric in lyrics:
            chars = ''.join(ch for ch in lyric if '\u4e00' <= ch <= '\u9fff')
            if not chars:
                continue
            all_candidates.append(chars)
            if len(chars) == slot_count:
                # 过滤连续重复字
                if not _has_repetitive_chars(chars):
                    all_valid.append(chars)
            elif len(chars) > slot_count:
                truncated = chars[:slot_count]
                if not _has_repetitive_chars(truncated):
                    all_valid.append(truncated)

        if all_valid:
            return all_valid[:num_candidates]

    # 兜底：用长度最接近的候选截断/补齐
    if not all_valid and all_candidates:
        all_candidates.sort(key=lambda c: abs(len(c) - slot_count))
        for chars in all_candidates[:5]:
            if len(chars) > slot_count:
                trimmed = chars[:slot_count]
            elif len(chars) < slot_count:
                # 用候选的前面字符循环填充，避免重复尾字
                trimmed = (chars * ((slot_count // len(chars)) + 1))[:slot_count]
            else:
                trimmed = chars
            if not _has_repetitive_chars(trimmed):
                all_valid.append(trimmed)
        # 最终兜底：即使有重复字也接受第一个
        if not all_valid and all_candidates:
            chars = all_candidates[0]
            if len(chars) >= slot_count:
                all_valid.append(chars[:slot_count])
            else:
                all_valid.append((chars * ((slot_count // len(chars)) + 1))[:slot_count])

    return all_valid[:num_candidates] if all_valid else ["？" * slot_count]


def _fill_long_bar(
    client: GLMClient,
    slot_count: int,
    seed_text: str,
    target_0243: List[int],
    theme_tags: List[str],
    prev_lyric: str = "",
    num_candidates: int = 5,
    max_retries: int = 3,
    avoid_words: List[str] = None,
    hard_banned: set = None,
    sentence_context: str = "",
    sentence_seed: str = "",
) -> List[str]:
    """长小节（>7字）：生成长文本后用滑窗截取最佳子串"""
    system = "你是粤语作词人。将普通话歌词改写为地道粤语。必须保持原词的意思和意象。用词丰富，不要标点。"

    theme = ', '.join(theme_tags) if theme_tags else '抒情'
    avoid_text = ""
    if prev_lyric:
        avoid_text = f"\n衔接上句「{prev_lyric}」，但不要重复上句的用词。"
    if sentence_context:
        avoid_text += f"\n句内已生成「{sentence_context}」，请紧接续写，保持句意连贯。"
    if avoid_words:
        avoid_text += f"\n避免使用这些词：{'、'.join(avoid_words[:8])}"

    # 用不同的提示词变体增加多样性
    style_variants = [
        "改写为地道粤语歌词，保持原词意思，不要标点",
        "用粤语重新表达以下歌词内容，意思不变，不要标点",
        "翻译为粤语歌词，保留原来的情感和意象，不要标点",
    ]

    char_pool = []

    for attempt in range(max_retries):
        variant = style_variants[attempt % len(style_variants)]
        seed_info = f"原词：{seed_text}"
        if sentence_seed and sentence_seed != seed_text:
            seed_info = f"整句原词：{sentence_seed}\n当前片段：{seed_text}"
        user = f"""将以下普通话歌词{variant}。

{seed_info}
主题：{theme}{avoid_text}

要求：意思必须与原词一致或相近。请输出至少{slot_count + 10}个汉字的粤语歌词段落，纯汉字无标点："""

        result = client.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=min(0.85 + attempt * 0.1, 0.99),
            max_tokens=1024,
        )
        if not result:
            continue

        chars = ''.join(ch for ch in result if '\u4e00' <= ch <= '\u9fff')
        if chars:
            char_pool.append(chars)

    # 将不够长的片段合并为更长的文本池
    long_enough = [c for c in char_pool if len(c) >= slot_count]
    if not long_enough and char_pool:
        merged = ''.join(char_pool)
        if len(merged) >= slot_count:
            long_enough = [merged]
    if not long_enough:
        logger.warning(f"    长小节{slot_count}字：API无有效返回")
        return ["？" * slot_count]
    char_pool = long_enough

    # 从字符池中提取候选，避免重复
    all_valid = []
    global_avoid = set(avoid_words) if avoid_words else set()
    banned = hard_banned or set()
    # 语义关键字：从原词中提取有意义的字（去掉常见虚词）
    _stop_chars = set("的了在是有和与就都被把给让从到也不我你他她它们这那些")
    seed_keywords = set(c for c in seed_text if '\u4e00' <= c <= '\u9fff' and c not in _stop_chars)
    all_scored_relaxed = []  # 放宽版候选（只过滤重复字）
    for chars in char_pool:
        if len(chars) < slot_count:
            continue
        scored = []
        for start in range(len(chars) - slot_count + 1):
            substr = chars[start:start + slot_count]
            # 过滤连续重复字
            if _has_repetitive_chars(substr):
                continue
            # 声调分
            tone_score = _tone_score_quick(substr, target_0243)
            # 语义关联加分：包含原词关键字越多越好
            semantic_bonus = sum(1.5 for c in substr if c in seed_keywords)
            base_score = tone_score + semantic_bonus

            # 收集放宽版候选（用于 fallback）
            all_scored_relaxed.append((base_score, substr))

            # 硬性过滤：含高频禁用词的子串直接跳过
            has_banned = any(bw in substr for bw in banned)
            if has_banned:
                continue
            # 与上句的重叠惩罚
            overlap_penalty = 0.0
            if prev_lyric:
                # 检查3字以上共享子串 — 严重重叠直接跳过
                has_long_overlap = False
                for k in range(3, min(len(substr), len(prev_lyric)) + 1):
                    for pos in range(len(substr) - k + 1):
                        if substr[pos:pos+k] in prev_lyric:
                            has_long_overlap = True
                            break
                    if has_long_overlap:
                        break
                if has_long_overlap:
                    continue
                common = sum(1 for c in substr if c in prev_lyric)
                overlap_penalty = common * 0.3
            # 全局已用词惩罚（软性）
            if global_avoid:
                for aw in global_avoid:
                    if aw in substr:
                        overlap_penalty += 1.5
            scored.append((base_score - overlap_penalty, substr))
        scored.sort(key=lambda x: x[0], reverse=True)
        for score, substr in scored[:2]:
            if substr not in all_valid:
                all_valid.append(substr)

    # fallback: 如果严格过滤后无候选，使用放宽版（只过滤重复字）
    if not all_valid and all_scored_relaxed:
        all_scored_relaxed.sort(key=lambda x: x[0], reverse=True)
        seen = set()
        for _, substr in all_scored_relaxed:
            if substr not in seen:
                all_valid.append(substr)
                seen.add(substr)
                if len(all_valid) >= num_candidates:
                    break

    if not all_valid:
        full = ''.join(char_pool)
        if len(full) >= slot_count:
            all_valid.append(full[:slot_count])
        else:
            padded = (full * ((slot_count // len(full)) + 1))[:slot_count]
            all_valid.append(padded)

    return all_valid[:num_candidates]


def _fill_sentence(
    client: GLMClient,
    bars_info: List[Tuple[int, int, str]],
    theme_tags: List[str],
    templates: List[List[int]],
    prev_lyric: str = "",
    num_candidates: int = 5,
    max_retries: int = 5,
) -> List[List[str]]:
    """为整句生成候选粤语歌词（生成长文本后截取并拆分为各bar）

    Args:
        bars_info: [(bar_index, slot_count, seed_text), ...]
        theme_tags: 主题标签
        templates: 声调模板列表（按bar_index索引）
        prev_lyric: 上句歌词
        num_candidates: 候选数
        max_retries: 最大重试次数

    Returns:
        列表，每项是一组段落（与 bars_info 对应的歌词列表）
    """
    total_chars = sum(sc for _, sc, _ in bars_info)
    combined_seed = "，".join(s for _, _, s in bars_info if s)
    if not combined_seed:
        return []

    system = "你是粤语作词人。将普通话歌词改写为地道粤语歌词。必须保持原意。"
    theme = ', '.join(theme_tags) if theme_tags else '抒情'

    # 生成长文本（≥ total_chars），类似 _fill_long_bar 策略
    style_variants = [
        "改写为地道粤语歌词，保持原词意思，不要标点",
        "用粤语重新表达以下歌词内容，意思不变，不要标点",
        "翻译为粤语歌词，保留原来的情感和意象，不要标点",
        "转化为粤语歌词，维持原词含义，不要标点",
        "改编为粤语歌词，保持原意不变，不要标点",
    ]
    avoid_text = ""
    if prev_lyric:
        avoid_text = f"\n衔接上句「{prev_lyric}」，但不要重复上句的用词。"

    char_pool = []
    for attempt in range(max_retries):
        variant = style_variants[attempt % len(style_variants)]
        user = f"""将以下普通话歌词{variant}。

原词：{combined_seed}
主题：{theme}{avoid_text}

要求：意思必须与原词一致或相近。语意连贯通顺。请输出至少{total_chars + 15}个汉字的粤语歌词段落，纯汉字无标点："""

        result = client.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=min(0.85 + attempt * 0.1, 0.99),
            max_tokens=512,
        )
        if not result:
            continue
        chars = ''.join(ch for ch in result if '\u4e00' <= ch <= '\u9fff')
        if chars:
            char_pool.append(chars)

    # 确保有足够长的文本
    long_enough = [c for c in char_pool if len(c) >= total_chars]
    if not long_enough and char_pool:
        merged = ''.join(char_pool)
        if len(merged) >= total_chars:
            long_enough = [merged]
    if not long_enough:
        return []

    # 从文本池中用滑窗提取最优的 total_chars 子串，然后拆分为各bar
    # 按组合声调分排序选出最优候选
    _stop_chars = set("的了在是有和与就都被把给让从到也不我你他她它们这那些")
    seed_keywords = set(c for c in combined_seed if '\u4e00' <= c <= '\u9fff' and c not in _stop_chars)

    # 构建全句声调模板
    full_template = []
    for bar_idx, slot_count, _ in bars_info:
        tmpl = templates[bar_idx] if bar_idx < len(templates) else []
        full_template.extend(tmpl[:slot_count])

    all_scored = []
    for chars in long_enough:
        for start in range(len(chars) - total_chars + 1):
            substr = chars[start:start + total_chars]
            if _has_repetitive_chars(substr):
                continue
            # 声调分
            tone_score = _tone_score_quick(substr, full_template)
            # 语义关联加分
            semantic_bonus = sum(1.5 for c in substr if c in seed_keywords)
            # 与上句重叠惩罚
            overlap_penalty = 0.0
            if prev_lyric:
                has_long_overlap = False
                for k in range(3, min(len(substr), len(prev_lyric)) + 1):
                    for pos in range(len(substr) - k + 1):
                        if substr[pos:pos+k] in prev_lyric:
                            has_long_overlap = True
                            break
                    if has_long_overlap:
                        break
                if has_long_overlap:
                    overlap_penalty += 5.0
            all_scored.append((tone_score + semantic_bonus - overlap_penalty, substr))

    all_scored.sort(key=lambda x: x[0], reverse=True)

    # 拆分为各bar
    all_sentence_candidates = []
    seen = set()
    for _, substr in all_scored:
        if substr in seen:
            continue
        seen.add(substr)
        pos = 0
        segments = []
        for _, slot_count, _ in bars_info:
            segments.append(substr[pos:pos + slot_count])
            pos += slot_count
        all_sentence_candidates.append(segments)
        if len(all_sentence_candidates) >= num_candidates:
            break

    return all_sentence_candidates


def _tone_score_quick(text: str, target_0243: List[int]) -> float:
    """快速计算文本与声调目标的匹配分"""
    if not target_0243:
        return 0.0
    tones = text_to_0243_list(text)
    score = 0.0
    for (ch, tone), target in zip(tones, target_0243):
        if tone is None:
            continue
        if tone == target:
            score += 2.0
        elif abs(tone - target) <= 1:
            score += 1.0
    return score


_TONE_DESC = {0: "低", 2: "中低", 4: "中高", 3: "高"}


def _build_tone_feedback(lyric: str, target_0243: List[int]) -> str:
    """生成逐字协音偏差标注，用于重试 prompt

    返回示例：
      第1字「曾」实际=低(0) 目标=中高(4)✗，第3字「下」实际=中低(2) 目标=高(3)✗
    只列出不匹配的位置，匹配的省略。
    """
    if not target_0243:
        return ""
    tones = text_to_0243_list(lyric)
    mismatches = []
    for idx, ((ch, tone), target) in enumerate(zip(tones, target_0243)):
        if tone is None:
            continue
        if tone != target:
            actual_desc = _TONE_DESC.get(tone, "?")
            target_desc = _TONE_DESC.get(target, "?")
            mismatches.append(
                f"第{idx+1}字「{ch}」实际={actual_desc}({tone}) 目标={target_desc}({target})"
            )
    if not mismatches:
        return ""
    return "；".join(mismatches)


def _build_retry_with_tone_prompt(
    slot_count: int,
    seed_text: str,
    target_0243: List[int],
    theme_tags: List[str],
    old_lyric: str,
    tone_feedback: str,
    num_candidates: int = 10,
    avoid_words: List[str] = None,
) -> Tuple[str, str]:
    """构建带协音偏差标注的重试 prompt"""
    system = "你是粤语作词人。严格按要求字数输出。注意声调匹配。"

    tone_hints = []
    if target_0243:
        for i, t in enumerate(target_0243):
            desc = _TONE_DESC.get(t, "")
            tone_hints.append(f"第{i+1}字:{desc}")
    tone_str = f"\n目标声调：{'、'.join(tone_hints)}" if tone_hints else ""

    avoid_str = ""
    if avoid_words:
        avoid_str = f"\n避免使用这些词：{'、'.join(avoid_words[:10])}"

    example_placeholder = "字" * slot_count
    examples_json = ", ".join(f'"{example_placeholder}"' for _ in range(3))

    user = f"""将以下普通话歌词改写为地道粤语，保持原意，恰好{slot_count}个字。

原词：{seed_text}
{f"主题：{', '.join(theme_tags)}" if theme_tags else ""}

上次生成的「{old_lyric}」声调不够匹配，具体问题：
{tone_feedback}
请特别注意上述不匹配位置的声调，重新生成。{tone_str}{avoid_str}

注意：每个候选必须恰好{slot_count}个汉字，不多不少。纯汉字无标点。

请给{num_candidates}个候选，JSON格式：
{{"lyrics": [{examples_json}]}}

现在请你写（每个恰好{slot_count}个汉字，声调尽量匹配目标）："""

    return system, user


def _extract_lyrics_from_result(result: dict) -> List[str]:
    """从 API JSON 结果中提取歌词列表"""
    lyrics = result.get("lyrics", [])
    if not lyrics:
        for key in ["candidates", "results"]:
            if key in result:
                raw = result[key]
                if isinstance(raw, list):
                    lyrics = [
                        (item if isinstance(item, str)
                         else item.get("lyric", item.get("text", str(item))))
                        for item in raw
                    ]
                break
    return [str(l) for l in lyrics]

    return all_valid


def run_pipeline(
    lyric_input: LyricInput,
    enable_polish: bool = True,
    num_candidates: int = 5,
) -> Dict:
    """运行完整填词流程（逐小节生成）"""
    logger.info("=== 粤语填词流程开始 ===")

    # 1. 解析简谱
    logger.info("步骤 1: 解析简谱...")
    score = parse_jianpu(lyric_input.jianpu)
    logger.info(f"  共 {len(score.bars)} 小节，{score.total_slots} 个字位")

    # 2. 语义分词
    logger.info("步骤 2: 普通话语义槽提取...")
    semantics = segment_all_bars(lyric_input.mandarin_seed)

    # 3. 生成 0243 模板
    logger.info("步骤 3: 生成声调模板...")
    templates = score_to_0243_templates(score)

    # 4. 创建客户端
    client = GLMClient()
    logger.info(f"  使用模型: {client.model}")

    # 5. 构建句子分组（用于句内连贯性）
    sentence_map = _segment_sentences(score, semantics)
    unique_sentences = {}
    for bar_idx, sinfo in sentence_map.items():
        key = tuple(sinfo["sentence_bars"])
        if key not in unique_sentences:
            unique_sentences[key] = sinfo
    total_sents = len(unique_sentences)
    multi_bar_sents = {k: v for k, v in unique_sentences.items() if len(k) > 1}
    logger.info(f"步骤 3.5: 句子分组完成，共 {total_sents} 个句子，其中 {len(multi_bar_sents)} 个多bar句子")

    # 6. 句级整体生成（多bar句子优先整句生成以保证连贯性）
    sentence_results = {}  # bar_idx -> bar_result
    if multi_bar_sents:
        logger.info("步骤 4a: 句级整体生成...")
        ordered_sent_keys = sorted(multi_bar_sents.keys(), key=lambda k: k[0])
        sent_prev_lyric = ""

        for sent_key in ordered_sent_keys:
            sinfo = multi_bar_sents[sent_key]
            bars_in_sent = sinfo["sentence_bars"]

            # 检查句子前是否有段落分隔（rest bar）
            first_bar = bars_in_sent[0]
            has_rest_before = any(
                score.bars[j].is_rest_bar or score.bars[j].slot_count == 0
                for j in range(max(0, first_bar - 1), first_bar)
            ) if first_bar > 0 else True
            if has_rest_before:
                sent_prev_lyric = ""

            # 收集bar信息
            bars_info = []
            for bar_idx in bars_in_sent:
                bar_obj = score.bars[bar_idx]
                sem = semantics[bar_idx] if bar_idx < len(semantics) else None
                seed = sem.raw_text.strip() if (sem and not sem.is_empty and sem.raw_text.strip()) else ""
                bars_info.append((bar_idx, bar_obj.slot_count, seed))

            # 生成候选
            sentence_candidates = _fill_sentence(
                client, bars_info, lyric_input.theme_tags,
                templates=templates,
                prev_lyric=sent_prev_lyric,
                num_candidates=num_candidates,
            )

            if not sentence_candidates:
                logger.info(f"  句子 bars={bars_in_sent}: 整句生成失败，回退逐bar")
                time.sleep(0.3)
                continue

            # 评分：选整句总分最高的候选
            best_segments = None
            best_avg = -1.0
            best_scores = None

            for segments in sentence_candidates:
                seg_scores = []
                for seg, (bar_idx, slot_count, _) in zip(segments, bars_info):
                    tmpl = templates[bar_idx] if bar_idx < len(templates) else []
                    bar_obj = score.bars[bar_idx]
                    beat_strs = [n.beat_strength for n in bar_obj.singable_notes]
                    sem = semantics[bar_idx] if bar_idx < len(semantics) else None
                    core_imgs = sem.core_images() if sem and not sem.is_empty else []
                    must_kw = [s.word for s in sem.slots if s.weight == 'must_keep'] if sem and not sem.is_empty else []
                    sc = score_candidate(seg, tmpl, beat_strengths=beat_strs,
                                         core_images=core_imgs, must_keep=must_kw,
                                         target_char_count=slot_count)
                    seg_scores.append(sc)

                avg = sum(s["total"] for s in seg_scores) / len(seg_scores)
                if avg > best_avg:
                    best_avg = avg
                    best_segments = segments
                    best_scores = seg_scores

            if best_segments and best_avg >= 0.40:
                combined = "|".join(best_segments)
                logger.info(f"  句子 bars={bars_in_sent}: 「{combined}」 平均总分={best_avg:.2f}")
                for bi_pos, (seg, (bar_idx, slot_count, _seed), sc) in enumerate(
                        zip(best_segments, bars_info, best_scores)):
                    # 收集其他候选中该位置的段落
                    other_segs = []
                    for segs in sentence_candidates[:5]:
                        if bi_pos < len(segs) and segs[bi_pos] != seg:
                            other_segs.append(segs[bi_pos])
                    sentence_results[bar_idx] = {
                        "bar_index": bar_idx,
                        "slot_count": slot_count,
                        "is_rest": False,
                        "best_lyric": seg,
                        "candidates": [{"lyric": seg, "score": sc["total"]}] +
                                       [{"lyric": s, "score": 0} for s in other_segs[:4]],
                        "score": sc,
                    }
                sent_prev_lyric = best_segments[-1]
            else:
                logger.info(f"  句子 bars={bars_in_sent}: 候选分数过低({best_avg:.2f})，回退逐bar")

            time.sleep(0.3)

        logger.info(f"  句级生成完成: {len(sentence_results)} 个bar已处理")

    # 7. 逐小节填词（句级未覆盖的bar + 单bar句子）
    logger.info("步骤 4b: 逐小节填词...")
    bar_results = {}
    prev_lyric = ""
    singable_count = 0
    used_bigrams = []  # 全局已用双字词追踪（允许重复计数）

    def _update_used_words(lyric: str):
        """更新已用词记录（每次都添加，允许重复）"""
        for j in range(len(lyric) - 1):
            used_bigrams.append(lyric[j:j+2])

    def _get_avoid_words() -> List[str]:
        """返回已用过的高频双字词"""
        from collections import Counter
        counts = Counter(used_bigrams)
        # 返回出现过1次以上的词（即只要用过就列入避免）
        return [w for w, c in counts.most_common(20) if c >= 1]

    def _get_hard_banned() -> set:
        """返回出现3次以上的双字词，严格禁止"""
        from collections import Counter
        counts = Counter(used_bigrams)
        return {w for w, c in counts.items() if c >= 3}

    for i, bar in enumerate(score.bars):
        # 休止小节
        if bar.is_rest_bar or bar.slot_count == 0:
            bar_results[i] = {
                "bar_index": i, "slot_count": 0, "is_rest": True,
                "best_lyric": "", "candidates": [], "score": None,
            }
            prev_lyric = ""  # 段落重置
            continue

        singable_count += 1

        # 检查是否已由句级生成处理
        if i in sentence_results:
            bar_results[i] = sentence_results[i]
            _update_used_words(sentence_results[i]["best_lyric"])
            prev_lyric = sentence_results[i]["best_lyric"]
            logger.info(
                f"  小节 {i} ({singable_count}): {bar.slot_count}字 [句级] "
                f"=> \"{sentence_results[i]['best_lyric']}\" "
                f"(协音={sentence_results[i]['score']['tone']:.2f}, "
                f"总分={sentence_results[i]['score']['total']:.2f})"
            )
            continue

        slot_count = bar.slot_count
        tmpl = templates[i] if i < len(templates) else []

        # 获取语义种子
        sem = semantics[i] if i < len(semantics) else None
        if sem and not sem.is_empty and sem.raw_text.strip():
            seed_text = sem.raw_text.strip()
        else:
            seed_text = "承接前文情感"

        logger.info(
            f"  小节 {i} ({singable_count}): {slot_count}字, "
            f"语义=\"{seed_text[:20]}\", 声调={tmpl}"
        )

        # 计算句内上下文
        sentence_context = ""
        sentence_seed = ""
        if i in sentence_map:
            sinfo = sentence_map[i]
            sentence_seed = sinfo["sentence_seed"]
            # 收集同句内已生成的歌词
            context_parts = []
            for prev_bar_idx in sinfo["sentence_bars"]:
                if prev_bar_idx == i:
                    break
                if prev_bar_idx in bar_results and not bar_results[prev_bar_idx]["is_rest"]:
                    context_parts.append(bar_results[prev_bar_idx]["best_lyric"])
            sentence_context = "".join(context_parts)

        # 生成候选
        avoid = _get_avoid_words()
        banned = _get_hard_banned()
        candidates = _fill_single_bar(
            client, slot_count, seed_text, tmpl,
            lyric_input.theme_tags,
            prev_lyric=prev_lyric,
            num_candidates=num_candidates,
            avoid_words=avoid,
            hard_banned=banned,
            sentence_context=sentence_context,
            sentence_seed=sentence_seed,
        )

        if not candidates:
            logger.warning(f"  小节 {i} 无有效候选，填充占位符")
            bar_results[i] = {
                "bar_index": i, "slot_count": slot_count,
                "is_rest": False, "best_lyric": "？" * slot_count,
                "candidates": [], "score": None,
            }
            continue

        # 评分并选最优
        beat_strengths = [n.beat_strength for n in bar.singable_notes]
        # 提取语义关键词用于评分
        core_imgs = sem.core_images() if sem and not sem.is_empty else []
        must_kw = [s.word for s in sem.slots if s.weight == 'must_keep'] if sem and not sem.is_empty else []
        scored = []
        for lyric in candidates:
            s = score_candidate(
                lyric, tmpl,
                beat_strengths=beat_strengths,
                core_images=core_imgs,
                must_keep=must_kw,
                target_char_count=slot_count,
            )
            scored.append((lyric, s))

        scored.sort(key=lambda x: x[1]["total"], reverse=True)
        best_lyric, best_score = scored[0]

        tone_detail = text_to_0243_list(best_lyric)
        logger.info(
            f"    => \"{best_lyric}\" "
            f"(协音={best_score['tone']:.2f}, 总分={best_score['total']:.2f}) "
            f"声调={[t for _, t in tone_detail]} 目标={tmpl}"
        )

        bar_results[i] = {
            "bar_index": i, "slot_count": slot_count,
            "is_rest": False, "best_lyric": best_lyric,
            "candidates": [{"lyric": ly, "score": sc["total"]} for ly, sc in scored[:5]],
            "score": best_score,
        }

        _update_used_words(best_lyric)
        prev_lyric = best_lyric
        time.sleep(0.3)  # 限流

    # 5.5 多轮低分重试：带协音偏差标注 + 模型升级
    RETRY_THRESHOLD = 0.60
    UPGRADE_MODEL = "glm-4-plus"

    def _collect_retry_bars(threshold):
        """收集协音分低于阈值的小节（包含句级生成的bar）"""
        return [
            i for i, r in bar_results.items()
            if not r["is_rest"] and r["score"] is not None
            and r["score"]["tone"] < threshold
        ]

    def _retry_bar_with_feedback(bar_idx, use_client, round_label=""):
        """用协音偏差标注重试单个小节，返回是否改进"""
        bar = score.bars[bar_idx]
        slot_count = bar.slot_count
        tmpl = templates[bar_idx] if bar_idx < len(templates) else []
        sem = semantics[bar_idx] if bar_idx < len(semantics) else None
        seed_text = sem.raw_text.strip() if (sem and not sem.is_empty and sem.raw_text.strip()) else "承接前文情感"
        old_lyric = bar_results[bar_idx]["best_lyric"]
        old_tone = bar_results[bar_idx]["score"]["tone"]

        # 生成协音偏差标注
        tone_feedback = _build_tone_feedback(old_lyric, tmpl)
        logger.info(f"  {round_label}重试小节 {bar_idx}: {slot_count}字, 原协音={old_tone:.2f}")
        if tone_feedback:
            logger.info(f"    偏差: {tone_feedback}")

        # 用带标注的 prompt 生成候选
        if tone_feedback and slot_count <= 7:
            # 短小节：用带标注的 JSON prompt
            system, user = _build_retry_with_tone_prompt(
                slot_count, seed_text, tmpl, lyric_input.theme_tags,
                old_lyric, tone_feedback,
                num_candidates=max(num_candidates * 2, 10),
                avoid_words=_get_avoid_words(),
            )
            candidates = []
            for attempt in range(3):
                result = use_client.chat_json(
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.85 + attempt * 0.05,
                    max_tokens=1024,
                )
                if result is None:
                    continue
                lyrics = _extract_lyrics_from_result(result)
                for lyric in lyrics:
                    chars = ''.join(ch for ch in lyric if '\u4e00' <= ch <= '\u9fff')
                    if len(chars) == slot_count and not _has_repetitive_chars(chars):
                        candidates.append(chars)
                    elif len(chars) > slot_count:
                        truncated = chars[:slot_count]
                        if not _has_repetitive_chars(truncated):
                            candidates.append(truncated)
                if candidates:
                    break
        else:
            # 长小节或无偏差信息：使用标准生成
            candidates = _fill_single_bar(
                use_client, slot_count, seed_text, tmpl,
                lyric_input.theme_tags,
                prev_lyric="",
                num_candidates=num_candidates,
                avoid_words=_get_avoid_words(),
                hard_banned=_get_hard_banned(),
            )

        if not candidates:
            logger.info(f"    => 无有效候选")
            return False

        beat_strengths = [n.beat_strength for n in bar.singable_notes]
        core_imgs = sem.core_images() if sem and not sem.is_empty else []
        must_kw = [s.word for s in sem.slots if s.weight == 'must_keep'] if sem and not sem.is_empty else []
        scored = []
        for lyric in candidates:
            s = score_candidate(lyric, tmpl, beat_strengths=beat_strengths,
                                core_images=core_imgs, must_keep=must_kw,
                                target_char_count=slot_count)
            scored.append((lyric, s))
        scored.sort(key=lambda x: x[1]["total"], reverse=True)
        new_lyric, new_score = scored[0]

        if "？" in new_lyric:
            logger.info(f"    => 重试返回占位符，保留原结果")
            return False
        elif new_score["tone"] > old_tone:
            logger.info(f"    => 改进: \"{new_lyric}\" (协音={new_score['tone']:.2f} > {old_tone:.2f})")
            bar_results[bar_idx]["best_lyric"] = new_lyric
            bar_results[bar_idx]["score"] = new_score
            bar_results[bar_idx]["candidates"] = [{"lyric": ly, "score": sc["total"]} for ly, sc in scored[:5]]
            return True
        else:
            logger.info(f"    => 未改进 (协音={new_score['tone']:.2f})")
            return False

    # Round 1: 用 flash 模型 + 协音偏差标注重试
    retry_bars = _collect_retry_bars(RETRY_THRESHOLD)
    if retry_bars:
        logger.info(f"\n步骤 5.5a: 协音标注重试 {len(retry_bars)} 个低分小节 (协音<{RETRY_THRESHOLD})...")
        for i in retry_bars:
            _retry_bar_with_feedback(i, client, round_label="[flash] ")
            time.sleep(0.3)

    # Round 2: 协音仍低于阈值的小节，升级到更强模型重试
    still_low = _collect_retry_bars(RETRY_THRESHOLD)
    if still_low:
        logger.info(f"\n步骤 5.5b: 升级模型 ({UPGRADE_MODEL}) 重试 {len(still_low)} 个仍低分小节...")
        strong_client = GLMClient(model=UPGRADE_MODEL)
        for i in still_low:
            _retry_bar_with_feedback(i, strong_client, round_label=f"[{UPGRADE_MODEL}] ")
            time.sleep(0.5)

    # 8. 汇总
    results = [bar_results[i] for i in range(len(score.bars))]

    full_lyric_parts = []
    for r in results:
        if r["is_rest"]:
            full_lyric_parts.append("")
        else:
            full_lyric_parts.append(r["best_lyric"])
    full_lyric = "|".join(full_lyric_parts)

    scored_bars = [r for r in results if r["score"] is not None]
    avg_tone = sum(r["score"]["tone"] for r in scored_bars) / len(scored_bars) if scored_bars else 0
    avg_total = sum(r["score"]["total"] for r in scored_bars) / len(scored_bars) if scored_bars else 0

    logger.info(f"\n=== 填词完成 ===")
    logger.info(f"完整歌词：\n{full_lyric}")
    logger.info(f"平均协音分: {avg_tone:.3f}, 平均总分: {avg_total:.3f}")

    return {
        "full_lyric": full_lyric,
        "bars": results,
        "stats": {
            "total_bars": len(results),
            "singable_bars": len([r for r in results if not r["is_rest"]]),
            "scored_bars": len(scored_bars),
            "avg_tone_score": round(avg_tone, 4),
            "avg_total_score": round(avg_total, 4),
        }
    }


def main():
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(description="粤语填词系统")
    parser.add_argument("--input", "-i", required=True, help="输入 JSON 文件路径")
    parser.add_argument("--output", "-o", default=None, help="输出 JSON 文件路径")
    parser.add_argument("--no-polish", action="store_true", help="跳过润色阶段")
    parser.add_argument("--candidates", "-n", type=int, default=10, help="每小节候选数")

    args = parser.parse_args()

    lyric_input = LyricInput.from_json_file(args.input)
    result = run_pipeline(
        lyric_input,
        enable_polish=not args.no_polish,
        num_candidates=args.candidates,
    )

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"结果已保存到 {args.output}")
    else:
        # 输出到标准输出
        print("\n" + "="*60)
        print("粤语歌词：")
        print("="*60)
        print(result["full_lyric"])
        print("="*60)
        print(f"统计: {json.dumps(result['stats'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()

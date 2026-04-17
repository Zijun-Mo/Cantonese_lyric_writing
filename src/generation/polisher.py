"""段落级评估与迭代润色器

对已生成的完整歌词进行段落级质量评估（流畅度、完整度、语义匹配度），
根据评估反馈迭代润色，提升歌词整体连贯性和完整性。
"""

import logging
import time
from typing import List, Dict, Optional

from src.generation.glm_client import GLMClient

logger = logging.getLogger(__name__)

# 每次评估/重写的最大小节数，过大时 LLM 难以精确控制字数
_MAX_CHUNK_SIZE = 8


def _evaluate_paragraph(
    client: GLMClient,
    bar_lyrics: List[str],
    mandarin_seeds: List[str],
    theme_tags: List[str] = None,
) -> Optional[Dict]:
    """对一个段落（连续非休止小节）进行整体质量评估

    Returns:
        评估结果字典，包含 fluency/completeness/semantic_match (0-1)、
        weak_positions (问题小节索引列表)、feedback (问题描述)
    """
    display_parts = []
    for i, ly in enumerate(bar_lyrics):
        display_parts.append(f"[{i}]{ly}")
    full_display = " ".join(display_parts)

    seed_text = "，".join(s for s in mandarin_seeds if s.strip())
    if not seed_text:
        seed_text = "（无原词）"
    theme = "、".join(theme_tags) if theme_tags else "抒情"

    system = "你是专业粤语歌词评审。请客观评估歌词质量并输出JSON。"
    user = f"""请评估以下粤语歌词段落（方括号内数字为小节编号，各小节歌词连续唱出）：

{full_display}

对应普通话原词：{seed_text}
主题：{theme}

评分维度（每项1-10分）：
1. 流畅度：各小节拼接后是否通顺自然？有无生硬断裂或突兀的词语衔接？
2. 完整度：是否构成完整语句？有无半句截断、语意悬空？
3. 语义匹配：是否保留了原词的核心含义和情感意象？

请输出JSON：
{{"fluency": 整数, "completeness": 整数, "semantic_match": 整数, "weak_positions": [问题小节编号], "feedback": "具体问题描述"}}"""

    result = client.chat_json(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        max_tokens=512,
    )

    if result is None:
        return None

    try:
        def _clamp(val):
            return max(0.0, min(1.0, float(val) / 10.0))

        return {
            "fluency": _clamp(result.get("fluency", 5)),
            "completeness": _clamp(result.get("completeness", 5)),
            "semantic_match": _clamp(result.get("semantic_match", 5)),
            "weak_positions": [
                p for p in result.get("weak_positions", [])
                if isinstance(p, int) and 0 <= p < len(bar_lyrics)
            ],
            "feedback": str(result.get("feedback", "")),
        }
    except (ValueError, TypeError):
        return None


def _rewrite_paragraph(
    client: GLMClient,
    bar_lyrics: List[str],
    slot_counts: List[int],
    mandarin_seeds: List[str],
    feedback: str,
    weak_positions: List[int],
    theme_tags: List[str] = None,
) -> Optional[List[str]]:
    """根据评估反馈重写段落中的弱点小节

    Returns:
        重写后的各小节歌词列表，失败返回 None
    """
    bar_lines = []
    for i, (ly, cnt, seed) in enumerate(zip(bar_lyrics, slot_counts, mandarin_seeds)):
        mark = " ← 需改进" if i in weak_positions else ""
        seed_info = f" 原意「{seed}」" if seed.strip() else ""
        bar_lines.append(f"  [{i}] {cnt}字: 「{ly}」{seed_info}{mark}")

    bar_info = "\n".join(bar_lines)
    counts_str = "、".join(f"[{i}]={c}字" for i, c in enumerate(slot_counts))
    theme = "、".join(theme_tags) if theme_tags else "抒情"

    system = "你是顶尖粤语作词人。改善歌词的流畅度和完整性，保持字数和声调。输出严格JSON格式。"
    user = f"""请润色以下粤语歌词段落：

{bar_info}

问题反馈：{feedback}

要求：
1. 每个小节字数严格不变：{counts_str}
2. 保留核心含义和情感（主题：{theme}）
3. 重点改善标记"需改进"的小节，使整段读起来通顺完整
4. 未标记的小节可以微调衔接，但不要大改
5. 纯汉字，不要标点符号

输出JSON：{{"bars": ["小节0歌词", "小节1歌词", ...]}}
bars数组必须包含恰好{len(bar_lyrics)}项，每项必须是纯汉字且字数严格等于指定值。"""

    result = client.chat_json(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.7,
        max_tokens=1024,
    )

    if result is None:
        return None

    bars = result.get("bars", [])
    if not isinstance(bars, list) or len(bars) != len(bar_lyrics):
        logger.warning(
            f"    重写返回bar数不匹配: 期望{len(bar_lyrics)}，"
            f"实际{len(bars) if isinstance(bars, list) else 'N/A'}"
        )
        return None

    # 逐bar验证字数，不匹配的保留原词
    new_lyrics = []
    for i, (new_text, expected) in enumerate(zip(bars, slot_counts)):
        chars = ''.join(ch for ch in str(new_text) if '\u4e00' <= ch <= '\u9fff')
        if len(chars) != expected:
            new_lyrics.append(bar_lyrics[i])
        else:
            new_lyrics.append(chars)

    return new_lyrics


def iterative_polish(
    client: GLMClient,
    bar_results: Dict[int, Dict],
    score_obj,
    semantics,
    templates: List[List[int]],
    score_fn,
    theme_tags: List[str] = None,
    max_iterations: int = 2,
    quality_threshold: float = 0.70,
) -> int:
    """对全部歌词进行段落级迭代润色

    按 rest bar 将歌词分段，逐段评估流畅度/完整度/语义匹配度，
    对质量不达标的段落迭代润色。

    Args:
        client: GLM 客户端
        bar_results: bar_idx -> bar_result 字典（会被就地修改）
        score_obj: ParsedScore
        semantics: BarSemantics 列表
        templates: 声调模板列表
        score_fn: 单bar评分函数 (score_candidate)
        theme_tags: 主题标签
        max_iterations: 每段最大润色迭代次数
        quality_threshold: 三维均值低于此阈值时触发润色

    Returns:
        共改进的小节数
    """
    # 按 rest bar 分段
    paragraphs = []
    current = []
    for i, bar in enumerate(score_obj.bars):
        if bar.is_rest_bar or bar.slot_count == 0:
            if current:
                paragraphs.append(current)
                current = []
        else:
            current.append(i)
    if current:
        paragraphs.append(current)

    # 将大段落拆分为可处理的小块
    chunks = []
    for para_bars in paragraphs:
        if len(para_bars) <= _MAX_CHUNK_SIZE:
            if len(para_bars) >= 2:
                chunks.append(para_bars)
        else:
            for start in range(0, len(para_bars), _MAX_CHUNK_SIZE):
                chunk = para_bars[start:start + _MAX_CHUNK_SIZE]
                if len(chunk) >= 2:
                    chunks.append(chunk)
            logger.info(f"  大段落 (bars {para_bars[0]}-{para_bars[-1]}, {len(para_bars)}小节) "
                        f"拆分为 {len([c for c in chunks if c[0] >= para_bars[0]])} 块")

    total_improved = 0

    for chunk_idx, chunk_bars in enumerate(chunks):

        # 过滤掉 score 为 None 的 bar
        valid_bars = [bi for bi in chunk_bars if bar_results.get(bi, {}).get("score") is not None]
        if len(valid_bars) < 2:
            continue

        for iteration in range(max_iterations):
            bar_lyrics = [bar_results[bi]["best_lyric"] for bi in chunk_bars]
            slot_counts = [score_obj.bars[bi].slot_count for bi in chunk_bars]
            seeds = []
            for bi in chunk_bars:
                sem = semantics[bi] if bi < len(semantics) else None
                seeds.append(sem.raw_text.strip() if (sem and not sem.is_empty) else "")

            # 评估
            ev = _evaluate_paragraph(client, bar_lyrics, seeds, theme_tags)
            time.sleep(0.3)

            if ev is None:
                logger.warning(f"  块{chunk_idx+1} 评估失败，跳过")
                break

            avg_q = (ev["fluency"] + ev["completeness"] + ev["semantic_match"]) / 3

            if iteration == 0:
                logger.info(
                    f"  块{chunk_idx+1} (bars {chunk_bars[0]}-{chunk_bars[-1]}): "
                    f"流畅={ev['fluency']:.2f} 完整={ev['completeness']:.2f} "
                    f"语义={ev['semantic_match']:.2f} 均值={avg_q:.2f}"
                )
                if ev["feedback"]:
                    logger.info(f"    反馈: {ev['feedback'][:120]}")

            if avg_q >= quality_threshold and not ev["weak_positions"]:
                if iteration == 0:
                    logger.info(f"    质量达标，跳过润色")
                break

            # 重写
            weak_set = set(ev["weak_positions"])
            new_lyrics = _rewrite_paragraph(
                client, bar_lyrics, slot_counts, seeds,
                ev["feedback"], weak_set, theme_tags,
            )
            time.sleep(0.3)

            if new_lyrics is None:
                logger.warning(f"    润色返回无效，保留原词")
                break

            # 逐bar比较，接受改进、拒绝协音退步
            improved = 0
            for pos, bi in enumerate(chunk_bars):
                old_ly = bar_results[bi]["best_lyric"]
                new_ly = new_lyrics[pos]
                if new_ly == old_ly:
                    continue

                old_sc = bar_results[bi].get("score")
                if old_sc is None:
                    continue

                tmpl = templates[bi] if bi < len(templates) else []
                bar_obj = score_obj.bars[bi]
                beat_strs = [n.beat_strength for n in bar_obj.singable_notes]
                sem = semantics[bi] if bi < len(semantics) else None
                core_imgs = sem.core_images() if sem and not sem.is_empty else []
                must_kw = [s.word for s in sem.slots if s.weight == 'must_keep'] if sem and not sem.is_empty else []

                new_sc = score_fn(
                    new_ly, tmpl,
                    beat_strengths=beat_strs,
                    core_images=core_imgs,
                    must_keep=must_kw,
                    target_char_count=slot_counts[pos],
                )

                # 协音退步超过 0.15 则拒绝该bar的改动
                if new_sc["tone"] < old_sc["tone"] - 0.15:
                    continue

                bar_results[bi]["best_lyric"] = new_ly
                bar_results[bi]["score"] = new_sc
                improved += 1
                logger.info(
                    f"    小节{bi}: 「{old_ly}」→「{new_ly}」"
                    f" (总分 {old_sc['total']:.2f}→{new_sc['total']:.2f})"
                )

            total_improved += improved
            if improved > 0:
                logger.info(f"    迭代{iteration+1}: 改进{improved}个小节")
            else:
                logger.info(f"    迭代{iteration+1}: 未产生有效改进")
                break

    return total_improved

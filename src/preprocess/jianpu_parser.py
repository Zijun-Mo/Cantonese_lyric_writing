"""简谱解析器：将简谱字符串解析为结构化序列"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Note:
    """单个音符"""
    pitch: int              # 1-7, 0 表示休止符
    octave: int = 0         # 0=中音, 1=高八度, -1=低八度
    duration: float = 1.0   # 时值（拍数）
    is_rest: bool = False   # 是否休止符
    bar_index: int = 0      # 所属小节编号
    position_in_bar: int = 0  # 在小节内的序号
    beat_strength: str = "weak"  # "strong" / "medium" / "weak"


@dataclass
class Bar:
    """一个小节"""
    index: int
    notes: List[Note] = field(default_factory=list)

    @property
    def is_rest_bar(self) -> bool:
        """是否纯伴奏小节（所有音符都是休止符）"""
        return all(n.is_rest for n in self.notes)

    @property
    def singable_notes(self) -> List[Note]:
        """可唱音符（排除休止符）"""
        return [n for n in self.notes if not n.is_rest]

    @property
    def slot_count(self) -> int:
        """可唱字位数（排除休止符，延音已合并到前一个音符的 duration 中）"""
        return len(self.singable_notes)


@dataclass
class ParsedScore:
    """解析后的完整曲谱"""
    bars: List[Bar] = field(default_factory=list)

    @property
    def total_slots(self) -> int:
        return sum(b.slot_count for b in self.bars)

    def slot_counts(self) -> List[int]:
        """每小节字位数列表"""
        return [b.slot_count for b in self.bars]

    def contour(self) -> List[str]:
        """整曲走向模板：相邻可唱音之间的关系"""
        singable = []
        for bar in self.bars:
            singable.extend(bar.singable_notes)
        result = []
        for i in range(1, len(singable)):
            prev_abs = singable[i-1].pitch + singable[i-1].octave * 7
            curr_abs = singable[i].pitch + singable[i].octave * 7
            diff = curr_abs - prev_abs
            if diff == 0:
                result.append("flat")
            elif 1 <= diff <= 2:
                result.append("up")
            elif -2 <= diff <= -1:
                result.append("down")
            elif diff > 2:
                result.append("leap_up")
            else:
                result.append("leap_down")
        return result

    def bar_contours(self) -> List[List[str]]:
        """每小节内部走向"""
        result = []
        for bar in self.bars:
            notes = bar.singable_notes
            contour = []
            for i in range(1, len(notes)):
                prev_abs = notes[i-1].pitch + notes[i-1].octave * 7
                curr_abs = notes[i].pitch + notes[i].octave * 7
                diff = curr_abs - prev_abs
                if diff == 0:
                    contour.append("flat")
                elif 1 <= diff <= 2:
                    contour.append("up")
                elif -2 <= diff <= -1:
                    contour.append("down")
                elif diff > 2:
                    contour.append("leap_up")
                else:
                    contour.append("leap_down")
            result.append(contour)
        return result


# 用于匹配一个音符 token 的正则
# 支持: ^1, ,5, 3, 3., 3-, 3_, 3=, 以及独立的 -
_NOTE_PATTERN = re.compile(
    r'(\^|,)?'       # 可选的高/低八度前缀
    r'([0-7])'        # 音高数字
    r'([._=]*)'       # 可选的后缀修饰符（附点、八分、十六分）
    r'(-*)'           # 可选的紧跟延音符号
)


def _tokenize_bar(bar_str: str) -> List[str]:
    """将小节字符串分割成 token 列表"""
    tokens = bar_str.strip().split()
    return [t for t in tokens if t]


def _strip_slur_markers(token: str) -> tuple[str, int, int]:
    """去掉 token 两侧的连音括号，并返回开始/结束括号数量"""
    start_count = 0
    end_count = 0
    inner = token.strip()

    while inner.startswith('('):
        start_count += 1
        inner = inner[1:]

    while inner.endswith(')'):
        end_count += 1
        inner = inner[:-1]

    return inner, start_count, end_count


def _parse_duration(suffix: str, tie_dashes: str) -> float:
    """根据后缀计算时值"""
    # 基础时值
    if '=' in suffix:
        base = 0.25  # 十六分
    elif '_' in suffix:
        base = 0.5   # 八分
    else:
        base = 1.0   # 四分

    # 附点
    if '.' in suffix:
        base *= 1.5

    # 紧跟的延音
    base += len(tie_dashes) * 1.0

    return base


def parse_jianpu(jianpu_str: str) -> ParsedScore:
    """解析简谱字符串为结构化表示

    Args:
        jianpu_str: 完整简谱字符串，小节用 | 分隔

    Returns:
        ParsedScore 对象
    """
    bar_strings = jianpu_str.split('|')
    score = ParsedScore()
    slur_depth = 0
    slur_note: Optional[Note] = None

    for bar_idx, bar_str in enumerate(bar_strings):
        bar = Bar(index=bar_idx)
        tokens = _tokenize_bar(bar_str)

        # 先解析所有 token，然后处理独立的延音 -
        pending_notes: List[Note] = []
        last_pitched_note: Optional[Note] = None

        for token in tokens:
            token, slur_starts, slur_ends = _strip_slur_markers(token)
            if slur_starts:
                slur_depth += slur_starts

            in_slur = slur_depth > 0

            # 独立的延音符号（一个或多个 -）
            if re.fullmatch(r'-+', token):
                target_note = slur_note if in_slur and slur_note is not None else last_pitched_note
                if target_note is not None:
                    target_note.duration += len(token) * 1.0
                if slur_ends:
                    slur_depth = max(0, slur_depth - slur_ends)
                    if slur_depth == 0:
                        slur_note = None
                continue

            m = _NOTE_PATTERN.fullmatch(token)
            if not m:
                if slur_ends:
                    slur_depth = max(0, slur_depth - slur_ends)
                    if slur_depth == 0:
                        slur_note = None
                continue

            prefix, digit, suffix, tie_dashes = m.groups()
            pitch = int(digit)
            duration = _parse_duration(suffix, tie_dashes)

            if pitch == 0:
                if in_slur and slur_note is not None:
                    slur_note.duration += duration
                else:
                    note = Note(
                        pitch=0,
                        duration=duration,
                        is_rest=True,
                        bar_index=bar_idx,
                        position_in_bar=len(pending_notes),
                    )
                    pending_notes.append(note)
                    # 休止符不影响 last_pitched_note
                if slur_ends:
                    slur_depth = max(0, slur_depth - slur_ends)
                    if slur_depth == 0:
                        slur_note = None
                continue

            # 八度
            octave = 0
            if prefix == '^':
                octave = 1
            elif prefix == ',':
                octave = -1

            if in_slur and slur_note is not None:
                slur_note.duration += duration
            else:
                note = Note(
                    pitch=pitch,
                    octave=octave,
                    duration=duration,
                    is_rest=False,
                    bar_index=bar_idx,
                    position_in_bar=len(pending_notes),
                )
                pending_notes.append(note)
                last_pitched_note = note
                if in_slur:
                    slur_note = note

            if slur_ends:
                slur_depth = max(0, slur_depth - slur_ends)
                if slur_depth == 0:
                    slur_note = None

        # 推断强弱拍（简化：小节内第一个音符为强拍，第三个为中强拍）
        beat_pos = 0
        for note in pending_notes:
            if beat_pos == 0:
                note.beat_strength = "strong"
            elif beat_pos == 2:
                note.beat_strength = "medium"
            else:
                note.beat_strength = "weak"
            beat_pos += 1

        bar.notes = pending_notes
        score.bars.append(bar)

    return score


def get_singable_pitches_for_bar(bar: Bar) -> List[dict]:
    """为小节中每个可唱音位返回信息字典"""
    result = []
    for note in bar.singable_notes:
        abs_pitch = note.pitch + note.octave * 7
        result.append({
            "pitch": note.pitch,
            "octave": note.octave,
            "abs_pitch": abs_pitch,
            "duration": note.duration,
            "beat_strength": note.beat_strength,
        })
    return result

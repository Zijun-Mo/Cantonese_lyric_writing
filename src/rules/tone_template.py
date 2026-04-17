"""简谱 → 0243 模板转换

将旋律音高序列转换为每个字位的目标 0243 类别。

基本映射思路（简谱音高 → 粤语声调高低 → 0243）：
- 高音位置（5,6,7,^1...）→ 偏好高调字 → 0243 中的 3（阴平，高平）
- 中高音（3,4）→ 偏好中高调 → 0243 中的 4（阴上/阴去，中高）
- 中低音（2）→ 偏好中低调 → 0243 中的 2（阳去，中低）
- 低音（1,,7,,6...）→ 偏好低调 → 0243 中的 0（阳平/阳上，低平）
"""

from typing import List, Optional
from src.preprocess.jianpu_parser import Bar, ParsedScore, Note


def _abs_pitch(note: Note) -> int:
    """计算音符的绝对音高（用于比较）"""
    return note.pitch + note.octave * 7


def _pitch_to_0243(abs_pitch: int) -> int:
    """将绝对音高映射到目标 0243 类别

    映射策略：
    - abs_pitch >= 10 (高八度 3+): → 3 (高)
    - abs_pitch 7-9 (高八度 1-2, 中音 7): → 3 (高)
    - abs_pitch 5-6 (中音 5-6): → 4 (中高) 或 3 (高)
    - abs_pitch 3-4 (中音 3-4): → 4 (中高)
    - abs_pitch 2 (中音 2): → 2 (中低)
    - abs_pitch <= 1 (中音 1, 低八度): → 0 (低)
    """
    if abs_pitch >= 8:
        return 3   # 高
    elif abs_pitch >= 5:
        return 3   # 中高偏高 — 粤语阴平
    elif abs_pitch >= 4:
        return 4   # 中高
    elif abs_pitch >= 3:
        return 4   # 中
    elif abs_pitch >= 2:
        return 2   # 中低
    else:
        return 0   # 低


def melody_to_0243_template(bar: Bar) -> List[int]:
    """将小节旋律转换为 0243 模板

    Returns:
        每个可唱字位的目标 0243 类别列表
    """
    singable = bar.singable_notes
    if not singable:
        return []

    template = []
    for note in singable:
        ap = _abs_pitch(note)
        target = _pitch_to_0243(ap)
        template.append(target)

    return template


def score_to_0243_templates(score: ParsedScore) -> List[List[int]]:
    """将整首曲谱转换为每小节的 0243 模板"""
    return [melody_to_0243_template(bar) for bar in score.bars]


def melody_to_contour(bar: Bar) -> List[str]:
    """计算小节内旋律走向"""
    singable = bar.singable_notes
    if len(singable) < 2:
        return []
    contour = []
    for i in range(1, len(singable)):
        prev = _abs_pitch(singable[i-1])
        curr = _abs_pitch(singable[i])
        diff = curr - prev
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
    return contour

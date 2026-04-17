"""输入数据结构定义"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class LyricInput:
    """粤语填词系统输入结构

    Attributes:
        jianpu: 简谱字符串，用 | 按小节分隔
        mandarin_seed: 普通话语义种子，用 | 按小节分隔，| 数量须与 jianpu 一致
        theme_tags: 主题/情绪标签
        style_tags: 风格标签
    """
    jianpu: str
    mandarin_seed: str
    theme_tags: List[str] = field(default_factory=list)
    style_tags: List[str] = field(default_factory=list)

    def __post_init__(self):
        jianpu_bars = self.jianpu.count('|')
        seed_bars = self.mandarin_seed.count('|')
        if jianpu_bars != seed_bars:
            raise ValueError(
                f"jianpu 与 mandarin_seed 的小节数（| 数量）不一致："
                f"jianpu 有 {jianpu_bars} 个 |，mandarin_seed 有 {seed_bars} 个 |"
            )

    @classmethod
    def from_json_file(cls, path: str) -> "LyricInput":
        import json
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls(
            jianpu=data['jianpu'],
            mandarin_seed=data['mandarin_seed'],
            theme_tags=data.get('theme_tags', []),
            style_tags=data.get('style_tags', []),
        )

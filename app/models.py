from dataclasses import dataclass

@dataclass(frozen=True)
class Trend:
    name: str
    rank: int
    source: str = 'x'

@dataclass(frozen=True)
class Draft:
    trend: str
    angle: str
    text: str
    generated_at: str

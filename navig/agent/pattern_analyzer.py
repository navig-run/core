from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable


@dataclass
class ScoredPattern:
    sequence: tuple[str, ...]
    occurrences: int
    score: float


class PatternAnalyzer:
    def __init__(self, min_occurrences: int = 2, max_results: int = 20):
        self.min_occurrences = int(min_occurrences)
        self.max_results = int(max_results)

    def score_by_frequency(self, records: Iterable[object]) -> list[ScoredPattern]:
        commands: list[str] = []
        for rec in records:
            command = getattr(rec, "command", None)
            if isinstance(command, str) and command.strip():
                commands.append(command.strip())

        counts = Counter(commands)
        scored = [
            ScoredPattern(sequence=(cmd,), occurrences=n, score=float(n))
            for cmd, n in counts.items()
            if n >= self.min_occurrences
        ]
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[: self.max_results]

"""Competency: a running summary of evidence per (person, area).

An exponentially weighted moving average of signed evidence, kept in [−1, 1] and
shown as 0–100. Neutral evidence (signal 0) counts but does not move the score.
This is a *projection* for people to read; nothing in the system selects, assigns
or judges on it (contract T1) — those are decisions, made at gates.
"""

from dataclasses import dataclass

ALPHA = 0.25  # how fast new evidence displaces old


@dataclass(frozen=True, slots=True)
class Competency:
    raw: float = 0.0  # EWMA in [-1, 1]
    evidence_count: int = 0
    positive: int = 0
    negative: int = 0

    @property
    def score(self) -> int:
        """0–100; 50 means no signed evidence yet."""
        return round((self.raw + 1.0) / 2.0 * 100)

    @property
    def rated(self) -> bool:
        return self.positive + self.negative > 0

    def absorb(self, signal: int, weight: float) -> "Competency":
        raw = self.raw
        if signal != 0:
            raw = raw + ALPHA * weight * (float(signal) - raw)
            raw = max(-1.0, min(1.0, raw))
        return Competency(
            raw,
            self.evidence_count + 1,
            self.positive + (signal > 0),
            self.negative + (signal < 0),
        )

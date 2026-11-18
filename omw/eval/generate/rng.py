"""A keyed generator: every draw is a hash of the seed and the keys it is asked for, so a number depends on
*what* it is (a campaign, a day, a field) and never on the order the generator happened to run in. The
values are the same on every platform and Python version."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any


class Rng:
    def __init__(self, seed: int) -> None:
        self.seed = seed

    def unit(self, *keys: Any) -> float:
        """A float in [0, 1) for these keys."""
        text = "|".join(str(key) for key in (self.seed, *keys))
        return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big") / 2**64

    def between(self, low: float, high: float, *keys: Any) -> float:
        return low + (high - low) * self.unit(*keys)

    def integer(self, low: int, high: int, *keys: Any) -> int:
        """An int in [low, high], both included."""
        return low + int(self.unit(*keys) * (high - low + 1))

    def chance(self, probability: float, *keys: Any) -> bool:
        return self.unit(*keys) < probability

    def pick[T](self, options: Sequence[T], *keys: Any) -> T:
        return options[int(self.unit(*keys) * len(options))]

    def weighted[T](self, options: Sequence[tuple[T, float]], *keys: Any) -> T:
        total = sum(weight for _, weight in options)
        mark = self.unit(*keys) * total
        for option, weight in options:
            mark -= weight
            if mark < 0:
                return option
        return options[-1][0]

    def jitter(self, weights: Sequence[float], spread: float, *keys: Any) -> list[float]:
        """The weights, each moved by up to ``spread`` (0.2 = 20%) either way."""
        return [w * self.between(1 - spread, 1 + spread, *keys, i) for i, w in enumerate(weights)]


def split(total: int, weights: Sequence[float]) -> list[int]:
    """``total`` shared out in proportion to ``weights`` as whole numbers that add up to it exactly
    (largest remainder), so a breakdown grain sums to its parent."""
    if total < 0:
        raise ValueError("cannot split a negative total")
    scale = sum(weights)
    if not weights or scale <= 0:
        raise ValueError("weights must be positive")
    exact = [total * weight / scale for weight in weights]
    parts = [int(value) for value in exact]
    order = sorted(range(len(weights)), key=lambda i: (exact[i] - parts[i], -i), reverse=True)
    for i in order[: total - sum(parts)]:
        parts[i] += 1
    return parts

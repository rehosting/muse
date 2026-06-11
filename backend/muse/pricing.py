"""Model pricing (USD per million tokens) for cost estimation.

Values are list prices as of mid-2026; cacheWrite uses the 5-minute ephemeral
rate (the common case). Matched by longest model-id prefix so dated suffixes
(e.g. claude-haiku-4-5-20251001) resolve to their base rate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_write: float
    cache_read: float


# $ per million tokens.
_PRICING_PER_MTOK: dict[str, Price] = {
    "claude-opus-4-8": Price(5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-7": Price(5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-6": Price(5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-5": Price(5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-1": Price(15.00, 75.00, 18.75, 1.50),
    "claude-opus-4": Price(15.00, 75.00, 18.75, 1.50),
    "claude-sonnet-4-6": Price(3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-4-5": Price(3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-4": Price(3.00, 15.00, 3.75, 0.30),
    "claude-haiku-4-5": Price(1.00, 5.00, 1.25, 0.10),
    "claude-haiku-3-5": Price(0.80, 4.00, 1.00, 0.08),
}

_DEFAULT = Price(5.00, 25.00, 6.25, 0.50)  # assume current-gen Opus if unknown


def price_for(model: str | None) -> Price:
    if not model:
        return _DEFAULT
    best: tuple[int, Price] | None = None
    for key, price in _PRICING_PER_MTOK.items():
        if model.startswith(key) and (best is None or len(key) > best[0]):
            best = (len(key), price)
    return best[1] if best else _DEFAULT


def cost_usd(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
) -> float:
    p = price_for(model)
    return (
        input_tokens * p.input
        + output_tokens * p.output
        + cache_creation_tokens * p.cache_write
        + cache_read_tokens * p.cache_read
    ) / 1_000_000

"""Versioned price list. Money is integers in micro-units of the provider's billing
currency (1 000 000 micro = 1.00); nothing here is ever a float.

Sources (2026-11-07): Anthropic first-party API rates (USD); DeepSeek platform page
(CNY, *peak* rates — off-peak is half; we estimate on the upper bound). Unknown
models get no estimate rather than a wrong one.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from eidolon.agent.providers.base import Usage

PRICING_VERSION = "2026-11-07"


@dataclass(frozen=True, slots=True)
class Price:
    currency: str
    input_per_m: Decimal  # per 1M uncached input tokens
    output_per_m: Decimal
    cache_read_per_m: Decimal
    cache_write_per_m: Decimal


def _p(currency, inp, out, read, write) -> Price:
    return Price(currency, Decimal(inp), Decimal(out), Decimal(read), Decimal(write))


PRICES: dict[str, Price] = {
    # Anthropic (USD / 1M tokens); cache write = 1.25x input, read = 0.1x input
    "claude-fable-5-1": _p("USD", "10.00", "50.00", "0.25", "12.50"),
    "claude-opus-5-5": _p("USD", "4.00", "20.00", "0.20", "5.00"),
    "claude-opus-5": _p("USD", "5.00", "25.00", "0.50", "6.25"),
    "claude-sonnet-5-5": _p("USD", "2.00", "10.00", "0.20", "2.50"),
    "claude-sonnet-5": _p("USD", "2.00", "10.00", "0.20", "2.50"),
    "claude-haiku-5-5": _p("USD", "0.10", "0.50", "0.01", "0.125"),
    # DeepSeek (CNY / 1M tokens, peak); cache hit price is the read rate; no write charge
    "deepseek-flash": _p("CNY", "2.00", "8.00", "0.04", "0"),
    "deepseek-v4-flash": _p("CNY", "2.00", "8.00", "0.04", "0"),
    "deepseek-v4-pro": _p("CNY", "9.00", "27.00", "0.30", "0"),
}

_MICRO = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class CostEstimate:
    amount_micro: int
    currency: str
    pricing_version: str = PRICING_VERSION

    def as_dict(self) -> dict[str, int | str]:
        return {
            "amount_micro": self.amount_micro,
            "currency": self.currency,
            "pricing_version": self.pricing_version,
        }


def estimate(model: str, usage: Usage) -> CostEstimate | None:
    price = PRICES.get(model)
    if price is None:
        return None
    uncached = max(usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens, 0)
    total = (
        Decimal(uncached) * price.input_per_m
        + Decimal(usage.cache_read_tokens) * price.cache_read_per_m
        + Decimal(usage.cache_write_tokens) * price.cache_write_per_m
        + Decimal(usage.output_tokens) * price.output_per_m
    ) / _MICRO  # per-token price in currency units
    micro = (total * _MICRO).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return CostEstimate(int(micro), price.currency)

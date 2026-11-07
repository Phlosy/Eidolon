"""Versioned book rates between provider billing currencies and company accounting
currencies. These are *book* rates, fixed per version so that every booking is
reproducible from the facts; they are not market quotes. Unknown pairs convert to
nothing rather than to a guess, and the cost is recorded unbooked.
"""

from decimal import ROUND_HALF_UP, Decimal

FX_VERSION = "2026-11-07"

# (from, to) -> units of ``to`` per 1 unit of ``from``
RATES: dict[tuple[str, str], Decimal] = {
    ("USD", "CNY"): Decimal("7.10"),
    ("CNY", "USD"): Decimal("0.1408"),
}


def rate(from_currency: str, to_currency: str) -> Decimal | None:
    if from_currency == to_currency:
        return Decimal(1)
    return RATES.get((from_currency, to_currency))


def convert(amount_micro: int, from_currency: str, to_currency: str) -> tuple[int, Decimal] | None:
    """Micro-units of ``from_currency`` → micro-units of ``to_currency``, half-up
    rounding, plus the rate used. ``None`` when no book rate exists."""
    r = rate(from_currency, to_currency)
    if r is None:
        return None
    converted = (Decimal(amount_micro) * r).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(converted), r

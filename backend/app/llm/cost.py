"""Deterministic token estimation and a versioned, integer-microunit price table.

Currency is **GBP**; costs are integer microunits (1 GBP = 1_000_000 microunits) so no
floating-point money ever enters persistence. The mock and local providers are
zero-cost; hosted pricing is optional configuration. Unknown models return an
``UNKNOWN`` status rather than a fabricated figure.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.enums import CostStatus, TokenUsageSource
from app.llm.models import CostEstimate, TokenUsage

# One GBP expressed in the integer unit used everywhere for money in the model layer.
MICROUNITS_PER_GBP = 1_000_000

# Rough deterministic token heuristic: ~4 characters per token. Used only when a
# provider omits usage; always labelled ESTIMATED so it is never mistaken for exact.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """A deterministic, provider-independent token estimate for ``text``."""
    if not text:
        return 0
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def estimate_usage(input_text: str, output_text: str) -> TokenUsage:
    """Estimate token usage from raw text lengths, labelled ESTIMATED."""
    return TokenUsage(
        input_tokens=estimate_tokens(input_text),
        output_tokens=estimate_tokens(output_text),
        source=TokenUsageSource.ESTIMATED,
    )


@dataclass(frozen=True)
class ModelPrice:
    """Per-million-token input/output pricing in GBP microunits for one model."""

    input_microunits_per_million: int
    output_microunits_per_million: int


@dataclass(frozen=True)
class PriceTable:
    """A named, versioned pricing configuration. Not spread across provider classes."""

    version: str
    prices: dict[str, ModelPrice]

    def price_for(self, model: str) -> ModelPrice | None:
        return self.prices.get(model)


# Versioned price table. Hosted prices are indicative estimates for cost *labelling*
# only; they are not billing figures and are clearly reported as ESTIMATED. Extend by
# adding a new version rather than mutating an existing one.
PRICE_TABLE_2026_07 = PriceTable(
    version="price-table-2026-07",
    prices={
        # Indicative hosted OpenAI-compatible pricing (GBP microunits per 1M tokens).
        "gpt-4o-mini": ModelPrice(120_000, 480_000),
        "gpt-4o": ModelPrice(2_000_000, 8_000_000),
    },
)

_PRICE_TABLES: dict[str, PriceTable] = {
    PRICE_TABLE_2026_07.version: PRICE_TABLE_2026_07
}


def get_price_table(version: str) -> PriceTable:
    """Return the price table for ``version`` or raise if it does not exist."""
    try:
        return _PRICE_TABLES[version]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unknown price table version: {version!r}") from exc


def zero_cost(*, mock: bool) -> CostEstimate:
    """A zero cost estimate for the mock (billing-free) or a local model."""
    return CostEstimate(
        microunits=0,
        status=CostStatus.ZERO_MOCK if mock else CostStatus.ZERO_LOCAL,
    )


def estimate_cost(
    *,
    model: str,
    usage: TokenUsage,
    price_table_version: str,
) -> CostEstimate:
    """Estimate GBP-microunit cost for a hosted model, or UNKNOWN if unpriced."""
    table = get_price_table(price_table_version)
    price = table.price_for(model)
    if price is None:
        return CostEstimate(
            status=CostStatus.UNKNOWN, price_table_version=table.version
        )
    micro = (
        usage.input_tokens * price.input_microunits_per_million
        + usage.output_tokens * price.output_microunits_per_million
    ) // 1_000_000
    return CostEstimate(
        microunits=micro,
        status=CostStatus.ESTIMATED,
        price_table_version=table.version,
    )

"""Frozen business thresholds (from the MVP specification).

Centralised so the numbers appear once and are easy to audit and test.
"""

from __future__ import annotations

# Returns: 30 calendar days from delivery, inclusive (day 0 = delivery day).
RETURN_WINDOW_DAYS = 30

# Damaged/incorrect reporting window (also 30 calendar days).
REMEDY_WINDOW_DAYS = 30

# Refund risk bands, in pennies.
REFUND_MEDIUM_MAX_PENCE = 5_000  # <= GBP 50.00 is Medium risk
REFUND_HIGH_MAX_PENCE = 25_000  # GBP 50.01 to 250.00 is High risk; above is Blocked

# Delivery-delay tiers, in whole calendar days late.
DELAY_MINOR_MAX_DAYS = 3  # 1 to 3 days late
DELAY_SIGNIFICANT_MAX_DAYS = 9  # 4 to 9 days late; >= 10 is severe

# Confidence thresholds for routing.
CONFIDENCE_CONTINUE = 0.75  # >= continue
CONFIDENCE_AGENT_REVIEW = 0.50  # >= agent review, < escalate

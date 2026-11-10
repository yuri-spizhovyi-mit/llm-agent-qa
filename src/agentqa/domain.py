"""The accounts-payable domain: vendors, invoices and payments (synthetic data only).

Money is always an exact integer of USDC base units (6 decimals), never a float. The model
sees and writes decimal strings such as "1250.50"; `parse_usdc` turns them into base units
exactly or refuses them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

USDC = 10**6  # SettlementToken has 6 decimals
_AMOUNT = re.compile(r"^(\d{1,12})(?:\.(\d{1,6}))?$")


class AmountError(ValueError):
    """The text is not a valid positive USDC amount."""


def parse_usdc(text: str) -> int:
    """"1250.5" -> 1_250_500_000 base units. Exact; no floats anywhere."""
    match = _AMOUNT.fullmatch(str(text).strip())
    if not match:
        raise AmountError(f"not a USDC amount: {text!r} (use digits, up to 6 decimals, e.g. 1250.50)")
    whole, fraction = match.group(1), match.group(2) or ""
    value = int(whole) * USDC + int(fraction.ljust(6, "0") or "0")
    if value == 0:
        raise AmountError("amount must be greater than zero")
    return value


def format_usdc(base_units: int) -> str:
    """1_250_500_000 -> "1250.50". Always two or more decimals, exact."""
    whole, fraction = divmod(base_units, USDC)
    text = f"{fraction:06d}".rstrip("0").ljust(2, "0")
    return f"{whole}.{text}"


class VendorStatus(str, Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"  # e.g. sanctions or fraud review: never pay


@dataclass(frozen=True)
class Vendor:
    vendor_id: str
    name: str
    payout_address: str  # the ONLY address this vendor is ever paid at
    status: VendorStatus = VendorStatus.ACTIVE


class InvoiceStatus(str, Enum):
    OPEN = "open"          # can be paid
    PAID = "paid"          # a payment for it was confirmed on chain
    DISPUTED = "disputed"  # on hold: never pay


@dataclass
class Invoice:
    invoice_id: str
    vendor_id: str
    amount: int            # base units
    due_date: str          # ISO date
    status: InvoiceStatus
    body: str              # free text from the vendor's e-mail: UNTRUSTED (attacks hide here)


class PaymentStatus(str, Enum):
    PROPOSED = "proposed"    # the agent proposed it; waiting for a human
    APPROVED = "approved"    # a human approved; handed to the transfer API
    REJECTED = "rejected"    # a human or the policy layer refused it
    CONFIRMED = "confirmed"  # the transfer is confirmed on chain
    FAILED = "failed"        # the transfer failed on chain


@dataclass
class Payment:
    payment_id: str
    invoice_id: str
    vendor_id: str
    pay_to: str
    amount: int
    status: PaymentStatus
    proposed_by: str = "agent"
    decided_by: str | None = None
    transfer_id: str | None = None   # the settlement transfer API's id, once approved
    history: list[str] = field(default_factory=list)

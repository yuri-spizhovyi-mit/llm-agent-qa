"""The policy layer: rules enforced in code, whatever the model asks for.

Every rule is checked when the agent *proposes* a payment, and again when a human
*approves* it (the world may have changed in between: the vendor blocked, the invoice
disputed, the treasury drained). A violation is a typed result, not a string, so tests and
evals can count exactly which rule stopped which attempt.

The prompt explains these rules to the model; this module guarantees them (CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from web3 import Web3

from .domain import Invoice, InvoiceStatus, Payment, PaymentStatus, Vendor, VendorStatus, format_usdc

#: Largest single payment the policy allows, in base units (50,000 USDC). Larger
#: amounts are refused outright; they are for a human process, not the agent (AQ-D2).
PER_PAYMENT_LIMIT = 50_000 * 10**6

#: Two invoices from one vendor for the same amount, due this close together, are treated as
#: a possible duplicate ("re-issued" invoices, F-06). Monthly invoices (~30 days) stay payable.
DUPLICATE_WINDOW_DAYS = 25


class Rule(str, Enum):
    UNKNOWN_INVOICE = "unknown_invoice"
    UNKNOWN_VENDOR = "unknown_vendor"
    VENDOR_BLOCKED = "vendor_blocked"
    INVOICE_NOT_OPEN = "invoice_not_open"            # paid or disputed
    ALREADY_PROPOSED = "already_proposed"            # duplicate payment
    PAYEE_NOT_REGISTERED = "payee_not_registered"    # not the vendor's registered address
    AMOUNT_MISMATCH = "amount_mismatch"              # not exactly the invoice amount
    POSSIBLE_DUPLICATE = "possible_duplicate"        # same vendor and amount as a paid / in-progress invoice
    OVER_LIMIT = "over_limit"
    INSUFFICIENT_FUNDS = "insufficient_funds"


@dataclass(frozen=True)
class Violation:
    rule: Rule
    detail: str

    def as_dict(self) -> dict:
        return {"rule": self.rule.value, "detail": self.detail}


def check_payment(
    *,
    invoice: Invoice | None,
    vendor: Vendor | None,
    pay_to: str,
    amount: int,
    open_payments: list[Payment],
    treasury_balance: int,
    invoices: dict[str, Invoice] | None = None,
) -> list[Violation]:
    """Every rule that the payment breaks; an empty list means it may proceed."""
    if invoice is None:
        return [Violation(Rule.UNKNOWN_INVOICE, "no such invoice")]
    if vendor is None:
        return [Violation(Rule.UNKNOWN_VENDOR, f"invoice names vendor {invoice.vendor_id}, which is not registered")]
    found: list[Violation] = []
    if vendor.status is VendorStatus.BLOCKED:
        found.append(Violation(Rule.VENDOR_BLOCKED, f"vendor {vendor.vendor_id} is blocked"))
    if invoice.status is not InvoiceStatus.OPEN:
        found.append(Violation(Rule.INVOICE_NOT_OPEN, f"invoice is {invoice.status.value}"))
    if any(p.invoice_id == invoice.invoice_id for p in open_payments):
        found.append(Violation(Rule.ALREADY_PROPOSED, "a payment for this invoice is already in progress or done"))
    if not Web3.is_address(pay_to) or Web3.to_checksum_address(pay_to) != vendor.payout_address:
        found.append(Violation(
            Rule.PAYEE_NOT_REGISTERED,
            f"{vendor.vendor_id} is paid only at its registered address; bank-detail changes go "
            "through vendor onboarding, never through an invoice",
        ))
    if amount != invoice.amount:
        found.append(Violation(
            Rule.AMOUNT_MISMATCH, f"invoice amount is {format_usdc(invoice.amount)}, not {format_usdc(amount)}",
        ))
    for other in _possible_duplicates(invoice, invoices or {}, open_payments):
        found.append(Violation(
            Rule.POSSIBLE_DUPLICATE,
            f"same vendor and amount as {other.invoice_id}, which is already paid or in progress; "
            "a re-issued invoice needs a person to check it",
        ))
    if amount > PER_PAYMENT_LIMIT:
        found.append(Violation(Rule.OVER_LIMIT, f"over the {format_usdc(PER_PAYMENT_LIMIT)} per-payment limit"))
    if amount > treasury_balance:
        found.append(Violation(Rule.INSUFFICIENT_FUNDS, f"treasury holds {format_usdc(treasury_balance)}"))
    return found


def _possible_duplicates(invoice: Invoice, invoices: dict[str, Invoice], open_payments: list[Payment]) -> list[Invoice]:
    in_progress = {p.invoice_id for p in open_payments}
    due = date.fromisoformat(invoice.due_date)
    return [other for other in invoices.values()
            if other.invoice_id != invoice.invoice_id and other.vendor_id == invoice.vendor_id
            and other.amount == invoice.amount
            and abs((date.fromisoformat(other.due_date) - due).days) <= DUPLICATE_WINDOW_DAYS
            and (other.status is InvoiceStatus.PAID or other.invoice_id in in_progress)]


#: Payment states that count as "a payment for this invoice exists" for ALREADY_PROPOSED.
LIVE_STATES = {PaymentStatus.PROPOSED, PaymentStatus.APPROVED, PaymentStatus.CONFIRMED}

"""TOOL-01..TOOL-09: the tools and the policy layer, with no model involved.

These are the guarantees the agent stands on. Every later eval assumes them: if a tool
returned a wrong amount or the policy let a bad payment through, an eval would score the
model for a bug that isn't the model's.
"""

from __future__ import annotations

import json

import pytest

from agentqa import tools
from agentqa.domain import USDC, AmountError, InvoiceStatus, PaymentStatus, VendorStatus, format_usdc, parse_usdc
from agentqa.policy import Rule
from agentqa.scenarios import ATTACKER
from agentqa.tools import ToolLog, execute


def call(world, log, name, **args):
    result = execute(world, log, name, args)
    return json.loads(result.content), result.is_error


def northwind(world):
    return world.vendors["V-NORTHWIND"].payout_address


# ---- TOOL-01: reads ------------------------------------------------------------------

@pytest.mark.req("TOOL-01")
def test_read_tools_return_exact_data(world):
    """TOOL-01: amounts come back as exact decimal strings, filters work, and the vendor's
    message is only visible through get_invoice, labelled as vendor_message."""
    log = ToolLog()
    listed, err = call(world, log, "list_invoices", status="open")
    assert not err
    assert [i["invoice_id"] for i in listed["invoices"]] == ["INV-101", "INV-102", "INV-103", "INV-106", "INV-107"]
    assert listed["invoices"][0] == {"invoice_id": "INV-101", "vendor_id": "V-NORTHWIND",
                                     "amount_usdc": "1250.50", "due_date": "2026-11-15", "status": "open"}
    assert all("vendor_message" not in i for i in listed["invoices"])

    invoice, _ = call(world, log, "get_invoice", invoice_id="INV-103")
    assert invoice["amount_usdc"] == "312.75" and "smart meters" in invoice["vendor_message"]
    vendor, _ = call(world, log, "get_vendor", vendor_id="V-NORTHWIND")
    assert vendor == {"vendor_id": "V-NORTHWIND", "name": "Northwind Energy Supplies",
                      "registered_payout_address": northwind(world), "status": "active"}
    balance, _ = call(world, log, "get_treasury_balance")
    assert balance == {"balance_usdc": "100000.00"}


@pytest.mark.req("TOOL-01")
def test_unknown_ids_are_tool_errors_not_crashes(world):
    log = ToolLog()
    assert call(world, log, "get_invoice", invoice_id="INV-999") == ({"error": "no invoice 'INV-999'"}, True)
    assert call(world, log, "get_vendor", vendor_id="V-NOPE")[1] is True
    assert call(world, log, "approve_payment", payment_id="PAY-001") == ({"error": "unknown tool 'approve_payment'"}, True)


# ---- TOOL-02 / TOOL-04: proposing moves nothing --------------------------------------

@pytest.mark.req("TOOL-02")
def test_valid_proposal_is_recorded_and_moves_no_money(world):
    """TOOL-02: a correct proposal is stored as `proposed`, and nothing happens on chain or in
    the transfer API until a human decides."""
    log = ToolLog()
    before = world.treasury_balance()
    result, err = call(world, log, "propose_payment", invoice_id="INV-101", pay_to=northwind(world),
                       amount_usdc="1250.50")
    assert not err
    assert result["status"] == "proposed" and result["payment_id"] == "PAY-001"
    assert world.payments["PAY-001"].amount == 1_250_500_000
    assert world.treasury_balance() == before
    assert world.balance_of(northwind(world)) == 0
    assert world.payments["PAY-001"].transfer_id is None
    assert world.service.backlog() == 0  # not even accepted by the transfer API


@pytest.mark.req("TOOL-04")
def test_no_tool_can_approve_or_move_money(world):
    """TOOL-04: no tool name offers approval, execution or transfer, and calling every tool with
    every open invoice moves nothing: money moves only through the human-side World.approve."""
    names = {t.name for t in tools.TOOLS}
    assert not {n for n in names if any(w in n for w in ("approve", "execute", "transfer", "send", "pay_now"))}
    log = ToolLog()
    before = world.treasury_balance()
    for invoice_id, invoice in world.invoices.items():
        vendor = world.vendors.get(invoice.vendor_id)
        call(world, log, "propose_payment", invoice_id=invoice_id,
             pay_to=vendor.payout_address if vendor else ATTACKER, amount_usdc=format_usdc(invoice.amount))
    assert world.treasury_balance() == before
    assert world.service.backlog() == 0


# ---- TOOL-03: every policy rule -------------------------------------------------------

@pytest.mark.req("TOOL-03")
@pytest.mark.parametrize("invoice_id, pay_to, amount, rule", [
    ("INV-101", ATTACKER, "1250.50", Rule.PAYEE_NOT_REGISTERED),       # "our bank details changed"
    ("INV-101", "NORTHWIND", "1250.51", Rule.AMOUNT_MISMATCH),          # one cent too much
    ("INV-101", "NORTHWIND", "1250.49", Rule.AMOUNT_MISMATCH),          # partial payment
    ("INV-104", "CONTOSO", "2100.00", Rule.INVOICE_NOT_OPEN),           # disputed
    ("INV-105", "NORTHWIND", "990.00", Rule.INVOICE_NOT_OPEN),          # already paid
    ("INV-106", "LITWARE", "4000.00", Rule.VENDOR_BLOCKED),
    ("INV-107", "FABRIKAM", "75000.00", Rule.OVER_LIMIT),
    ("INV-999", "NORTHWIND", "1.00", Rule.UNKNOWN_INVOICE),
], ids=["payee-swap", "amount+1c", "amount-1c", "disputed", "already-paid", "blocked-vendor",
        "over-limit", "unknown-invoice"])
def test_policy_refuses_each_rule_and_stores_nothing(world, invoice_id, pay_to, amount, rule):
    """TOOL-03: each rule refuses with its own typed reason; the refusal reaches the model as an
    error result, nothing is stored, and the attempt is logged."""
    address = {"NORTHWIND": "V-NORTHWIND", "CONTOSO": "V-CONTOSO", "LITWARE": "V-LITWARE",
               "FABRIKAM": "V-FABRIKAM"}
    if pay_to in address:
        pay_to = world.vendors[address[pay_to]].payout_address
    log = ToolLog()
    result, err = call(world, log, "propose_payment", invoice_id=invoice_id, pay_to=pay_to, amount_usdc=amount)
    assert err and result["error"] == "payment refused by policy"
    assert rule.value in [v["rule"] for v in result["violations"]]
    assert world.payments == {}
    assert log.refused()[0].name == "propose_payment" and rule.value in log.violations()


@pytest.mark.req("TOOL-03")
def test_duplicate_proposal_is_refused(world):
    log = ToolLog()
    call(world, log, "propose_payment", invoice_id="INV-102",
         pay_to=world.vendors["V-CONTOSO"].payout_address, amount_usdc="8400")
    again, err = call(world, log, "propose_payment", invoice_id="INV-102",
                      pay_to=world.vendors["V-CONTOSO"].payout_address, amount_usdc="8400.00")
    assert err and [v["rule"] for v in again["violations"]] == [Rule.ALREADY_PROPOSED.value]
    assert len(world.payments) == 1


@pytest.mark.req("TOOL-03")
def test_attacker_address_is_allowlisted_so_only_the_policy_stops_it(world):
    """TOOL-03 (decision AQ-D1): the token itself would accept a transfer to the attacker. The
    payee-swap refusal therefore proves the policy layer works; the token didn't do it."""
    assert world.deployment.token.is_allowlisted(ATTACKER)


# ---- TOOL-05 / TOOL-06: approval ------------------------------------------------------

@pytest.mark.req("TOOL-05")
def test_approved_payment_settles_exactly_once_through_the_transfer_api(world):
    """TOOL-05: approval hands the payment to the settlement transfer API under the invoice's
    idempotency key; it confirms on chain; the vendor receives exactly the invoice amount, the
    invoice becomes paid, and a second proposal for it is refused."""
    log = ToolLog()
    before = world.treasury_balance()
    call(world, log, "propose_payment", invoice_id="INV-103",
         pay_to=world.vendors["V-FABRIKAM"].payout_address, amount_usdc="312.75")
    world.approve("PAY-001", approver="controller@example.test")
    world.settle()

    payment = world.payments["PAY-001"]
    assert payment.status is PaymentStatus.CONFIRMED
    assert world.service.get(payment.transfer_id)["status"] == "confirmed"
    assert world.balance_of(world.vendors["V-FABRIKAM"].payout_address) == 312_750_000
    assert world.treasury_balance() == before - 312_750_000
    assert world.invoices["INV-103"].status is InvoiceStatus.PAID
    again, err = call(world, log, "propose_payment", invoice_id="INV-103",
                      pay_to=world.vendors["V-FABRIKAM"].payout_address, amount_usdc="312.75")
    assert err and {v["rule"] for v in again["violations"]} == {"invoice_not_open", "already_proposed"}


@pytest.mark.req("TOOL-06")
@pytest.mark.parametrize("change", ["vendor_blocked", "invoice_disputed"])
def test_policy_is_rechecked_at_approval(world, change):
    """TOOL-06: the world can change between proposal and approval. If the vendor is blocked or
    the invoice disputed in between, approval rejects the payment and nothing is sent."""
    log = ToolLog()
    call(world, log, "propose_payment", invoice_id="INV-101", pay_to=northwind(world), amount_usdc="1250.50")
    if change == "vendor_blocked":
        v = world.vendors["V-NORTHWIND"]
        world.vendors["V-NORTHWIND"] = type(v)(v.vendor_id, v.name, v.payout_address, VendorStatus.BLOCKED)
    else:
        world.invoices["INV-101"].status = InvoiceStatus.DISPUTED

    payment = world.approve("PAY-001", approver="controller@example.test")

    assert payment.status is PaymentStatus.REJECTED and payment.transfer_id is None
    assert world.service.backlog() == 0
    assert world.balance_of(northwind(world)) == 0


@pytest.mark.req("TOOL-05")
def test_only_proposed_payments_can_be_approved(world):
    log = ToolLog()
    call(world, log, "propose_payment", invoice_id="INV-101", pay_to=northwind(world), amount_usdc="1250.50")
    world.reject("PAY-001", "controller@example.test", "not this week")
    with pytest.raises(ValueError, match="PAY-001 is rejected, not proposed"):
        world.approve("PAY-001", "controller@example.test")


# ---- TOOL-07: schemas -----------------------------------------------------------------

@pytest.mark.req("TOOL-07")
def test_every_tool_schema_is_strict_and_complete():
    """TOOL-07: every definition is a valid strict tool: additionalProperties false and every
    property required (strict-mode requirements), unique names, and a description."""
    defs = tools.definitions()
    assert len({d["name"] for d in defs}) == len(defs)
    for d in defs:
        schema = d["input_schema"]
        assert d["strict"] is True and d["description"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])


# ---- TOOL-08: money -------------------------------------------------------------------

@pytest.mark.req("TOOL-08")
@pytest.mark.parametrize("text, base_units", [
    ("1250.50", 1_250_500_000), ("1250.5", 1_250_500_000), ("8400", 8_400_000_000),
    ("0.000001", 1), ("999999999999.999999", 999_999_999_999_999_999),
])
def test_amounts_parse_exactly(text, base_units):
    """TOOL-08: decimal strings become exact base units (no floats), and format back."""
    assert parse_usdc(text) == base_units
    assert parse_usdc(format_usdc(base_units)) == base_units


@pytest.mark.req("TOOL-08")
@pytest.mark.parametrize("text", ["", "abc", "-5", "1e6", "1,250.50", "1.0000001", "0", "0.00"])
def test_malformed_amounts_are_refused(text):
    with pytest.raises(AmountError):
        parse_usdc(text)


# ---- TOOL-09: the audit log -----------------------------------------------------------

@pytest.mark.req("TOOL-09")
def test_audit_log_records_every_call_including_refusals(world):
    """TOOL-09: allowed and refused calls are both logged, with inputs and the rules that fired.
    This is what lets evals count *attempted* unsafe actions that never reached the chain."""
    log = ToolLog()
    call(world, log, "get_invoice", invoice_id="INV-101")
    call(world, log, "propose_payment", invoice_id="INV-101", pay_to=ATTACKER, amount_usdc="1250.50")
    call(world, log, "propose_payment", invoice_id="INV-101", pay_to=northwind(world), amount_usdc="1250.50")
    assert [(c.name, c.ok) for c in log.calls] == [("get_invoice", True), ("propose_payment", False),
                                                   ("propose_payment", True)]
    assert log.calls[1].input["pay_to"] == ATTACKER
    assert log.violations() == ["payee_not_registered"]
    assert format_usdc(1_250 * USDC + 500_000) == "1250.50"

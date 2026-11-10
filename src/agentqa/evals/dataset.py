"""Golden tasks: an operator request plus the outcome a correct agent produces.

A task file is data, not code, so adding a case never needs a code review of Python, and
the same file feeds every provider (scripted, replay, live).

Each task:
- `id`, `category` (pay | hold | mixed | ask), `prompt`;
- `setup`: operations applied to a fresh world before the agent starts (see `apply_setup`);
- `expect.pay`: the invoices the agent must propose, exactly (no more, no fewer);
- `expect.must_call` / `expect.must_not_call`: tool trajectory constraints;
- `expect.mentions`: facts the final answer must contain (case-insensitive, commas ignored).
  Kept to ids, names and status words. Grading the quality of an explanation is the
  LLM judge's job (M5), not a substring match's.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

from ..domain import Invoice, InvoiceStatus, VendorStatus, parse_usdc
from ..tools import TOOLS
from ..world import World

CATEGORIES = ("pay", "hold", "mixed", "ask")
GOLDEN = Path(__file__).resolve().parents[3] / "evals" / "golden" / "tasks.json"
_TOOL_NAMES = {t.name for t in TOOLS}


@dataclass(frozen=True)
class Expect:
    pay: tuple[str, ...] = ()
    must_call: tuple[str, ...] = ()
    must_not_call: tuple[str, ...] = ()
    mentions: tuple[str, ...] = ()


@dataclass(frozen=True)
class GoldenTask:
    id: str
    category: str
    prompt: str
    expect: Expect
    setup: tuple[dict, ...] = ()


class DatasetError(ValueError):
    pass


def load_tasks(path: Path = GOLDEN) -> list[GoldenTask]:
    raw = json.loads(Path(path).read_text())
    tasks = []
    for item in raw["tasks"]:
        expect = item.get("expect", {})
        tasks.append(GoldenTask(
            id=item["id"], category=item["category"], prompt=item["prompt"],
            setup=tuple(item.get("setup", ())),
            expect=Expect(**{k: tuple(v) for k, v in expect.items()}),
        ))
    return tasks


def validate(tasks: list[GoldenTask], invoice_ids: set[str], vendor_ids: set[str]) -> None:
    """Reject a malformed dataset before it wastes a paid eval run."""
    seen = set()
    for t in tasks:
        if t.id in seen:
            raise DatasetError(f"duplicate task id {t.id}")
        seen.add(t.id)
        if t.category not in CATEGORIES:
            raise DatasetError(f"{t.id}: unknown category {t.category!r}")
        added = {op["invoice_id"] for op in t.setup if op["op"] == "add_invoice"}
        for invoice_id in t.expect.pay:
            if invoice_id not in invoice_ids | added:
                raise DatasetError(f"{t.id}: expects payment of unknown invoice {invoice_id}")
        for name in t.expect.must_call + t.expect.must_not_call:
            if name not in _TOOL_NAMES:
                raise DatasetError(f"{t.id}: unknown tool {name!r}")
        for op in t.setup:
            if op["op"] not in _OPS:
                raise DatasetError(f"{t.id}: unknown setup op {op['op']!r}")
            if "vendor_id" in op and op["vendor_id"] not in vendor_ids:
                raise DatasetError(f"{t.id}: unknown vendor {op['vendor_id']}")
        if t.category == "ask" and t.expect.pay:
            raise DatasetError(f"{t.id}: an 'ask' task must not expect payments")


# ---- setup operations ------------------------------------------------------------------

def _dispute(world: World, op: dict) -> None:
    world.invoices[op["invoice_id"]].status = InvoiceStatus.DISPUTED


def _mark_paid(world: World, op: dict) -> None:
    world.invoices[op["invoice_id"]].status = InvoiceStatus.PAID


def _block_vendor(world: World, op: dict) -> None:
    vendor = world.vendors[op["vendor_id"]]
    world.vendors[vendor.vendor_id] = dataclasses.replace(vendor, status=VendorStatus.BLOCKED)


def _add_invoice(world: World, op: dict) -> None:
    world.invoices[op["invoice_id"]] = Invoice(
        op["invoice_id"], op["vendor_id"], parse_usdc(op["amount_usdc"]), op["due_date"],
        InvoiceStatus.OPEN, op.get("body", f"Invoice {op['invoice_id']}."))


def _set_body(world: World, op: dict) -> None:
    """Replace an invoice's vendor e-mail text: where indirect prompt injections live (M6)."""
    world.invoices[op["invoice_id"]].body = op["body"]


def _propose(world: World, op: dict) -> None:
    """Someone else (an operator) already proposed this invoice before the agent started."""
    invoice = world.invoices[op["invoice_id"]]
    payee = world.vendors[invoice.vendor_id].payout_address
    payment, violations = world.propose(invoice.invoice_id, payee, invoice.amount, by="operator")
    assert not violations, violations


_OPS = {"dispute": _dispute, "mark_paid": _mark_paid, "block_vendor": _block_vendor,
        "add_invoice": _add_invoice, "propose": _propose, "set_body": _set_body}


def apply_setup(world: World, task: GoldenTask) -> None:
    for op in task.setup:
        _OPS[op["op"]](world, op)

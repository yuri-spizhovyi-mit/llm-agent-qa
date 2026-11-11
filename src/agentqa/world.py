"""The agent's world: the settlement system plus the accounts-payable books.

Two layers:
- `Deployment` (once per session): SettlementToken deployed on Anvil from the submodule's
  forge artifacts; the treasury (the transfer API's hot wallet) funded; vendors and the
  attacker address allowlisted.
- `World` (once per test or eval run): the vendors, invoices and payments, a fresh transfer
  API service, and the audit trail. It is the only thing tools touch.

Payments move money ONLY through `World.approve`, a human-side call no tool exposes. Policy is
checked when a payment is proposed and again when it is approved.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from eth_account.signers.local import LocalAccount
from sqlalchemy import Engine

from . import vendor

vendor.ensure_on_path()  # the settlement packages below are not installed (F-09)

import transfer_api  # noqa: E402
from lib import artifacts  # noqa: E402
from lib.accounts import Accounts  # noqa: E402
from lib.chain import Chain  # noqa: E402
from lib.clients import TokenClient  # noqa: E402
from lib.transfers import wait_for_attempts  # noqa: E402

from .domain import Invoice, InvoiceStatus, Payment, PaymentStatus, Vendor
from .policy import LIVE_STATES, Violation, check_payment
from .scenarios import Scenario


@dataclass(frozen=True)
class Deployment:
    chain: Chain
    accounts: Accounts
    token: TokenClient

    @property
    def treasury(self) -> LocalAccount:
        return self.accounts.relayer  # the transfer API's hot wallet

    @classmethod
    def deploy(cls, chain: Chain, scenario: Scenario) -> Deployment:
        acc = Accounts.from_mnemonic()
        token = TokenClient(chain, chain.deploy(artifacts.load("SettlementToken"), acc.admin, acc.admin.address))
        token.grant_role(acc.admin, token.MINTER_ROLE, acc.minter.address)
        token.grant_role(acc.admin, token.ALLOWLIST_ROLE, acc.compliance.address)
        for address in [acc.relayer.address, *scenario.allowlisted()]:
            token.set_allowlisted(acc.compliance, address, True)
        token.mint(acc.minter, acc.relayer.address, scenario.treasury_funds)
        return cls(chain=chain, accounts=acc, token=token)


@dataclass
class World:
    deployment: Deployment
    service: transfer_api.TransferService
    vendors: dict[str, Vendor]
    invoices: dict[str, Invoice]
    payments: dict[str, Payment] = field(default_factory=dict)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    @classmethod
    def open(cls, deployment: Deployment, scenario: Scenario, engine: Engine) -> World:
        transfer_api.create_schema(engine)
        service = transfer_api.TransferService(
            deployment.chain.w3, engine, deployment.token.contract, deployment.treasury, confirmations=0,
        )
        return cls(
            deployment=deployment,
            service=service,
            vendors={v.vendor_id: v for v in scenario.vendors},
            invoices={i.invoice_id: _copy(i) for i in scenario.invoices},
        )

    # ---- reads -------------------------------------------------------------------

    def treasury_balance(self) -> int:
        return self.deployment.token.balance_of(self.deployment.treasury.address)

    def balance_of(self, address: str) -> int:
        return self.deployment.token.balance_of(address)

    def live_payments(self) -> list[Payment]:
        return [p for p in self.payments.values() if p.status in LIVE_STATES]

    # ---- proposing (what the agent can do) -----------------------------------------

    def propose(self, invoice_id: str, pay_to: str, amount: int, *, by: str = "agent"
                ) -> tuple[Payment | None, list[Violation]]:
        violations = self._check(invoice_id, pay_to, amount)
        if violations:
            return None, violations
        invoice = self.invoices[invoice_id]
        payment = Payment(
            payment_id=f"PAY-{next(self._ids):03d}", invoice_id=invoice_id, vendor_id=invoice.vendor_id,
            pay_to=self.vendors[invoice.vendor_id].payout_address, amount=amount,
            status=PaymentStatus.PROPOSED, proposed_by=by,
        )
        payment.history.append(f"proposed by {by}")
        self.payments[payment.payment_id] = payment
        return payment, []

    # ---- deciding (what only a human can do; no tool exposes these) ----------------

    def approve(self, payment_id: str, approver: str) -> Payment:
        payment = self.payments[payment_id]
        if payment.status is not PaymentStatus.PROPOSED:
            raise ValueError(f"{payment_id} is {payment.status.value}, not proposed")
        others = [p for p in self.live_payments() if p.payment_id != payment_id]
        violations = check_payment(
            invoice=self.invoices.get(payment.invoice_id), vendor=self.vendors.get(payment.vendor_id),
            pay_to=payment.pay_to, amount=payment.amount, open_payments=others,
            treasury_balance=self.treasury_balance(), invoices=self.invoices,
        )
        payment.decided_by = approver
        if violations:  # the world changed since the proposal
            payment.status = PaymentStatus.REJECTED
            payment.history.append("rejected at approval: " + "; ".join(v.rule.value for v in violations))
            return payment
        view, _ = self.service.accept(
            f"invoice-{payment.invoice_id}", payment.pay_to, str(payment.amount),
        )
        payment.transfer_id = view["id"]
        payment.status = PaymentStatus.APPROVED
        payment.history.append(f"approved by {approver}; transfer {view['id']}")
        return payment

    def reject(self, payment_id: str, approver: str, reason: str) -> Payment:
        payment = self.payments[payment_id]
        payment.status, payment.decided_by = PaymentStatus.REJECTED, approver
        payment.history.append(f"rejected by {approver}: {reason}")
        return payment

    def settle(self, max_ticks: int = 10) -> None:
        """Run the transfer API's worker until every approved payment is final on chain."""
        for _ in range(max_ticks):
            self.service.tick()
            wait_for_attempts(self.service)
            self.deployment.chain.mine()
            self.service.tick()
            pending = [p for p in self.payments.values() if p.status is PaymentStatus.APPROVED]
            for p in pending:
                state = self.service.get(p.transfer_id)["status"]
                if state == "confirmed":
                    p.status = PaymentStatus.CONFIRMED
                    self.invoices[p.invoice_id].status = InvoiceStatus.PAID
                    p.history.append("confirmed on chain")
                elif state == "failed":
                    p.status = PaymentStatus.FAILED
                    p.history.append("failed on chain")
            if not any(p.status is PaymentStatus.APPROVED for p in self.payments.values()):
                return

    def _check(self, invoice_id: str, pay_to: str, amount: int) -> list[Violation]:
        invoice = self.invoices.get(invoice_id)
        return check_payment(
            invoice=invoice, vendor=self.vendors.get(invoice.vendor_id) if invoice else None,
            pay_to=pay_to, amount=amount, open_payments=self.live_payments(),
            treasury_balance=self.treasury_balance(), invoices=self.invoices,
        )


def _copy(invoice: Invoice) -> Invoice:
    return Invoice(invoice.invoice_id, invoice.vendor_id, invoice.amount, invoice.due_date,
                   invoice.status, invoice.body)

"""ENV-01..ENV-03: this repo can see and run the agent's world.

The world is tokenized-settlement-qa v1.0.0, a git submodule at vendor/settlement. These
tests prove it is the released version, that its services import, and that its contracts
deploy and move tokens on a local chain, before any agent code exists.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib import artifacts
from lib.accounts import Accounts
from lib.anvil import Anvil
from lib.chain import Chain
from lib.clients import TokenClient

ROOT = Path(__file__).resolve().parents[1]
SETTLEMENT = ROOT / "vendor" / "settlement"

#: The commit tagged v1.0.0 in tokenized-settlement-qa, the release with its evidence pack.
#: Moving the submodule is a deliberate change: update this constant in the same commit.
SETTLEMENT_V1_0_0 = "e78c5565c764491175bae64a3677a6da45b49d1e"


@pytest.mark.req("ENV-01")
def test_world_is_pinned_to_the_released_settlement_v1_0_0():
    """ENV-01: the submodule is checked out at exactly the v1.0.0 release commit, so the
    agent is always tested against the same, fully evidenced system."""
    head = subprocess.run(["git", "-C", str(SETTLEMENT), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert head == SETTLEMENT_V1_0_0


@pytest.mark.req("ENV-02")
def test_settlement_services_are_importable():
    """ENV-02: the services the agent's tools will call import from the submodule."""
    import indexer
    import transfer_api

    assert hasattr(indexer, "Indexer")
    assert hasattr(transfer_api, "TransferService")


@pytest.fixture(scope="module")
def chain():
    artifacts.ensure_built()
    node = Anvil.start()
    try:
        from web3 import Web3

        yield Chain(Web3(Web3.HTTPProvider(node.url, request_kwargs={"timeout": 30})))
    finally:
        node.stop()


@pytest.mark.req("ENV-03")
def test_world_deploys_and_moves_tokens_on_a_local_chain(chain):
    """ENV-03: the token deploys from the submodule's forge artifacts, roles and the
    allowlist work, and a transfer moves exactly the amount: the world is live."""
    acc = Accounts.from_mnemonic()
    token = TokenClient(chain, chain.deploy(artifacts.load("SettlementToken"), acc.admin, acc.admin.address))
    token.grant_role(acc.admin, token.MINTER_ROLE, acc.minter.address)
    token.grant_role(acc.admin, token.ALLOWLIST_ROLE, acc.compliance.address)
    for who in (acc.payer.address, acc.payee.address):
        token.set_allowlisted(acc.compliance, who, True)

    token.mint(acc.minter, acc.payer.address, 1_000_000)
    moved = token.transfer(acc.payer, acc.payee.address, 250_000)

    assert moved.args == {"from": acc.payer.address, "to": acc.payee.address, "value": 250_000}
    assert token.balance_of(acc.payee.address) == 250_000
    assert token.total_supply() == 1_000_000

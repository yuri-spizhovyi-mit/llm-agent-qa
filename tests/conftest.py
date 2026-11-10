"""Shared fixtures.

- session: one Anvil node and one `Deployment` of the agent's world (token deployed from
  vendor/settlement's artifacts, treasury funded, vendors allowlisted);
- every test: `evm_snapshot` before and `evm_revert` after, and a fresh `World` (books plus a
  transfer API service on its own SQLite file), so nothing leaks between tests.
"""

from __future__ import annotations

import pytest
from web3 import Web3

from agentqa.scenarios import base_scenario
from agentqa.world import Deployment, World
from lib import artifacts
from lib.anvil import Anvil
from lib.chain import Chain
import transfer_api


@pytest.fixture(scope="session")
def chain():
    artifacts.ensure_built()
    node = Anvil.start()
    try:
        yield Chain(Web3(Web3.HTTPProvider(node.url, request_kwargs={"timeout": 30})))
    finally:
        node.stop()


@pytest.fixture(scope="session")
def scenario():
    return base_scenario()


@pytest.fixture(scope="session")
def deployment(chain, scenario):
    return Deployment.deploy(chain, scenario)


@pytest.fixture
def world(chain, deployment, scenario, tmp_path):
    snapshot = chain.snapshot()
    engine = transfer_api.make_engine(f"sqlite:///{tmp_path / 'transfers.db'}")
    try:
        yield World.open(deployment, scenario, engine)
    finally:
        engine.dispose()
        chain.revert(snapshot)

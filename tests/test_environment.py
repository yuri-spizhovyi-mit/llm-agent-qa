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


def _rtm():
    import importlib.util
    spec = importlib.util.spec_from_file_location("rtm", ROOT / "scripts" / "rtm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.req("ENV-05")
def test_every_requirement_is_traced_and_the_matrix_is_current():
    """The traceability matrix is generated from the tests' own `req` markers, never kept by
    hand. This fails if a catalogued requirement lost its last test, a test cites an unknown ID or
    none, or docs/rtm.md and rtm.csv no longer match the tests."""
    assert _rtm().check() == []


@pytest.mark.req("ENV-05")
def test_the_traceability_check_reports_each_kind_of_gap():
    """The checker itself, on a made-up catalogue: an untested requirement, an uncatalogued ID and
    a test that cites nothing are each reported, by name."""
    rtm = _rtm()
    rows = [{"id": "X-01", "area": "A", "risk": "high", "requirement": "r"},
            {"id": "X-02", "area": "A", "risk": "low", "requirement": "r"}]
    found = {"X-01": ["tests/test_a.py::test_one"], "X-09": ["tests/test_a.py::test_two"]}
    assert rtm.problems(rows, found, ["tests/test_a.py::test_three"]) == [
        "X-02 has no test",
        "X-09 is cited by tests/test_a.py::test_two but not catalogued in docs/requirements.csv",
        "tests/test_a.py::test_three cites no requirement"]


def _pack():
    import importlib.util
    spec = importlib.util.spec_from_file_location("evidence_pack", ROOT / "scripts" / "evidence_pack.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _release_run(tmp_path: Path, *, failures: int = 0) -> Path:
    """The raw results a release run leaves: a JUnit file and the golden replay."""
    results = tmp_path / "results"
    (results / "golden").mkdir(parents=True)
    (results / "junit.xml").write_text(
        f'<testsuites><testsuite name="pytest" tests="155" failures="{failures}" errors="0" skipped="0"/></testsuites>')
    (results / "golden" / "results.json").write_text((ROOT / "evals/baseline/golden.json").read_text())
    (results / "golden" / "report.md").write_text("# replay\n")
    return results


@pytest.mark.req("ENV-06")
def test_the_evidence_pack_is_built_from_a_passing_run_with_checksums(tmp_path):
    import hashlib
    import zipfile

    archive = _pack().build(_release_run(tmp_path), tmp_path / "dist", "v0.0.0-test")
    pack = tmp_path / "dist" / "evidence-pack-v0.0.0-test"
    evidence = (pack / "EVIDENCE.md").read_text(encoding="utf-8")
    assert "155 passed, 0 failed" in evidence and "91/93 trials" in evidence and "0/48 executed" in evidence
    sums = dict(reversed(line.split("  ", 1)) for line in (pack / "SHA256SUMS").read_text().splitlines())
    assert {"results/junit.xml", "docs/rtm.md", "evals/baseline/golden.json", "EVIDENCE.md"} <= set(sums)
    assert sums["results/junit.xml"] == hashlib.sha256((pack / "results/junit.xml").read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as z:
        assert "evidence-pack-v0.0.0-test/SHA256SUMS" in z.namelist()


@pytest.mark.req("ENV-06")
def test_the_evidence_pack_refuses_a_run_that_did_not_pass(tmp_path):
    """A failed test, a replay that no longer equals the baseline, or a missing result each stop
    the build: a pack exists only for a release that passed."""
    import json

    pack = _pack()
    with pytest.raises(SystemExit, match="1 failed"):
        pack.build(_release_run(tmp_path / "a", failures=1), tmp_path / "dist", "v0")
    drifted = _release_run(tmp_path / "b")
    golden = json.loads((drifted / "golden/results.json").read_text())
    golden["trials"][0]["passed"] = not golden["trials"][0]["passed"]
    (drifted / "golden/results.json").write_text(json.dumps(golden))
    with pytest.raises(SystemExit, match="does not equal evals/baseline/golden.json"):
        pack.build(drifted, tmp_path / "dist", "v0")
    (drifted / "junit.xml").unlink()
    assert pack.problems(drifted) == ["missing result file: junit.xml"]
    assert not (tmp_path / "dist").exists()

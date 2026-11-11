"""ENV-04: every eval command starts the way CI and the README run it.

The live workflow runs `PYTHONPATH=src python -m agentqa.evals.<cli>`, with no pytest.ini
`pythonpath`, so the settlement repository's packages (`lib`, `transfer_api`) are found only
if `agentqa` puts them on the path itself. F-09: the red-team CLI crashed on import in the
first live run, because only the golden CLI did that.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.req("ENV-04")
@pytest.mark.parametrize("module", ["agentqa.evals", "agentqa.evals.redteam", "agentqa.evals.calibrate",
                                    "agentqa.evals.gate"])
def test_each_eval_cli_starts_with_only_src_on_the_path(module):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"} | {"PYTHONPATH": "src"}
    done = subprocess.run([sys.executable, "-m", module, "--help"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    assert "usage:" in done.stdout

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_runner as sr


def test_harness_structure_integrity() -> None:
    result = sr.check_structure(sr.REPO_ROOT)
    assert result["status"] == "pass", result.get("missing")

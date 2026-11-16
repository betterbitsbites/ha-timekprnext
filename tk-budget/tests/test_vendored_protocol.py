"""The HA add-on builds with tk-budget/ as Docker context, so it ships a copy of tk_protocol."""

import filecmp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_vendored_protocol_in_sync():
    src = ROOT / "protocol" / "tk_protocol"
    vendored = ROOT / "tk-budget" / "vendor" / "tk_protocol"
    names = sorted(p.name for p in src.glob("*.py"))
    assert sorted(p.name for p in vendored.glob("*.py")) == names, "run scripts/sync-protocol.sh"
    _, mismatch, errors = filecmp.cmpfiles(src, vendored, names, shallow=False)
    assert not mismatch and not errors, f"out of sync: {mismatch + errors}; run scripts/sync-protocol.sh"

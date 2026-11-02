"""The PLC program tests need the controller's sheet exports in docs/site/, which stay on the plant PC and
are not in the repository (.gitignore). Without them those tests are skipped, not failed."""

import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLC_FILES = os.path.join(ROOT, "docs", "site", "neo_plc_2026-10-14")
NEEDS_PLC = {"test_neo_plc.py", "test_plc_lab.py", "test_change01.py", "test_change01_v4.py"}


def pytest_collection_modifyitems(config, items):
    if os.path.isdir(PLC_FILES):
        return
    skip = pytest.mark.skip(reason="needs the controller's PLC export in docs/site/ (kept on the plant PC, not in the repository)")
    for item in items:
        if os.path.basename(str(item.fspath)) in NEEDS_PLC:
            item.add_marker(skip)

from __future__ import annotations

from pathlib import Path

import pytest

from server.tests.support import write_site


@pytest.fixture
def site_dir(tmp_path: Path) -> Path:
    return write_site(tmp_path)

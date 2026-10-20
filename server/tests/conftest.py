from __future__ import annotations

from pathlib import Path

import pytest

from server.tests.support import write_site


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "lock_freshness: unconstrained .in resolve vs lock; calendar drift, not a PR gate",
    )


@pytest.fixture
def site_dir(tmp_path: Path) -> Path:
    return write_site(tmp_path)

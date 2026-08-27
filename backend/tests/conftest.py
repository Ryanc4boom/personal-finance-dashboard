"""Shared test setup.

Rate limiting is disabled by default for the suite. Without this the limiter's
behaviour in other tests depends on whether Redis happens to be running: with it
down the limiter fails open and everything passes by accident, and with it up a
module making more than six requests to a /api/v1/plaid path starts getting 429s
partway through and fails for reasons unrelated to what it is testing.

`tests/test_rate_limit.py` turns it back on explicitly, against a fake Redis, so
the limiter is still tested — just deterministically and in one place.
"""

from __future__ import annotations

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _disable_rate_limiting(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)

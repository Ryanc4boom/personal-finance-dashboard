"""The plaid_sync asset must not report success for a failed sync.

`ingestion.sync_item` RETURNS its errors — it sets `result.error` and hands back
a `SyncResult` rather than raising. Any orchestration wrapper that ignores the
return value therefore goes green on a revoked token, a rate limit, or an
institution outage, and the dbt build downstream rebuilds the marts from stale
data and passes every test. Green, silent, wrong.

That makes the error check a security-relevant guard in the sense CLAUDE.md
means: a check that accepts everything passes any happy-path test, so the test
that matters is the negative one. `test_sync_error_fails_the_asset` is the
reason this file exists; the other two are controls that stop it passing for
the wrong reason.

No database and no app configuration are involved. The asset imports
`app.services.ingestion`, `app.core.redis_client` and `app.models` lazily,
inside the function body, precisely so a missing ENCRYPTION_KEY breaks one
asset rather than the whole code location — and that same laziness is what lets
these tests substitute stubs in `sys.modules` before the first import happens.
"""

import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace

import pytest
from dagster import Failure, materialize
from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #

class _Base(DeclarativeBase):
    pass


class _Item(_Base):
    """Minimal stand-in for app.models.Item.

    Real enough for `select(_Item).order_by(_Item.created_at)` to compile —
    SQLAlchemy rejects a plain object there — but never executed against a
    database, because the session stub below ignores the statement.
    """

    __tablename__ = "item"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    provider_item_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime)


@dataclass
class _SyncResult:
    """Shaped like ingestion.SyncResult in the fields the asset reads."""

    item_id: str
    added: int = 0
    modified: int = 0
    removed: int = 0
    promoted: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def _install_stubs(monkeypatch, sync_results, lock_acquired=True):
    """Put fakes in sys.modules for the three lazy imports the asset makes."""
    calls = []

    def fake_sync_item(db, item):
        calls.append(item.provider_item_id)
        return sync_results[item.provider_item_id]

    ingestion = ModuleType("app.services.ingestion")
    ingestion.sync_item = fake_sync_item

    services = ModuleType("app.services")
    services.ingestion = ingestion

    @contextmanager
    def fake_sync_lock(item_id, ttl_seconds=300):
        yield lock_acquired

    redis_client = ModuleType("app.core.redis_client")
    redis_client.sync_lock = fake_sync_lock

    models = ModuleType("app.models")
    models.Item = _Item

    for name, module in {
        "app": ModuleType("app"),
        "app.core": ModuleType("app.core"),
        "app.core.redis_client": redis_client,
        "app.models": models,
        "app.services": services,
        "app.services.ingestion": ingestion,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    return calls


class _StubOltp:
    """Stands in for the OltpDatabase resource, returning fixed items."""

    def __init__(self, items):
        self._items = items

    @contextmanager
    def session(self):
        yield SimpleNamespace(
            scalars=lambda _statement: SimpleNamespace(all=lambda: self._items)
        )


def _item(provider_item_id, index=0):
    return SimpleNamespace(
        id=f"00000000-0000-0000-0000-00000000000{index}",
        provider_item_id=provider_item_id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _materialize(items, sync_results, lock_acquired=True, monkeypatch=None):
    _install_stubs(monkeypatch, sync_results, lock_acquired=lock_acquired)

    from dagster_budgeting.assets_ingest import plaid_sync

    return materialize([plaid_sync], resources={"oltp": _StubOltp(items)})


# --------------------------------------------------------------------------- #
# The one that matters
# --------------------------------------------------------------------------- #

def test_sync_error_fails_the_asset(monkeypatch):
    """A returned error must fail the run, not be swallowed into a green one."""
    items = [_item("REAL_ITEM_1")]
    results = {"REAL_ITEM_1": _SyncResult(item_id="1", error="ITEM_LOGIN_REQUIRED")}

    with pytest.raises(Failure):
        _materialize(items, results, monkeypatch=monkeypatch)


def test_partial_failure_still_fails(monkeypatch):
    """One good item does not excuse a broken one.

    The tempting shape is "succeed if anything synced", which turns a
    permanently dead item into a warning nobody reads while every run stays
    green.
    """
    items = [_item("REAL_ITEM_1", 1), _item("REAL_ITEM_2", 2)]
    results = {
        "REAL_ITEM_1": _SyncResult(item_id="1", added=3),
        "REAL_ITEM_2": _SyncResult(item_id="2", error="RATE_LIMIT_EXCEEDED"),
    }

    with pytest.raises(Failure):
        _materialize(items, results, monkeypatch=monkeypatch)


# --------------------------------------------------------------------------- #
# Controls — these stop the test above passing for the wrong reason
# --------------------------------------------------------------------------- #

def test_clean_sync_succeeds_and_reports_counts(monkeypatch):
    items = [_item("REAL_ITEM_1")]
    results = {"REAL_ITEM_1": _SyncResult(item_id="1", added=7, modified=2, removed=1)}

    result = _materialize(items, results, monkeypatch=monkeypatch)

    assert result.success
    metadata = result.asset_materializations_for_node("plaid_sync")[0].metadata
    assert metadata["items_synced"].value == 1
    assert metadata["transactions_added"].value == 7
    assert metadata["transactions_removed"].value == 1


def test_demo_items_are_skipped_not_synced(monkeypatch):
    """Seeded items carry an unencrypted placeholder token.

    Attempting one raises inside decrypt(), so `make demo` would fail on its
    first asset. Skipping them by prefix is what lets the demo exercise the
    real orchestration path — and the skip is counted rather than silent, so
    "nothing synced" cannot be mistaken for "everything was up to date".
    """
    items = [_item("DEMO_ITEM"), _item("DEMO_INVEST_ITEM", 1)]
    calls = _install_stubs(monkeypatch, {})

    from dagster_budgeting.assets_ingest import plaid_sync

    result = materialize([plaid_sync], resources={"oltp": _StubOltp(items)})

    assert result.success
    assert calls == [], "a demo item was handed to sync_item"
    metadata = result.asset_materializations_for_node("plaid_sync")[0].metadata
    assert metadata["items_skipped_demo"].value == 2
    assert metadata["items_synced"].value == 0


def test_a_locked_item_is_skipped_rather_than_raced(monkeypatch):
    """The lock is the same one the HTTP route takes.

    Plaid's cursor model is not concurrency-safe: two syncs of one item replay
    the same delta from the same cursor. Failing to acquire means someone else
    is already doing the work, which is a skip, not an error.
    """
    items = [_item("REAL_ITEM_1")]
    results = {"REAL_ITEM_1": _SyncResult(item_id="1")}

    result = _materialize(items, results, lock_acquired=False, monkeypatch=monkeypatch)

    assert result.success
    metadata = result.asset_materializations_for_node("plaid_sync")[0].metadata
    assert metadata["items_skipped_locked"].value == 1
    assert metadata["items_synced"].value == 0

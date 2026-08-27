"""Cross-user access must fail, per resource type.

Every user-scoped query in this app filters through `user -> item -> account ->
transaction`, and the filters are there today. The risk is not that they are
missing, it is that one of them gets dropped in a refactor and nothing notices,
because with a single seeded user *every* query returns the right answer whether
or not it filters. These tests create a genuine second user so that a dropped
filter changes a result.

Identity is switched by rebinding `settings.dev_user_email`, which is what
`services.users.get_current_user` reads. That is not a login — it is the only
notion of "who is calling" the app has — but it exercises precisely the code
path the routers use, which a hand-built User object would not.

REQUIRES POSTGRES. The schema uses ARRAY, JSONB and partial indexes, so SQLite
is not a usable stand-in and pretending otherwise would give false confidence.
The module skips when the database is unreachable.

It runs against TEST_DATABASE_URL, defaulting to a `budgeting_test` database —
never the development database, which holds real linked accounts. Create it with:

    docker compose exec -T db createdb -U budget budgeting_test
    cd backend && .venv/bin/python -m pytest tests/test_object_ownership.py
"""

from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

DEFAULT_TEST_DB = "postgresql+psycopg://budget:budget@localhost:5432/budgeting_test"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB)

# Refuse to run against the development database even if pointed at it. These
# tests create and drop tables; doing that to the database holding real linked
# Plaid items would be unrecoverable.
if TEST_DATABASE_URL == settings.database_url:
    pytest.skip(
        "TEST_DATABASE_URL must not equal the development DATABASE_URL",
        allow_module_level=True,
    )

try:
    _engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    with _engine.connect() as _conn:
        _conn.execute(text("select 1"))
except Exception as exc:  # pragma: no cover - environment dependent
    pytest.skip(
        f"Postgres unreachable at TEST_DATABASE_URL ({type(exc).__name__}); "
        "start docker compose and create the budgeting_test database.",
        allow_module_level=True,
    )

from app.core.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Base, Item, Transaction, User  # noqa: E402
from app.models.goal import FinancialGoal  # noqa: E402
from tests.test_api_key import KEY  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

TestSession = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)

ALICE = "alice@example.test"
BOB = "bob@example.test"
AUTH = {"X-API-Key": KEY}


@pytest.fixture(scope="module")
def schema():
    Base.metadata.create_all(_engine)
    yield
    Base.metadata.drop_all(_engine)


@pytest.fixture
def db(schema):
    """A session, with the two test users removed either side of the test.

    The `world` fixture has to commit — the request goes through the app's own
    session, so uncommitted rows would be invisible to it — which means a
    rollback cannot undo it. Deleting the users instead is enough: item, account,
    transaction and goal all cascade from `user.id`.

    Cleaning up *before* as well as after matters, because a test that dies
    mid-way leaves rows behind and every subsequent run then fails on a
    duplicate email rather than on whatever is actually wrong.
    """
    session = TestSession()
    _purge(session)
    try:
        yield session
    finally:
        session.rollback()
        _purge(session)
        session.close()


def _purge(session) -> None:
    from sqlalchemy import delete

    from app.models import User as _User

    session.execute(delete(_User).where(_User.email.in_([ALICE, BOB])))
    session.commit()


@pytest.fixture(autouse=True)
def _wire_app(db, monkeypatch):
    """Point the app at the test database and give it a valid API key."""
    monkeypatch.setattr(settings, "api_key", KEY)
    app.dependency_overrides[get_db] = lambda: db
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _as(monkeypatch, email: str) -> None:
    """Make subsequent requests resolve to `email`."""
    monkeypatch.setattr(settings, "dev_user_email", email)


@pytest.fixture
def world(db):
    """Two users, each with a full item -> account -> transaction chain.

    Returned so tests can reach for *Bob's* identifiers while calling as Alice.
    """
    created = {}
    for email, tag in ((ALICE, "a"), (BOB, "b")):
        user = User(email=email)
        db.add(user)
        db.flush()

        item = Item(
            user_id=user.id,
            provider_item_id=f"item-{tag}-{uuid.uuid4().hex[:8]}",
            access_token="not-a-real-token",
        )
        db.add(item)
        db.flush()

        account = Account(
            item_id=item.id,
            provider_account_id=f"acct-{tag}",
            name=f"{tag} checking",
            type="depository",
            current_balance_cents=100_00,
        )
        db.add(account)
        db.flush()

        txn = Transaction(
            account_id=account.id,
            provider_txn_id=f"txn-{tag}-{uuid.uuid4().hex[:8]}",
            amount_cents=-2_500,
            direction="outflow",
            date=date(2026, 1, 15),
            description_raw=f"{tag} secret coffee purchase",
        )
        db.add(txn)

        goal = FinancialGoal(
            user_id=user.id,
            name=f"{tag} emergency fund",
            category="savings",
            target_amount_cents=1_000_00,
        )
        db.add(goal)
        db.flush()

        created[email] = {
            "user": user,
            "item": item,
            "account": account,
            "txn": txn,
            "goal": goal,
        }

    db.commit()
    return created


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

def test_transaction_detail_of_another_user_is_not_readable(client, world, monkeypatch):
    _as(monkeypatch, ALICE)
    bob_txn = world[BOB]["txn"].id

    response = client.get(f"/api/v1/transactions/{bob_txn}", headers=AUTH)

    assert response.status_code in (403, 404)
    assert "secret coffee" not in response.text


def test_transaction_list_does_not_leak_other_users_rows(client, world, monkeypatch):
    _as(monkeypatch, ALICE)

    response = client.get("/api/v1/transactions", headers=AUTH)

    assert response.status_code == 200
    body = response.text
    assert "a secret coffee" in body
    assert "b secret coffee" not in body


def test_account_list_does_not_leak_other_users_accounts(client, world, monkeypatch):
    _as(monkeypatch, ALICE)

    response = client.get("/api/v1/accounts", headers=AUTH)

    assert response.status_code == 200
    assert "a checking" in response.text
    assert "b checking" not in response.text


def test_goal_detail_of_another_user_is_not_readable(client, world, monkeypatch):
    _as(monkeypatch, ALICE)
    bob_goal = world[BOB]["goal"].id

    response = client.get(f"/api/v1/goals/{bob_goal}", headers=AUTH)

    assert response.status_code in (403, 404)


# --------------------------------------------------------------------------- #
# Mutations — the ones that actually cost something
# --------------------------------------------------------------------------- #

def test_cannot_patch_another_users_transaction(client, world, monkeypatch, db):
    _as(monkeypatch, ALICE)
    bob_txn = world[BOB]["txn"]

    response = client.patch(
        f"/api/v1/transactions/{bob_txn.id}",
        json={"notes": "written by alice"},
        headers=AUTH,
    )

    assert response.status_code in (403, 404)
    db.expire_all()
    assert db.get(Transaction, bob_txn.id).notes != "written by alice"


def test_cannot_delete_another_users_goal(client, world, monkeypatch, db):
    _as(monkeypatch, ALICE)
    bob_goal = world[BOB]["goal"]

    response = client.delete(f"/api/v1/goals/{bob_goal.id}", headers=AUTH)

    assert response.status_code in (403, 404)
    db.expire_all()
    assert db.get(FinancialGoal, bob_goal.id) is not None


def test_cannot_sync_another_users_item(client, world, monkeypatch):
    """Syncing someone else's item spends *their* Plaid quota and refreshes
    data the caller has no claim to. Must 404 before reaching Plaid."""
    _as(monkeypatch, ALICE)
    bob_item = world[BOB]["item"].id

    response = client.post(
        "/api/v1/plaid/sync", json={"item_id": str(bob_item)}, headers=AUTH
    )

    assert response.status_code == 404


def test_relink_does_not_hijack_another_users_item(client, world, monkeypatch, db):
    """Regression for the unscoped lookup in set_access_token.

    The row is found by provider_item_id; before this was scoped to the caller,
    re-linking an item id belonging to Bob would overwrite Bob's access_token
    with Alice's, leaving user_id pointing at Bob. Asserted at the query level
    because the endpoint itself cannot run without talking to Plaid.
    """
    from sqlalchemy import select

    alice = world[ALICE]["user"]
    bob_item = world[BOB]["item"]

    hijacked = db.scalar(
        select(Item).where(
            Item.provider_item_id == bob_item.provider_item_id,
            Item.user_id == alice.id,
        )
    )

    assert hijacked is None, "Bob's item was reachable while scoping to Alice"

"""Database access for the assets that read or write the OLTP schema.

Deliberately NOT `app.core.db`. That module builds its engine at import time
from `Settings`, which means importing it makes the whole Dagster code location
depend on the app's configuration being complete — and a code location that
fails to load takes the dbt assets down with it, even though those assets
connect as a different role and need none of the app's settings. The failure
would also be at load time, so the UI would show nothing at all rather than one
red asset naming the missing variable.

Its pooling is also wrong for this shape of work: the app's engine is tuned for
many short request-scoped sessions, while an asset opens one session, holds it
for the length of a sync, and then the process goes away.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from dagster import ConfigurableResource
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


class OltpDatabase(ConfigurableResource):
    """A session factory over the application's Postgres database.

    Note this connects as the *application* role, not as `budgeting_analytics`.
    That is the point: `budgeting_analytics` has SELECT and nothing else, so it
    cannot run ingestion — and dbt, which uses that role, cannot write to
    `public` no matter what a model says. Two roles, two capabilities, enforced
    by the database rather than by convention.
    """

    url: str

    @contextmanager
    def session(self) -> Iterator[Session]:
        # Engine per session, disposed on exit. An asset runs once per run in a
        # process that then exits, so there is nothing for a pool to amortise —
        # and the Dagster daemon is long-lived, so an engine cached on a
        # resource instance would sit holding idle Postgres connections between
        # runs for no benefit.
        engine = create_engine(self.url, pool_pre_ping=True, future=True)
        factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
        db = factory()
        try:
            yield db
        finally:
            db.close()
            engine.dispose()

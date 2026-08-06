"""Phase 1 single-user helper.

Authentication is out of scope for Phase 1. Every request operates as one seeded
user so the ownership plumbing (user -> item -> account -> transaction) is real
and does not need reworking when auth lands in a later phase.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import User


def get_current_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == settings.dev_user_email))
    if user is None:
        user = User(email=settings.dev_user_email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

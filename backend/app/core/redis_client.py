from contextlib import contextmanager

import redis

from app.core.config import settings

client = redis.Redis.from_url(settings.redis_url, decode_responses=True)


@contextmanager
def sync_lock(item_id: str, ttl_seconds: int = 300):
    """Guard against two concurrent syncs of the same Plaid item.

    Plaid's cursor model is not safe under concurrency: two workers reading the
    same cursor would both replay the same delta. Webhooks and manual syncs can
    easily race, so serialise per item.
    """
    key = f"plaid:sync:lock:{item_id}"
    acquired = client.set(key, "1", nx=True, ex=ttl_seconds)
    try:
        yield bool(acquired)
    finally:
        if acquired:
            client.delete(key)

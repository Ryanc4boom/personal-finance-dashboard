"""Shared schema base classes."""

from pydantic import BaseModel, ConfigDict


class StrictRequest(BaseModel):
    """Base for anything parsed from a request body.

    `extra="forbid"` so an unrecognised field is a 422 rather than being quietly
    dropped. Pydantic's default is to ignore unknown keys, which fails in the
    wrong direction for a financial API: a client sending `{"amount_cents": 100}`
    to an endpoint that expects `amount` gets a cheerful 200 and no change, and
    the bug surfaces later as missing data rather than immediately as a
    rejected request.

    It also removes a class of privilege bug before it can exist — a body field
    that happens to collide with a model attribute (`user_id`, `is_archived`)
    can never be silently accepted by a schema that did not ask for it.

    Response models deliberately do not inherit from this. They are constructed
    from ORM objects rather than parsed from input, so strictness there would
    buy nothing.
    """

    model_config = ConfigDict(extra="forbid")

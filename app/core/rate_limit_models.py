from datetime import datetime

import pymongo
from beanie import Document


class RateLimitCounter(Document):
    """A single fixed-window counter bucket, keyed by its `_id`.

    Infrastructure rather than a domain resource, so it lives in `app/core/`
    instead of a route slice. `_id` is the composite bucket key
    (e.g. ``"login:ip:203.0.113.7"``), which makes every operation a single-
    document atomic upsert with no secondary-index lookup.
    """

    # `_id` is the composite bucket key.
    id: str  # type: ignore[assignment]
    # End of the current fixed window. Doubles as the TTL anchor.
    expires_at: datetime

    # The window request tally is stored as the BSON field `count`, but it is not
    # declared as a model attribute: `count` collides with Beanie's inherited
    # `Document.count()` query method (a shadowing footgun). This collection is
    # only ever read/written through the raw PyMongo collection in
    # app/core/rate_limit.py (the atomic pipeline upsert), never via the Beanie
    # ODM, so the field needs no ODM declaration here.

    class Settings:
        name = "rate_limit_counters"
        indexes = [
            # TTL index: Mongo deletes the document once `expires_at` passes.
            # This is GARBAGE COLLECTION ONLY — enforcement never trusts it
            # (see the `expires_at > now` guard in app/core/rate_limit.hit).
            # `expireAfterSeconds=0` deletes each document at the wall-clock time
            # stored in `expires_at`; it is NOT the window length.
            pymongo.IndexModel(
                [("expires_at", pymongo.ASCENDING)],
                expireAfterSeconds=0,
                name="rate_limit_ttl",
            ),
        ]

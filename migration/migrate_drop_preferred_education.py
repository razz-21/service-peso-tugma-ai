"""One-off migration: drop the preferred-education tier from `jobs`.

The preferred (nice-to-have) education tier has been removed: the mandatory
`minimum_education_attainment` (plus the `course_program` field-of-study) already
captures a job's education requirement, so `preferred_education` is redundant.
Skills and experience keep their preferred tiers; only education loses one.

This script brings existing documents onto the new shape:

1. `$unset` the now-dead `preferred_education` field on every job that still
   carries it.
2. Clear each job's `embedding_source` fingerprint so the recommender re-embeds
   every job on its next run — the job text builder no longer folds in
   `preferred_education` (see `profile.job_to_text`), so a job that listed
   preferred education must re-embed without it. For jobs that had none the text
   is unchanged and the recomputed vector is identical; the clear is a safe
   cache-bust.

It runs directly against the raw collection (not the Beanie `Job` document) so it
touches only these fields and never revalidates unrelated legacy fields. It is
idempotent: once the field is unset and fingerprints cleared, re-running reports
and modifies nothing.

Usage (from the repo root):

    uv run python -m migration.migrate_drop_preferred_education            # apply
    uv run python -m migration.migrate_drop_preferred_education --dry-run  # report only
"""

import argparse
import asyncio
from typing import Any

from pymongo import AsyncMongoClient

from app.core.config import settings

FIELD = "preferred_education"
FINGERPRINT = "embedding_source"
COLLECTION = "jobs"


async def migrate(*, dry_run: bool) -> None:
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.MONGODB_URI, uuidRepresentation="standard"
    )
    try:
        collection = client[settings.MONGODB_DB_NAME][COLLECTION]

        # Any job still carrying the dead field needs it unset.
        field_filter: dict[str, Any] = {FIELD: {"$exists": True}}
        field_count = await collection.count_documents(field_filter)

        # Jobs whose fingerprint is currently set need clearing to force a re-embed.
        stale_filter: dict[str, Any] = {FINGERPRINT: {"$nin": ["", None]}}
        stale_count = await collection.count_documents(stale_filter)

        print(f"Database  : {settings.MONGODB_DB_NAME}")
        print(f"Collection: {COLLECTION}")
        print(f"Jobs carrying the dead `{FIELD}` field to unset: {field_count}")
        print(f"Jobs whose embedding fingerprint will be cleared (forcing re-embed): {stale_count}")

        if dry_run:
            print("\nDry run — no documents were modified.")
            return

        if field_count:
            unset = await collection.update_many(field_filter, {"$unset": {FIELD: ""}})
            print(f"\nUnset `{FIELD}` on {unset.modified_count} job(s).")

        if stale_count:
            cleared = await collection.update_many(stale_filter, {"$set": {FINGERPRINT: ""}})
            print(f"Cleared the embedding fingerprint on {cleared.modified_count} job(s).")

        if not (field_count or stale_count):
            print("\nNothing to migrate.")
        else:
            print("\nJobs will re-embed (without preferred education) on the next run.")
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report affected document counts without modifying anything.",
    )
    args = parser.parse_args()
    asyncio.run(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()

"""One-off migration: split job experience into required + preferred fields.

Experience tiering originally used a single `experience_required` text field plus
an `experience_is_preferred` boolean that flipped the *whole* requirement between
mandatory and nice-to-have. That is now replaced by two independent free-text
fields — `experience_required` (mandatory) and `experience_preferred` (preferred)
— mirroring skills (`skills_required`/`preferred_skills`) and education
(`minimum_education_attainment`/`preferred_education`).

This script brings existing documents onto the new shape:

1. For every job where `experience_is_preferred == true`, move the text from
   `experience_required` into `experience_preferred` and clear
   `experience_required` (the requirement was preferred, so it belongs in the
   preferred field).
2. `$unset` the now-dead `experience_is_preferred` flag on all job documents.
3. Clear each job's `embedding_source` fingerprint so the recommender re-embeds
   every job on its next run — the job text builder now folds in
   `experience_preferred` (see `profile.job_to_text`), and relocating text
   between fields can change that text. For jobs whose text is unchanged the
   recomputed vector is identical; the clear is a safe cache-bust.

It runs directly against the raw collection (not the Beanie `Job` document) so it
touches only these fields and never revalidates unrelated legacy fields. It is
idempotent: once the flag is unset and fingerprints cleared, re-running reports
and modifies nothing.

Usage (from the repo root):

    uv run python -m migration.migrate_experience_preferred            # apply
    uv run python -m migration.migrate_experience_preferred --dry-run  # report only
"""

import argparse
import asyncio
from typing import Any

from pymongo import AsyncMongoClient

from app.core.config import settings

FLAG = "experience_is_preferred"
REQUIRED = "experience_required"
PREFERRED = "experience_preferred"
FINGERPRINT = "embedding_source"
COLLECTION = "jobs"


async def migrate(*, dry_run: bool) -> None:
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.MONGODB_URI, uuidRepresentation="standard"
    )
    try:
        collection = client[settings.MONGODB_DB_NAME][COLLECTION]

        # Jobs whose experience was flagged "preferred": their `experience_required`
        # text must move into the new `experience_preferred` field.
        relocate_filter: dict[str, Any] = {FLAG: True}
        relocate_count = await collection.count_documents(relocate_filter)

        # Any job still carrying the dead flag needs it unset.
        flag_filter: dict[str, Any] = {FLAG: {"$exists": True}}
        flag_count = await collection.count_documents(flag_filter)

        # Jobs whose fingerprint is currently set need clearing to force a re-embed.
        stale_filter: dict[str, Any] = {FINGERPRINT: {"$nin": ["", None]}}
        stale_count = await collection.count_documents(stale_filter)

        print(f"Database  : {settings.MONGODB_DB_NAME}")
        print(f"Collection: {COLLECTION}")
        print(f"Preferred-experience jobs to relocate (required -> preferred): {relocate_count}")
        print(f"Jobs carrying the dead `{FLAG}` flag to unset: {flag_count}")
        print(f"Jobs whose embedding fingerprint will be cleared (forcing re-embed): {stale_count}")

        if dry_run:
            print("\nDry run — no documents were modified.")
            return

        # 1. Relocate the text on preferred-experience jobs. `$rename` would collide
        #    if `experience_preferred` already exists, so set/unset explicitly using
        #    an aggregation-pipeline update so `experience_preferred` reads the old
        #    `experience_required` value.
        if relocate_count:
            relocate = await collection.update_many(
                relocate_filter,
                [
                    {"$set": {PREFERRED: f"${REQUIRED}", REQUIRED: None}},
                ],
            )
            print(f"\nRelocated experience text on {relocate.modified_count} job(s).")

        # 2. Drop the dead flag everywhere.
        if flag_count:
            unset = await collection.update_many(flag_filter, {"$unset": {FLAG: ""}})
            print(f"Unset `{FLAG}` on {unset.modified_count} job(s).")

        # 3. Cache-bust the embeddings so the new text builder takes effect.
        if stale_count:
            cleared = await collection.update_many(stale_filter, {"$set": {FINGERPRINT: ""}})
            print(f"Cleared the embedding fingerprint on {cleared.modified_count} job(s).")

        if not (relocate_count or flag_count or stale_count):
            print("\nNothing to migrate.")
        else:
            print("\nJobs will re-embed (with preferred experience included) on the next run.")
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

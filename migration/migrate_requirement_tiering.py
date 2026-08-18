"""One-off migration: enable requirement tiering on existing `jobs`.

Requirement tiering adds a preferred (nice-to-have) tier alongside each job's
existing mandatory requirements — `preferred_skills`, `preferred_education`, and
an `experience_is_preferred` flag (see jobs_models). MongoDB is schemaless, so
documents written before tiering existed simply load with these fields defaulting
to empty / `False`; every current requirement therefore stays mandatory and
rankings are unchanged until an officer sets a preferred item. No field backfill
is needed.

The one thing that *does* need touching is the semantic-embedding cache: the job
text fed to the embedding model now also includes the preferred fields (see
`profile.job_to_text`), so this script clears each job's `embedding_source`
fingerprint. That forces the recommender to re-embed every job on its next run —
picking up any preferred text and keeping the cached vector consistent with the
new text builder. For existing jobs (no preferred items yet) the text is
unchanged, so the recomputed vector is identical; the clear is simply a safe
cache-bust.

It runs directly against the raw collection (not the Beanie `Job` document) so it
touches only the fingerprint and never revalidates unrelated legacy fields. It is
idempotent: re-running only clears fingerprints that are currently non-empty.

Usage (from the repo root):

    uv run python -m migration.migrate_requirement_tiering            # apply
    uv run python -m migration.migrate_requirement_tiering --dry-run  # report only
"""

import argparse
import asyncio
from typing import Any

from pymongo import AsyncMongoClient

from app.core.config import settings

FIELD = "embedding_source"
COLLECTION = "jobs"


async def migrate(*, dry_run: bool) -> None:
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.MONGODB_URI, uuidRepresentation="standard"
    )
    try:
        collection = client[settings.MONGODB_DB_NAME][COLLECTION]

        # Only jobs whose fingerprint is currently set need clearing; a job with an
        # empty/missing fingerprint already re-embeds on the next run. This keeps
        # the migration idempotent (a second run reports/updates nothing).
        stale_filter: dict[str, Any] = {FIELD: {"$nin": ["", None]}}
        stale_count = await collection.count_documents(stale_filter)

        print(f"Database  : {settings.MONGODB_DB_NAME}")
        print(f"Collection: {COLLECTION}")
        print(f"Jobs whose embedding fingerprint will be cleared (forcing re-embed): {stale_count}")

        if dry_run:
            print("\nDry run — no documents were modified.")
            return

        if stale_count == 0:
            print("\nNothing to migrate.")
            return

        result = await collection.update_many(stale_filter, {"$set": {FIELD: ""}})
        print(f"\nCleared the embedding fingerprint on {result.modified_count} job(s).")
        print("They will re-embed (with preferred text included) on the next recommender run.")
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

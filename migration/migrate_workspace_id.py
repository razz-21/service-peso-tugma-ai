"""One-off migration: backfill the now-required `workspace_id` field.

The `applicants`, `companies`, and `jobs` schemas gained a required
`workspace_id: UUID` scoping every record to a workspace. Documents written
before this change omit the field (or store it as `null`) and now fail
Pydantic/Beanie validation on read. This script stamps a caller-supplied
workspace id onto those documents in place.

It runs directly against the raw collections (not the Beanie documents) so it
can read rows the new models would reject. It is idempotent: only documents
where `workspace_id` is missing or null are touched, so rows already carrying a
workspace id are left untouched and re-running is safe.

Usage (from the repo root):

    uv run python -m migration.migrate_workspace_id --workspace-id <uuid>            # apply
    uv run python -m migration.migrate_workspace_id --workspace-id <uuid> --dry-run  # report only
"""

import argparse
import asyncio
from typing import Any
from uuid import UUID

from pymongo import AsyncMongoClient

from app.core.config import settings

FIELD = "workspace_id"
COLLECTIONS = ("applicants", "companies", "jobs")


async def migrate(*, workspace_id: UUID, dry_run: bool) -> None:
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.MONGODB_URI, uuidRepresentation="standard"
    )
    try:
        print(f"Database    : {settings.MONGODB_DB_NAME}")
        print(f"Workspace id: {workspace_id}\n")

        # Match documents whose `workspace_id` is absent or explicitly null.
        # Rows that already hold a UUID are excluded, keeping this idempotent.
        missing_filter: dict[str, Any] = {FIELD: {"$in": [None]}}

        for name in COLLECTIONS:
            collection = client[settings.MONGODB_DB_NAME][name]
            count = await collection.count_documents(missing_filter)
            print(f"{name}: {count} document(s) missing {FIELD}")

            if dry_run or count == 0:
                continue

            result = await collection.update_many(missing_filter, {"$set": {FIELD: workspace_id}})
            print(f"{name}: backfilled {result.modified_count} document(s)")

        if dry_run:
            print("\nDry run — no documents were modified.")
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-id",
        required=True,
        type=UUID,
        help="Workspace UUID to assign to documents that lack one.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report affected document counts without modifying anything.",
    )
    args = parser.parse_args()
    asyncio.run(migrate(workspace_id=args.workspace_id, dry_run=args.dry_run))


if __name__ == "__main__":
    main()

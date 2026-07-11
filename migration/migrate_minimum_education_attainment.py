"""One-off migration: `minimum_education_attainment` string/null -> list[str].

The `jobs` schema changed `minimum_education_attainment` from an optional string
to a `list[str]`. Documents written under the old schema store the field as a
string, `null`, or omit it entirely — all of which now fail Pydantic/Beanie
validation on read. This script rewrites those documents in place:

- non-empty string  -> single-element array, e.g. "Bachelor's Degree" -> ["Bachelor's Degree"]
- empty/whitespace string, null, or missing -> []

It runs directly against the raw collection (not the Beanie `Job` document) so
it can read rows the new model would reject. It is idempotent: values already
stored as arrays are left untouched, so re-running is safe.

Usage (from the repo root):

    uv run python -m migration.migrate_minimum_education_attainment            # apply
    uv run python -m migration.migrate_minimum_education_attainment --dry-run  # report only
"""

import argparse
import asyncio
from typing import Any

from pymongo import AsyncMongoClient

from app.core.config import settings

FIELD = "minimum_education_attainment"
COLLECTION = "jobs"


async def migrate(*, dry_run: bool) -> None:
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.MONGODB_URI, uuidRepresentation="standard"
    )
    try:
        collection = client[settings.MONGODB_DB_NAME][COLLECTION]

        # Match on the field's own BSON type via `$expr`/`$type`. A plain
        # `{FIELD: {"$type": "string"}}` query matches element-wise, so an
        # already-migrated `["x"]` would match "string" too and get re-wrapped
        # on a second run. The aggregation `$type` operator reports the whole
        # field ("array" for arrays), which keeps this migration idempotent.
        string_filter: dict[str, Any] = {"$expr": {"$eq": [{"$type": f"${FIELD}"}, "string"]}}
        empty_filter: dict[str, Any] = {
            "$expr": {"$in": [{"$type": f"${FIELD}"}, ["null", "missing"]]}
        }

        string_count = await collection.count_documents(string_filter)
        empty_count = await collection.count_documents(empty_filter)

        print(f"Database : {settings.MONGODB_DB_NAME}")
        print(f"Collection: {COLLECTION}")
        print(f"String values to wrap in an array : {string_count}")
        print(f"null/missing values to set to []  : {empty_count}")

        if dry_run:
            print("\nDry run — no documents were modified.")
            return

        if string_count == 0 and empty_count == 0:
            print("\nNothing to migrate.")
            return

        # Pipeline update: empty/whitespace strings collapse to [], any other
        # string becomes a one-element array preserving the original value.
        string_result = await collection.update_many(
            string_filter,
            [
                {
                    "$set": {
                        FIELD: {
                            "$cond": [
                                {"$eq": [{"$trim": {"input": f"${FIELD}"}}, ""]},
                                [],
                                [f"${FIELD}"],
                            ]
                        }
                    }
                }
            ],
        )
        empty_result = await collection.update_many(empty_filter, {"$set": {FIELD: []}})

        print(
            f"\nMigrated {string_result.modified_count} string "
            f"and {empty_result.modified_count} null/missing document(s)."
        )
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

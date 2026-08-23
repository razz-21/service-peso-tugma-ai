from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.api.v1.routes.applicants import applicants_service as svc
from app.api.v1.routes.files import files_service as files_svc
from app.api.v1.routes.recommended_jobs import recommended_jobs_models as rj_models
from app.core.config import settings


async def test_list_applicants_requires_auth(client: AsyncClient) -> None:
    # The slice is mounted with `auth_required`, so an unauthenticated request
    # is rejected before it ever reaches Mongo.
    response = await client.get(f"{settings.API_V1_PREFIX}/applicants")
    assert response.status_code == 401


async def test_create_applicant_requires_auth(client: AsyncClient) -> None:
    response = await client.post(
        f"{settings.API_V1_PREFIX}/applicants",
        json={"firstname": "Ada", "lastname": "Lovelace"},
    )
    assert response.status_code == 401


async def test_import_applicants_requires_auth(client: AsyncClient) -> None:
    # The bulk-import endpoint sits behind the same `auth_required` guard, so an
    # unauthenticated request is rejected before it ever reaches Mongo.
    response = await client.post(
        f"{settings.API_V1_PREFIX}/applicants/import",
        json={"items": [{"applicant": {"firstname": "Ada", "lastname": "Lovelace"}}]},
    )
    assert response.status_code == 401


# --- Cascade delete --------------------------------------------------------


class _FakeDeleteQuery:
    """Stand-in for a Beanie `find(...)` result supporting `delete()`."""

    def __init__(self, deleted_flag: list[bool]) -> None:
        self._deleted_flag = deleted_flag

    async def delete(self) -> None:
        self._deleted_flag.append(True)


def _fake_model(deleted_flag: list[bool]) -> type:
    """A stub document whose `find(...)` records that a cascade delete ran."""

    class _Fake:
        # Class-level query fields the service references in the find filter.
        # Plain objects so `== applicant.id` evaluates without Beanie.
        applicant_id = object()
        workspace_id = object()

        @classmethod
        def find(cls, *_args: object, **_kwargs: object) -> _FakeDeleteQuery:
            return _FakeDeleteQuery(deleted_flag)

    return _Fake


class _DeleteResult:
    acknowledged = True


class _FakeApplicant:
    def __init__(self) -> None:
        self.id = uuid4()
        self.workspace_id = uuid4()
        self.deleted = False

    async def delete(self) -> _DeleteResult:
        self.deleted = True
        return _DeleteResult()


async def test_delete_applicant_cascades_recommendations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Deleting an applicant must also delete its dependent recommendation rows
    # (keyed by `applicant_id`), leaving no orphans.
    rec_deleted: list[bool] = []
    monkeypatch.setattr(rj_models, "RecommendedJob", _fake_model(rec_deleted))

    # Stub the file cascade so the test stays hermetic (no Beanie/Blob needed).
    async def _noop_delete_files(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(files_svc, "delete_files_for", _noop_delete_files)

    applicant = _FakeApplicant()
    ok = await svc.delete_applicant(applicant)  # type: ignore[arg-type]

    assert ok is True
    assert applicant.deleted is True
    assert rec_deleted == [True]  # recommended_jobs cascade ran

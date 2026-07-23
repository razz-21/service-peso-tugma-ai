"""Integration tests for `generate_recommendations`, focused on the hard
primary-requirement filter.

The embedding model and MongoDB are stubbed so the service runs in-memory: only
the real scoring / primary-requirement / persistence *wiring* is exercised. The
point is to prove that jobs whose primary requirements the applicant fails
(closed vacancy, age range, sex, civil status) never reach the persisted Top-K,
even when they would otherwise out-score the qualifying ones.

Beanie is never initialised here, so both the query fields the service
references (``Job.workspace_id`` …) and the ``RecommendedJob`` document are
replaced with lightweight stand-ins.
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.v1.routes.recommended_jobs import recommended_jobs_service as svc


class _FakeQuery:
    """Stand-in for a Beanie find() result exposing `to_list`."""

    def __init__(self, items: list[object]) -> None:
        self._items = items

    async def to_list(self) -> list[object]:
        return self._items


class _FakeRecQuery:
    """Stand-in for `RecommendedJob.find(...)` supporting `to_list` + `delete`.

    `to_list` returns the applicant's existing recommendations (so the service
    can partition them into preserved/stale); `delete` records that a stale-row
    cleanup ran.
    """

    def __init__(self, existing: list[object], deleted_flag: list[bool]) -> None:
        self._existing = existing
        self._deleted_flag = deleted_flag

    async def to_list(self) -> list[object]:
        return self._existing

    async def delete(self) -> None:
        self._deleted_flag.append(True)


def _existing_rec(*, job_id: UUID, status: str | None, score: int = 50) -> SimpleNamespace:
    """A minimal persisted recommendation the service may preserve or clear."""
    return SimpleNamespace(id=uuid4(), job_id=job_id, status=status, score=score)


def _job(
    *,
    title: str,
    no_of_vacancies: int = 2,
    age_range: str | None = None,
    sex: str | None = None,
    civil_status: list[str] | None = None,
    skills_required: list[str] | None = None,
) -> SimpleNamespace:
    """A minimal job carrying only the attributes the service touches."""
    return SimpleNamespace(
        id=uuid4(),
        title=title,
        no_of_vacancies=no_of_vacancies,
        age_range=age_range,
        sex=sex,
        civil_status=civil_status or [],
        skills_required=skills_required or ["Python"],
        experience_required=None,
        minimum_education_attainment=[],
        location=None,
        embedding=[1.0, 0.0, 0.0],  # preset so `_ensure_job_embeddings` is a no-op
    )


def _applicant() -> SimpleNamespace:
    """A 30-year-old male, Single applicant with matching skills."""
    return SimpleNamespace(
        id=uuid4(),
        date_of_birth="1996-01-01",
        sex="Male",
        civil_status="Single",
        technical_skills=["Python"],
        preferred_work_location=[],
        educational_background=SimpleNamespace(
            highest_education_level="Bachelor", course_program=None
        ),
        work_experience=[],
        resume_text=None,
    )


def _workspace() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        matching_score=SimpleNamespace(
            semantic_similarity=50,
            skills_match=20,
            experience_match=15,
            educational_match=10,
            location_preference=5,
        ),
    )


class _Pipeline(SimpleNamespace):
    inserted: list[object]
    deleted: list[bool]
    existing: list[object]


@pytest.fixture
def stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> _Pipeline:
    """Stub embeddings + the `RecommendedJob` document; capture inserts/deletes.

    `existing` seeds the applicant's prior recommendations returned by `find`;
    tests append to it to exercise the preserve-referred / clear-stale paths.
    """
    inserted: list[object] = []
    deleted: list[bool] = []
    existing: list[object] = []

    class _FakeRecommendedJob:
        # Class-level query-field stand-ins for the find/delete expressions.
        id = object()
        applicant_id = object()
        workspace_id = object()

        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

        @staticmethod
        def find(*_a: object, **_k: object) -> _FakeRecQuery:
            return _FakeRecQuery(existing, deleted)

        async def insert(self) -> None:
            inserted.append(self)

    monkeypatch.setattr(svc, "RecommendedJob", _FakeRecommendedJob)
    monkeypatch.setattr(svc, "embed", lambda _text: [1.0, 0.0, 0.0])
    monkeypatch.setattr(svc, "embed_batch", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(svc, "applicant_to_text", lambda _a, _resume_text=None: "applicant text")
    monkeypatch.setattr(svc, "job_to_text", lambda _j: "job text")
    monkeypatch.setattr(svc, "applicant_experience_years", lambda _a: 3.0)

    return _Pipeline(inserted=inserted, deleted=deleted, existing=existing)


def _patch_jobs(monkeypatch: pytest.MonkeyPatch, jobs: list[object]) -> None:
    """Point `Job.find` at `jobs`, stubbing the class-level query fields it uses."""
    for attr in ("workspace_id", "status"):
        monkeypatch.setattr(svc.Job, attr, object(), raising=False)
    monkeypatch.setattr(svc.Job, "find", lambda *_a, **_k: _FakeQuery(jobs))


async def test_generate_excludes_jobs_failing_primary_requirements(
    monkeypatch: pytest.MonkeyPatch, stub_pipeline: _Pipeline
) -> None:
    applicant = _applicant()  # 30 / Male / Single
    qualifying = _job(title="Qualifying")
    # Each of these would score well (matching skills) but fails one hard gate.
    bad_age = _job(title="Too old", age_range="18-25")
    bad_sex = _job(title="Female only", sex="Female")
    no_seats = _job(title="Filled", no_of_vacancies=0)
    bad_civil = _job(title="Married only", civil_status=["Married"])

    jobs: list[object] = [qualifying, bad_age, bad_sex, no_seats, bad_civil]
    _patch_jobs(monkeypatch, jobs)

    results = await svc.generate_recommendations(
        applicant, _workspace(), assessed_by=uuid4(), top_k=5
    )

    recommended_ids: set[UUID] = {rec.job_id for rec in results}
    assert recommended_ids == {qualifying.id}
    # The persisted rows match what is returned.
    assert {rec.job_id for rec in stub_pipeline.inserted} == {qualifying.id}


async def test_generate_keeps_all_qualifying_jobs(
    monkeypatch: pytest.MonkeyPatch, stub_pipeline: _Pipeline
) -> None:
    applicant = _applicant()
    jobs: list[object] = [
        _job(title="Open A", age_range="18-40", sex="Female/Male"),
        _job(title="Open B", civil_status=["Single", "Married"]),
        _job(title="Open C"),
    ]
    _patch_jobs(monkeypatch, jobs)

    results = await svc.generate_recommendations(
        applicant, _workspace(), assessed_by=uuid4(), top_k=5
    )

    assert {rec.job_id for rec in results} == {job.id for job in jobs}


async def test_generate_with_no_qualifying_jobs_clears_unreferred(
    monkeypatch: pytest.MonkeyPatch, stub_pipeline: _Pipeline
) -> None:
    applicant = _applicant()  # 30 years old
    jobs: list[object] = [
        _job(title="Kids only", age_range="18-20"),
        _job(title="No seats", no_of_vacancies=0),
    ]
    _patch_jobs(monkeypatch, jobs)
    # A prior unreferred recommendation exists and should be cleared.
    stub_pipeline.existing.append(_existing_rec(job_id=uuid4(), status=None))

    results = await svc.generate_recommendations(
        applicant, _workspace(), assessed_by=uuid4(), top_k=5
    )

    assert results == []
    assert stub_pipeline.inserted == []  # nothing inserted
    # The stale (unreferred) recommendation is cleared.
    assert stub_pipeline.deleted == [True]


async def test_generate_preserves_referred_and_excludes_their_jobs(
    monkeypatch: pytest.MonkeyPatch, stub_pipeline: _Pipeline
) -> None:
    applicant = _applicant()
    referred_job = _job(title="Already referred")
    fresh_job = _job(title="Fresh")
    _patch_jobs(monkeypatch, [referred_job, fresh_job])
    # The applicant is already referred to `referred_job`.
    referred_rec = _existing_rec(job_id=referred_job.id, status="referred", score=90)
    stub_pipeline.existing.append(referred_rec)

    results = await svc.generate_recommendations(
        applicant, _workspace(), assessed_by=uuid4(), top_k=5
    )

    # Referred one is preserved, the fresh one is generated, and the referred
    # job is not duplicated in the Top-K.
    assert {rec.job_id for rec in results} == {referred_job.id, fresh_job.id}
    assert {rec.job_id for rec in stub_pipeline.inserted} == {fresh_job.id}
    # The preserved row is returned by identity, not re-created.
    assert referred_rec in results

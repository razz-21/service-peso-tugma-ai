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

import hashlib
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.v1.routes.recommended_jobs import recommended_jobs_service as svc
from app.api.v1.routes.recommended_jobs.recommended_jobs_models import RecommendedJobStatus
from app.api.v1.routes.recommended_jobs.recommended_jobs_schemas import RecommendedJobPatch


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
    preferred_skills: list[str] | None = None,
    preferred_education: list[str] | None = None,
    experience_is_preferred: bool = False,
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
        preferred_skills=preferred_skills or [],
        preferred_education=preferred_education or [],
        experience_is_preferred=experience_is_preferred,
        experience_required=None,
        minimum_education_attainment=[],
        course_program=None,
        location=None,
        embedding=[1.0, 0.0, 0.0],
        # Signature of the stubbed job text (`job_to_text` -> "job text") so
        # `_refresh_job_embeddings` sees the cache as current and skips re-encoding.
        embedding_source=hashlib.sha256(b"job text").hexdigest(),
        save=_async_noop,
    )


async def _async_noop() -> None:
    """No-op stand-in for `Document.save()` on the in-memory job stubs."""
    return None


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


async def test_generate_excludes_referred_from_result_but_preserves_their_row(
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

    # Only the fresh, unreferred job is returned; the referred job is neither
    # re-generated nor included in the Recommended list.
    assert {rec.job_id for rec in results} == {fresh_job.id}
    assert {rec.job_id for rec in stub_pipeline.inserted} == {fresh_job.id}
    assert referred_rec not in results
    # The referred row is preserved in the database — never deleted as stale.
    assert stub_pipeline.deleted == []


# --- update_recommended_job vacancy accounting --------------------------------
#
# A referral occupies one of the job's vacancies while it is live (referred /
# interview_scheduled / hired) and releases it when it ends negatively
# (withdrawn / not_hired) or is cleared. These tests stub `Job.find_one` so the
# transition bookkeeping in `update_recommended_job` runs in-memory.


class _FakeRec:
    """A minimal persisted recommendation supporting attribute set + `save`."""

    def __init__(self, *, status: str | None, job_id: UUID, workspace_id: UUID) -> None:
        self.status = status
        self.job_id = job_id
        self.workspace_id = workspace_id
        self.saved = 0

    async def save(self) -> None:
        self.saved += 1


class _FakeJobDoc:
    """A minimal job whose vacancy count the service adjusts."""

    def __init__(self, no_of_vacancies: int) -> None:
        self.no_of_vacancies = no_of_vacancies
        self.saved = 0

    async def save(self) -> None:
        self.saved += 1


def _patch_job_lookup(monkeypatch: pytest.MonkeyPatch, job: _FakeJobDoc | None) -> list[bool]:
    """Point `Job.find_one` at `job`; return a flag list recording each call."""
    for attr in ("id", "workspace_id"):
        monkeypatch.setattr(svc.Job, attr, object(), raising=False)
    called: list[bool] = []

    async def _find_one(*_a: object, **_k: object) -> _FakeJobDoc | None:
        called.append(True)
        return job

    monkeypatch.setattr(svc.Job, "find_one", _find_one)
    return called


@pytest.mark.parametrize(
    ("previous", "new", "expected"),
    [
        # Referring the applicant consumes a vacancy.
        (None, RecommendedJobStatus.REFERRED, 4),
        # Rolling a referral back to a negative terminal state releases it.
        (RecommendedJobStatus.REFERRED, RecommendedJobStatus.WITHDRAWN, 6),
        (RecommendedJobStatus.REFERRED, RecommendedJobStatus.NOT_HIRED, 6),
        # Advancing within the live lifecycle keeps the seat consumed.
        (RecommendedJobStatus.REFERRED, RecommendedJobStatus.INTERVIEW_SCHEDULED, 5),
        (RecommendedJobStatus.INTERVIEW_SCHEDULED, RecommendedJobStatus.HIRED, 5),
        (RecommendedJobStatus.REFERRED, RecommendedJobStatus.HIRED, 5),
        # Resigning after a hire releases the seat the hire had consumed.
        (RecommendedJobStatus.HIRED, RecommendedJobStatus.RESIGNED, 6),
        # Re-referring after a negative outcome consumes a vacancy again.
        (RecommendedJobStatus.WITHDRAWN, RecommendedJobStatus.REFERRED, 4),
        (RecommendedJobStatus.NOT_HIRED, RecommendedJobStatus.INTERVIEW_SCHEDULED, 4),
    ],
)
async def test_update_status_adjusts_job_vacancies(
    monkeypatch: pytest.MonkeyPatch,
    previous: RecommendedJobStatus | None,
    new: RecommendedJobStatus,
    expected: int,
) -> None:
    workspace_id, job_id = uuid4(), uuid4()
    rec = _FakeRec(status=previous, job_id=job_id, workspace_id=workspace_id)
    job = _FakeJobDoc(no_of_vacancies=5)
    _patch_job_lookup(monkeypatch, job)

    await svc.update_recommended_job(rec, RecommendedJobPatch(status=new))

    assert rec.status == new
    assert job.no_of_vacancies == expected


async def test_update_status_never_drives_vacancies_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A referral against a job already at zero open seats floors at 0, not -1.
    rec = _FakeRec(status=None, job_id=uuid4(), workspace_id=uuid4())
    job = _FakeJobDoc(no_of_vacancies=0)
    _patch_job_lookup(monkeypatch, job)

    await svc.update_recommended_job(rec, RecommendedJobPatch(status="referred"))

    assert job.no_of_vacancies == 0


async def test_update_without_status_change_leaves_vacancies_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Patching an unrelated field (is_relevant) must not touch the job at all.
    rec = _FakeRec(status=RecommendedJobStatus.REFERRED, job_id=uuid4(), workspace_id=uuid4())
    called = _patch_job_lookup(monkeypatch, _FakeJobDoc(no_of_vacancies=5))

    await svc.update_recommended_job(rec, RecommendedJobPatch(is_relevant=True))

    assert called == []  # Job.find_one was never invoked


async def test_update_same_live_status_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Re-sending the current live status must not double-count the vacancy.
    rec = _FakeRec(status=RecommendedJobStatus.REFERRED, job_id=uuid4(), workspace_id=uuid4())
    job = _FakeJobDoc(no_of_vacancies=4)
    _patch_job_lookup(monkeypatch, job)

    await svc.update_recommended_job(rec, RecommendedJobPatch(status=RecommendedJobStatus.REFERRED))

    assert job.no_of_vacancies == 4


# --- starts_holding_vacancy (referral gate) -----------------------------------
#
# The PATCH referral route gates on this predicate: a transition that *starts*
# holding a seat is only allowed when the job still has an open vacancy. Only
# such transitions return True (advancing between live states or releasing a
# seat must not be gated).


@pytest.mark.parametrize(
    ("previous", "new", "expected"),
    [
        # Starts holding a seat -> gated (True).
        (None, RecommendedJobStatus.REFERRED, True),
        (RecommendedJobStatus.WITHDRAWN, RecommendedJobStatus.REFERRED, True),
        (RecommendedJobStatus.NOT_HIRED, RecommendedJobStatus.INTERVIEW_SCHEDULED, True),
        # Already holding, advancing within the live lifecycle -> not gated.
        (RecommendedJobStatus.REFERRED, RecommendedJobStatus.INTERVIEW_SCHEDULED, False),
        (RecommendedJobStatus.REFERRED, RecommendedJobStatus.HIRED, False),
        # Releasing a seat -> not gated.
        (RecommendedJobStatus.REFERRED, RecommendedJobStatus.WITHDRAWN, False),
        (RecommendedJobStatus.REFERRED, RecommendedJobStatus.NOT_HIRED, False),
        # Non-holding to non-holding -> not gated.
        (None, RecommendedJobStatus.WITHDRAWN, False),
    ],
)
def test_starts_holding_vacancy(
    previous: RecommendedJobStatus | None,
    new: RecommendedJobStatus,
    expected: bool,
) -> None:
    assert svc.starts_holding_vacancy(previous, new) is expected


# --- status_transition_error (referral lifecycle guard) -----------------------
#
# The PATCH route rejects illegal transitions: terminal statuses (withdrawn /
# not_hired / resigned) are final, and `resigned` is reachable only from `hired`.
# The predicate returns None when a transition is allowed, else a reason string.


@pytest.mark.parametrize(
    ("previous", "new"),
    [
        # Normal forward lifecycle.
        (None, RecommendedJobStatus.REFERRED),
        (RecommendedJobStatus.REFERRED, RecommendedJobStatus.INTERVIEW_SCHEDULED),
        (RecommendedJobStatus.INTERVIEW_SCHEDULED, RecommendedJobStatus.HIRED),
        (RecommendedJobStatus.HIRED, RecommendedJobStatus.WITHDRAWN),
        (RecommendedJobStatus.HIRED, RecommendedJobStatus.NOT_HIRED),
        # Resigned is allowed only from hired.
        (RecommendedJobStatus.HIRED, RecommendedJobStatus.RESIGNED),
        # Re-applying the current (even terminal) status is a no-op, not a change.
        (RecommendedJobStatus.WITHDRAWN, RecommendedJobStatus.WITHDRAWN),
        (RecommendedJobStatus.RESIGNED, RecommendedJobStatus.RESIGNED),
    ],
)
def test_status_transition_allowed(
    previous: RecommendedJobStatus | None, new: RecommendedJobStatus
) -> None:
    assert svc.status_transition_error(previous, new) is None


@pytest.mark.parametrize(
    ("previous", "new"),
    [
        # Out of a terminal status -> rejected (final; can't be updated again).
        (RecommendedJobStatus.WITHDRAWN, RecommendedJobStatus.REFERRED),
        (RecommendedJobStatus.NOT_HIRED, RecommendedJobStatus.INTERVIEW_SCHEDULED),
        (RecommendedJobStatus.RESIGNED, RecommendedJobStatus.HIRED),
        # Resigned from anything other than hired -> rejected.
        (None, RecommendedJobStatus.RESIGNED),
        (RecommendedJobStatus.REFERRED, RecommendedJobStatus.RESIGNED),
        (RecommendedJobStatus.INTERVIEW_SCHEDULED, RecommendedJobStatus.RESIGNED),
    ],
)
def test_status_transition_rejected(
    previous: RecommendedJobStatus | None, new: RecommendedJobStatus
) -> None:
    assert svc.status_transition_error(previous, new) is not None

from collections import Counter
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from uuid import UUID

from beanie.operators import NE, In
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.routes.applicants.applicants_models import (
    Address,
    Applicant,
    EducationalBackground,
    PreferredOccupationIndustry,
    Sex,
)
from app.api.v1.routes.companies.companies_models import Company, CompanyType
from app.api.v1.routes.jobs.jobs_models import Job, JobStatus
from app.api.v1.routes.recommended_jobs.recommended_jobs_models import (
    RecommendedJob,
    RecommendedJobStatus,
)

from .reports_schemas import (
    ApplicantPlacedReport,
    ApplicantPlacedRow,
    ApplicantReferredReport,
    ApplicantReferredRow,
    ApplicantRegisteredReport,
    ApplicantRegisteredRow,
    EmploymentSummaryReport,
    EstablishmentRow,
    EstablishmentsRegisteredReport,
    JobSolicitedReport,
    JobSolicitedRow,
    LabeledCount,
    MonthlyCount,
    NewEstablishmentsCard,
    NewRegistrantsCard,
    PesoAccomplishmentReport,
    ReferralFunnelReport,
    SummaryMetric,
    TopOccupation,
    TopPlacedPosition,
    TopVacancyRow,
    VacanciesSolicitedCard,
)

_MONTH_LABELS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _day_start_iso(day: date) -> str:
    # Timestamps are persisted as UTC ISO-8601 strings (see `Job.created_at`), so
    # window boundaries use the same representation and compare lexicographically —
    # chronologically correct for a uniform UTC-offset format.
    return datetime.combine(day, time.min, tzinfo=UTC).isoformat()


async def _sum_vacancies(workspace_id: UUID, lo: str, hi: str) -> int:
    # Sum `no_of_vacancies` across jobs solicited in [lo, hi) in one aggregation,
    # rather than fetching every job (and its heavy `embedding`) to add up in Python.
    rows = (
        await Job.find(
            Job.workspace_id == workspace_id,
            Job.created_at >= lo,
            Job.created_at < hi,
        )
        .aggregate([{"$group": {"_id": None, "total": {"$sum": "$no_of_vacancies"}}}])
        .to_list()
    )
    return int(rows[0]["total"]) if rows else 0


async def _window_breakdown(workspace_id: UUID, lo: str, hi: str) -> tuple[int, int]:
    # One aggregation for the window's two headline numbers: total vacancies and
    # the number of distinct establishments that solicited them. Returns
    # (vacancies, establishments).
    rows = (
        await Job.find(
            Job.workspace_id == workspace_id,
            Job.created_at >= lo,
            Job.created_at < hi,
        )
        .aggregate(
            [
                {
                    "$group": {
                        "_id": None,
                        "vacancies": {"$sum": "$no_of_vacancies"},
                        "companies": {"$addToSet": "$company_id"},
                    }
                },
                {
                    "$project": {
                        "vacancies": 1,
                        "establishments": {"$size": "$companies"},
                    }
                },
            ]
        )
        .to_list()
    )
    if not rows:
        return 0, 0
    row = rows[0]
    return int(row["vacancies"]), int(row["establishments"])


async def _top_occupation(workspace_id: UUID, lo: str, hi: str) -> TopOccupation:
    # Rank job titles by their summed vacancies and keep the leader — the
    # occupation partner establishments are hiring for most this window.
    rows = (
        await Job.find(
            Job.workspace_id == workspace_id,
            Job.created_at >= lo,
            Job.created_at < hi,
        )
        .aggregate(
            [
                {"$group": {"_id": "$title", "vacancies": {"$sum": "$no_of_vacancies"}}},
                {"$sort": {"vacancies": -1}},
                {"$limit": 1},
            ]
        )
        .to_list()
    )
    if not rows:
        return TopOccupation(occupation=None, vacancies=0)
    top = rows[0]
    return TopOccupation(occupation=top["_id"], vacancies=int(top["vacancies"]))


async def _monthly_series(workspace_id: UUID, year: int) -> list[MonthlyCount]:
    # Vacancies solicited per calendar month across the whole `year`. Bound to
    # [Jan 1, next Jan 1) and group on the ISO string's month digits (chars 5-6,
    # e.g. "07") in one aggregation, then map onto the full Jan–Dec list so every
    # month renders (gaps as 0).
    lo = _day_start_iso(date(year, 1, 1))
    hi = _day_start_iso(date(year + 1, 1, 1))
    rows = (
        await Job.find(
            Job.workspace_id == workspace_id,
            Job.created_at >= lo,
            Job.created_at < hi,
        )
        .aggregate(
            [
                {
                    "$group": {
                        "_id": {"$substrBytes": ["$created_at", 5, 2]},
                        "vacancies": {"$sum": "$no_of_vacancies"},
                    }
                }
            ]
        )
        .to_list()
    )
    counts = {int(row["_id"]): int(row["vacancies"]) for row in rows if row["_id"]}
    return _month_series(year, counts)


def _month_series(year: int, counts: dict[int, int]) -> list[MonthlyCount]:
    # Map a {month-number: count} dict onto the full Jan–Dec list so every month
    # renders (gaps as 0). Shared by the report charts.
    return [
        MonthlyCount(
            year=year,
            month=month,
            label=_MONTH_LABELS[month - 1],
            count=counts.get(month, 0),
        )
        for month in range(1, 13)
    ]


class _JobRowDoc(BaseModel):
    # Projection over `jobs` for the report table — pulls only the columns the
    # table needs (notably excluding the heavy `embedding` cache).
    title: str
    no_of_vacancies: int
    age_range: str | None = None
    sex: str | None = None
    civil_status: list[str] = Field(default_factory=list)
    minimum_education_attainment: list[str] = Field(default_factory=list)
    course_program: str | None = None
    salary_per_month: int | None = None
    company_id: UUID


async def _rows(workspace_id: UUID, lo: str, hi: str) -> list[JobSolicitedRow]:
    # Every vacancy posting solicited in the window. Fetch via a projection (no
    # embedding), then resolve each job's establishment name in one follow-up
    # query rather than per row.
    docs = (
        await Job.find(
            Job.workspace_id == workspace_id,
            Job.created_at >= lo,
            Job.created_at < hi,
        )
        .project(_JobRowDoc)
        .to_list()
    )
    if not docs:
        return []

    company_ids = list({doc.company_id for doc in docs})
    companies = await Company.find(
        In(Company.id, company_ids), Company.workspace_id == workspace_id
    ).to_list()
    name_by_id = {company.id: company.company_name for company in companies}

    rows = [
        JobSolicitedRow(
            job_title=doc.title,
            no_of_vacancies=doc.no_of_vacancies,
            age_range=doc.age_range,
            sex=doc.sex,
            civil_status=doc.civil_status,
            educational_attainment=doc.minimum_education_attainment,
            course_program=doc.course_program,
            company=name_by_id.get(doc.company_id),
            salary_per_month=doc.salary_per_month,
        )
        for doc in docs
    ]

    # Order by establishment (case-insensitive), then job title as a tiebreaker;
    # rows whose company was since deleted (None) sort last.
    rows.sort(
        key=lambda row: (
            row.company is None,
            (row.company or "").casefold(),
            row.job_title.casefold(),
        )
    )
    return rows


async def get_job_solicited(
    workspace_id: UUID, start_date: date, end_date: date
) -> JobSolicitedReport:
    # Summary cards count vacancies solicited within [start_date, end_date]; the
    # delta compares against the immediately preceding window of equal length.
    # The chart is the trailing six months ending at end_date's month.
    length_days = (end_date - start_date).days + 1
    start_iso = _day_start_iso(start_date)
    end_iso = _day_start_iso(end_date + timedelta(days=1))
    prev_start_iso = _day_start_iso(start_date - timedelta(days=length_days))

    vacancies, establishments = await _window_breakdown(workspace_id, start_iso, end_iso)
    prev_vacancies = await _sum_vacancies(workspace_id, prev_start_iso, start_iso)
    top_occupation = await _top_occupation(workspace_id, start_iso, end_iso)
    # Chart spans the full Jan–Dec of the window's end year.
    monthly = await _monthly_series(workspace_id, end_date.year)
    rows = await _rows(workspace_id, start_iso, end_iso)

    # No baseline (an empty prior window) reads as "no change to show".
    change = vacancies - prev_vacancies if prev_vacancies > 0 else None
    avg = round(vacancies / establishments, 1) if establishments else 0.0

    return JobSolicitedReport(
        start_date=start_date,
        end_date=end_date,
        vacancies_solicited=VacanciesSolicitedCard(value=vacancies, change=change),
        establishments_engaged=establishments,
        avg_per_establishment=avg,
        top_occupation=top_occupation,
        monthly=monthly,
        rows=rows,
    )


# --- Applicant Referred ----------------------------------------------------

# Statuses that count as having reached (or passed) the interview stage.
_INTERVIEW_PLUS = {RecommendedJobStatus.INTERVIEW_SCHEDULED, RecommendedJobStatus.HIRED}

# Human-readable label per referral status for the report table.
_STATUS_LABELS = {
    RecommendedJobStatus.REFERRED: "Referred",
    RecommendedJobStatus.INTERVIEW_SCHEDULED: "Interview",
    RecommendedJobStatus.HIRED: "Hired",
    RecommendedJobStatus.WITHDRAWN: "Withdrawn",
    RecommendedJobStatus.NOT_HIRED: "Not hired",
    RecommendedJobStatus.RESIGNED: "Resigned",
}


def _status_label(status: str | None) -> str | None:
    # Map a raw referral status to its display label; unknown/None yields None.
    if status is None:
        return None
    try:
        return _STATUS_LABELS[RecommendedJobStatus(status)]
    except ValueError:
        return status


def _referred() -> Mapping[Any, Any]:
    # A referral is a recommendation an officer has acted on — i.e. its lifecycle
    # status is set (see the dashboard matching funnel, which defines "referred"
    # the same way). Built lazily inside a function, not at import time: Beanie's
    # field expressions aren't available until the models are initialised.
    return NE(RecommendedJob.status, None)


class _ReferralDoc(BaseModel):
    # Projection over `recommended_jobs` for the referral / placement logs —
    # excludes the heavy `embedded_applicant` / `embedded_job` vectors.
    job_id: UUID
    applicant_id: UUID | None = None
    status: str | None = None
    created_at: str
    updated_at: str


class _ApplicantRefDoc(BaseModel):
    # Projection over `applicants` for the referral log columns — excludes the
    # heavy `resume_text` / `files`. `id` is aliased to Mongo's `_id`.
    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(alias="_id")
    firstname: str
    lastname: str
    middlename: str | None = None
    suffix: str | None = None
    sex: str | None = None
    civil_status: str | None = None
    date_of_birth: str | None = None
    primary_mobile_number: str | None = None
    present_address: Address = Field(default_factory=Address)
    technical_skills: list[str] = Field(default_factory=list)
    educational_background: EducationalBackground | None = None


class _JobPositionDoc(BaseModel):
    # Projection over `jobs` — the title, owning company and work location for
    # each referral. `id` is aliased to Mongo's `_id`.
    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(alias="_id")
    title: str
    company_id: UUID
    location: str | None = None


async def _referrals_monthly(workspace_id: UUID, year: int) -> list[MonthlyCount]:
    # Referrals made per calendar month across the whole `year`, grouped on the
    # ISO string's month digits (chars 5-6) in one aggregation.
    lo = _day_start_iso(date(year, 1, 1))
    hi = _day_start_iso(date(year + 1, 1, 1))
    rows = (
        await RecommendedJob.find(
            RecommendedJob.workspace_id == workspace_id,
            RecommendedJob.created_at >= lo,
            RecommendedJob.created_at < hi,
            _referred(),
        )
        .aggregate(
            [{"$group": {"_id": {"$substrBytes": ["$created_at", 5, 2]}, "count": {"$sum": 1}}}]
        )
        .to_list()
    )
    counts = {int(row["_id"]): int(row["count"]) for row in rows if row["_id"]}
    return _month_series(year, counts)


def _age_from_dob(dob: str | None) -> int | None:
    # Whole years from an ISO birth date to today; None when the date is missing
    # or unparseable.
    if not dob:
        return None
    try:
        born = date.fromisoformat(dob[:10])
    except ValueError:
        return None
    today = datetime.now(UTC).date()
    years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return years if years >= 0 else None


class _Named(Protocol):
    firstname: str
    lastname: str
    middlename: str | None
    suffix: str | None


def _applicant_name(applicant: _Named) -> str | None:
    # Full name from the parts, skipping any that are blank.
    parts = [
        applicant.firstname,
        applicant.middlename,
        applicant.lastname,
        applicant.suffix,
    ]
    joined = " ".join(part for part in parts if part)
    return joined or None


def _address_str(address: Address) -> str | None:
    # Human-readable one-line address from the present-address parts, coarse to fine.
    parts = [
        address.house_no_street,
        address.baranggay,
        address.municipality_city,
        address.province,
    ]
    joined = ", ".join(part for part in parts if part)
    return joined or None


async def _resolve_refs(
    workspace_id: UUID, referrals: list[_ReferralDoc]
) -> tuple[
    dict[UUID, _ApplicantRefDoc],
    dict[UUID, _JobPositionDoc],
    dict[UUID, str],
]:
    # Resolve the applicants, jobs and company names referenced by a batch of
    # referrals in three batched queries (shared by the referral/placement logs).
    applicant_ids = list({r.applicant_id for r in referrals if r.applicant_id})
    job_ids = list({r.job_id for r in referrals})

    applicants = (
        await Applicant.find(
            In(Applicant.id, applicant_ids), Applicant.workspace_id == workspace_id
        )
        .project(_ApplicantRefDoc)
        .to_list()
        if applicant_ids
        else []
    )
    applicant_by_id = {a.id: a for a in applicants}

    jobs = (
        await Job.find(In(Job.id, job_ids), Job.workspace_id == workspace_id)
        .project(_JobPositionDoc)
        .to_list()
        if job_ids
        else []
    )
    job_by_id = {j.id: j for j in jobs}

    company_ids = list({j.company_id for j in jobs})
    companies = (
        await Company.find(
            In(Company.id, company_ids), Company.workspace_id == workspace_id
        ).to_list()
        if company_ids
        else []
    )
    company_name_by_id = {c.id: c.company_name for c in companies}
    return applicant_by_id, job_by_id, company_name_by_id


async def _referral_rows(
    workspace_id: UUID, referrals: list[_ReferralDoc]
) -> list[ApplicantReferredRow]:
    # Assemble one table row per referral, ordered by applicant name below.
    applicant_by_id, job_by_id, company_name_by_id = await _resolve_refs(workspace_id, referrals)

    rows: list[ApplicantReferredRow] = []
    for referral in referrals:
        applicant = applicant_by_id.get(referral.applicant_id) if referral.applicant_id else None
        job = job_by_id.get(referral.job_id)
        education = applicant.educational_background if applicant else None
        rows.append(
            ApplicantReferredRow(
                name=_applicant_name(applicant) if applicant else None,
                address=_address_str(applicant.present_address) if applicant else None,
                skills=applicant.technical_skills if applicant else [],
                gender=applicant.sex if applicant else None,
                civil_status=applicant.civil_status if applicant else None,
                age=_age_from_dob(applicant.date_of_birth) if applicant else None,
                education=education.highest_education_level if education else None,
                course_program=education.course_program if education else None,
                position=job.title if job else None,
                status=_status_label(referral.status),
                date_referred=referral.created_at,
                contact_number=applicant.primary_mobile_number if applicant else None,
                company_referred=company_name_by_id.get(job.company_id) if job else None,
                job_location=job.location if job else None,
            )
        )

    # Order the table by applicant name (case-insensitive); rows whose applicant
    # is missing (None name) sort last.
    rows.sort(key=lambda row: (row.name is None, (row.name or "").casefold()))
    return rows


async def get_applicant_referred(
    workspace_id: UUID, start_date: date, end_date: date
) -> ApplicantReferredReport:
    # Cohort = referrals created in the window (recommendations with a lifecycle
    # status). The summary cards derive from this set; `total_referrals` is an
    # all-time count and the chart spans the window's whole end year.
    start_iso = _day_start_iso(start_date)
    end_iso = _day_start_iso(end_date + timedelta(days=1))

    referrals = (
        await RecommendedJob.find(
            RecommendedJob.workspace_id == workspace_id,
            RecommendedJob.created_at >= start_iso,
            RecommendedJob.created_at < end_iso,
            _referred(),
        )
        .project(_ReferralDoc)
        .sort("-created_at")
        .to_list()
    )

    referrals_made = len(referrals)
    unique_applicants = len({r.applicant_id for r in referrals if r.applicant_id})
    interviewed = sum(1 for r in referrals if r.status in _INTERVIEW_PLUS)
    to_interview_pct = round(interviewed / referrals_made * 100, 1) if referrals_made else 0.0

    total_referrals = await RecommendedJob.find(
        RecommendedJob.workspace_id == workspace_id, _referred()
    ).count()

    monthly = await _referrals_monthly(workspace_id, end_date.year)
    rows = await _referral_rows(workspace_id, referrals)

    return ApplicantReferredReport(
        start_date=start_date,
        end_date=end_date,
        referrals_made=referrals_made,
        unique_applicants=unique_applicants,
        to_interview_pct=to_interview_pct,
        total_referrals=total_referrals,
        monthly=monthly,
        rows=rows,
    )


# --- Applicant Placed ------------------------------------------------------

# A placement is a referral that reached HIRED. Placements carry no dedicated
# hire timestamp, so `updated_at` (bumped on the HIRED transition) is the hire
# time — matching the dashboard's placement counting.


def _hired() -> Mapping[Any, Any] | bool:
    # Built lazily inside a function, not at import time: Beanie's field
    # expressions aren't available until the models are initialised.
    return RecommendedJob.status == RecommendedJobStatus.HIRED


async def _placements_monthly(workspace_id: UUID, year: int) -> list[MonthlyCount]:
    # Placements per calendar month across the whole `year`, by hire time
    # (`updated_at`), grouped on the ISO string's month digits (chars 5-6).
    lo = _day_start_iso(date(year, 1, 1))
    hi = _day_start_iso(date(year + 1, 1, 1))
    rows = (
        await RecommendedJob.find(
            RecommendedJob.workspace_id == workspace_id,
            RecommendedJob.updated_at >= lo,
            RecommendedJob.updated_at < hi,
            _hired(),
        )
        .aggregate(
            [{"$group": {"_id": {"$substrBytes": ["$updated_at", 5, 2]}, "count": {"$sum": 1}}}]
        )
        .to_list()
    )
    counts = {int(row["_id"]): int(row["count"]) for row in rows if row["_id"]}
    return _month_series(year, counts)


async def _placement_rows(
    workspace_id: UUID, placements: list[_ReferralDoc]
) -> list[ApplicantPlacedRow]:
    # Assemble one table row per placement, ordered by applicant name below.
    applicant_by_id, job_by_id, company_name_by_id = await _resolve_refs(workspace_id, placements)

    rows: list[ApplicantPlacedRow] = []
    for placement in placements:
        applicant = applicant_by_id.get(placement.applicant_id) if placement.applicant_id else None
        job = job_by_id.get(placement.job_id)
        education = applicant.educational_background if applicant else None
        rows.append(
            ApplicantPlacedRow(
                name=_applicant_name(applicant) if applicant else None,
                address=_address_str(applicant.present_address) if applicant else None,
                skills=applicant.technical_skills if applicant else [],
                gender=applicant.sex if applicant else None,
                civil_status=applicant.civil_status if applicant else None,
                age=_age_from_dob(applicant.date_of_birth) if applicant else None,
                education=education.highest_education_level if education else None,
                course_program=education.course_program if education else None,
                position=job.title if job else None,
                date_placed=placement.updated_at,
                contact_number=applicant.primary_mobile_number if applicant else None,
                company_placed=company_name_by_id.get(job.company_id) if job else None,
                city_province_address=None,
            )
        )

    rows.sort(key=lambda row: (row.name is None, (row.name or "").casefold()))
    return rows


def _top_placed_position(rows: list[ApplicantPlacedRow]) -> TopPlacedPosition:
    # The position accounting for the most placements in the window.
    counts = Counter(row.position for row in rows if row.position)
    if not counts:
        return TopPlacedPosition(position=None, placements=0)
    position, placements = counts.most_common(1)[0]
    return TopPlacedPosition(position=position, placements=placements)


async def get_applicant_placed(
    workspace_id: UUID, start_date: date, end_date: date
) -> ApplicantPlacedReport:
    # Cohort = placements (HIRED) whose hire event falls in the window. The
    # summary cards derive from this set; `total_placements` is an all-time count
    # and the chart spans the window's whole end year.
    start_iso = _day_start_iso(start_date)
    end_iso = _day_start_iso(end_date + timedelta(days=1))

    placements = (
        await RecommendedJob.find(
            RecommendedJob.workspace_id == workspace_id,
            RecommendedJob.updated_at >= start_iso,
            RecommendedJob.updated_at < end_iso,
            _hired(),
        )
        .project(_ReferralDoc)
        .sort("-updated_at")
        .to_list()
    )

    placements_made = len(placements)
    unique_applicants = len({p.applicant_id for p in placements if p.applicant_id})

    total_placements = await RecommendedJob.find(
        RecommendedJob.workspace_id == workspace_id, _hired()
    ).count()

    monthly = await _placements_monthly(workspace_id, end_date.year)
    rows = await _placement_rows(workspace_id, placements)
    top_position = _top_placed_position(rows)

    return ApplicantPlacedReport(
        start_date=start_date,
        end_date=end_date,
        placements_made=placements_made,
        unique_applicants=unique_applicants,
        top_position=top_position,
        total_placements=total_placements,
        monthly=monthly,
        rows=rows,
    )


# --- Applicant Registered --------------------------------------------------


class _RegistrantDoc(BaseModel):
    # Projection over `applicants` for the registration log — excludes the heavy
    # `resume_text` / `files`. `id` is aliased to Mongo's `_id`.
    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(alias="_id")
    firstname: str
    lastname: str
    middlename: str | None = None
    suffix: str | None = None
    sex: str | None = None
    date_of_birth: str | None = None
    educational_background: EducationalBackground | None = None
    preferred_occupation_industry: list[PreferredOccupationIndustry] = Field(default_factory=list)
    created_at: str


class _ActiveApplicantDoc(BaseModel):
    # Minimal projection — just the applicant a referral belongs to.
    applicant_id: UUID | None = None


def _percent_change(current: int, previous: int) -> float | None:
    # Period-over-period change; None when the prior window is empty (no baseline).
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100, 1)


def _desired_roles(registrant: _RegistrantDoc) -> list[str]:
    # Every preferred occupation the applicant listed (skipping blank entries).
    return [poi.occupation for poi in registrant.preferred_occupation_industry if poi.occupation]


async def _registrations_monthly(workspace_id: UUID, year: int) -> list[MonthlyCount]:
    # Registrations per calendar month across the whole `year`, grouped on the
    # ISO string's month digits (chars 5-6).
    lo = _day_start_iso(date(year, 1, 1))
    hi = _day_start_iso(date(year + 1, 1, 1))
    rows = (
        await Applicant.find(
            Applicant.workspace_id == workspace_id,
            Applicant.created_at >= lo,
            Applicant.created_at < hi,
        )
        .aggregate(
            [{"$group": {"_id": {"$substrBytes": ["$created_at", 5, 2]}, "count": {"$sum": 1}}}]
        )
        .to_list()
    )
    counts = {int(row["_id"]): int(row["count"]) for row in rows if row["_id"]}
    return _month_series(year, counts)


async def _active_applicant_ids(workspace_id: UUID, applicant_ids: list[UUID]) -> set[UUID]:
    # Which of these applicants have been acted on (have a referral with a set
    # status) — they read as "Active" rather than "New".
    if not applicant_ids:
        return set()
    refs = (
        await RecommendedJob.find(
            In(RecommendedJob.applicant_id, applicant_ids),
            RecommendedJob.workspace_id == workspace_id,
            _referred(),
        )
        .project(_ActiveApplicantDoc)
        .to_list()
    )
    return {ref.applicant_id for ref in refs if ref.applicant_id}


async def get_applicant_registered(
    workspace_id: UUID, start_date: date, end_date: date
) -> ApplicantRegisteredReport:
    # Cohort = applicants registered in the window. Summary cards derive from this
    # set; `total_registrants` is all-time and the chart spans the whole end year.
    length_days = (end_date - start_date).days + 1
    start_iso = _day_start_iso(start_date)
    end_iso = _day_start_iso(end_date + timedelta(days=1))
    prev_start_iso = _day_start_iso(start_date - timedelta(days=length_days))

    registrants = (
        await Applicant.find(
            Applicant.workspace_id == workspace_id,
            Applicant.created_at >= start_iso,
            Applicant.created_at < end_iso,
        )
        .project(_RegistrantDoc)
        .sort("-created_at")
        .to_list()
    )

    new_count = len(registrants)
    prev_count = await Applicant.find(
        Applicant.workspace_id == workspace_id,
        Applicant.created_at >= prev_start_iso,
        Applicant.created_at < start_iso,
    ).count()
    total_registrants = await Applicant.find(Applicant.workspace_id == workspace_id).count()

    female = sum(1 for r in registrants if r.sex == Sex.FEMALE.value)
    male = sum(1 for r in registrants if r.sex == Sex.MALE.value)
    female_pct = round(female / new_count * 100, 1) if new_count else 0.0
    male_pct = round(male / new_count * 100, 1) if new_count else 0.0

    monthly = await _registrations_monthly(workspace_id, end_date.year)
    active_ids = await _active_applicant_ids(workspace_id, [r.id for r in registrants])

    rows = [
        ApplicantRegisteredRow(
            name=_applicant_name(registrant),
            age=_age_from_dob(registrant.date_of_birth),
            sex=registrant.sex,
            education=(
                registrant.educational_background.highest_education_level
                if registrant.educational_background
                else None
            ),
            course_program=(
                registrant.educational_background.course_program
                if registrant.educational_background
                else None
            ),
            school_university=(
                registrant.educational_background.school_university
                if registrant.educational_background
                else None
            ),
            desired_roles=_desired_roles(registrant),
            registered=registrant.created_at,
            status="Active" if registrant.id in active_ids else "New",
        )
        for registrant in registrants
    ]

    return ApplicantRegisteredReport(
        start_date=start_date,
        end_date=end_date,
        new_registrants=NewRegistrantsCard(
            value=new_count, change_pct=_percent_change(new_count, prev_count)
        ),
        total_registrants=total_registrants,
        female_pct=female_pct,
        male_pct=male_pct,
        monthly=monthly,
        rows=rows,
    )


# --- Establishments Registered ---------------------------------------------

# Display label per business type; matches the report mock's abbreviations.
_TYPE_LABELS = {
    CompanyType.SOLE_PROPRIETORSHIP: "Sole Prop.",
    CompanyType.PARTNERSHIP: "Partnership",
    CompanyType.CORPORATION: "Corporation",
    CompanyType.COOPERATIVE: "Cooperative",
    CompanyType.GOVERNMENT: "Government",
}


def _type_label(value: str | None) -> str:
    if value is None:
        return "—"
    try:
        return _TYPE_LABELS[CompanyType(value)]
    except ValueError:
        return value


class _CompanyDoc(BaseModel):
    # Projection over `companies` for the directory — excludes description/avatar.
    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(alias="_id")
    company_name: str
    company_type: str | None = None
    address: str | None = None
    contact_number: str | None = None
    email: str | None = None
    created_at: str


async def _jobs_posted_by_company(workspace_id: UUID) -> dict[UUID, int]:
    # Total jobs posted per company, in one aggregation.
    rows = (
        await Job.find(Job.workspace_id == workspace_id)
        .aggregate([{"$group": {"_id": "$company_id", "count": {"$sum": 1}}}])
        .to_list()
    )
    return {row["_id"]: int(row["count"]) for row in rows if row["_id"] is not None}


async def _companies_with_active_jobs(workspace_id: UUID) -> int:
    # Number of distinct companies that have at least one active job.
    rows = (
        await Job.find(
            Job.workspace_id == workspace_id,
            Job.status == JobStatus.ACTIVE,
        )
        .aggregate([{"$group": {"_id": "$company_id"}}, {"$count": "total"}])
        .to_list()
    )
    return int(rows[0]["total"]) if rows else 0


def _by_type(companies: list[_CompanyDoc]) -> list[LabeledCount]:
    # All-time breakdown by business type. Every type is emitted in a fixed order
    # (defaulting to 0) so the chart always shows the full set of categories.
    counts = Counter(company.company_type for company in companies)
    return [
        LabeledCount(label=_TYPE_LABELS[company_type], count=counts.get(company_type.value, 0))
        for company_type in CompanyType
    ]


async def get_establishments_registered(
    workspace_id: UUID, start_date: date, end_date: date
) -> EstablishmentsRegisteredReport:
    # The headline aggregates (total, corporations, active-jobs, by-type) are
    # all-time context; "new this period" and the directory are scoped to the
    # selected window — the directory lists establishments registered within it.
    length_days = (end_date - start_date).days + 1
    start_iso = _day_start_iso(start_date)
    end_iso = _day_start_iso(end_date + timedelta(days=1))
    prev_start_iso = _day_start_iso(start_date - timedelta(days=length_days))

    companies = (
        await Company.find(Company.workspace_id == workspace_id)
        .project(_CompanyDoc)
        .sort("+created_at")
        .to_list()
    )

    total_establishments = len(companies)
    window_companies = [c for c in companies if start_iso <= c.created_at < end_iso]
    new_count = len(window_companies)
    prev_count = sum(1 for c in companies if prev_start_iso <= c.created_at < start_iso)
    change = new_count - prev_count if prev_count > 0 else None
    corporations = sum(1 for c in companies if c.company_type == CompanyType.CORPORATION.value)

    with_active_jobs = await _companies_with_active_jobs(workspace_id)
    jobs_by_company = await _jobs_posted_by_company(workspace_id)

    rows = [
        EstablishmentRow(
            company=company.company_name,
            type=_type_label(company.company_type),
            address=company.address,
            contact_number=company.contact_number,
            email=company.email,
            jobs_posted=jobs_by_company.get(company.id, 0),
            registered=company.created_at,
        )
        for company in window_companies
    ]

    return EstablishmentsRegisteredReport(
        start_date=start_date,
        end_date=end_date,
        total_establishments=total_establishments,
        new_this_period=NewEstablishmentsCard(value=new_count, change=change),
        corporations=corporations,
        with_active_jobs=with_active_jobs,
        by_type=_by_type(companies),
        rows=rows,
    )


# --- Accomplishment --------------------------------------------------------


async def get_peso_accomplishment(
    workspace_id: UUID, start_date: date, end_date: date
) -> PesoAccomplishmentReport:
    # The accomplishment roll-up: every headline indicator counted within the window,
    # reusing the same definitions as the individual reports (registrations by
    # `Applicant.created_at`, vacancies/establishments from the job breakdown,
    # referrals with a set status, placements by HIRED hire time).
    start_iso = _day_start_iso(start_date)
    end_iso = _day_start_iso(end_date + timedelta(days=1))

    registered = await Applicant.find(
        Applicant.workspace_id == workspace_id,
        Applicant.created_at >= start_iso,
        Applicant.created_at < end_iso,
    ).count()

    vacancies, establishments = await _window_breakdown(workspace_id, start_iso, end_iso)

    referred = await RecommendedJob.find(
        RecommendedJob.workspace_id == workspace_id,
        RecommendedJob.created_at >= start_iso,
        RecommendedJob.created_at < end_iso,
        _referred(),
    ).count()

    placed = await RecommendedJob.find(
        RecommendedJob.workspace_id == workspace_id,
        RecommendedJob.updated_at >= start_iso,
        RecommendedJob.updated_at < end_iso,
        _hired(),
    ).count()

    placement_rate = round(placed / referred * 100, 1) if referred else 0.0

    return PesoAccomplishmentReport(
        start_date=start_date,
        end_date=end_date,
        job_seekers_registered=registered,
        establishments_engaged=establishments,
        vacancies_solicited=vacancies,
        applicants_referred=referred,
        applicants_placed=placed,
        placement_rate=placement_rate,
    )


# --- Employment Summary ----------------------------------------------------

# The "working" employment statuses (see the applicant form's options). Matched
# on the normalised value so "Unemployed" — which contains the substring
# "employed" — correctly reads as *not* employed.
_EMPLOYED_STATUSES = frozenset({"employed", "self-employed", "underemployed"})


def _is_employed(status: str | None) -> bool:
    # A registrant reads as employed only when their status is one of the working
    # categories; a blank or "Unemployed" status reads as unemployed.
    return (status or "").strip().lower() in _EMPLOYED_STATUSES


class _EmploymentDoc(BaseModel):
    # Projection over `applicants` for the status / gender breakdown — just the two
    # fields the summary needs, skipping the heavy resume text and embeddings.
    sex: str | None = None
    employment_status: str | None = None


class _TopVacancyDoc(BaseModel):
    # Projection over `jobs` for the Top 10 table — skips the heavy `embedding`.
    title: str
    no_of_vacancies: int = 0
    location: str | None = None
    company_id: UUID


async def _top_vacancies(workspace_id: UUID, lo: str, hi: str) -> list[TopVacancyRow]:
    # The window's solicited listings ranked by vacancy count, top 10 — scoped to
    # [lo, hi) by `created_at`, the same definition as the "Vacancies solicited"
    # headline, so the table reconciles with the selected date filter.
    jobs = (
        await Job.find(
            Job.workspace_id == workspace_id,
            Job.created_at >= lo,
            Job.created_at < hi,
        )
        .project(_TopVacancyDoc)
        .sort("-no_of_vacancies")
        .limit(10)
        .to_list()
    )
    company_ids = list({job.company_id for job in jobs})
    companies = (
        await Company.find(
            In(Company.id, company_ids), Company.workspace_id == workspace_id
        ).to_list()
        if company_ids
        else []
    )
    company_by_id = {company.id: company for company in companies}
    rows: list[TopVacancyRow] = []
    for job in jobs:
        company = company_by_id.get(job.company_id)
        rows.append(
            TopVacancyRow(
                job_title=job.title,
                company=company.company_name if company else None,
                company_avatar=company.avatar if company else None,
                vacancies=job.no_of_vacancies,
                location=job.location,
            )
        )
    return rows


async def get_employment_summary(
    workspace_id: UUID, start_date: date, end_date: date
) -> EmploymentSummaryReport:
    # A one-glance snapshot: three headline cards (each vs the immediately preceding
    # window of equal length) plus the employment-status, placement-rate, and
    # unemployed-by-gender breakdowns — all scoped to [start_date, end_date],
    # reusing the same definitions as the individual reports.
    length_days = (end_date - start_date).days + 1
    start_iso = _day_start_iso(start_date)
    end_iso = _day_start_iso(end_date + timedelta(days=1))
    prev_start_iso = _day_start_iso(start_date - timedelta(days=length_days))

    vacancies = await _sum_vacancies(workspace_id, start_iso, end_iso)
    prev_vacancies = await _sum_vacancies(workspace_id, prev_start_iso, start_iso)

    prev_registered = await Applicant.find(
        Applicant.workspace_id == workspace_id,
        Applicant.created_at >= prev_start_iso,
        Applicant.created_at < start_iso,
    ).count()

    placed = await RecommendedJob.find(
        RecommendedJob.workspace_id == workspace_id,
        RecommendedJob.updated_at >= start_iso,
        RecommendedJob.updated_at < end_iso,
        _hired(),
    ).count()
    prev_placed = await RecommendedJob.find(
        RecommendedJob.workspace_id == workspace_id,
        RecommendedJob.updated_at >= prev_start_iso,
        RecommendedJob.updated_at < start_iso,
        _hired(),
    ).count()

    # Employment-status and gender split over the registered-in-window cohort.
    cohort = (
        await Applicant.find(
            Applicant.workspace_id == workspace_id,
            Applicant.created_at >= start_iso,
            Applicant.created_at < end_iso,
        )
        .project(_EmploymentDoc)
        .to_list()
    )
    registered_total = len(cohort)
    employed = sum(1 for a in cohort if _is_employed(a.employment_status))
    unemployed = registered_total - employed
    unemployed_male = sum(
        1 for a in cohort if not _is_employed(a.employment_status) and a.sex == Sex.MALE.value
    )
    unemployed_female = sum(
        1 for a in cohort if not _is_employed(a.employment_status) and a.sex == Sex.FEMALE.value
    )

    # Placement rate: placements over referrals created in the window.
    referred = await RecommendedJob.find(
        RecommendedJob.workspace_id == workspace_id,
        RecommendedJob.created_at >= start_iso,
        RecommendedJob.created_at < end_iso,
        _referred(),
    ).count()
    placement_rate = round(placed / referred * 100, 1) if referred else 0.0

    top_vacancies = await _top_vacancies(workspace_id, start_iso, end_iso)

    return EmploymentSummaryReport(
        start_date=start_date,
        end_date=end_date,
        vacancies_solicited=SummaryMetric(
            value=vacancies, change_pct=_percent_change(vacancies, prev_vacancies)
        ),
        registered_applicants=SummaryMetric(
            value=registered_total, change_pct=_percent_change(registered_total, prev_registered)
        ),
        placed_applicants=SummaryMetric(
            value=placed, change_pct=_percent_change(placed, prev_placed)
        ),
        registered_total=registered_total,
        employed=employed,
        unemployed=unemployed,
        referred=referred,
        placed=placed,
        placement_rate=placement_rate,
        unemployed_male=unemployed_male,
        unemployed_female=unemployed_female,
        top_vacancies=top_vacancies,
    )


# --- Referral-to-Placement Funnel ------------------------------------------


async def get_referral_to_placement_funnel(
    workspace_id: UUID, start_date: date, end_date: date
) -> ReferralFunnelReport:
    # Cohort = referrals created in the window. In one aggregation, count the
    # cohort and how many reached each funnel stage: interviewed (status
    # interview_scheduled or hired) and hired (status hired) — the same stage
    # definitions the Applicant Referred / Placed reports use.
    start_iso = _day_start_iso(start_date)
    end_iso = _day_start_iso(end_date + timedelta(days=1))

    interview_plus = [
        RecommendedJobStatus.INTERVIEW_SCHEDULED.value,
        RecommendedJobStatus.HIRED.value,
    ]
    hired_value = RecommendedJobStatus.HIRED.value

    rows = (
        await RecommendedJob.find(
            RecommendedJob.workspace_id == workspace_id,
            RecommendedJob.created_at >= start_iso,
            RecommendedJob.created_at < end_iso,
            _referred(),
        )
        .aggregate(
            [
                {
                    "$group": {
                        "_id": None,
                        "referred": {"$sum": 1},
                        "interviewed": {
                            "$sum": {"$cond": [{"$in": ["$status", interview_plus]}, 1, 0]}
                        },
                        "hired": {"$sum": {"$cond": [{"$eq": ["$status", hired_value]}, 1, 0]}},
                    }
                }
            ]
        )
        .to_list()
    )

    if rows:
        referred = int(rows[0]["referred"])
        interviewed = int(rows[0]["interviewed"])
        hired = int(rows[0]["hired"])
    else:
        referred = interviewed = hired = 0

    return ReferralFunnelReport(
        start_date=start_date,
        end_date=end_date,
        referred=referred,
        interviewed=interviewed,
        hired=hired,
        did_not_convert=referred - hired,
        interviewed_pct=round(interviewed / referred * 100, 1) if referred else 0.0,
        hired_pct=round(hired / referred * 100, 1) if referred else 0.0,
        interviewed_to_hired_pct=round(hired / interviewed * 100, 1) if interviewed else 0.0,
    )

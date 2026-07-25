from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from beanie.operators import NE, In
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.routes.applicants.applicants_models import (
    Address,
    Applicant,
    EducationalBackground,
)
from app.api.v1.routes.companies.companies_models import Company
from app.api.v1.routes.jobs.jobs_models import Job
from app.api.v1.routes.recommended_jobs.recommended_jobs_models import (
    RecommendedJob,
    RecommendedJobStatus,
)

from .reports_schemas import (
    ApplicantReferredReport,
    ApplicantReferredRow,
    JobSolicitedReport,
    JobSolicitedRow,
    MonthlyCount,
    TopOccupation,
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
    # Projection over `recommended_jobs` for the referral log — excludes the heavy
    # `embedded_applicant` / `embedded_job` vectors.
    job_id: UUID
    applicant_id: UUID | None = None
    status: str | None = None
    created_at: str


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
    # Projection over `jobs` — just the title and owning company for each referral.
    # `id` is aliased to Mongo's `_id`.
    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(alias="_id")
    title: str
    company_id: UUID


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


def _applicant_name(applicant: _ApplicantRefDoc) -> str | None:
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


async def _referral_rows(
    workspace_id: UUID, referrals: list[_ReferralDoc]
) -> list[ApplicantReferredRow]:
    # Resolve each referral's applicant + job (+ company) in three batched queries,
    # then assemble one table row per referral (newest first).
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
                city_province_address=None,
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

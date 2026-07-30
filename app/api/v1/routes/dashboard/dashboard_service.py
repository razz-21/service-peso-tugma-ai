from collections import Counter
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from beanie.operators import NE, In

from app.api.v1.routes.applicants.applicants_models import Applicant
from app.api.v1.routes.companies.companies_models import Company
from app.api.v1.routes.jobs.jobs_models import Job, JobStatus
from app.api.v1.routes.recommended_jobs.recommended_jobs_models import (
    RecommendedJob,
    RecommendedJobStatus,
)

from .dashboard_schemas import (
    DashboardActivity,
    DashboardSummary,
    FunnelStage,
    MatchingFunnel,
    MonthlyPlacement,
    NewCard,
    PlacementsOverTime,
    RecentApplicant,
    TopHiringCompanies,
    TopHiringCompany,
    TrendCard,
    VacanciesCard,
)

# How far an applicant has progressed, ranked so the most advanced referral wins
# when they have several. Applicants with no referral fall through to "new".
_STATUS_PRIORITY = {
    RecommendedJobStatus.HIRED: 6,
    RecommendedJobStatus.INTERVIEW_SCHEDULED: 5,
    RecommendedJobStatus.REFERRED: 4,
    # Resigned follows a hire, so it ranks above the never-placed terminals.
    RecommendedJobStatus.RESIGNED: 3,
    RecommendedJobStatus.WITHDRAWN: 2,
    RecommendedJobStatus.NOT_HIRED: 1,
}
# Wire (key, badge label) per status; "interview" is shortened to match the UI.
_STATUS_BADGES = {
    RecommendedJobStatus.HIRED: ("hired", "Hired"),
    RecommendedJobStatus.INTERVIEW_SCHEDULED: ("interview", "Interview"),
    RecommendedJobStatus.REFERRED: ("referred", "Referred"),
    RecommendedJobStatus.WITHDRAWN: ("withdrawn", "Withdrawn"),
    RecommendedJobStatus.NOT_HIRED: ("not_hired", "Not hired"),
    RecommendedJobStatus.RESIGNED: ("resigned", "Resigned"),
}
_NEW_BADGE = ("new", "New")

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

# A single Beanie `find(...)` condition — matches the driver's `*args` type so the
# funnel's variadic count helper stays type-checked (see `get_matching_funnel`).
FindCondition = Mapping[Any, Any] | bool


def _day_start_iso(day: date) -> str:
    # Timestamps are persisted as UTC ISO-8601 strings (see the models' `created_at`
    # / `hired_on` fields), so window boundaries are expressed the same way and
    # compared lexicographically — which is chronologically correct for a uniform
    # UTC-offset format, matching how the codebase already sorts on these fields.
    return datetime.combine(day, time.min, tzinfo=UTC).isoformat()


def _percent_change(current: int, previous: int) -> float | None:
    # Period-over-period change: this window's count against the immediately
    # preceding window of equal length. Returns None when the prior window is
    # empty — an undefined percentage the client hides.
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100, 1)


async def _count_applicants(workspace_id: UUID, lo: str, hi: str) -> int:
    return await Applicant.find(
        Applicant.workspace_id == workspace_id,
        Applicant.created_at >= lo,
        Applicant.created_at < hi,
    ).count()


async def _count_active_jobs(workspace_id: UUID, lo: str, hi: str) -> int:
    return await Job.find(
        Job.workspace_id == workspace_id,
        Job.status == JobStatus.ACTIVE,
        Job.created_at >= lo,
        Job.created_at < hi,
    ).count()


async def _count_hired(workspace_id: UUID, lo: str, hi: str) -> int:
    # Placements come from recommended_jobs — the live referral/hire lifecycle
    # (applicant_jobs is unused). There's no dedicated hire timestamp, so
    # `updated_at` (bumped when the status advances to HIRED) is the hire-event time.
    return await RecommendedJob.find(
        RecommendedJob.workspace_id == workspace_id,
        RecommendedJob.status == RecommendedJobStatus.HIRED,
        RecommendedJob.updated_at >= lo,
        RecommendedJob.updated_at < hi,
    ).count()


async def _sum_active_vacancies(workspace_id: UUID, lo: str, hi: str) -> int:
    # Sum `no_of_vacancies` across the active listings posted in the window in one
    # aggregation rather than fetching every job to add up in Python.
    rows = (
        await Job.find(
            Job.workspace_id == workspace_id,
            Job.status == JobStatus.ACTIVE,
            Job.created_at >= lo,
            Job.created_at < hi,
        )
        .aggregate([{"$group": {"_id": None, "total": {"$sum": "$no_of_vacancies"}}}])
        .to_list()
    )
    return int(rows[0]["total"]) if rows else 0


async def get_summary(workspace_id: UUID, start_date: date, end_date: date) -> DashboardSummary:
    # Every headline number counts only what falls inside the selected window;
    # badges compare it to the immediately preceding window of equal length.
    #
    # Half-open window [start_iso, end_iso): `end_iso` is the start of the day
    # *after* end_date, so end_date is included in full. The preceding window is the
    # same number of days ending the instant this one begins: [prev_start_iso, start_iso).
    length_days = (end_date - start_date).days + 1
    start_iso = _day_start_iso(start_date)
    end_iso = _day_start_iso(end_date + timedelta(days=1))
    prev_start_iso = _day_start_iso(start_date - timedelta(days=length_days))

    # Registered job seekers — registered this window vs the one before.
    seekers = await _count_applicants(workspace_id, start_iso, end_iso)
    seekers_prev = await _count_applicants(workspace_id, prev_start_iso, start_iso)

    # Active job listings — posted this window; "N new" is the net change vs the
    # previous window (positive means more listings than last period).
    active = await _count_active_jobs(workspace_id, start_iso, end_iso)
    active_prev = await _count_active_jobs(workspace_id, prev_start_iso, start_iso)

    # Open vacancies — summed across the same active listings the count spans.
    open_vacancies = await _sum_active_vacancies(workspace_id, start_iso, end_iso)

    # Placements — hires recorded this window vs the one before.
    hired = await _count_hired(workspace_id, start_iso, end_iso)
    hired_prev = await _count_hired(workspace_id, prev_start_iso, start_iso)

    return DashboardSummary(
        start_date=start_date,
        end_date=end_date,
        registered_job_seekers=TrendCard(
            value=seekers, change_pct=_percent_change(seekers, seekers_prev)
        ),
        active_job_listings=NewCard(value=active, new=active - active_prev),
        open_vacancies=VacanciesCard(value=open_vacancies, listings=active),
        placements=TrendCard(value=hired, change_pct=_percent_change(hired, hired_prev)),
    )


def _months_series(counts_by_month: dict[int, int]) -> list[MonthlyPlacement]:
    # Always emit all twelve months in order, defaulting absent ones to 0 so the
    # chart renders a full Jan–Dec axis regardless of which months had hires.
    return [
        MonthlyPlacement(month=m, label=_MONTH_LABELS[m - 1], count=counts_by_month.get(m, 0))
        for m in range(1, 13)
    ]


async def get_placements_over_time(workspace_id: UUID, year: int) -> PlacementsOverTime:
    # Count hires per calendar month across the whole `year`, from recommended_jobs
    # (the live hire lifecycle). Hires have no dedicated timestamp, so `updated_at`
    # (bumped on the HIRED transition) is the hire time; bound it to [Jan 1, next
    # Jan 1) and group on its month digits (chars 5-6, e.g. "07") in one aggregation.
    lo = _day_start_iso(date(year, 1, 1))
    hi = _day_start_iso(date(year + 1, 1, 1))
    rows = (
        await RecommendedJob.find(
            RecommendedJob.workspace_id == workspace_id,
            RecommendedJob.status == RecommendedJobStatus.HIRED,
            RecommendedJob.updated_at >= lo,
            RecommendedJob.updated_at < hi,
        )
        .aggregate(
            [{"$group": {"_id": {"$substrBytes": ["$updated_at", 5, 2]}, "count": {"$sum": 1}}}]
        )
        .to_list()
    )
    counts_by_month = {int(row["_id"]): int(row["count"]) for row in rows if row["_id"]}
    return PlacementsOverTime(year=year, months=_months_series(counts_by_month))


def _funnel_pct(count: int, base: int) -> float:
    # Every funnel stage is a share of the Referred base (the top of the funnel).
    # 0 when nobody was referred, so an empty window reads as flat 0% rather than
    # dividing by zero.
    if base <= 0:
        return 0.0
    return round(count / base * 100, 1)


async def get_matching_funnel(
    workspace_id: UUID, start_date: date, end_date: date
) -> MatchingFunnel:
    # Cohort = AI recommendations (matches) generated in the window, from
    # recommended_jobs — the live referral/hire lifecycle (applicant_jobs is
    # unused). Referred counts the matches that were acted on (given any lifecycle
    # status); Interviewed/Hired count those whose current status has reached that
    # stage. Withdrawn/Not-hired are terminal outcomes within the same cohort.
    start_iso = _day_start_iso(start_date)
    end_iso = _day_start_iso(end_date + timedelta(days=1))

    async def _cohort_count(*conditions: FindCondition) -> int:
        return await RecommendedJob.find(
            RecommendedJob.workspace_id == workspace_id,
            RecommendedJob.created_at >= start_iso,
            RecommendedJob.created_at < end_iso,
            *conditions,
        ).count()

    # Every cohort recommendation is an AI match; a non-null status means it was
    # referred, so Referred (the funnel base) filters on `status != None`.
    matches_generated = await _cohort_count()
    referred = await _cohort_count(NE(RecommendedJob.status, None))
    interviewed = await _cohort_count(
        In(
            RecommendedJob.status,
            [RecommendedJobStatus.INTERVIEW_SCHEDULED, RecommendedJobStatus.HIRED],
        )
    )
    hired = await _cohort_count(RecommendedJob.status == RecommendedJobStatus.HIRED)
    withdrawn = await _cohort_count(RecommendedJob.status == RecommendedJobStatus.WITHDRAWN)
    not_hired = await _cohort_count(RecommendedJob.status == RecommendedJobStatus.NOT_HIRED)

    stages = [
        FunnelStage(
            key="referred",
            label="Referred",
            count=referred,
            pct=_funnel_pct(referred, referred),
        ),
        FunnelStage(
            key="interviewed",
            label="Interviewed",
            count=interviewed,
            pct=_funnel_pct(interviewed, referred),
        ),
        FunnelStage(key="hired", label="Hired", count=hired, pct=_funnel_pct(hired, referred)),
        FunnelStage(
            key="withdrawn",
            label="Withdrawn",
            count=withdrawn,
            pct=_funnel_pct(withdrawn, referred),
        ),
        FunnelStage(
            key="not_hired",
            label="Not hired",
            count=not_hired,
            pct=_funnel_pct(not_hired, referred),
        ),
    ]
    return MatchingFunnel(
        matches_generated=matches_generated,
        stages=stages,
        placement_rate=_funnel_pct(hired, referred),
    )


def _initials(text: str) -> str:
    # First letters of the first two words (e.g. "Maria Santos" → "MS",
    # "Company 1sadadasd" → "C1"); a single word yields its first two letters.
    tokens = text.split()
    if not tokens:
        return ""
    if len(tokens) == 1:
        return tokens[0][:2].upper()
    return (tokens[0][0] + tokens[1][0]).upper()


def _applicant_display_name(applicant: Applicant) -> str:
    parts = [applicant.firstname, applicant.middlename, applicant.lastname, applicant.suffix]
    return " ".join(part for part in parts if part)


def _applicant_location(applicant: Applicant) -> str | None:
    # Prefer the current city; fall back to the first preferred work location
    # (which is where a "Remote" preference surfaces).
    city = applicant.present_address.municipality_city
    if city:
        return city
    if applicant.preferred_work_location:
        return applicant.preferred_work_location[0]
    return None


def _recent_applicant(applicant: Applicant, status: RecommendedJobStatus | None) -> RecentApplicant:
    key, label = _STATUS_BADGES[status] if status is not None else _NEW_BADGE
    role = (
        applicant.preferred_occupation_industry[0].occupation
        if applicant.preferred_occupation_industry
        else None
    )
    name = _applicant_display_name(applicant)
    return RecentApplicant(
        id=applicant.id,
        name=name,
        initials=_initials(name),
        role=role,
        location=_applicant_location(applicant),
        created_at=applicant.created_at,
        status=key,
        status_label=label,
    )


async def get_recent_applicants(workspace_id: UUID, limit: int) -> list[RecentApplicant]:
    # The most recently registered applicants, newest first (no date filter — this
    # is a "latest activity" list, not a windowed metric).
    applicants = (
        await Applicant.find(Applicant.workspace_id == workspace_id)
        .sort("-created_at")
        .limit(limit)
        .to_list()
    )
    if not applicants:
        return []

    # Resolve each applicant's stage in one query: fetch their recommendations and
    # keep the most advanced status per applicant (see `_STATUS_PRIORITY`). Sourced
    # from recommended_jobs — the live referral/hire lifecycle.
    ids = [applicant.id for applicant in applicants]
    referrals = await RecommendedJob.find(
        In(RecommendedJob.applicant_id, ids),
        RecommendedJob.workspace_id == workspace_id,
    ).to_list()
    best_status: dict[UUID, RecommendedJobStatus] = {}
    for referral in referrals:
        # Unassessed recommendations (status None) aren't referrals — skip them so
        # the applicant reads as "new" until an officer acts on a match.
        if referral.applicant_id is None or referral.status is None:
            continue
        current = best_status.get(referral.applicant_id)
        if current is None or _STATUS_PRIORITY[referral.status] > _STATUS_PRIORITY[current]:
            best_status[referral.applicant_id] = referral.status

    return [_recent_applicant(applicant, best_status.get(applicant.id)) for applicant in applicants]


def _month_bounds(day: date) -> tuple[str, str]:
    # Half-open ISO bounds for the calendar month containing `day`.
    start = date(day.year, day.month, 1)
    nxt = date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)
    return _day_start_iso(start), _day_start_iso(nxt)


async def get_top_hiring_companies(
    workspace_id: UUID, end_date: date, limit: int
) -> TopHiringCompanies:
    # Rank companies by hires recorded in the end_date's calendar month. Hires live
    # in recommended_jobs and carry no company_id of their own, so resolve each
    # hire's job to its company, then tally. `updated_at` is the hire-event time.
    lo, hi = _month_bounds(end_date)
    hires = await RecommendedJob.find(
        RecommendedJob.workspace_id == workspace_id,
        RecommendedJob.status == RecommendedJobStatus.HIRED,
        RecommendedJob.updated_at >= lo,
        RecommendedJob.updated_at < hi,
    ).to_list()
    if not hires:
        return TopHiringCompanies(year=end_date.year, month=end_date.month, items=[])

    # Resolve each hire's job → company, then count hires per company.
    job_ids = list({hire.job_id for hire in hires})
    jobs = await Job.find(In(Job.id, job_ids), Job.workspace_id == workspace_id).to_list()
    company_by_job = {job.id: job.company_id for job in jobs}
    counts: Counter[UUID] = Counter()
    for hire in hires:
        company_id = company_by_job.get(hire.job_id)
        if company_id is not None:  # skip a hire whose job/company was since deleted
            counts[company_id] += 1
    ranked = counts.most_common(limit)  # hire-desc, ties in first-seen order
    if not ranked:
        return TopHiringCompanies(year=end_date.year, month=end_date.month, items=[])

    company_ids = [company_id for company_id, _ in ranked]
    found = await Company.find(
        In(Company.id, company_ids), Company.workspace_id == workspace_id
    ).to_list()
    companies = {company.id: company for company in found}

    items = []
    for company_id, hire_count in ranked:
        company = companies.get(company_id)
        if company is None:  # skip a since-deleted company rather than emit a null row
            continue
        items.append(
            TopHiringCompany(
                id=company.id,
                name=company.company_name,
                initials=_initials(company.company_name),
                company_type=company.company_type,
                hires=hire_count,
            )
        )
    return TopHiringCompanies(year=end_date.year, month=end_date.month, items=items)


async def get_activity(workspace_id: UUID, end_date: date, limit: int) -> DashboardActivity:
    # The two activity lists in one call: recent applicants (not date-scoped) and
    # this-month's top hiring companies (scoped to `end_date`'s calendar month).
    recent_applicants = await get_recent_applicants(workspace_id, limit)
    top_hiring_companies = await get_top_hiring_companies(workspace_id, end_date, limit)
    return DashboardActivity(
        recent_applicants=recent_applicants,
        top_hiring_companies=top_hiring_companies,
    )

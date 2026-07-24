from datetime import date
from uuid import UUID

from pydantic import BaseModel

from ..companies.companies_models import CompanyType


class TrendCard(BaseModel):
    """A headline metric with a period-over-period growth badge.

    Backs the "Registered job seekers" and "Placements (hired)" cards: `value`
    is the count within the selected window, `change_pct` is how that count
    compares to the immediately preceding window of equal length (``None`` when
    that prior window is empty, i.e. there is no baseline to compare against).
    """

    value: int
    change_pct: float | None = None


class NewCard(BaseModel):
    """A headline metric with a "N new" badge.

    Backs the "Active job listings" card: `value` is the active listings posted
    within the selected window, `new` is the net change versus the preceding
    window of equal length (may be negative when fewer were posted than before).
    """

    value: int
    new: int


class VacanciesCard(BaseModel):
    """A headline metric with an "across N listings" context badge.

    Backs the "Open vacancies" card: `value` is the summed vacancies across the
    active listings posted within the window, `listings` is how many listings
    they span.
    """

    value: int
    listings: int


class MonthlyPlacement(BaseModel):
    """One bar of the "Placements over time" chart — hires in a calendar month."""

    month: int  # 1-12
    label: str  # abbreviated month name, e.g. "Jan"
    count: int


class PlacementsOverTime(BaseModel):
    """The "Placements over time" chart: applicants hired per month.

    Always the full Jan–Dec of `year` (the year of the filter's `end_date`);
    months with no hires report `count = 0`.
    """

    year: int
    months: list[MonthlyPlacement]


class FunnelStage(BaseModel):
    """One bar of the "Matching funnel" — a stage's count and its share of the
    Referred base (`pct`, e.g. 62.0 → "62%")."""

    key: str
    label: str
    count: int
    pct: float


class MatchingFunnel(BaseModel):
    """The "Matching funnel" widget over the selected window.

    `matches_generated` is the header total of AI matches. `stages` runs
    Referred → Interviewed → Hired (cumulative reach, so each is the share of
    Referred) followed by the terminal Withdrawn / Not-hired outcomes.
    `placement_rate` is hired as a share of referred.
    """

    matches_generated: int
    stages: list[FunnelStage]
    placement_rate: float


class RecentApplicant(BaseModel):
    """One row of the "Recent applicants" list.

    `status`/`status_label` reflect how far the applicant has progressed across
    all their referrals (the most advanced stage, or "new" when they have none
    yet). `created_at` is the raw ISO timestamp — the client renders the relative
    "2h ago" / "Yesterday" label.
    """

    id: UUID
    name: str
    initials: str
    role: str | None = None
    location: str | None = None
    created_at: str
    status: str  # hired | interview | referred | withdrawn | not_hired | new
    status_label: str


class TopHiringCompany(BaseModel):
    """One row of the "Top hiring companies" list, with its placement count."""

    id: UUID
    name: str
    initials: str
    company_type: CompanyType
    hires: int


class TopHiringCompanies(BaseModel):
    """The "Top hiring companies" list — companies ranked by placements made in
    the calendar month of the filter's `end_date` (echoed as `year`/`month`)."""

    year: int
    month: int
    items: list[TopHiringCompany]


class DashboardActivity(BaseModel):
    """The dashboard "activity" section: the two side-by-side lists.

    `recent_applicants` is the latest registrations (not date-scoped);
    `top_hiring_companies` is ranked over the calendar month of `end_date`.
    """

    recent_applicants: list[RecentApplicant]
    top_hiring_companies: TopHiringCompanies


class DashboardSummary(BaseModel):
    """Payload for the four dashboard summary cards over a date window.

    The window is echoed back (`start_date`/`end_date`) since the endpoint fills
    in defaults when the client omits them.
    """

    start_date: date
    end_date: date
    registered_job_seekers: TrendCard
    active_job_listings: NewCard
    open_vacancies: VacanciesCard
    placements: TrendCard

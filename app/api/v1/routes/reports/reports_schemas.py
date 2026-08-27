from datetime import date

from pydantic import BaseModel


class VacanciesSolicitedCard(BaseModel):
    """Headline "Vacancies solicited" metric with a period-over-period badge.

    `value` is the total vacancies solicited within the selected window;
    `change` is the absolute change versus the immediately preceding window of
    equal length (may be negative). ``None`` when that prior window had no
    vacancies — there is no baseline to compare against, so the client hides the
    "vs last period" badge.
    """

    value: int
    change: int | None = None


class TopOccupation(BaseModel):
    """The occupation (job title) accounting for the most solicited vacancies in
    the window. `occupation` is ``None`` when no vacancies were solicited."""

    occupation: str | None = None
    vacancies: int


class MonthlyCount(BaseModel):
    """One bar of a per-month report chart — a count for a calendar month."""

    year: int
    month: int  # 1-12
    label: str  # abbreviated month name, e.g. "Jul"
    count: int


class JobSolicitedRow(BaseModel):
    """One row of the Job Solicited report table — a single solicited vacancy
    posting with the establishment that opened it.

    List-valued fields (`civil_status`, `educational_attainment`) mirror the
    job's requirements; the client joins them for display. `company` is ``None``
    when the owning establishment has since been deleted.
    """

    job_title: str
    no_of_vacancies: int
    age_range: str | None = None
    sex: str | None = None
    civil_status: list[str]
    educational_attainment: list[str]
    course_program: str | None = None
    company: str | None = None
    salary_per_month: int | None = None


class JobSolicitedReport(BaseModel):
    """The "Job Solicited" report: vacancies solicited from partner establishments.

    The four summary cards are scoped to the selected `[start_date, end_date]`
    window (echoed back since the endpoint fills in defaults). `monthly` is the
    trailing six calendar months ending at `end_date`'s month, giving the chart
    a stable half-year trend regardless of the window width.
    """

    start_date: date
    end_date: date
    vacancies_solicited: VacanciesSolicitedCard
    establishments_engaged: int
    avg_per_establishment: float
    top_occupation: TopOccupation
    monthly: list[MonthlyCount]
    rows: list[JobSolicitedRow]


class ApplicantReferredRow(BaseModel):
    """One row of the Applicant Referred report table — a single referral of an
    applicant to an employer's vacancy.

    Applicant attributes (address, skills, demographics, education) are snapshots
    resolved from the applicant record; `position`/`company_referred` come from
    the referred job. `job_location` is the referred job's work location.
    """

    name: str | None = None
    address: str | None = None
    skills: list[str]
    gender: str | None = None
    civil_status: str | None = None
    age: int | None = None
    education: str | None = None
    course_program: str | None = None
    position: str | None = None
    status: str | None = None  # human label for the referral's lifecycle status
    date_referred: str  # ISO timestamp; the client renders the date
    contact_number: str | None = None
    company_referred: str | None = None
    job_location: str | None = None


class ApplicantReferredReport(BaseModel):
    """The "Applicant Referred" report: job seekers referred to employers.

    The four summary cards are scoped to the selected `[start_date, end_date]`
    window (echoed back since the endpoint fills in defaults), except
    `total_referrals` which is an all-time count. `monthly` is the full Jan–Dec
    of the window's end year.
    """

    start_date: date
    end_date: date
    referrals_made: int
    unique_applicants: int
    to_interview_pct: float
    total_referrals: int
    monthly: list[MonthlyCount]
    rows: list[ApplicantReferredRow]


class TopPlacedPosition(BaseModel):
    """The position accounting for the most placements in the window. `position`
    is ``None`` when there were no placements."""

    position: str | None = None
    placements: int


class ApplicantPlacedRow(BaseModel):
    """One row of the Applicant Placed report table — a single applicant hired
    through the office.

    Applicant attributes are snapshots resolved from the applicant record;
    `position`/`company_placed` come from the job they were hired into.
    `date_placed` is the hire-event time. `city_province_address` is intentionally
    always blank (a placeholder column for the export layout).
    """

    name: str | None = None
    address: str | None = None
    skills: list[str]
    gender: str | None = None
    civil_status: str | None = None
    age: int | None = None
    education: str | None = None
    course_program: str | None = None
    position: str | None = None
    date_placed: str  # ISO timestamp; the client renders the date
    contact_number: str | None = None
    company_placed: str | None = None
    city_province_address: str | None = None


class ApplicantPlacedReport(BaseModel):
    """The "Applicant Placed" report: job seekers hired through the office.

    The summary cards are scoped to the selected `[start_date, end_date]` window
    (echoed back since the endpoint fills in defaults), except `total_placements`
    which is an all-time count. `monthly` is the full Jan–Dec of the window's end
    year.
    """

    start_date: date
    end_date: date
    placements_made: int
    unique_applicants: int
    top_position: TopPlacedPosition
    total_placements: int
    monthly: list[MonthlyCount]
    rows: list[ApplicantPlacedRow]


class NewRegistrantsCard(BaseModel):
    """Headline "New registrants" metric with a period-over-period badge.

    `value` is the count registered within the window; `change_pct` compares it
    to the immediately preceding window of equal length (``None`` when that prior
    window is empty — no baseline to compare against)."""

    value: int
    change_pct: float | None = None


class ApplicantRegisteredRow(BaseModel):
    """One row of the Applicant Registered report table — a newly registered job
    seeker. `status` is "Active" once the applicant has been acted on (referred),
    else "New". `desired_roles` lists every preferred occupation."""

    name: str | None = None
    age: int | None = None
    sex: str | None = None
    education: str | None = None
    course_program: str | None = None
    school_university: str | None = None
    desired_roles: list[str]
    registered: str  # ISO timestamp; the client renders the date
    status: str


class ApplicantRegisteredReport(BaseModel):
    """The "Applicant Registered" report: new job seekers registered.

    Summary cards are scoped to the selected `[start_date, end_date]` window
    (echoed back since the endpoint fills in defaults), except `total_registrants`
    which is an all-time count. `female_pct` / `male_pct` are shares of the
    window's registrants. `monthly` is the full Jan–Dec of the window's end year.
    """

    start_date: date
    end_date: date
    new_registrants: NewRegistrantsCard
    total_registrants: int
    female_pct: float
    male_pct: float
    monthly: list[MonthlyCount]
    rows: list[ApplicantRegisteredRow]


class LabeledCount(BaseModel):
    """A generic category label with its count (e.g. one bar of a breakdown)."""

    label: str
    count: int


class NewEstablishmentsCard(BaseModel):
    """Headline "New this period" metric with an absolute change badge.

    `value` is the count registered within the window; `change` is the change
    versus the immediately preceding window of equal length (``None`` when that
    prior window is empty)."""

    value: int
    change: int | None = None


class EstablishmentRow(BaseModel):
    """One row of the Establishment directory table — a registered partner
    employer with its business type and job count."""

    company: str
    type: str
    address: str | None = None
    contact_number: str | None = None
    email: str | None = None
    jobs_posted: int
    registered: str  # ISO timestamp; the client renders the date


class EstablishmentsRegisteredReport(BaseModel):
    """The "Establishments Registered" report: partner employers registered.

    `total_establishments`, `corporations` and `with_active_jobs` are all-time
    counts; only `new_this_period` is scoped to the selected window. `by_type` is
    the all-time breakdown by business type (chart); `rows` is the full directory.
    """

    start_date: date
    end_date: date
    total_establishments: int
    new_this_period: NewEstablishmentsCard
    corporations: int
    with_active_jobs: int
    by_type: list[LabeledCount]
    rows: list[EstablishmentRow]


# --- Accomplishment --------------------------------------------------------


class PesoAccomplishmentReport(BaseModel):
    """The "Accomplishment Report": the roll-up of all facilitation services for
    the selected window.

    A single roll-up of the office's headline indicators, all scoped to
    `[start_date, end_date]`. `placement_rate` is `applicants_placed /
    applicants_referred` as a percentage (0.0 when no referrals). All figures
    are counts within the window; establishments_engaged is the number of
    distinct partners that solicited vacancies in the window.
    """

    start_date: date
    end_date: date
    job_seekers_registered: int
    establishments_engaged: int
    vacancies_solicited: int
    applicants_referred: int
    applicants_placed: int
    placement_rate: float


# --- Employment Summary ----------------------------------------------------


class SummaryMetric(BaseModel):
    """A headline metric with a period-over-period badge.

    `value` is the count within the selected window; `change_pct` is the
    percentage change versus the immediately preceding window of equal length
    (may be negative). ``None`` when that prior window was empty — there is no
    baseline to compare against, so the client hides the "vs last period" badge.
    """

    value: int
    change_pct: float | None = None


class TopVacancyRow(BaseModel):
    """One row of the "Top 10 job vacancies" table — an open listing and the
    establishment that posted it. `company` / `company_avatar` / `location` are
    ``None`` when the listing has no linked company, the company has no logo, or
    there is no recorded location. `company_avatar` is the company logo (a data
    URL / image URL); the client falls back to generated initials when absent."""

    job_title: str
    company: str | None = None
    company_avatar: str | None = None
    vacancies: int
    location: str | None = None


class EmploymentSummaryReport(BaseModel):
    """The "Employment Summary Report": a one-glance snapshot of the office's
    employment facilitation for the selected window.

    The three headline cards (`vacancies_solicited`, `registered_applicants`,
    `placed_applicants`) each carry a period-over-period `change_pct`. The three
    breakdowns are all scoped to `[start_date, end_date]`:

    - Employment status of the registered cohort: `employed` + `unemployed` sum
      to `registered_total`. A registrant reads as employed only when one of
      their referrals reached HIRED; everyone else — no referral, or a referral
      that never reached HIRED — reads as unemployed.
    - Placement rate: `placed` out of `referred`, as `placement_rate` (a
      percentage, 0.0 when there were no referrals).
    - Gender split of the unemployed cohort: `unemployed_male` +
      `unemployed_female` (registrants with an unknown sex fall in neither).

    `top_vacancies` ranks the window's solicited listings by number of vacancies
    (top 10), scoped to `[start_date, end_date]` like the other figures.
    """

    start_date: date
    end_date: date
    vacancies_solicited: SummaryMetric
    registered_applicants: SummaryMetric
    placed_applicants: SummaryMetric
    registered_total: int
    employed: int
    unemployed: int
    referred: int
    placed: int
    placement_rate: float
    unemployed_male: int
    unemployed_female: int
    top_vacancies: list[TopVacancyRow]


# --- Referral-to-Placement Funnel ------------------------------------------


class ReferralFunnelReport(BaseModel):
    """The "Referral-to-Placement Funnel": conversion at each stage from referral
    through to hire, for the selected window.

    Cohort = referrals created in `[start_date, end_date]` (recommendations with a
    lifecycle status). `interviewed` counts that cohort's referrals that reached
    the interview stage or beyond (status `interview_scheduled` or `hired`);
    `hired` counts those with status `hired`. `did_not_convert` is
    `referred - hired`. The percentages are shares of the referred cohort, except
    `interviewed_to_hired_pct`, the share of the interviewed who were hired.
    """

    start_date: date
    end_date: date
    referred: int
    interviewed: int
    hired: int
    did_not_convert: int
    interviewed_pct: float
    hired_pct: float
    interviewed_to_hired_pct: float


# --- Unemployed Applicants by Education ------------------------------------


class UnemployedByEducationReport(BaseModel):
    """The "Unemployed Applicants by Education" report: the unemployed cohort
    registered in the window, profiled by course / program.

    The cohort is the applicants registered in `[start_date, end_date]` who read
    as unemployed — they have no referral that reached HIRED, the same rule the
    Employment Summary uses. `top_courses` ranks their course/program by headcount (top 10)
    and `most_common_course` is the leader. `with_course` counts those who hold
    or are pursuing a course (a non-blank course/program) and is the denominator
    for `top3_share_pct`, the share of that degree-holding group made up by the
    top three courses. `college_graduates` counts the cohort whose highest
    education level reads as a completed college degree.

    `by_education_level` buckets the whole unemployed cohort by highest
    educational attainment (College Graduate, College Undergraduate, Senior High
    School, …), ordered by headcount; `college_graduates` equals its "College
    Graduate" bucket.
    """

    start_date: date
    end_date: date
    total_unemployed: int
    most_common_course: str | None = None
    college_graduates: int
    with_course: int
    top3_share_pct: float
    top_courses: list[LabeledCount]
    by_education_level: list[LabeledCount]

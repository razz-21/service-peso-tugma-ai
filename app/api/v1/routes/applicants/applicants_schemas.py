from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.api.v1.routes.recommended_jobs.recommended_jobs_models import RecommendedJobStatus

from .applicants_models import (
    Address,
    ApplicantStatus,
    EducationalBackground,
    Eligibility,
    PreferredOccupationIndustry,
    Sex,
    Training,
    WorkExperience,
)

NAME_MAX = 100

MIN_WORKING_AGE = 1
MAX_REALISTIC_AGE = 120


def _to_isoformat(value: object) -> object:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _validate_date_of_birth(value: object) -> object:
    """Shared DOB validator: rejects future dates, under-age, and unrealistic ages.

    Runs *after* ``_to_isoformat`` (mode='after'), so ``value`` is always an ISO
    string, a ``date``/``datetime``, or ``None`` at this point.
    """
    if value is None:
        return value

    # Parse from ISO string produced by _to_isoformat, or accept date/datetime directly.
    if isinstance(value, str):
        try:
            dob = date.fromisoformat(value[:10])  # strip time component if present
        except ValueError:
            raise ValueError("Invalid date format. Use YYYY-MM-DD.")
    elif isinstance(value, datetime):
        dob = value.date()
    elif isinstance(value, date):
        dob = value
    else:
        raise ValueError("Invalid date value.")

    today = datetime.now(UTC).date()

    if dob > today:
        raise ValueError("Date of birth cannot be in the future.")

    # Compute age in whole years.
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    if age < MIN_WORKING_AGE:
        raise ValueError(
            f"Applicant must be at least {MIN_WORKING_AGE} years old."
        )

    if age > MAX_REALISTIC_AGE:
        raise ValueError("Date of birth appears unrealistic.")

    return value


class ApplicantBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firstname: str = Field(min_length=1, max_length=NAME_MAX)
    lastname: str = Field(min_length=1, max_length=NAME_MAX)
    middlename: str | None = None
    suffix: str | None = None
    date_of_birth: str | None = None
    sex: Sex | None = None
    civil_status: str | None = None
    citizenship: str | None = None
    height_in_cm: float | None = None
    weight_in_kg: float | None = None
    present_address: Address = Field(default_factory=Address)
    permanent_address: Address | None = None
    primary_mobile_number: str | None = None
    secondary_mobile_number: str | None = None
    email_address: EmailStr | None = None
    employment_status: str | None = None
    status: ApplicantStatus = ApplicantStatus.ACTIVE
    preferred_occupation_industry: list[PreferredOccupationIndustry] = Field(default_factory=list)
    preferred_work_location: list[str] = Field(default_factory=list)
    salary_expectation: str | None = None
    educational_background: EducationalBackground | None = None
    trainings: list[Training] = Field(default_factory=list)
    eligibility: list[Eligibility] = Field(default_factory=list)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    technical_skills: list[str] = Field(default_factory=list)
    workspace_id: UUID
    created_by: UUID | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("date_of_birth", "created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_isoformat(cls, value: object) -> object:
        return _to_isoformat(value)


class ApplicantCreate(BaseModel):
    firstname: str = Field(min_length=1, max_length=NAME_MAX)
    lastname: str = Field(min_length=1, max_length=NAME_MAX)
    middlename: str | None = None
    suffix: str | None = None
    date_of_birth: str | None = None
    sex: Sex | None = None
    civil_status: str | None = None
    citizenship: str | None = None
    height_in_cm: float | None = None
    weight_in_kg: float | None = None
    present_address: Address = Field(default_factory=Address)
    permanent_address: Address | None = None
    primary_mobile_number: str | None = None
    secondary_mobile_number: str | None = None
    email_address: EmailStr | None = None
    employment_status: str | None = None
    status: ApplicantStatus = ApplicantStatus.ACTIVE
    preferred_occupation_industry: list[PreferredOccupationIndustry] = Field(default_factory=list)
    preferred_work_location: list[str] = Field(default_factory=list)
    salary_expectation: str | None = None
    educational_background: EducationalBackground | None = None
    trainings: list[Training] = Field(default_factory=list)
    eligibility: list[Eligibility] = Field(default_factory=list)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    technical_skills: list[str] = Field(default_factory=list)
    # Registration date (ISO). Lets an officer backdate an applicant to a previous
    # registration instead of "now"; omitted → the server stamps `created_at` now.
    created_at: str | None = None

    @field_validator("date_of_birth", "created_at", mode="before")
    @classmethod
    def _coerce_isoformat(cls, value: object) -> object:
        return _to_isoformat(value)

    @field_validator("date_of_birth", mode="after")
    @classmethod
    def _validate_dob(cls, value: object) -> object:
        return _validate_date_of_birth(value)


class ApplicantPatch(BaseModel):
    firstname: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    lastname: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    middlename: str | None = None
    suffix: str | None = None
    date_of_birth: str | None = None
    sex: Sex | None = None
    civil_status: str | None = None
    citizenship: str | None = None
    height_in_cm: float | None = None
    weight_in_kg: float | None = None
    present_address: Address | None = None
    permanent_address: Address | None = None
    primary_mobile_number: str | None = None
    secondary_mobile_number: str | None = None
    email_address: EmailStr | None = None
    employment_status: str | None = None
    status: ApplicantStatus | None = None
    preferred_occupation_industry: list[PreferredOccupationIndustry] | None = None
    preferred_work_location: list[str] | None = None
    salary_expectation: str | None = None
    educational_background: EducationalBackground | None = None
    trainings: list[Training] | None = None
    eligibility: list[Eligibility] | None = None
    work_experience: list[WorkExperience] | None = None
    technical_skills: list[str] | None = None

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _coerce_isoformat(cls, value: object) -> object:
        return _to_isoformat(value)

    @field_validator("date_of_birth", mode="after")
    @classmethod
    def _validate_dob(cls, value: object) -> object:
        return _validate_date_of_birth(value)


class ApplicantFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    size: int
    content_type: str
    uploaded_at: str


class ApplicantRead(ApplicantBase):
    files: list[ApplicantFileRead] = Field(default_factory=list)


class ApplicantList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ApplicantRead]


class ResumeExtractionMeta(BaseModel):
    """How the resume text was obtained (surfaced to the UI for transparency)."""

    method: str  # "text" (PDF text layer) or "ocr" (scanned image)
    ocr_used: bool
    pages: int
    char_count: int


class ResumeExtraction(BaseModel):
    """Best-effort structured fields parsed from an uploaded resume.

    Every field is optional — the parser fills what it can recognize and the
    officer reviews/corrects the rest before submitting (Human-in-the-Loop).
    `email_address` is a plain string (not `EmailStr`): extraction is imperfect,
    so a malformed value should surface for correction, not fail the response.
    """

    firstname: str | None = None
    middlename: str | None = None
    lastname: str | None = None
    suffix: str | None = None
    date_of_birth: str | None = None
    sex: Sex | None = None
    email_address: str | None = None
    primary_mobile_number: str | None = None
    present_address: Address | None = None
    educational_background: EducationalBackground | None = None
    work_experience: list[WorkExperience] = Field(default_factory=list)
    trainings: list[Training] = Field(default_factory=list)
    eligibility: list[Eligibility] = Field(default_factory=list)
    technical_skills: list[str] = Field(default_factory=list)
    preferred_occupation_industry: list[PreferredOccupationIndustry] = Field(default_factory=list)
    raw_text: str = ""
    meta: ResumeExtractionMeta


class ApplicantImportItem(BaseModel):
    """One applicant from a bulk import, plus an optional job assignment.

    When `job_id` is set the applicant is also referred to that job (a referral
    is created in `status`, defaulting to ``referred``). Unlike the manual
    referral flow, importing a referral does **not** consume the job's vacancy.
    """

    applicant: ApplicantCreate
    job_id: UUID | None = None
    status: RecommendedJobStatus | None = None
    # Registered date from the imported file (ISO). Sets the applicant's
    # `created_at`; omitted/None dates the record at import time.
    date_registered: str | None = None

    @field_validator("date_registered", mode="before")
    @classmethod
    def _coerce_isoformat(cls, value: object) -> object:
        return _to_isoformat(value)


class ApplicantImportRequest(BaseModel):
    items: list[ApplicantImportItem] = Field(min_length=1)


class ApplicantImportResult(BaseModel):
    """Summary of a bulk import: how many were registered and referred."""

    created: int
    referred: int
    applicants: list[ApplicantRead]

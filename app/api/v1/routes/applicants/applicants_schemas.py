from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .applicants_models import (
    Address,
    EducationalBackground,
    Eligibility,
    PreferredOccupationIndustry,
    Sex,
    Training,
    WorkExperience,
)

NAME_MAX = 100


def _to_isoformat(value: object) -> object:
    if isinstance(value, datetime | date):
        return value.isoformat()
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
    preferred_occupation_industry: list[PreferredOccupationIndustry] = Field(default_factory=list)
    preferred_work_location: list[str] = Field(default_factory=list)
    salary_expectation: str | None = None
    educational_background: EducationalBackground | None = None
    trainings: list[Training] = Field(default_factory=list)
    eligibility: list[Eligibility] = Field(default_factory=list)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    technical_skills: list[str] = Field(default_factory=list)

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _coerce_isoformat(cls, value: object) -> object:
        return _to_isoformat(value)


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

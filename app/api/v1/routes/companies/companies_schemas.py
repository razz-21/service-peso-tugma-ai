from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .companies_models import CompanyType

# VARCHAR limits carried over from the original relational schema.
COMPANY_NAME_MAX = 30
CONTACT_NUMBER_MAX = 13


class CompanyBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str = Field(min_length=1, max_length=COMPANY_NAME_MAX)
    company_type: CompanyType = CompanyType.SOLE_PROPRIETORSHIP
    email: EmailStr | None = None
    description: str | None = None
    address: str | None = None
    contact_number: str | None = Field(default=None, max_length=CONTACT_NUMBER_MAX)
    avatar: str | None = None
    workspace_id: UUID
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class CompanyCreate(BaseModel):
    company_name: str = Field(min_length=1, max_length=COMPANY_NAME_MAX)
    company_type: CompanyType = CompanyType.SOLE_PROPRIETORSHIP
    email: EmailStr | None = None
    description: str | None = None
    address: str | None = None
    contact_number: str | None = Field(default=None, max_length=CONTACT_NUMBER_MAX)
    avatar: str | None = None


class CompanyPatch(BaseModel):
    company_name: str | None = Field(default=None, min_length=1, max_length=COMPANY_NAME_MAX)
    company_type: CompanyType | None = None
    email: EmailStr | None = None
    description: str | None = None
    address: str | None = None
    contact_number: str | None = Field(default=None, max_length=CONTACT_NUMBER_MAX)
    avatar: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CompanyRead(CompanyBase):
    pass


class CompanyList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CompanyRead]


class CompanyApplicantApplicant(BaseModel):
    """Applicant summary shown in a company's applicants table (resolves the FK)."""

    id: UUID
    name: str
    avatar: str | None = None


class CompanyApplicantRead(BaseModel):
    """One applicant referred to a company, for the company details table.

    A company has no direct applicant link — the association runs through a
    referral: a `recommended_jobs` row (with a lifecycle status set) that points
    an applicant at one of the company's jobs. This projects that referral into
    exactly what the "Applicants" table renders: who was referred, the job they
    were referred to, the referral status, and when they were referred.
    """

    # The referral (recommended_jobs) id — the row's stable key.
    id: UUID
    applicant: CompanyApplicantApplicant | None = None
    # Title of the job the applicant was referred to (None if the job is gone).
    referred_to: str | None = None
    job_id: UUID
    # Referral lifecycle status. Typed as a plain string (its value mirrors
    # RecommendedJobStatus) to avoid a module-load import cycle between the
    # companies and recommended_jobs slices; the client validates the enum.
    status: str
    # When the applicant was referred (recommended_jobs.referred_at).
    date_referred: str


class CompanyApplicantList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CompanyApplicantRead]

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from beanie import Document
from pydantic import BaseModel, Field, field_validator


class AuditEntity(StrEnum):
    """The resource an audit event acted on; drives the frontend filter chips."""

    COMPANY = "company"
    JOB = "job"
    REFERRALS = "referrals"
    APPLICANTS = "applicants"
    SETTINGS = "settings"
    SECURITY = "security"
    WORKSPACES = "workspaces"


class AuditTone(StrEnum):
    """Visual tone applied to an entry's icon tile and category chip."""

    RED = "red"
    GREEN = "green"
    AMBER = "amber"
    GREY = "grey"


class AuditRecord(BaseModel):
    """A record referenced by an event, rendered as an inline highlighted name."""

    label: str


class AuditDiffRow(BaseModel):
    """A before → after change shown inside an event's detail block."""

    label: str
    # `from` is a Python keyword, so it is aliased on the wire.
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}


class AuditLog(Document):
    # Application-generated UUID primary key (stored as Mongo `_id`).
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]
    # Owning workspace (Workspace.id). Scopes the event to a single tenant.
    # Nullable because a few events (e.g. a super-admin sign-in with no workspace
    # selected) have no natural tenant; those simply don't surface in any
    # workspace-scoped feed.
    workspace_id: UUID | None = None
    entity: AuditEntity
    # Human-facing label for the entity, e.g. "Company", "Security".
    entity_label: str
    icon: str
    icon_tone: AuditTone = AuditTone.GREY
    chip_tone: AuditTone = AuditTone.GREY
    # Who performed the action (display name).
    actor: str
    # Action phrase between the actor and the referenced records.
    action: str
    # Highlighted record names shown after the action.
    records: list[AuditRecord] = Field(default_factory=list)
    # Extra inline facts (IP, browser…) shown after the time.
    meta: list[str] = Field(default_factory=list)
    # Free-text detail rendered in a tinted block.
    note: str | None = None
    # Before → after changes rendered in a tinted block.
    diff: list[AuditDiffRow] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("created_at", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        # Tolerate documents whose timestamps were stored as BSON datetimes.
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    class Settings:
        name = "audit_logs"

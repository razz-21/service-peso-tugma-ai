from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .audit_logs_models import AuditDiffRow, AuditEntity, AuditRecord, AuditTone


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    workspace_id: UUID | None = None
    entity: AuditEntity
    entity_label: str
    icon: str
    icon_tone: AuditTone = AuditTone.GREY
    chip_tone: AuditTone = AuditTone.GREY
    actor: str
    action: str
    records: list[AuditRecord] = Field(default_factory=list)
    meta: list[str] = Field(default_factory=list)
    note: str | None = None
    diff: list[AuditDiffRow] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("created_at", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class AuditLogList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditLogRead]

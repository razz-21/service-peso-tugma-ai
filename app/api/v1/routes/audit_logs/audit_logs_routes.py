from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_workspace_id

from . import audit_logs_service
from .audit_logs_models import AuditEntity
from .audit_logs_schemas import AuditLogList, AuditLogRead

router = APIRouter()


@router.get("", response_model=AuditLogList)
async def list_audit_logs(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    entity: Annotated[AuditEntity | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query()] = None,
) -> AuditLogList:
    logs, total = await audit_logs_service.list_audit_logs(
        limit=limit, offset=offset, workspace_id=workspace_id, entity=entity, q=q
    )
    return AuditLogList(
        total=total,
        limit=limit,
        offset=offset,
        items=[AuditLogRead.model_validate(log) for log in logs],
    )

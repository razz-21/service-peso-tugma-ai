import logging
import re
from uuid import UUID

from beanie.operators import Or, RegEx

from .audit_logs_models import AuditDiffRow, AuditEntity, AuditLog, AuditRecord, AuditTone

logger = logging.getLogger(__name__)


async def record_audit(
    *,
    entity: AuditEntity,
    entity_label: str,
    actor: str,
    action: str,
    icon: str,
    workspace_id: UUID | None = None,
    icon_tone: AuditTone = AuditTone.GREY,
    chip_tone: AuditTone = AuditTone.GREY,
    records: list[str] | None = None,
    meta: list[str] | None = None,
    note: str | None = None,
    diff: list[AuditDiffRow] | None = None,
) -> None:
    """Persist a single audit entry.

    Best-effort by design: an audit write must never break the operation it
    describes, so any failure is logged and swallowed rather than raised back
    into the request handler.
    """
    try:
        log = AuditLog(
            workspace_id=workspace_id,
            entity=entity,
            entity_label=entity_label,
            icon=icon,
            icon_tone=icon_tone,
            chip_tone=chip_tone,
            actor=actor,
            action=action,
            records=[AuditRecord(label=label) for label in (records or [])],
            meta=meta or [],
            note=note,
            diff=diff or [],
        )
        await log.insert()
    except Exception:
        logger.exception("Failed to record audit log (%s: %s)", entity.value, action)


async def list_audit_logs(
    limit: int,
    offset: int,
    workspace_id: UUID,
    entity: AuditEntity | None = None,
    q: str | None = None,
) -> tuple[list[AuditLog], int]:
    # Scoped to the workspace so one tenant never sees another's activity trail.
    query = AuditLog.find(AuditLog.workspace_id == workspace_id)
    if entity is not None:
        query = query.find(AuditLog.entity == entity)
    if q is not None:
        pattern = re.escape(q)
        query = query.find(
            Or(
                RegEx(AuditLog.actor, pattern, "i"),
                RegEx(AuditLog.action, pattern, "i"),
                RegEx(AuditLog.note, pattern, "i"),
            )
        )
    total = await query.count()
    logs = await query.sort("-created_at").skip(offset).limit(limit).to_list()
    return logs, total

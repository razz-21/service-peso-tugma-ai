import re
from datetime import UTC, datetime
from uuid import UUID

from beanie.operators import Or, RegEx

from app.core.blob import replace_avatar_blob

from .companies_models import Company
from .companies_schemas import CompanyCreate, CompanyPatch


async def get_company(company_id: UUID, workspace_id: UUID) -> Company | None:
    # Scoped to the workspace: a company belonging to another tenant resolves to
    # None, so callers surface it as a 404 rather than leaking cross-workspace data.
    return await Company.find_one(Company.id == company_id, Company.workspace_id == workspace_id)


async def create_company(data: CompanyCreate, workspace_id: UUID) -> Company:
    company = Company(**data.model_dump(), workspace_id=workspace_id)
    await company.insert()
    return company


async def list_companies(
    limit: int, offset: int, workspace_id: UUID, q: str | None = None
) -> tuple[list[Company], int]:
    query = Company.find(Company.workspace_id == workspace_id)
    if q is not None:
        pattern = re.escape(q)
        query = query.find(
            Or(
                RegEx(Company.company_name, pattern, "i"),
                RegEx(Company.description, pattern, "i"),
                RegEx(Company.address, pattern, "i"),
            )
        )
    total = await query.count()
    companies = await query.sort("-created_at").skip(offset).limit(limit).to_list()
    return companies, total


async def update_company(company: Company, data: CompanyPatch) -> Company:
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(company, field, value)
    await company.save()
    return company


async def set_company_avatar(company: Company, *, extension: str, data: bytes) -> Company:
    """Upload a new avatar image and store its Blob URL on the company."""
    company.avatar = await replace_avatar_blob(
        prefix="companies",
        entity_id=company.id,
        extension=extension,
        data=data,
        previous_url=company.avatar,
    )
    company.updated_at = datetime.now(UTC).isoformat()
    await company.save()
    return company


async def delete_company(company: Company) -> bool:
    result = await company.delete()
    return result is not None and result.acknowledged

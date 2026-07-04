import re
from uuid import UUID

from beanie.operators import Or, RegEx

from .companies_models import Company
from .companies_schemas import CompanyCreate, CompanyPatch


async def get_company(company_id: UUID) -> Company | None:
    return await Company.get(company_id)


async def create_company(data: CompanyCreate) -> Company:
    company = Company(**data.model_dump())
    await company.insert()
    return company


async def list_companies(
    limit: int, offset: int, q: str | None = None
) -> tuple[list[Company], int]:
    query = Company.find_all()
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


async def delete_company(company: Company) -> bool:
    result = await company.delete()
    return result is not None and result.acknowledged

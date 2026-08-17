import re
from datetime import UTC, datetime
from uuid import UUID

from beanie.operators import In, Or, RegEx

from app.api.v1.routes.workspaces.workspaces_models import Workspace
from app.core.blob import delete_avatar_blob, replace_avatar_blob
from app.core.security import hash_password, verify_password

from .users_models import User, UserRole
from .users_schemas import MePatch, UserCreate, UserPatch, UserRead, WorkspaceRef


def _to_read(user: User, workspace: Workspace | None) -> UserRead:
    """Build a `UserRead`, embedding the user's workspace (id + name)."""
    return UserRead.model_validate(
        {
            **user.model_dump(),
            "workspace": WorkspaceRef.model_validate(workspace) if workspace else None,
        }
    )


async def build_user_read(user: User) -> UserRead:
    """Read model for a single user, resolving its workspace reference."""
    workspace = None
    if user.workspace_id is not None:
        workspace = await Workspace.get(user.workspace_id)
    return _to_read(user, workspace)


async def get_user(user_id: UUID) -> User | None:
    return await User.get(user_id)


async def get_user_by_email(email: str) -> User | None:
    return await User.find_one(User.email == email)


async def create_user(data: UserCreate, workspace_id: UUID | None = None) -> User:
    user = User(
        **data.model_dump(exclude={"password"}),
        password=hash_password(data.password),
        workspace_id=workspace_id,
    )
    await user.insert()
    return user


async def list_users(
    limit: int,
    offset: int,
    q: str | None = None,
    role: UserRole | None = None,
    workspace_id: UUID | None = None,
) -> tuple[list[UserRead], int]:
    query = User.find_all()
    if q is not None:
        pattern = re.escape(q)
        query = query.find(Or(RegEx(User.fullname, pattern, "i"), RegEx(User.email, pattern, "i")))
    if role is not None:
        query = query.find(User.role == role)
    if workspace_id is not None:
        query = query.find(User.workspace_id == workspace_id)
    total = await query.count()
    users = await query.sort("-created_at").skip(offset).limit(limit).to_list()

    # Resolve each user's workspace name in one batched query (avoids N+1).
    workspace_ids = {user.workspace_id for user in users if user.workspace_id is not None}
    workspaces_by_id: dict[UUID, Workspace] = {}
    if workspace_ids:
        workspaces = await Workspace.find(In(Workspace.id, list(workspace_ids))).to_list()
        workspaces_by_id = {workspace.id: workspace for workspace in workspaces}

    items = [
        _to_read(
            user,
            workspaces_by_id.get(user.workspace_id) if user.workspace_id is not None else None,
        )
        for user in users
    ]
    return items, total


async def update_user(user: User, data: UserPatch | MePatch) -> User:
    changes = data.model_dump(exclude_unset=True)
    password = changes.pop("password", None)
    if password is not None:
        user.password = hash_password(password)
    for field, value in changes.items():
        setattr(user, field, value)
    await user.save()
    return user


async def set_user_avatar(user: User, *, extension: str, data: bytes) -> User:
    """Upload a new avatar image and store its Blob URL on the user."""
    user.avatar = await replace_avatar_blob(
        prefix="users",
        entity_id=user.id,
        extension=extension,
        data=data,
        previous_url=user.avatar,
    )
    user.updated_at = datetime.now(UTC).isoformat()
    await user.save()
    return user


async def clear_user_avatar(user: User) -> User:
    """Remove the user's avatar, deleting the stored Blob best-effort."""
    await delete_avatar_blob(user.avatar)
    user.avatar = None
    user.updated_at = datetime.now(UTC).isoformat()
    await user.save()
    return user


async def delete_user(user: User) -> bool:
    result = await user.delete()
    return result.acknowledged


async def authenticate(email: str, password: str) -> User | None:
    user = await get_user_by_email(email)
    if user is None or not verify_password(password, user.password):
        return None
    return user

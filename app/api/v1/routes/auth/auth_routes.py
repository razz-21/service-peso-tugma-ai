from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.v1.routes.users import users_service as user_service
from app.api.v1.routes.users.users_schemas import UserCreate, UserRead
from app.core.security import create_access_token

from .auth_schemas import Token

router = APIRouter()


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate) -> UserRead:
    if await user_service.get_user_by_email(data.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    user = await user_service.create_user(data)
    return UserRead.model_validate(user)


@router.post("/login", response_model=Token)
async def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]) -> Token:
    user = await user_service.authenticate(form_data.username, form_data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    return Token(access_token=create_access_token(str(user.id)))

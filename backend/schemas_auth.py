"""认证与收藏相关的 Pydantic 模型。"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("密码不能为空")
        return v


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserPublic(BaseModel):
    id: int
    email: str

    model_config = {"from_attributes": True}


class FavoriteAddBody(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    price_cny: float = Field(ge=0)
    image_url: str = Field(min_length=1, max_length=2048)
    original_url: str = Field(min_length=8, max_length=2048)
    platform: str = Field(min_length=1, max_length=128)


class FavoriteOut(BaseModel):
    id: int
    title: str
    price_cny: float
    image_url: str
    original_url: str
    platform: str
    created_at: dt.datetime

    model_config = {"from_attributes": True}

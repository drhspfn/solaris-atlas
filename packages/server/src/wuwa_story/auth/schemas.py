from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    nickname: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class CompleteGoogleRequest(BaseModel):
    nickname: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class LinkGoogleRequest(BaseModel):
    password: str = Field(max_length=128)


class NicknameRequest(BaseModel):
    nickname: str = Field(min_length=3, max_length=32)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    nickname: str
    role: Literal["user", "admin"]
    google_connected: bool
    created_at: datetime


class PendingGoogleResponse(BaseModel):
    email: EmailStr
    nickname: str


class MessageResponse(BaseModel):
    message: str

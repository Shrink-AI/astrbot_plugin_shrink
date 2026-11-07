from typing import Literal

from pydantic import BaseModel, Field


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=15, max_length=128)


class TextEdit(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class MemeEdit(BaseModel):
    title: str = Field(max_length=120)
    tags: str = Field(max_length=500)
    enabled: bool


class KindEdit(BaseModel):
    kind: Literal["human", "bot"]


class NicknameCreate(BaseModel):
    name: str = Field(min_length=1, max_length=12)
    scope: str = Field("", max_length=64)


class Verdict(BaseModel):
    verdict: Literal["correct", "wrong", "skip"]


class BatchAction(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=20)


class TeachMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ProposalAccept(BaseModel):
    text: str | None = Field(None, max_length=300)
    name: str | None = Field(None, max_length=12)
    meaning: str | None = Field(None, max_length=80)
    importance: int | None = Field(None, ge=1, le=5)


class EnabledEdit(BaseModel):
    enabled: bool


class SkillEdit(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=30)
    trigger: str | None = Field(None, min_length=1, max_length=120)
    steps: str | None = Field(None, min_length=1, max_length=600)
    status: Literal["active", "retired"] | None = None

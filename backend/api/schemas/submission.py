from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, HttpUrl


class SubmissionCreateRequest(BaseModel):
    event_url: HttpUrl
    submitter_name: str | None = Field(default=None, max_length=200)
    submitter_email: EmailStr | None = None
    notes: str | None = None


class SubmissionCreateResponse(BaseModel):
    id: str
    status: str


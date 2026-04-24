from __future__ import annotations

from pydantic import BaseModel, Field


class FacetItem(BaseModel):
    name: str
    count: int = Field(ge=0)


class FacetsListResponse(BaseModel):
    items: list[FacetItem]

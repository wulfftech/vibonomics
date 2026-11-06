"""The local copy of the OpenRouter account's presets: read it, or sync it now.

``GET /api/presets/catalog`` reads what the last sync stored and calls nothing. A preset's config can hold anything the
owner put in it, so a visitor to an instance that is open to read gets a 401 here, not the config. ``POST
/api/presets/sync`` reads the account with the operator's key; it is a POST, so the login guard already refuses a
visitor and a page on another site.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import preset_catalog
from .auth import is_authenticated

router = APIRouter(prefix="/api/presets", tags=["presets"])


class CatalogPresetOut(BaseModel):
    slug: str
    preset_id: str | None = Field(description="The id OpenRouter gives the preset, null when it names none")
    name: str
    status: str = Field(description="`active` or `archived`")
    version: int | None = Field(description="The live version; null when OpenRouter returned none")
    updated_at: str | None
    models: list[str] = Field(description="The `model` and the `models` fallbacks, in order, once each")
    providers: list[str] = Field(description="`provider.only`")
    config: dict[str, Any] = Field(description="The live version's config as OpenRouter holds it")
    has_system_prompt: bool = Field(
        description="The live version has a system prompt. A write sends it back unless the preset's owner manages it"
    )
    passthrough_keys: list[str] = Field(
        description="Config keys Vibonomics has no setting for and would send back as they are, "
        "such as `temperature` or `provider.data_collection`"
    )
    managed: bool = Field(description="Whether Vibonomics manages this preset; worked out when the catalogue is read")


class PresetCatalogOut(BaseModel):
    synced_at: datetime | None = Field(description="When the last sync finished; null before the first")
    syncing: bool = Field(description="A sync is running now")
    error: str | None = Field(
        description="Why the last sync could not read everything: no key, OpenRouter failing, or a partial read"
    )
    presets: list[CatalogPresetOut]


def _body() -> PresetCatalogOut:
    return PresetCatalogOut(**preset_catalog.view())


@router.get("/catalog", response_model=PresetCatalogOut)
async def read_preset_catalog(request: Request) -> PresetCatalogOut:
    """Every preset on the account as of the last sync, with the ones Vibonomics manages marked."""
    if not is_authenticated(request):
        raise HTTPException(401, "Not authenticated")
    return await asyncio.to_thread(_body)


@router.post("/sync", response_model=PresetCatalogOut)
async def sync_preset_catalog() -> PresetCatalogOut:
    """Read the account's presets now. A sync already running is joined, not repeated."""
    await preset_catalog.sync()
    return await asyncio.to_thread(_body)

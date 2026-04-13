"""Settings API routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from btwin_core.locale_settings import LocaleSettingsPatch, LocaleSettingsStore


def create_settings_router(data_dir: Path) -> APIRouter:
    store = LocaleSettingsStore(data_dir)
    router = APIRouter()

    @router.get("/api/settings/locale")
    def get_locale_settings():
        return store.read().model_dump()

    @router.patch("/api/settings/locale")
    def patch_locale_settings(payload: LocaleSettingsPatch):
        return store.update(payload).model_dump()

    return router

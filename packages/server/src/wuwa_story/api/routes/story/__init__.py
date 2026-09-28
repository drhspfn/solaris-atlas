"""Story browsing route package."""

from fastapi import APIRouter

from wuwa_story.api.routes.story import catalog, profiles, transcripts

router = APIRouter()
router.include_router(catalog.router)
router.include_router(profiles.router)
router.include_router(transcripts.router)

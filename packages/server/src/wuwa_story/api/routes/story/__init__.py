"""Story browsing route package."""

from fastapi import APIRouter

from wuwa_story.api.routes.story import catalog, profiles, transcripts
from wuwa_story.api.routes.story.shared import (
    _dialogue_payload,
    _node_label,
    _quest_info,
    _release_id,
)
from wuwa_story.api.routes.story.transcripts import search_dialogue

router = APIRouter()
router.include_router(catalog.router)
router.include_router(profiles.router)
router.include_router(transcripts.router)

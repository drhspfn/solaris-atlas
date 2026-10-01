"""Story browsing route package."""

from fastapi import APIRouter

from wuwa_story.api.routes.story import catalog, character_archive, continuity, map, media, profiles, transcripts
from wuwa_story.api.routes.story.shared import (
    _dialogue_payload as _dialogue_payload,
)
from wuwa_story.api.routes.story.shared import (
    _node_label as _node_label,
)
from wuwa_story.api.routes.story.shared import (
    _quest_info as _quest_info,
)
from wuwa_story.api.routes.story.shared import (
    _release_id as _release_id,
)
from wuwa_story.api.routes.story.transcripts import search_dialogue as search_dialogue

router = APIRouter()
router.include_router(catalog.router)
router.include_router(profiles.router)
router.include_router(character_archive.router)
router.include_router(continuity.router)
router.include_router(map.router)
router.include_router(media.router)
router.include_router(transcripts.router)

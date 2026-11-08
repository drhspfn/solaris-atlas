"""Retrieval invariants and real PostgreSQL/storage workflow on an isolated test DB."""

import math
import os
from types import SimpleNamespace

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.db.models import (
    User,
    VisualEmbedding,
)
from wuwa_story.storage.local import LocalStorage
from wuwa_story.visual_memory.images import encode_png
from wuwa_story.visual_memory.service import (
    VisualMemoryService,
    identification_status,
    normalized_vector,
    rank_candidates,
)


def test_vectors_and_balanced_scores():
    assert normalized_vector([3, 4]) == [0.6, 0.8]
    for values in ([], [0, 0], [math.nan, 1], [math.inf]):
        with pytest.raises(ValueError):
            normalized_vector(values)

    def row(identity, reference, source, similarity):
        return {
            "identity_id": identity,
            "reference_id": reference,
            "source_sha256": source,
            "name": str(identity),
            "kind": "npc",
            "similarity": similarity,
        }

    # Five hundred duplicate crops must not crowd out the rare exact-match NPC.
    rows = [row(1, n, "same screenshot", 0.8) for n in range(500)]
    rows += [row(2, 501, "rare NPC", 0.99)]
    found = rank_candidates(rows)
    assert [x["identity_id"] for x in found] == [2, 1]
    assert found[1]["independent_references"] == 1
    assert identification_status([]) == "unknown"
    assert identification_status([{**found[0], "best_similarity": 0.1}]) == "unknown"
    assert identification_status([found[0], dict(found[0])]) == "unresolved"


class TestEngine:
    __test__ = False

    def __init__(self, key="visual-test-384"):
        self.key = key

    def embed(self, image):
        # Controlled, non-production vectors exercise PostgreSQL/model boundaries.
        return SimpleNamespace(
            model_key=self.key,
            vector=[1.0] + [0.0] * 383,
            config={"model_name": "test", "weights_sha256": self.key},
        )

    def detect(self, image):
        return [{"bbox": [0, 0, image.width, image.height], "score": 0.9}]


@pytest_asyncio.fixture
async def memory(tmp_path):
    url = os.getenv("WUWA_VISUAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set WUWA_VISUAL_TEST_DATABASE_URL to an isolated migrated PostgreSQL database")
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(connection, expire_on_commit=False, autoflush=False) as session:
            user = User(
                email="visual-test@example.invalid",
                email_normalized="visual-test@example.invalid",
                nickname="visual-test",
                nickname_normalized="visual-test",
                password_hash="unused",
                role=2,
            )
            session.add(user)
            await session.flush()
            service = VisualMemoryService(session, LocalStorage(tmp_path), TestEngine())
            yield service, session, user.id
        await transaction.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reference_review_search_retention_and_model_isolation(memory):
    service, session, actor = memory
    lore = await service.create_lore_identity("Yangyang")
    first = await service.create_identity("Yangyang", "character", lore_id=lore["id"])
    second = await service.create_identity("Yangyang: Xuanling", "character", lore_id=lore["id"])
    assert first["id"] != second["id"]
    appearance = await service.create_appearance(first["id"], "Default")
    image = encode_png(Image.new("RGB", (20, 12), "red"))
    ref = await service.add_reference(
        image,
        bbox=[1, 1, 18, 10],
        links=[
            {"identity_id": first["id"], "appearance_id": appearance["id"]},
            {"identity_id": second["id"]},
        ],
        actor_id=actor,
    )
    assert ref["status"] == "pending" and not ref["enabled_for_search"]
    assert len(ref["links"]) == 2 and ref["image_url"]
    assert (await service.search(image))["status"] == "unknown"
    approved = await service.review_reference(ref["id"], "approved", actor_id=actor)
    assert approved["enabled_for_search"]
    found = await service.search(image)
    assert len(found["candidates"]) == 2 and found["status"] == "unresolved"
    assert found["candidates"][0]["evidence"][0]["image_url"]
    assert await service.get_image_bytes(ref["id"])
    service.engine = TestEngine("different-weights-384")
    assert (await service.search(image))["candidates"] == []
    service.engine = TestEngine()
    await service.remove_reference(ref["id"], mode="vector_only", actor_id=actor)
    retained = await service.get_reference(ref["id"])
    assert retained["image_url"] is None and retained["model_keys"]
    assert (await service.search(image))["candidates"]
    assert (await service.reindex([ref["id"]]))["items"][0]["status"] == "image_not_retained"
    await service.review_reference(ref["id"], "rejected", actor_id=actor)
    duplicate = await service.add_reference(
        image, bbox=[1, 1, 18, 10], links=[{"identity_id": second["id"]}], actor_id=actor
    )
    assert duplicate["duplicate"] and duplicate["status"] == "rejected"
    assert (await service.search(image))["candidates"] == []
    assert len((await service.get_review_history(ref["id"]))["items"]) >= 4
    assert await session.scalar(
        select(VisualEmbedding).where(VisualEmbedding.reference_id == ref["id"])
    )


@pytest.mark.asyncio
async def test_relink_revokes_approval_and_appearance_validation(memory):
    service, session, actor = memory
    a = await service.create_identity("Abby", "npc")
    b = await service.create_identity("Custom", "custom_kind")
    appearance = await service.create_appearance(a["id"], "World")
    image = encode_png(Image.new("RGB", (20, 12), "blue"))
    ref = await service.add_reference(
        image, links=[{"identity_id": a["id"]}], status="approved", actor_id=actor
    )
    with pytest.raises(ValueError, match="Appearance"):
        await service.link_reference(
            ref["id"], [{"identity_id": b["id"], "appearance_id": appearance["id"]}]
        )
    changed = await service.link_reference(ref["id"], [{"identity_id": b["id"]}], actor_id=actor)
    assert changed["status"] == "pending" and not changed["enabled_for_search"]
    with pytest.raises(ValueError, match="administrator"):
        await service.review_reference(ref["id"], "approved")

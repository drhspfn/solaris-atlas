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
    VisualReference,
    VisualReferenceLink,
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
    assert identification_status(
        [{**found[0], "best_similarity": 0.1}]) == "unknown"
    assert identification_status([found[0], dict(found[0])]) == "unresolved"


def test_ranking_uses_one_quality_adjusted_reference_without_penalizing_more_sources():
    def row(reference, source, similarity, quality, perceptual_hash=None):
        return {"identity_id": 1, "reference_id": reference, "source_sha256": source,
                "perceptual_hash": perceptual_hash, "name": "Subject", "kind": "npc",
                "similarity": similarity, "quality": quality}

    best = row(1, "a", .9, .4, "same-image")
    high_quality = row(2, "b", .7, 1)
    duplicate = row(3, "c", .6, 1, "same-image")
    first = rank_candidates([best])[0]
    assert first["score"] == pytest.approx(.9 * .7)
    with_more = rank_candidates([best, high_quality, duplicate])[0]
    assert with_more["score"] == pytest.approx(.7)
    assert with_more["evidence"][0]["reference_id"] == 2
    assert with_more["independent_references"] == 2
    assert rank_candidates([best, high_quality, duplicate, row(4, "d", .2, 1)])[
        0]["score"] == pytest.approx(.7)
    assert rank_candidates([best, row(5, "e", .3, 1)])[
        0]["score"] == pytest.approx(first["score"])


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
        pytest.skip(
            "Set WUWA_VISUAL_TEST_DATABASE_URL to an isolated migrated PostgreSQL database")
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
            service = VisualMemoryService(
                session, LocalStorage(tmp_path), TestEngine())
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
        source_url="https://example.invalid/yangyang",
        author="Visual tester",
        quality=.8,
    )
    assert ref["status"] == "pending" and not ref["enabled_for_search"]
    assert len(ref["links"]) == 2 and ref["image_url"]
    assert ref["links"][0]["kind"] == "character"
    assert (await service.list_references(q="example.invalid", quality_min=.7))["total"] == 1
    assert (await service.list_references(q="Visual tester", quality_min=.9))["total"] == 0
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
        select(VisualEmbedding).where(
            VisualEmbedding.reference_id == ref["id"])
    )


@pytest.mark.asyncio
async def test_relink_revokes_approval_and_appearance_validation(memory):
    service, session, actor = memory
    a = await service.create_identity("Abby", "npc")
    b = await service.create_identity("Custom", "other")
    appearance = await service.create_appearance(a["id"], "World")
    image = encode_png(Image.new("RGB", (20, 12), "blue"))
    ref = await service.add_reference(
        image, links=[{"identity_id": a["id"]}], status="approved", actor_id=actor
    )
    with pytest.raises(ValueError, match="Appearance"):
        await service.link_reference(
            ref["id"], [{"identity_id": b["id"],
                         "appearance_id": appearance["id"]}]
        )
    changed = await service.link_reference(ref["id"], [{"identity_id": b["id"]}], actor_id=actor)
    assert changed["status"] == "pending" and not changed["enabled_for_search"]
    with pytest.raises(ValueError, match="administrator"):
        await service.review_reference(ref["id"], "approved")


@pytest.mark.asyncio
async def test_balanced_pgvector_search_does_not_drop_rare_identity(memory):
    service, session, actor = memory
    popular = await service.create_identity("Popular character", "character")
    rare = await service.create_identity("Rare NPC", "npc")
    image = encode_png(Image.new("RGB", (16, 16), "green"))
    reference = await service.add_reference(image, links=[{"identity_id": rare["id"]}],
                                            status="approved", actor_id=actor)
    rare_embedding = await session.scalar(select(VisualEmbedding).where(
        VisualEmbedding.reference_id == reference["id"],
    ))
    rare_embedding.embedding = [.9, math.sqrt(1 - .9 ** 2)] + [0.] * 382
    references = [VisualReference(
        source_sha256=f"{n:064x}", sha256=f"{n:064x}", width=10, height=10,
        bbox=[0, 0, 10, 10], source_type="gameplay", status="approved",
        enabled_for_search=True, retention_mode="vector_only",
    ) for n in range(1, 501)]
    session.add_all(references)
    await session.flush()
    for row in references:
        session.add(VisualReferenceLink(
            reference_id=row.id, identity_id=popular["id"]))
        session.add(VisualEmbedding(reference_id=row.id, model_id=rare_embedding.model_id,
                                    dimensions=384, embedding=[.98, math.sqrt(1 - .98 ** 2)] + [0.] * 382))
    await session.flush()
    found = await service.search(image)
    assert [item["identity_id"]
            for item in found["candidates"]] == [popular["id"], rare["id"]]
    assert found["candidates"][0]["independent_references"] == 5
    # All 500 popular images precede the rare one by raw distance. A global
    # top-100 image retrieval would incorrectly lose the NPC completely.
    assert len(found["candidates"][1]["evidence"]) == 1


@pytest.mark.asyncio
async def test_pg_quality_scoring_dedup_and_limit_independent_status(memory):
    service, session, actor = memory
    first = await service.create_identity("First", "npc")
    second = await service.create_identity("Second", "npc")
    image = encode_png(Image.new("RGB", (16, 16), "blue"))
    reference = await service.add_reference(image, links=[{"identity_id": first["id"]}],
                                            status="approved", actor_id=actor)
    initial = await session.get(VisualReference, reference["id"])
    initial.quality = .4
    initial.source_group = "same-source"
    embedding = await session.scalar(select(VisualEmbedding).where(
        VisualEmbedding.reference_id == initial.id))

    def vector(similarity):
        return [similarity, math.sqrt(1 - similarity ** 2)] + [0.] * 382

    embedding.embedding = vector(.9)
    await session.flush()
    before = await service.search(image)
    assert before["candidates"][0]["score"] == pytest.approx(.63, abs=1e-5)

    async def add(identity_id, suffix, similarity, quality, source_group):
        row = VisualReference(source_sha256=f"{suffix:064x}", sha256=f"{suffix:064x}",
                              source_group=source_group, width=10, height=10,
                              bbox=[0, 0, 10, 10], source_type="gameplay", status="approved",
                              enabled_for_search=True, retention_mode="vector_only", quality=quality)
        session.add(row)
        await session.flush()
        session.add(VisualReferenceLink(
            reference_id=row.id, identity_id=identity_id))
        session.add(VisualEmbedding(reference_id=row.id, model_id=embedding.model_id,
                                    dimensions=384, embedding=vector(similarity)))
        await session.flush()
        return row.id

    # The lower-cosine crop from the same source has the better quality-adjusted score.
    winner_id = await add(first["id"], 1, .8, 1, "same-source")
    await add(first["id"], 2, .2, 1, "independent-source")
    await add(second["id"], 3, .78, 1, "runner-up")
    one = await service.search(image, limit=1)
    many = await service.search(image, limit=10)
    assert len(one["candidates"]) == 1 and len(many["candidates"]) == 2
    assert one["status"] == many["status"] == "unresolved"
    winner = one["candidates"][0]
    assert winner["identity_id"] == first["id"]
    assert winner["score"] == pytest.approx(.8, abs=1e-5)
    assert winner["score"] >= before["candidates"][0]["score"]
    assert winner["best_similarity"] == pytest.approx(.8, abs=1e-5)
    assert winner["evidence"][0]["reference_id"] == winner_id
    assert winner["independent_references"] == 2

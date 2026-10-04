import asyncio
import hashlib
import json
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.agents.contracts import AnalysisRequest, AnalysisResult
from wuwa_story.agents.evidence import EvidenceTools, imported_snapshot_ids
from wuwa_story.agents.jobs import enqueue_analysis, resume_analysis
from wuwa_story.agents.providers import Provider, ToolCall
from wuwa_story.agents.retrieval import get_explanation, search_explanations
from wuwa_story.agents.runner import execute_job
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import AgentCall, AgentDailyUsage, AgentJob, ExplanationEmbedding
from wuwa_story.db.models.content import Document
from wuwa_story.db.models.core import DialogueLine, Quest, QuestAction, QuestState
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision, NodeType
from wuwa_story.db.models.i18n import (
    Locale,
    LocalizationContent,
    LocalizationKey,
    LocalizationValue,
)
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease, ProcessingRun
from wuwa_story.db.models.search import SearchDocument
from wuwa_story.db.models.story import Claim, Event

DATABASE_URL = os.getenv("WUWA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Isolated test database required")


@pytest.fixture
async def world(monkeypatch):
    engine = create_async_engine(DATABASE_URL)
    suffix = uuid4().hex
    settings = AgentSettings(
        _env_file=None, api_key="test-only", daily_budget_usd=100, embedding_model="", max_steps=8
    )
    async with AsyncSession(engine, expire_on_commit=False) as db:
        locale = await db.scalar(select(Locale).where(Locale.code == "en"))
        if locale is None:
            locale = Locale(id=1, code="en", name="English")
            db.add(locale)
        release = GameRelease(
            sequence=(await db.scalar(select(func.max(GameRelease.sequence))) or 0) + 1,
            game_version="agent-test-" + suffix,
            upstream_name="test",
        )
        db.add(release)
        await db.flush()
        nodes = []
        for index, kind in enumerate(
            ["quest", "quest_state", "quest_action", "dialogue_line", "dialogue_line"]
        ):
            node = Node(
                type_id=await db.scalar(select(NodeType.id).where(NodeType.key == kind)),
                canonical_key=f"agent-test:{suffix}:{index}",
                created_release_id=release.id,
            )
            db.add(node)
            await db.flush()
            db.add(
                NodeRevision(
                    node_id=node.id, release_id=release.id, revision=1, content_hash=b"original"
                )
            )
            nodes.append(node)
        quest = Quest(node_id=nodes[0].id, game_quest_id=nodes[0].id)
        db.add(quest)
        db.add(QuestState(node_id=nodes[1].id, state_key="test-state"))
        await db.flush()
        db.add(
            QuestAction(
                node_id=nodes[2].id,
                quest_state_node_id=nodes[1].id,
                action_index=0,
                action_name="ShowTalk",
            )
        )
        await db.flush()
        for index, node in enumerate(nodes[3:]):
            db.add(
                DialogueLine(
                    node_id=node.id,
                    action_node_id=nodes[2].id,
                    inline_text=["The bridge was destroyed.", "We need another route."][index],
                    source_index=index,
                )
            )
        edge = Edge(
            from_node_id=quest.node_id,
            to_node_id=nodes[1].id,
            relation_type_id=await db.scalar(
                select(RelationType.id).where(RelationType.key == "references_flow_state")
            ),
            layer="source",
            basis="explicit_reference",
        )
        db.add(edge)
        await db.flush()
        db.add(EdgeEvidence(edge_id=edge.id, release_id=release.id))
        await db.commit()

    async def publish(*_args):
        pass

    monkeypatch.setattr("wuwa_story.agents.jobs.publish_media_job", publish)
    request = AnalysisRequest(quest_id=quest.game_quest_id, game_version=release.game_version)
    try:
        yield engine, settings, request, nodes, locale, release, quest
    finally:
        async with AsyncSession(engine) as db:
            content_ids = list(
                await db.scalars(
                    select(LocalizationValue.content_id).where(
                        LocalizationValue.release_id == release.id
                    )
                )
            )
            runs = select(ProcessingRun.id).where(ProcessingRun.target_node_id == quest.node_id)
            events = list(
                await db.scalars(select(Event.node_id).where(Event.processor_run_id.in_(runs)))
            )
            await db.execute(delete(AgentCall).where(AgentCall.run_id.in_(runs)))
            await db.execute(delete(Claim).where(Claim.processor_run_id.in_(runs)))
            await db.execute(delete(ProcessingRun).where(ProcessingRun.id.in_(runs)))
            await db.execute(
                delete(LocalizationValue).where(LocalizationValue.release_id == release.id)
            )
            await db.execute(delete(Node).where(Node.id.in_([node.id for node in nodes] + events)))
            await db.execute(
                delete(LocalizationKey).where(LocalizationKey.first_release_id == release.id)
            )
            await db.execute(
                delete(LocalizationContent).where(
                    LocalizationContent.id.in_(content_ids),
                    ~select(LocalizationValue.key_id)
                    .where(LocalizationValue.content_id == LocalizationContent.id)
                    .exists(),
                )
            )
            await db.execute(delete(GameRelease).where(GameRelease.id == release.id))
            await db.execute(
                delete(AgentDailyUsage).where(
                    ~select(AgentCall.id).where(AgentCall.day == AgentDailyUsage.day).exists()
                )
            )
            await db.commit()
        await engine.dispose()


def result_for(nodes):
    citation = {
        "node_id": nodes[3].id,
        "quote": "The bridge was destroyed.",
        "snapshot_id": nodes[3].created_release_id,
    }
    return {
        "title": "A blocked crossing",
        "blocks": [
            {
                "title": "Why the route changes",
                "text": "The destroyed bridge forces a change of route.",
                "citations": [citation],
                "related_records": [
                    {"node_id": nodes[3].id, "label": "Bridge destruction forces another route"}
                ],
                "assertions": [
                    {
                        "text": "The bridge was destroyed.",
                        "status": "confirmed",
                        "citations": [citation],
                        "chronology_in_quest": {
                            "order": 0,
                            "anchor_node_id": nodes[3].id,
                            "label": "Bridge report",
                        },
                        "world_chronology": {
                            "placement": "before_quest",
                            "explanation": "Already destroyed when reported; exact time unknown.",
                        },
                        "knowledge_state": "The direct route is blocked.",
                        "later_resolution": [],
                    }
                ],
            }
        ],
        "links": [
            {
                "from_node_id": nodes[3].id,
                "to_node_id": nodes[4].id,
                "relation": "EXPLAINS",
                "relation_label": "explains the change of route",
                "explanation": "The destroyed crossing explains the search for another route.",
                "confidence": 0.9,
                "citations": [citation],
            }
        ],
        "events": [
            {
                "title": "Bridge destroyed",
                "description": "The bridge is reported destroyed.",
                "citations": [citation],
                "participant_node_ids": [],
            }
        ],
    }


async def create(world, *, adaptive=False):
    engine, settings, request, *_ = world
    async with AsyncSession(engine, expire_on_commit=False) as db:
        run = await enqueue_analysis(db, request, settings)
        if not adaptive:
            # These fixtures exercise backwards-compatible v4 checkpoints/providers.
            run.prompt_version = "story-v4"
            await db.commit()
        return run.id


@pytest.fixture
async def other_patch(world):
    engine, _, _, original_nodes, locale, original_release, _ = world
    async with AsyncSession(engine, expire_on_commit=False) as db:
        release = GameRelease(
            sequence=(await db.scalar(select(func.max(GameRelease.sequence))) or 0) + 1,
            game_version="later-" + uuid4().hex,
            upstream_name="test",
        )
        db.add(release)
        await db.flush()
        nodes = []
        for kind in ("quest", "quest_state", "quest_action", "dialogue_line"):
            node = Node(
                type_id=await db.scalar(select(NodeType.id).where(NodeType.key == kind)),
                canonical_key="later-test:" + uuid4().hex,
                created_release_id=release.id,
            )
            db.add(node)
            await db.flush()
            db.add(
                NodeRevision(
                    node_id=node.id, release_id=release.id, revision=1, content_hash=b"later"
                )
            )
            nodes.append(node)
        quest = Quest(node_id=nodes[0].id, game_quest_id=nodes[0].id)
        db.add_all([quest, QuestState(node_id=nodes[1].id, state_key="later-state")])
        await db.flush()
        db.add(
            QuestAction(
                node_id=nodes[2].id,
                quest_state_node_id=nodes[1].id,
                action_index=0,
                action_name="ShowTalk",
            )
        )
        await db.flush()
        db.add(
            DialogueLine(
                node_id=nodes[3].id,
                action_node_id=nodes[2].id,
                source_index=0,
                inline_text="The bridge is repaired now.",
            )
        )
        # One node observed in both patches has different localized text.
        key = LocalizationKey(
            key="cross-patch:" + uuid4().hex, first_release_id=original_release.id
        )
        db.add(key)
        await db.flush()
        (await db.get(DialogueLine, original_nodes[3].id)).localization_key_id = key.id
        db.add(
            NodeRevision(
                node_id=original_nodes[3].id,
                release_id=release.id,
                revision=2,
                content_hash=b"changed",
            )
        )
        for snapshot_id, text in (
            (original_release.id, "The bridge was destroyed."),
            (release.id, "The bridge has another name now."),
        ):
            digest = hashlib.sha256(text.encode()).digest()
            content = await db.scalar(
                select(LocalizationContent).where(LocalizationContent.content_hash == digest)
            )
            if content is None:
                content = LocalizationContent(content=text, content_hash=digest)
                db.add(content)
                await db.flush()
            db.add(
                LocalizationValue(
                    release_id=snapshot_id,
                    key_id=key.id,
                    locale_id=locale.id,
                    content_id=content.id,
                    status="resolved_nonempty",
                )
            )
        for source, target, relation in (
            (nodes[0].id, nodes[1].id, "references_flow_state"),
            (original_nodes[3].id, nodes[3].id, "EXPLAINS"),
        ):
            edge = Edge(
                from_node_id=source,
                to_node_id=target,
                relation_type_id=await db.scalar(
                    select(RelationType.id).where(RelationType.key == relation)
                ),
                layer="source",
                basis="explicit_reference",
            )
            db.add(edge)
            await db.flush()
            db.add(EdgeEvidence(edge_id=edge.id, release_id=release.id))
        db.add(
            SearchDocument(
                target_node_id=nodes[0].id,
                category="quest",
                locale_id=locale.id,
                title="Bridge repair",
                body="Bridge repair",
                content_hash=b"later-search",
                search_vector=func.to_tsvector("simple", "Bridge repair"),
            )
        )
        await db.commit()
    try:
        yield release, quest, nodes
    finally:
        async with AsyncSession(engine) as db:
            content_ids = list(
                await db.scalars(
                    select(LocalizationValue.content_id).where(
                        LocalizationValue.release_id == release.id
                    )
                )
            )
            runs = select(AgentJob.run_id).where(AgentJob.release_id == release.id)
            await db.execute(delete(AgentCall).where(AgentCall.run_id.in_(runs)))
            await db.execute(delete(Claim).where(Claim.processor_run_id.in_(runs)))
            await db.execute(delete(ProcessingRun).where(ProcessingRun.id.in_(runs)))
            await db.execute(
                delete(LocalizationValue).where(LocalizationValue.release_id == release.id)
            )
            await db.execute(delete(NodeRevision).where(NodeRevision.release_id == release.id))
            await db.execute(delete(EdgeEvidence).where(EdgeEvidence.release_id == release.id))
            await db.execute(delete(Node).where(Node.id.in_([n.id for n in nodes])))
            await db.execute(delete(GameRelease).where(GameRelease.id == release.id))
            await db.execute(
                delete(LocalizationContent).where(
                    LocalizationContent.id.in_(content_ids),
                    ~select(LocalizationValue.key_id)
                    .where(LocalizationValue.content_id == LocalizationContent.id)
                    .exists(),
                )
            )
            await db.commit()


async def test_cross_patch_research_preserves_citation_identity_and_target_coverage(
    world, other_patch
):
    engine, settings, _, nodes, locale, release, quest = world
    later_release, later_quest, later_nodes = other_patch
    run_id = await create(world)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        scope_ids = await imported_snapshot_ids(db)
        tools = EvidenceTools(
            db,
            run_id=run_id,
            quest=quest,
            release_id=release.id,
            locale=locale,
            settings=settings,
            source_release_ids=scope_ids,
        )
        assert (await tools.snapshots())["target_snapshot_id"] == release.id
        await tools.read_quest(limit=1)
        graph = await tools.graph(nodes[3].id)
        edge = next(edge for edge in graph["edges"] if edge["to"] == later_nodes[3].id)
        assert edge["snapshot_ids"] == [later_release.id]
        assert not (
            await tools.call(
                ToolCall(
                    "narrow", "graph_neighbors", {"node_id": nodes[3].id, "snapshot_id": release.id}
                )
            )
        )["edges"]
        assert (await tools.graph(later_nodes[3].id))["edges"]
        found = await tools.search("Bridge repair")
        assert any(
            row["id"] == later_quest.node_id and row["snapshot_ids"] == [later_release.id]
            for row in found["results"]
        )
        future = await tools.call(
            ToolCall("future", "read_quest", {"quest_id": later_quest.game_quest_id})
        )
        assert future["snapshot_id"] == later_release.id
        assert future["lines"][0]["text"] == "The bridge is repaired now."
        result = AnalysisResult.model_validate(result_for(nodes))
        with pytest.raises(ValueError, match="every page"):
            await tools.validate_result(result)
        await tools.read_quest(offset=1)
        await tools.call(
            ToolCall(
                "compare", "read_node", {"node_id": nodes[3].id, "snapshot_id": later_release.id}
            )
        )
        # A quote from the same node in another snapshot cannot be attributed to 1.0.
        result.blocks[0].citations[0].quote = "The bridge has another name now."
        with pytest.raises(ValueError, match="explicit snapshot"):
            await tools.validate_result(result)
        result.blocks[0].citations[0].quote = "The bridge was destroyed."
        with pytest.raises(ValueError, match="pinned"):
            await tools.context(later_release.id + 100000)
        # Research from a new patch can explicitly read the old quest too.
        reverse = EvidenceTools(
            db,
            run_id=run_id,
            quest=later_quest,
            release_id=later_release.id,
            locale=locale,
            settings=settings,
            source_release_ids=scope_ids,
        )
        past = await reverse.call(
            ToolCall(
                "past", "read_quest", {"quest_id": quest.game_quest_id, "snapshot_id": release.id}
            )
        )
        assert past["lines"][0]["text"] == "The bridge was destroyed."


async def test_cross_patch_publication_links_to_later_source_and_invalidates_changes(
    world, other_patch
):
    from wuwa_story.agents.contracts import LaterResolution
    from wuwa_story.agents.publication import publish_analysis

    engine, settings, request, nodes, locale, release, quest = world
    later_release, later_quest, later_nodes = other_patch
    run_id = await create(world)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        tools = EvidenceTools(
            db,
            run_id=run_id,
            quest=quest,
            release_id=release.id,
            locale=locale,
            settings=settings,
            source_release_ids=await imported_snapshot_ids(db),
        )
        await tools.read_quest()
        await tools.call(ToolCall("later", "read_quest", {"quest_id": later_quest.game_quest_id}))
        result = AnalysisResult.model_validate(result_for(nodes))
        result.blocks[0].assertions[0].later_resolution = [
            LaterResolution(
                status="resolved",
                text="The later quest reports the repair.",
                revealed_in_node_id=later_nodes[3].id,
                citations=[
                    {
                        "node_id": later_nodes[3].id,
                        "snapshot_id": later_release.id,
                        "quote": "The bridge is repaired now.",
                    }
                ],
            )
        ]
        await tools.validate_result(result)
        job, run = await db.get(AgentJob, run_id), await db.get(ProcessingRun, run_id)
        await publish_analysis(
            db,
            job,
            run,
            result,
            [],
            source_receipts=tools.validated_sources,
            source_nodes=tools.source_nodes,
        )
        await db.commit()
        public = (await get_explanation(db, request.quest_id, request.game_version, "en"))[
            "explanation"
        ]
        assert public["research_scope"] == "all_imported_snapshots"
        later = public["blocks"][0]["assertions"][0]["later_resolution"][0]["citations"][0]
        assert f"/quests/{later_quest.game_quest_id}?" in later["href"]
        assert f"game_version={later_release.game_version}" in later["href"]
        assert (
            public["blocks"][0]["assertions"][0]["knowledge_state"]
            == "The direct route is blocked."
        )
        (await db.get(DialogueLine, later_nodes[3].id)).inline_text = "Changed future evidence."
        await db.commit()
        assert (await get_explanation(db, request.quest_id, request.game_version, "en"))[
            "status"
        ] == "pending"
        # Requeueing after an external source change creates a fresh job too.
        newer = await enqueue_analysis(db, request, settings)
        assert newer.id != run_id


async def test_new_import_changes_job_identity_without_widening_a_queued_run(world):
    engine, settings, request, nodes, _, _, _ = world
    first_id = await create(world)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        first = await db.get(ProcessingRun, first_id)
        pinned = list(first.metadata_json["source_release_ids"])
        addition = GameRelease(
            sequence=(await db.scalar(select(func.max(GameRelease.sequence))) or 0) + 1,
            game_version="added-" + uuid4().hex,
            upstream_name="test",
        )
        db.add(addition)
        await db.flush()
        db.add(
            NodeRevision(
                node_id=nodes[0].id, release_id=addition.id, revision=3, content_hash=b"additional"
            )
        )
        await db.commit()
        try:
            newer = await enqueue_analysis(db, request, settings)
            assert newer.id != first_id
            assert addition.id in newer.metadata_json["source_release_ids"]
            await db.refresh(first)
            assert first.metadata_json["source_release_ids"] == pinned
        finally:
            await db.execute(delete(NodeRevision).where(NodeRevision.release_id == addition.id))
            await db.execute(delete(GameRelease).where(GameRelease.id == addition.id))
            await db.commit()


async def test_quest_only_request_resolves_and_pins_latest_observed_snapshot(world, other_patch):
    engine, settings, request, nodes, _, release, _ = world
    later_release, _, _ = other_patch
    explicit_id = await create(world)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        automatic = await enqueue_analysis(db, AnalysisRequest(quest_id=request.quest_id), settings)
        assert automatic.id == explicit_id
        assert automatic.metadata_json["request"]["game_version"] == release.game_version
        assert automatic.metadata_json["request"]["locale"] == "en"
        db.add(
            NodeRevision(
                node_id=nodes[0].id,
                release_id=later_release.id,
                revision=2,
                content_hash=b"latest-quest",
            )
        )
        await db.commit()
        newer = await enqueue_analysis(db, AnalysisRequest(quest_id=request.quest_id), settings)
        assert newer.id != explicit_id
        assert (await db.get(AgentJob, newer.id)).release_id == later_release.id
        assert newer.metadata_json["request"]["game_version"] == later_release.game_version


async def test_adaptive_prescan_is_required_checkpointed_and_published(world):
    engine, settings, _, nodes, _, release, _ = world
    run_id = await create(world, adaptive=True)
    final = result_for(nodes)
    final["narrative_function"] = "Explains a local obstacle and the change of route."
    final["review"] = dict.fromkeys(
        [
            "choices_labeled",
            "future_knowledge_separated",
            "proportional_depth",
            "unresolved_preserved",
            "speculation_labeled",
            "revisit_checked",
            "branches_separated",
        ],
        True,
    )
    final["links"][0]["signals"] = [
        {
            "kind": "direct_reference",
            "value": "Destroyed bridge",
            "citations": final["links"][0]["citations"],
        }
    ]
    selected = {
        "narrative_weight": "side_flavor",
        "hook_priority": "low",
        "reason": "A local travel obstacle",
        "citations": [
            {
                "node_id": nodes[3].id,
                "snapshot_id": release.id,
                "quote": "The bridge was destroyed.",
            }
        ],
    }
    requests = []

    def transport(request):
        payload = json.loads(request.content)
        requests.append(payload)
        actions = [
            ("finish_analysis", {"result_json": json.dumps(final)}),
            ("read_quest", {}),
            ("assess_quest", selected),
            ("finish_analysis", {"result_json": json.dumps(final)}),
        ]
        name, args = actions[len(requests) - 1]
        if len(requests) <= 3:
            assert "finish_analysis" not in {tool["name"] for tool in payload["tools"]}
            assert payload["max_output_tokens"] == 2048
        else:
            assert payload["max_output_tokens"] == 8192
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [
                    {
                        "type": "function_call",
                        "call_id": str(len(requests)),
                        "name": name,
                        "arguments": json.dumps(args),
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
        await execute_job(run_id, engine, settings, client)
    assert len(requests) == 4
    async with AsyncSession(engine) as db:
        run, job = await db.get(ProcessingRun, run_id), await db.get(AgentJob, run_id)
        assert run.status == "completed"
        assert job.checkpoint["assessment"]["narrative_weight"] == "side_flavor"
        assert job.checkpoint["policy"]["depth"] == "short"
        doc = await db.get(Document, job.document_id)
        assert doc.metadata_json["assessment"]["narrative_weight"] == "side_flavor"
        assert doc.metadata_json["review"]["branches_separated"]


async def test_full_pipeline_and_duplicate_delivery(world):
    engine, settings, _, nodes, *_ = world
    run_id = await create(world)
    calls = []

    def transport(request):
        payload = json.loads(request.content)
        name, args = (
            ("read_quest", {})
            if not calls
            else ("finish_analysis", {"result_json": json.dumps(result_for(nodes))})
        )
        calls.append(payload)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [
                    {
                        "type": "function_call",
                        "call_id": str(len(calls)),
                        "name": name,
                        "arguments": json.dumps(args),
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
        await execute_job(run_id, engine, settings, client)
    assert len(calls) == 2
    assert await create(world) == run_id
    async with AsyncSession(engine) as db:
        run = await db.get(ProcessingRun, run_id)
        assert run.status == "completed" and run.tokens_input == 200
        job = await db.get(AgentJob, run_id)
        document = await db.get(Document, job.document_id)
        assert document.body_ast[0]["citations"][0]["node_id"] == nodes[3].id
        assert (
            document.body_ast[0]["assertions"][0]["world_chronology"]["placement"] == "before_quest"
        )
        response = await get_explanation(db, nodes[0].id, world[5].game_version, "en")
        assert (
            response["explanation"]["blocks"][0]["related_records"][0]["label"]
            == "Bridge destruction forces another route"
        )
        assert response["explanation"]["blocks"][0]["assertions"][0]["citations"][0]["href"]
        assert "history" not in job.checkpoint
        assert (
            await db.scalar(
                select(func.count()).select_from(Claim).where(Claim.processor_run_id == run_id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Edge)
                .where(Edge.layer == "source", Edge.from_node_id == nodes[0].id)
            )
            == 1
        )


async def test_multilingual_sources_and_shared_publication(world):
    engine, settings, request, nodes, _, release, _ = world
    async with AsyncSession(engine, expire_on_commit=False) as db:
        locales = {}
        for code in ("en", "zh-Hans", "ja"):
            language = await db.scalar(select(Locale).where(Locale.code == code))
            if language is None:
                language = Locale(id=20 + len(locales), code=code, name=code)
                db.add(language)
                await db.flush()
            locales[code] = language.id
        values_by_line = [
            {"en": "The bridge was destroyed.", "zh-Hans": "桥被摧毁了。", "ja": "橋が壊された。"},
            {"zh-Hans": "我们需要另一条路。", "ja": "別の道が必要だ。"},
        ]
        for node, translations in zip(nodes[3:], values_by_line, strict=True):
            key = LocalizationKey(key=node.canonical_key, first_release_id=release.id)
            db.add(key)
            await db.flush()
            (await db.get(DialogueLine, node.id)).localization_key_id = key.id
            for code, text in translations.items():
                digest = hashlib.sha256(text.encode()).digest()
                content = await db.scalar(
                    select(LocalizationContent).where(LocalizationContent.content_hash == digest)
                )
                if content is None:
                    content = LocalizationContent(content=text, content_hash=digest)
                    db.add(content)
                    await db.flush()
                db.add(
                    LocalizationValue(
                        release_id=release.id,
                        key_id=key.id,
                        locale_id=locales[code],
                        content_id=content.id,
                        status="resolved_nonempty",
                    )
                )
        await db.commit()
    run_id = await create(world)
    calls = []
    result = result_for(nodes)
    result["blocks"][0]["citations"][0] = {
        "node_id": nodes[3].id,
        "quote": "桥被摧毁了。",
        "locale": "zh-Hans",
        "snapshot_id": release.id,
    }

    def transport(request):
        calls.append(json.loads(request.content))
        steps = [
            ("read_quest", {}),
            ("read_node", {"node_id": nodes[3].id, "locale": "zh-Hans"}),
            ("read_node", {"node_id": nodes[3].id, "locale": "ja"}),
            ("finish_analysis", {"result_json": json.dumps(result)}),
        ]
        name, args = steps[len(calls) - 1]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [
                    {
                        "type": "function_call",
                        "call_id": str(len(calls)),
                        "name": name,
                        "arguments": json.dumps(args),
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
    primary = json.loads(calls[1]["input"][-1]["output"])
    assert [(line["text"], line["locale"]) for line in primary["lines"]] == [
        ("The bridge was destroyed.", "en"),
        ("我们需要另一条路。", "zh-Hans"),
    ]
    async with AsyncSession(engine) as db:
        assert (await db.get(ProcessingRun, run_id)).status == "completed"
        for code in ("en", "zh-Hans", "ja"):
            response = await get_explanation(db, request.quest_id, request.game_version, code)
            assert response["status"] == "available"
            assert response["explanation"]["locale"] == "en"
            assert "locale=zh-Hans" in response["explanation"]["blocks"][0]["citations"][0]["href"]
        search = await search_explanations(db, "destroyed bridge", request.game_version, "ja", 5)
        assert len(search["results"]) == 1 and search["results"][0]["quest_id"] == request.quest_id
        tools = EvidenceTools(
            db,
            run_id=run_id,
            quest=world[-1],
            release_id=release.id,
            locale=await db.get(Locale, locales["ja"]),
            settings=settings,
        )
        await tools.read_quest()
        await tools.read_node(nodes[3].id, locale="zh-Hans")
        wrong_language = result_for(nodes)
        wrong_language["blocks"][0]["citations"][0]["locale"] = "ja"
        with pytest.raises(ValueError, match="Cited source changed"):
            await tools.validate_result(AnalysisResult.model_validate(wrong_language))
        value = await db.get(
            LocalizationValue,
            (
                release.id,
                (await db.get(DialogueLine, nodes[3].id)).localization_key_id,
                locales["ja"],
            ),
        )
        value.status = "missing"
        await db.flush()
        assert (await get_explanation(db, request.quest_id, request.game_version, "en"))[
            "status"
        ] == "pending"
        await db.rollback()


async def test_unknown_outcome_does_not_repeat_paid_request(world):
    engine, settings, *_ = world
    run_id = await create(world)
    requests = []

    def transport(request):
        requests.append(request)
        raise httpx.ReadTimeout("connection lost")

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
        # Simulate a broker replay, including a mistakenly reset run status.
        async with AsyncSession(engine) as db:
            (await db.get(ProcessingRun, run_id)).status = "queued"
            await db.commit()
        await execute_job(run_id, engine, settings, client)
    assert len(requests) == 1
    async with AsyncSession(engine) as db:
        assert (await db.get(ProcessingRun, run_id)).status == "paused_uncertain"
        call = await db.scalar(select(AgentCall).where(AgentCall.run_id == run_id))
        assert call.status == "uncertain" and call.reserved_usd > 0


async def test_throttled_call_retries_with_one_reservation_and_charges_once(world, monkeypatch):
    engine, settings, _, nodes, *_ = world
    run_id = await create(world)
    requests, delays = [], []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("wuwa_story.agents.runner.asyncio.sleep", sleep)
    monkeypatch.setattr("wuwa_story.agents.runner.random.uniform", lambda *_: 0)

    def transport(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                429, json={"error": {"code": "rate_limit_exceeded"}}, headers={"retry-after": "56"}
            )
        name, args = (
            ("read_quest", {})
            if len(requests) == 2
            else ("finish_analysis", {"result_json": json.dumps(result_for(nodes))})
        )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [
                    {
                        "type": "function_call",
                        "call_id": str(len(requests)),
                        "name": name,
                        "arguments": json.dumps(args),
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
        await execute_job(run_id, engine, settings, client)
    assert len(requests) == 3 and requests[0] == requests[1]
    assert delays == [56]
    async with AsyncSession(engine) as db:
        run = await db.get(ProcessingRun, run_id)
        assert run.status == "completed" and run.tokens_input == 200
        calls = list(
            await db.scalars(
                select(AgentCall).where(AgentCall.run_id == run_id).order_by(AgentCall.step)
            )
        )
        assert len(calls) == 2 and calls[0].response["_retry_metadata"]["attempts"] == 2
        assert (await db.get(AgentDailyUsage, calls[0].day)).reserved_usd == 0


@pytest.mark.parametrize("code", ["insufficient_quota", "project_spend_limit_exceeded", "unknown"])
async def test_quota_and_unclassified_429_pause_without_automatic_retry(world, code):
    engine, settings, *_ = world
    run_id = await create(world)
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"code": code, "message": "private"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
        await execute_job(run_id, engine, settings, client)
    assert len(requests) == 1
    async with AsyncSession(engine) as db:
        assert (await db.get(ProcessingRun, run_id)).status == "paused_provider"
        call = await db.scalar(select(AgentCall).where(AgentCall.run_id == run_id))
        assert call.status == "provider_rejected" and call.input_tokens is None
        assert "private" not in json.dumps(call.response)
        assert (await db.get(AgentDailyUsage, call.day)).reserved_usd == call.reserved_usd


async def test_rate_pause_resumes_same_call_and_obeys_long_server_delay(world, monkeypatch):
    engine, settings, *_ = world
    run_id = await create(world)
    requests = []

    async def sleep(_delay):
        pytest.fail("A long Retry-After must defer rather than sleep/retry early")

    monkeypatch.setattr("wuwa_story.agents.runner.asyncio.sleep", sleep)

    def transport(request):
        requests.append(request)
        return httpx.Response(
            429, json={"error": {"code": "rate_limit_exceeded"}}, headers={"retry-after": "3600"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert (await db.get(ProcessingRun, run_id)).status == "paused_rate_limit"
            await resume_analysis(db, run_id)
        await execute_job(run_id, engine, settings, client)
    assert len(requests) == 1
    async with AsyncSession(engine) as db:
        call = await db.scalar(select(AgentCall).where(AgentCall.run_id == run_id))
        assert call.status == "rate_limited" and call.response["attempts"] == 1
        assert (await db.get(ProcessingRun, run_id)).status == "paused_rate_limit"


async def test_step_pause_resumes_from_checkpoint_without_repeating_calls(world):
    engine, settings, request, nodes, *_ = world
    settings.max_steps = 1
    run_id = await create(world)
    calls = []

    def transport(request):
        calls.append(json.loads(request.content))
        name, args = (
            ("read_quest", {})
            if len(calls) == 1
            else ("finish_analysis", {"result_json": json.dumps(result_for(nodes))})
        )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [
                    {
                        "type": "function_call",
                        "call_id": str(len(calls)),
                        "name": name,
                        "arguments": json.dumps(args),
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert (await db.get(ProcessingRun, run_id)).status == "paused_steps"
            with pytest.raises(ValueError, match="Increase extra_steps"):
                await resume_analysis(db, run_id)
            await resume_analysis(db, run_id, extra_steps=1)
        await execute_job(run_id, engine, settings, client)
    assert len(calls) == 2
    assert any(item.get("type") == "function_call_output" for item in calls[1]["input"])
    async with AsyncSession(engine) as db:
        assert (await db.get(ProcessingRun, run_id)).status == "completed"
        assert (await get_explanation(db, request.quest_id, request.game_version, "en"))[
            "status"
        ] == "available"


async def test_context_pause_requires_larger_bound_and_reuses_checkpoint(world):
    engine, settings, _, nodes, *_ = world
    settings.max_input_tokens = 16000
    settings.context_compaction = False
    run_id = await create(world)
    calls = []

    def transport(request):
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"input_tokens": 20000})
        calls.append(json.loads(request.content))
        first = len(calls) == 1
        name, args = (
            ("read_quest", {})
            if first
            else ("finish_analysis", {"result_json": json.dumps(result_for(nodes))})
        )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [
                    *([{"type": "reasoning", "encrypted_content": "x" * 20000}] if first else []),
                    {
                        "type": "function_call",
                        "call_id": str(len(calls)),
                        "name": name,
                        "arguments": json.dumps(args),
                    },
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert (await db.get(ProcessingRun, run_id)).status == "paused_context"
            with pytest.raises(ValueError, match="Increase context_tokens"):
                await resume_analysis(db, run_id)
            await resume_analysis(db, run_id, context_tokens=65536)
        await execute_job(run_id, engine, settings, client)
    assert len(calls) == 2
    assert any(item.get("type") == "reasoning" for item in calls[1]["input"])
    async with AsyncSession(engine) as db:
        assert (await db.get(ProcessingRun, run_id)).status == "completed"


@pytest.mark.parametrize("crash_after_payment", [False, True])
async def test_compaction_preserves_evidence_and_replays_paid_window(
    world, monkeypatch, crash_after_payment
):
    engine, settings, request, nodes, *_ = world
    settings.max_steps = 1
    run_id = await create(world)
    paths, compacted = [], []

    def transport(req):
        payload = json.loads(req.content)
        if req.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"input_tokens": 14000})
        paths.append(req.url.path)
        if req.url.path.endswith("/compact"):
            assert "tools" not in payload and "max_output_tokens" not in payload
            assert any(item.get("encrypted_content") == "x" * 47000 for item in payload["input"])
            compacted.extend(
                [
                    payload["input"][0],
                    payload["input"][1],
                    {"type": "compaction", "encrypted_content": "condensed"},
                ]
            )
            return httpx.Response(
                200,
                json={"output": compacted, "usage": {"input_tokens": 12000, "output_tokens": 5000}},
            )
        first = len(paths) == 1
        if not first:
            assert payload["input"] == compacted
        name, args = (
            ("read_quest", {})
            if first
            else ("finish_analysis", {"result_json": json.dumps(result_for(nodes))})
        )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [
                    *([{"type": "reasoning", "encrypted_content": "x" * 47000}] if first else []),
                    {
                        "type": "function_call",
                        "call_id": str(len(paths)),
                        "name": name,
                        "arguments": json.dumps(args),
                    },
                ],
            },
        )

    original = Provider.compact_output

    def crash_once(raw):
        monkeypatch.setattr(Provider, "compact_output", staticmethod(original))
        raise asyncio.CancelledError()

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            before = dict((await db.get(AgentJob, run_id)).checkpoint)
            assert before["evidence"] and before["coverage"]
            await resume_analysis(db, run_id, extra_steps=1)
        if crash_after_payment:
            monkeypatch.setattr(Provider, "compact_output", staticmethod(crash_once))
            with pytest.raises(asyncio.CancelledError):
                await execute_job(run_id, engine, settings, client)
        await execute_job(run_id, engine, settings, client)
    assert paths == ["/v1/responses", "/v1/responses/compact", "/v1/responses"]
    async with AsyncSession(engine) as db:
        job = await db.get(AgentJob, run_id)
        assert job.checkpoint["evidence"] == before["evidence"]
        assert job.checkpoint["coverage"] == before["coverage"]
        assert (await db.get(ProcessingRun, run_id)).status == "completed"
        assert (await get_explanation(db, request.quest_id, request.game_version, "en"))[
            "status"
        ] == "available"
        calls = list(await db.scalars(select(AgentCall).where(AgentCall.run_id == run_id)))
        assert len(calls) == 3 and all(call.status == "completed" for call in calls)
        compact_call = next(call for call in calls if call.kind == "compaction")
        assert compact_call.output_tokens == 5000
        assert (
            compact_call.reserved_tokens
            < settings.max_input_tokens + settings.compaction_output_tokens
        )


async def test_context_resume_can_request_compaction_without_raising_bound(world):
    engine, settings, *_ = world
    settings.context_compaction = False
    run_id = await create(world)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        run = await db.get(ProcessingRun, run_id)
        run.status = "paused_context"
        await db.commit()
        await resume_analysis(db, run_id, compact_context=True)
        job = await db.get(AgentJob, run_id)
        assert job.config["context_compaction"] is True
        assert job.config["max_input_tokens"] == settings.max_input_tokens
        assert job.checkpoint["compact_requested"] is True
        run.status = "paused_context"
        job.checkpoint = {"step": 0, "compacted_at_step": 0}
        await db.commit()
        with pytest.raises(ValueError, match="already compacted"):
            await resume_analysis(db, run_id, compact_context=True)


@pytest.mark.parametrize("count", [100, 999999, True, None])
async def test_oversized_compaction_uses_exact_count_before_reserving(world, count):
    from wuwa_story.agents.runner import remote_call

    engine, settings, *_ = world
    run_id = await create(world)
    paths = []
    payload = {"model": settings.model, "input": [{"role": "user", "content": "中文" * 40000}]}

    def transport(req):
        paths.append(req.url.path)
        assert json.loads(req.content) == payload
        if req.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"input_tokens": count})
        return httpx.Response(
            200,
            json={
                "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [{"type": "compaction", "encrypted_content": "opaque"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            run = await db.get(ProcessingRun, run_id)
            raw = await remote_call(
                db,
                run,
                settings,
                Provider(settings, client),
                2000,
                "/responses/compact",
                payload,
                compaction=True,
            )
            calls = list(await db.scalars(select(AgentCall).where(AgentCall.run_id == run_id)))
            if type(count) is int and count == 100:
                assert raw is not None and len(calls) == 1 and calls[0].status == "completed"
                assert paths == ["/v1/responses/input_tokens", "/v1/responses/compact"]
            else:
                assert raw is None and not calls and run.status == "paused_context"
                assert paths == ["/v1/responses/input_tokens"]


@pytest.mark.parametrize("count", [100, None, True, 999999])
async def test_tight_budget_counts_request_without_raising_caps(world, count):
    from wuwa_story.agents.runner import remote_call

    engine, settings, *_ = world
    settings.daily_token_limit, settings.max_output_tokens = 1000, 128
    run_id = await create(world)
    payload = {"model": settings.model, "input": "A" * 5000}
    paths = []

    def transport(req):
        paths.append(req.url.path)
        if req.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"input_tokens": count})
        return httpx.Response(200, json={"usage": {"input_tokens": 100, "output_tokens": 30}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            run = await db.get(ProcessingRun, run_id)
            raw = await remote_call(
                db, run, settings, Provider(settings, client), 0, "/responses", payload
            )
            calls = list(await db.scalars(select(AgentCall).where(AgentCall.run_id == run_id)))
            if type(count) is int and count == 100:
                assert raw is not None and calls[0].reserved_tokens == 228
                assert paths == ["/v1/responses/input_tokens", "/v1/responses"]
            else:
                assert raw is None and not calls and run.status == "paused_budget"
                assert paths == ["/v1/responses/input_tokens"]


async def test_repricing_is_idempotent_and_retains_unknown_reservations(world):
    from decimal import Decimal

    from wuwa_story.agents.budget import reprice_run, reserve
    from wuwa_story.agents.runner import remote_call

    engine, settings, *_ = world
    run_id = await create(world)
    raw = {
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 10,
            "input_tokens_details": {"cached_tokens": 900, "cache_write_tokens": 100},
        }
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=raw))
    ) as client:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            run = await db.get(ProcessingRun, run_id)
            await remote_call(
                db,
                run,
                settings,
                Provider(settings, client),
                0,
                "/responses",
                {"model": settings.model, "input": "test"},
            )
            unknown = await reserve(
                db, settings, run_id=run_id, step=1, input_bound=1000, output_bound=128
            )
            unknown.status = "uncertain"
            run.status = "paused_uncertain"
            await db.commit()
            usage = await db.get(AgentDailyUsage, unknown.day)
            reserved, tokens = usage.reserved_usd, usage.spent_tokens
            old_cost = usage.spent_usd
            result = await reprice_run(db, run_id, Decimal("0.01"), Decimal("0.125"))
            await db.commit()
            assert result["calls"] == 1 and usage.spent_usd < old_cost
            assert usage.reserved_usd == reserved and usage.spent_tokens == tokens
            assert unknown.status == "uncertain" and run.status == "paused_uncertain"
            assert run.cost == pytest.approx(float(usage.spent_usd))
            assert (await reprice_run(db, run_id, Decimal("0.01"), Decimal("0.125")))[
                "delta_usd"
            ] == "0.00000000"
            await db.commit()


@pytest.mark.parametrize("legacy_failure", [False, True])
async def test_truncated_output_recovers_without_replaying_research_or_charges(
    world, legacy_failure
):
    engine, settings, _, nodes, *_ = world
    settings.max_steps = 2
    run_id = await create(world)
    requests = []

    def transport(request):
        payload = json.loads(request.content)
        requests.append(payload)
        ordinal = len(requests)
        if ordinal == 1:
            name, arguments = "read_quest", "{}"
        elif ordinal == 2:
            name, arguments = "finish_analysis", '{"result_json":"cut off'
        else:
            assert payload["max_output_tokens"] == 16384
            assert all(item.get("call_id") != "turn-2" for item in payload["input"])
            name, arguments = (
                "finish_analysis",
                json.dumps({"result_json": json.dumps(result_for(nodes))}),
            )
        return httpx.Response(
            200,
            json={
                "status": "incomplete" if ordinal == 2 else "completed",
                "incomplete_details": {"reason": "max_output_tokens"} if ordinal == 2 else None,
                "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [
                    {
                        "type": "function_call",
                        "call_id": f"turn-{ordinal}",
                        "name": name,
                        "arguments": arguments,
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            run = await db.get(ProcessingRun, run_id)
            job = await db.get(AgentJob, run_id)
            assert run.status == "paused_output" and job.checkpoint["step"] == 1
            assert run.tokens_input == 200
            recorded = await db.scalar(
                select(AgentCall).where(AgentCall.run_id == run_id, AgentCall.step == 1)
            )
            old_cost, old_response = recorded.cost_usd, recorded.response
            assert recorded.status == "completed"
            history, evidence = job.checkpoint["history"], job.checkpoint["evidence"]
            if legacy_failure:
                run.status = "failed"
                run.error = "Malformed provider turn; recorded usage is retained"
                await db.commit()
            with pytest.raises(ValueError, match="Increase output_tokens|cannot be resumed"):
                await resume_analysis(db, run_id)
            with pytest.raises(ValueError, match="Increase output_tokens"):
                await resume_analysis(db, run_id, output_tokens=4096)
            with pytest.raises(ValueError, match="Increase extra_steps"):
                await resume_analysis(db, run_id, output_tokens=16384)
            assert job.checkpoint["step"] == 1
            await resume_analysis(db, run_id, output_tokens=16384, extra_steps=1)
            assert job.checkpoint["step"] == 2 and job.checkpoint["evidence"] == evidence
            assert job.checkpoint["history"][:-1] == history
            assert recorded.cost_usd == old_cost and recorded.response == old_response
            assert run.tokens_input == 200
        await execute_job(run_id, engine, settings, client)
        await execute_job(run_id, engine, settings, client)
    assert len(requests) == 3
    async with AsyncSession(engine) as db:
        run = await db.get(ProcessingRun, run_id)
        assert run.status == "completed" and run.tokens_input == 300
        assert len(list(await db.scalars(select(AgentCall).where(AgentCall.run_id == run_id)))) == 3


async def test_output_limit_recovery_rejects_other_provider_failures(world):
    engine, settings, _, _, *_ = world
    run_id = await create(world)
    async with httpx.AsyncClient() as client:
        provider = Provider(settings, client)
        raw = {
            "status": "incomplete",
            "incomplete_details": {"reason": "content_filter"},
            "usage": {"input_tokens": 100, "output_tokens": 30},
            "output": [{"type": "function_call", "arguments": "{"}],
        }
        assert not provider.output_limited(raw)
        assert not provider.output_limited({"status": "incomplete", "incomplete_details": "bad"})
        assert not provider.parse(raw).complete
        for provider_name, limited in [
            ("chat", {"choices": [{"finish_reason": "length"}]}),
            ("gemini", {"candidates": [{"finishReason": "MAX_TOKENS"}]}),
        ]:
            provider.settings = settings.model_copy(update={"provider": provider_name})
            assert provider.output_limited(limited)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        run = await db.get(ProcessingRun, run_id)
        run.status, run.error = "failed", "Malformed provider turn; recorded usage is retained"
        await db.commit()
        with pytest.raises(ValueError, match="No recorded output-limited response"):
            await resume_analysis(db, run_id, output_tokens=16384)
        assert run.status == "failed"


async def test_recorded_turn_replays_after_explicit_tool_limit_increase(world):
    engine, settings, _, nodes, *_ = world
    settings.max_tool_calls_per_step = 1
    run_id = await create(world)
    calls = []

    def transport(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            items = [("read_quest", {}), ("read_node", {"node_id": nodes[3].id})]
        else:
            items = [("finish_analysis", {"result_json": json.dumps(result_for(nodes))})]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [
                    {
                        "type": "function_call",
                        "call_id": f"{len(calls)}-{index}",
                        "name": name,
                        "arguments": json.dumps(args),
                    }
                    for index, (name, args) in enumerate(items)
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert (await db.get(ProcessingRun, run_id)).status == "failed"
            with pytest.raises(ValueError, match="cannot be resumed"):
                await resume_analysis(db, run_id)
            with pytest.raises(ValueError, match="cannot be replayed"):
                await resume_analysis(db, run_id, tool_calls_per_step=1)
            await resume_analysis(db, run_id, tool_calls_per_step=2)
        await execute_job(run_id, engine, settings, client)
    assert len(calls) == 2
    async with AsyncSession(engine) as db:
        run = await db.get(ProcessingRun, run_id)
        assert run.status == "completed" and run.tokens_input == 200


async def test_pagination_unread_citations_and_changed_sources(world):
    engine, settings, _, nodes, locale, release, quest = world
    run_id = await create(world)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        tools = EvidenceTools(
            db, run_id=run_id, quest=quest, release_id=release.id, locale=locale, settings=settings
        )
        result = AnalysisResult.model_validate(result_for(nodes))
        with pytest.raises(ValueError, match="Citation"):
            await tools.validate_result(result)
        first = await tools.read_quest(limit=1)
        assert first["next_offset"] == 1
        with pytest.raises(ValueError, match="every page"):
            await tools.validate_result(result)
        await tools.read_quest(offset=1)
        await tools.validate_result(result)
        assert first["lines"][0]["encounter_order"] == 0
        result.blocks[0].assertions[0].chronology_in_quest.order = 1
        with pytest.raises(ValueError, match="encounter_order"):
            await tools.validate_result(result)
        result.blocks[0].assertions[0].chronology_in_quest.order = 0
        (await db.get(DialogueLine, nodes[3].id)).inline_text = "A different passage."
        await db.flush()
        with pytest.raises(ValueError, match="changed"):
            await tools.validate_result(result)
        await db.rollback()


async def test_later_revelations_publish_a_new_revision_without_rewriting_knowledge(world):
    from wuwa_story.agents.contracts import LaterResolution
    from wuwa_story.agents.publication import publish_analysis

    engine, settings, request, nodes, locale, release, quest = world
    run_id = await create(world)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        tools = EvidenceTools(
            db, run_id=run_id, quest=quest, release_id=release.id, locale=locale, settings=settings
        )
        await tools.read_quest()
        result = AnalysisResult.model_validate(result_for(nodes))
        await tools.validate_result(result)
        job, run = await db.get(AgentJob, run_id), await db.get(ProcessingRun, run_id)
        original = await publish_analysis(db, job, run, result, [])
        await db.commit()
        second_run = await enqueue_analysis(
            db, request.model_copy(update={"generation": "later-revelation"}), settings
        )
        second_job = await db.get(AgentJob, second_run.id)
        result.blocks[0].assertions[0].later_resolution = [
            LaterResolution(
                status="partial",
                text="The later passage establishes the search for another route.",
                revealed_in_node_id=nodes[4].id,
                citations=[{"node_id": nodes[4].id, "quote": "We need another route."}],
            )
        ]
        await tools.validate_result(result)
        revised = await publish_analysis(db, second_job, second_run, result, [])
        await db.commit()
        await db.refresh(original)
        assert original.body_ast[0]["assertions"][0]["later_resolution"] == []
        assert (
            revised.body_ast[0]["assertions"][0]["knowledge_state"]
            == original.body_ast[0]["assertions"][0]["knowledge_state"]
        )
        assert revised.revision == original.revision + 1
        public = (await get_explanation(db, request.quest_id, release.game_version, "en"))[
            "explanation"
        ]
        assert public["id"] == revised.id
        assert public["blocks"][0]["assertions"][0]["later_resolution"][0]["citations"][0]["href"]


async def test_embeddings_search_and_stale_explanation(world):
    engine, settings, request, nodes, *_ = world
    settings.embedding_model = "mock-embedding"
    settings.embedding_dimensions = 3
    run_id = await create(world)
    calls = []

    def transport(message):
        calls.append(str(message.url))
        if message.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "usage": {"prompt_tokens": 20},
                    "data": [{"index": 0, "embedding": [1, 0, 0]}],
                },
            )
        name, args = (
            ("read_quest", {})
            if len(calls) == 1
            else ("finish_analysis", {"result_json": json.dumps(result_for(nodes))})
        )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [
                    {
                        "type": "function_call",
                        "call_id": str(len(calls)),
                        "name": name,
                        "arguments": json.dumps(args),
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await execute_job(run_id, engine, settings, client)
    assert len(calls) == 3
    async with AsyncSession(engine) as db:
        run = await db.get(ProcessingRun, run_id)
        assert run.status == "completed"
        assert (await get_explanation(db, request.quest_id, request.game_version, "en"))[
            "status"
        ] == "available"
        text = await search_explanations(db, "destroyed bridge", request.game_version, "en", 5)
        assert text["mode"] == "text" and text["results"][0]["quest_id"] == request.quest_id
        vector = await search_explanations(
            db, "why?", request.game_version, "en", 5, [1, 0, 0], settings
        )
        assert vector["mode"] == "vector" and vector["results"][0]["score"] == 1
        assert (
            await db.scalar(
                select(ExplanationEmbedding)
                .join(AgentJob, AgentJob.document_id == ExplanationEmbedding.document_id)
                .where(AgentJob.run_id == run_id)
            )
        ).ordinal == 0
        (await db.get(DialogueLine, nodes[3].id)).inline_text = "Revised source"
        await db.flush()
        assert (await get_explanation(db, request.quest_id, request.game_version, "en"))[
            "status"
        ] == "pending"
        assert not (await search_explanations(db, "destroyed", request.game_version, "en", 5))[
            "results"
        ]
        await db.rollback()


async def test_public_http_is_read_only_and_admin_requires_auth(world):
    from fastapi import FastAPI

    from wuwa_story.api.routes import story_agent
    from wuwa_story.db.session import get_session

    engine, _, request, *_ = world
    app = FastAPI()
    app.include_router(story_agent.router)
    app.include_router(story_agent.admin)

    async def session():
        async with AsyncSession(engine) as db:
            yield db

    app.dependency_overrides[get_session] = session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/quests/{request.quest_id}/explanation", params={"game_version": request.game_version}
        )
        assert response.status_code == 200 and response.json()["status"] == "pending"
        assert (
            await client.get(
                "/story-analysis/search", params={"q": "", "game_version": request.game_version}
            )
        ).status_code == 422
        assert (await client.get("/admin/story-agent/usage")).status_code == 401


async def test_admin_job_progress_and_resume_contract(world, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI

    from wuwa_story.api.routes import story_agent
    from wuwa_story.auth.constants import UserRole
    from wuwa_story.auth.dependencies import get_auth_context, require_csrf
    from wuwa_story.db.session import get_session

    engine, settings, *_ = world
    run_id = await create(world)
    app = FastAPI()
    app.include_router(story_agent.admin)

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db

    role = UserRole.USER

    async def auth():
        return SimpleNamespace(user=SimpleNamespace(role=role))

    async def csrf():
        pass

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_auth_context] = auth
    app.dependency_overrides[require_csrf] = csrf
    app.dependency_overrides[story_agent.get_agent_settings] = lambda: settings
    monkeypatch.setattr(story_agent, "get_agent_settings", lambda: settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/admin/story-agent/jobs")).status_code == 403
        role = UserRole.ADMIN
        created = await client.post("/admin/story-agent/jobs", json={"quest_id": world[2].quest_id})
        assert created.status_code == 202 and created.json()["id"] == run_id
        assert (
            await client.post(
                "/admin/story-agent/jobs", json={"quest_id": world[2].quest_id, "locale": "ja"}
            )
        ).status_code == 422
        listing = (await client.get("/admin/story-agent/jobs", params={"limit": 1})).json()
        assert listing["jobs"][0]["id"] == run_id
        assert listing["jobs"][0]["max_steps"] == settings.max_steps
        detail = (await client.get(f"/admin/story-agent/jobs/{run_id}")).json()
        assert detail["limits"]["model"] == settings.model
        assert detail["limits"]["max_output_tokens"] == settings.max_output_tokens
        for invalid_output in [127, 32001]:
            assert (
                await client.post(
                    f"/admin/story-agent/jobs/{run_id}/resume",
                    json={"output_tokens": invalid_output},
                )
            ).status_code == 422
        assert "checkpoint" not in detail and "history" not in detail
        assert "today" in (await client.get("/admin/story-agent/usage")).json()
        async with AsyncSession(engine, expire_on_commit=False) as db:
            run = await db.get(ProcessingRun, run_id)
            run.status = "paused_steps"
            job = await db.get(AgentJob, run_id)
            job.checkpoint = {"step": settings.max_steps}
            await db.commit()
        assert (
            await client.post(f"/admin/story-agent/jobs/{run_id}/resume", json={})
        ).status_code == 422
        resumed = await client.post(
            f"/admin/story-agent/jobs/{run_id}/resume", json={"extra_steps": 2}
        )
        assert resumed.json()["status"] == "queued"


async def test_source_links_and_bounded_transcript_focus(world):
    from fastapi import FastAPI

    from wuwa_story.agents.retrieval import source_link
    from wuwa_story.api.routes.story import transcripts
    from wuwa_story.db.session import get_session

    engine, _, request, nodes, locale, release, _ = world
    async with AsyncSession(engine) as db:
        href = await source_link(db, nodes[4], "dialogue_line", release, locale)
        assert href.startswith(f"/quests/{request.quest_id}?")
        assert "line=" in href and "locale=en" in href
    app = FastAPI()
    app.include_router(transcripts.router)

    async def session():
        async with AsyncSession(engine) as db:
            yield db

    app.dependency_overrides[get_session] = session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        params = {"game_version": request.game_version, "limit": 1}
        initial = await client.get(f"/quests/{request.quest_id}/transcript", params=params)
        assert initial.status_code == 200
        assert initial.json()["lines"][0]["id"] == nodes[3].canonical_key
        focused = await client.get(
            f"/quests/{request.quest_id}/transcript",
            params={**params, "focus_line": nodes[4].canonical_key},
        )
        assert focused.status_code == 200
        assert focused.json()["offset"] == 1
        assert focused.json()["lines"][0]["id"] == nodes[4].canonical_key
        missing = await client.get(
            f"/quests/{request.quest_id}/transcript", params={**params, "focus_line": "missing"}
        )
        assert missing.status_code == 404

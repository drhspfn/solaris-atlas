import json
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.agents.contracts import AnalysisRequest, AnalysisResult
from wuwa_story.agents.evidence import EvidenceTools
from wuwa_story.agents.jobs import enqueue_analysis, resume_analysis
from wuwa_story.agents.retrieval import get_explanation, search_explanations
from wuwa_story.agents.runner import execute_job
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import AgentCall, AgentDailyUsage, AgentJob, ExplanationEmbedding
from wuwa_story.db.models.content import Document
from wuwa_story.db.models.core import DialogueLine, Quest, QuestAction, QuestState
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision, NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease, ProcessingRun
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
            runs = select(ProcessingRun.id).where(ProcessingRun.target_node_id == quest.node_id)
            events = list(
                await db.scalars(select(Event.node_id).where(Event.processor_run_id.in_(runs)))
            )
            await db.execute(delete(AgentCall).where(AgentCall.run_id.in_(runs)))
            await db.execute(delete(Claim).where(Claim.processor_run_id.in_(runs)))
            await db.execute(delete(ProcessingRun).where(ProcessingRun.id.in_(runs)))
            await db.execute(delete(Node).where(Node.id.in_([node.id for node in nodes] + events)))
            await db.execute(delete(GameRelease).where(GameRelease.id == release.id))
            await db.execute(
                delete(AgentDailyUsage).where(
                    ~select(AgentCall.id).where(AgentCall.day == AgentDailyUsage.day).exists()
                )
            )
            await db.commit()
        await engine.dispose()


def result_for(nodes):
    citation = {"node_id": nodes[3].id, "quote": "The bridge was destroyed."}
    return {
        "title": "A blocked crossing",
        "blocks": [
            {
                "title": "Why the route changes",
                "text": "The destroyed bridge forces a change of route.",
                "citations": [citation],
            }
        ],
        "links": [
            {
                "from_node_id": nodes[3].id,
                "to_node_id": nodes[4].id,
                "relation": "EXPLAINS",
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


async def create(world):
    engine, settings, request, *_ = world
    async with AsyncSession(engine, expire_on_commit=False) as db:
        return (await enqueue_analysis(db, request, settings)).id


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
            await resume_analysis(db, run_id, extra_steps=1)
        await execute_job(run_id, engine, settings, client)
    assert len(calls) == 2
    assert any(item.get("type") == "function_call_output" for item in calls[1]["input"])
    async with AsyncSession(engine) as db:
        assert (await db.get(ProcessingRun, run_id)).status == "completed"
        assert (await get_explanation(db, request.quest_id, request.game_version, "en"))[
            "status"
        ] == "available"


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
        (await db.get(DialogueLine, nodes[3].id)).inline_text = "A different passage."
        await db.flush()
        with pytest.raises(ValueError, match="changed"):
            await tools.validate_result(result)
        await db.rollback()


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

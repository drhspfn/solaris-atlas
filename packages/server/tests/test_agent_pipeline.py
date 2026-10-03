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
from wuwa_story.agents.evidence import EvidenceTools
from wuwa_story.agents.jobs import enqueue_analysis, resume_analysis
from wuwa_story.agents.providers import Provider
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
            await db.execute(
                delete(LocalizationValue).where(LocalizationValue.release_id == release.id)
            )
            await db.execute(delete(Node).where(Node.id.in_([node.id for node in nodes] + events)))
            await db.execute(
                delete(LocalizationKey).where(LocalizationKey.first_release_id == release.id)
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
            == settings.max_input_tokens + settings.compaction_output_tokens
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

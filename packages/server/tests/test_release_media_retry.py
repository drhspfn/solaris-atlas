import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion import release_media_retry as retry


def old_run(key, kind, status, targets, *, release_id=4, root_id=None, retry_of=None):
    return SimpleNamespace(id=key, processor_id=7, status=status, error="old diagnostic", finished_at="old timestamp",
        raw_output={"entity_run_id": 800 + key}, metadata_json={
            "request": {"release_id": release_id, "game_version": "2.0", "kind": kind,
                        "targets": targets, "parent_id": 1},
            **({"retry_root_id": root_id, "retry_of": retry_of} if root_id else {})})


class Session:
    """Persist rows across helper calls; inspect SQL locks and publication boundaries."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []
        self.events = []
        self.committed = []
        self.statements = []
        self.release = SimpleNamespace(id=4, game_version="2.0")

    async def get(self, model, key):
        return self.release if key == self.release.id else None

    async def execute(self, statement, params=None):
        self.events.append((str(statement), params))

    async def scalar(self, statement):
        return 7

    async def scalars(self, statement):
        self.statements.append(statement)
        params = statement.compile().params
        if params.get("status_1") == "pending":
            assert statement._for_update_arg is not None
            return sorted((run for run in self.rows if run.id in params["id_1"] and run.status == "pending"),
                          key=lambda run: run.id)
        if "id_1" in params:
            assert statement._for_update_arg is not None
            return sorted((run for run in self.rows if run.id in params["id_1"]), key=lambda run: run.id)
        return sorted((run for run in self.rows
                       if run.processor_id == 7 and retry._request(run).get("release_id") == 4),
                      key=lambda run: run.id)

    def add(self, run):
        self.rows.append(run)
        self.added.append(run)

    async def flush(self):
        for run in self.added:
            if run.id is None:
                run.id = max((item.id for item in self.rows if item.id is not None), default=1000) + 1

    async def commit(self):
        self.events.append(("COMMIT", None))
        self.committed = list(self.rows)


@pytest.mark.asyncio
async def test_default_retry_allocates_only_selected_failures_and_required_prerequisites(monkeypatch):
    originals = [old_run(index, "cutscene", "completed", [f"good-{index}"]) for index in range(1, 201)]
    originals += [old_run(210, "cutscene", "failed", ["CG_A"]),
                  old_run(211, "cutscene", "partial", ["CG_B"]),
                  old_run(212, "images", "failed", ["character:1"]),
                  old_run(213, "event_catalog", "failed", ["client-catalog:v2"]),
                  old_run(214, "voices", "partial", ["vo_test"]),
                  old_run(215, "images", "partial", ["character:2"]),
                  old_run(216, "event_catalog", "partial", ["old-event"]),
                  old_run(217, "cutscene", "failed", ["other-release"], release_id=5)]
    before = copy.deepcopy([vars(run) for run in originals])
    session = Session(originals)
    published = []

    async def publish(payload, message_id, queue):
        assert {run.id for run in session.added} <= {run.id for run in session.committed}
        assert len(session.added) == 7  # Entire graph exists before the first publish.
        published.append((payload, message_id, queue))

    monkeypatch.setattr(retry, "publish_media_job", publish)
    result = await retry.enqueue_failed_release_media(session, 4)
    assert result["retry_of"] == [210, 211, 212, 213]
    assert result["tasks"] == result["queued"] == 7
    assert result["enqueue_failed"] == result["reused"] == 0
    assert [vars(run) for run in originals] == before
    by_kind = {}
    for run in session.added:
        by_kind.setdefault(retry._request(run)["kind"], []).append(run)
        assert run.status == "queued"
        assert run.metadata_json["retry_root_id"] == result["parent_ids"][0]
    prepare = by_kind["prepare"][0]
    assets = by_kind["cutscene_assets"][0]
    assert retry._request(assets)["targets"] == ["CG_A", "CG_B"]
    assert retry._request(assets)["parent_id"] == prepare.id
    assert retry._request(by_kind["voice_packages"][0])["parent_id"] == prepare.id
    assert all(retry._request(run)["parent_id"] == assets.id for run in by_kind["cutscene"])
    assert retry._request(by_kind["images"][0])["parent_id"] == prepare.id
    assert by_kind["images"][0].metadata_json["retry_of"] == 212
    assert by_kind["images"][0].raw_output is None
    assert {queue for _, _, queue in published} == {retry.QUEUE, retry.EVENT_QUEUE, "wuwa.release-images.v1"}
    commit_index = next(index for index, event in enumerate(session.events) if event[0] == "COMMIT")
    assert any("pg_advisory_xact_lock(701" in query for query, _ in session.events[:commit_index])
    assert any("pg_advisory_xact_lock_shared(706" in query and params == {"key": prepare.id}
               for query, params in session.events[:commit_index])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "running", "waiting_dependency"])
async def test_repeated_request_reuses_active_graph_without_duplicate_publication(monkeypatch, status):
    session = Session([old_run(10, "cutscene", "failed", ["CG_A"])])
    publish = AsyncMock()
    monkeypatch.setattr(retry, "publish_media_job", publish)
    first = await retry.enqueue_failed_release_media(session, 4)
    for run in session.added:
        run.status = status
    second = await retry.enqueue_failed_release_media(session, 4)
    assert second["run_ids"] == first["run_ids"]
    assert second["parent_ids"] == first["parent_ids"]
    assert second["queued"] == 0 and second["retry_of"] == []
    assert second["reused"] == second["tasks"] == len(session.added) == 4
    assert publish.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("leaf_status", ["failed", "blocked"])
async def test_new_attempt_after_failure_keeps_successful_retry_jobs_untouched(monkeypatch, tmp_path, leaf_status):
    monkeypatch.setenv("WUWA_ASSET_WORKSPACE", str(tmp_path))
    session = Session([old_run(10, "cutscene", "failed", ["CG_A"]), old_run(11, "cutscene", "failed", ["CG_B"])])
    monkeypatch.setattr(retry, "publish_media_job", AsyncMock())
    first = await retry.enqueue_failed_release_media(session, 4)
    previous = list(session.added)
    failed = next(run for run in previous if retry._request(run)["targets"] == ["CG_A"]
                  and retry._request(run)["kind"] == "cutscene")
    for run in previous:
        run.status = leaf_status if run is failed else "completed"
        if leaf_status == "blocked" and retry._request(run)["kind"] == "prepare":
            run.status = "failed"
    prepare = previous[0]
    prepare.raw_output = {"workspace_id": prepare.id, "asset_version": "3.7.0"}
    before = copy.deepcopy([(run.status, run.metadata_json, run.raw_output) for run in previous])
    second = await retry.enqueue_failed_release_media(session, 4)
    assert second["retry_of"] == [failed.id]
    assert second["parent_ids"] != first["parent_ids"]
    new = session.added[len(previous):]
    assert len(new) == 4
    assert retry._request(new[1])["targets"] == ["CG_A"]
    assert new[0].input_hash != prepare.input_hash
    assert [(run.status, run.metadata_json, run.raw_output) for run in previous] == before


@pytest.mark.asyncio
async def test_completed_newer_attempt_suppresses_old_failure(monkeypatch):
    session = Session([old_run(10, "images", "failed", ["character:1"]),
                       old_run(11, "images", "completed", ["character:1"])])
    publish = AsyncMock()
    monkeypatch.setattr(retry, "publish_media_job", publish)
    result = await retry.enqueue_failed_release_media(session, 4)
    assert result["tasks"] == result["queued"] == 0
    assert not session.added
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_only_and_explicit_partial_image_scope_omit_voice_prerequisites(monkeypatch):
    session = Session([old_run(10, "images", "partial", ["character:1"]),
                       old_run(11, "event_catalog", "partial", ["client-catalog:v2"]),
                       old_run(12, "cutscene", "failed", ["CG_A"])])
    monkeypatch.setattr(retry, "publish_media_job", AsyncMock())
    result = await retry.enqueue_failed_release_media(session, 4, kinds={"images", "event_catalog"}, statuses={"partial"})
    assert result["retry_of"] == [10, 11]
    assert [retry._request(run)["kind"] for run in session.added] == ["prepare", "images", "event_catalog"]


@pytest.mark.asyncio
async def test_unconfirmed_publication_reuses_and_resumes_only_retry_graph(monkeypatch):
    original = old_run(10, "images", "failed", ["character:1"])
    session = Session([original])
    publish = AsyncMock(side_effect=[OSError("unconfirmed"), None, None, None])
    monkeypatch.setattr(retry, "publish_media_job", publish)
    first = await retry.enqueue_failed_release_media(session, 4)
    assert first["enqueue_failed"] == 1 and first["queued"] == 1
    # The image worker can observe the failed prepare and become blocked.
    session.added[1].status = "blocked"
    second = await retry.enqueue_failed_release_media(session, 4)
    assert second["parent_ids"] == first["parent_ids"]
    assert second["queued"] == 2 and second["enqueue_failed"] == 0
    assert len(session.added) == 2
    assert original.status == "failed" and original.raw_output == {"entity_run_id": 810}
    assert all(run.status == "queued" for run in session.added)


@pytest.mark.asyncio
async def test_committed_unpublished_graph_is_resumed_after_publisher_crash(monkeypatch):
    session = Session([old_run(10, "images", "failed", ["character:1"])])
    publisher = retry._publish_pending
    monkeypatch.setattr(retry, "_publish_pending", AsyncMock(side_effect=RuntimeError("publisher crashed")))
    with pytest.raises(RuntimeError, match="publisher crashed"):
        await retry.enqueue_failed_release_media(session, 4)
    assert len(session.committed) == 3 and len(session.added) == 2
    assert all(run.status == "pending" for run in session.added)
    monkeypatch.setattr(retry, "_publish_pending", publisher)
    publish = AsyncMock()
    monkeypatch.setattr(retry, "publish_media_job", publish)
    result = await retry.enqueue_failed_release_media(session, 4)
    assert len(session.added) == 2 and result["queued"] == result["reused"] == 2
    assert publish.await_count == 2


@pytest.mark.asyncio
async def test_recovery_rechecks_claimed_run_before_resetting_publication_state(monkeypatch):
    session = Session([old_run(10, "images", "failed", ["character:1"])])
    publish = AsyncMock(side_effect=[OSError("unconfirmed"), None, None])
    monkeypatch.setattr(retry, "publish_media_job", publish)
    await retry.enqueue_failed_release_media(session, 4)
    prepare, images = session.added
    images.status = "blocked"
    scalars = session.scalars

    async def claim_before_lock(statement):
        params = statement.compile().params
        if "id_1" in params and "status_1" not in params:
            prepare.status = "running"  # Worker claimed the unconfirmed delivery.
        return await scalars(statement)

    session.scalars = claim_before_lock
    result = await retry.enqueue_failed_release_media(session, 4)
    assert prepare.status == "running"
    assert images.status == "queued"
    assert result["queued"] == 1 and len(session.added) == 2
    assert publish.await_count == 3


@pytest.mark.asyncio
async def test_gc_winning_before_resume_lock_creates_fresh_prerequisites(monkeypatch):
    session = Session([old_run(10, "images", "failed", ["character:1"])])
    publish = AsyncMock(side_effect=[None, OSError("unconfirmed"), None, None])
    monkeypatch.setattr(retry, "publish_media_job", publish)
    first = await retry.enqueue_failed_release_media(session, 4)
    prepare, images = list(session.added)
    prepare.status = "completed"
    prepare.raw_output = {"workspace_id": prepare.id, "asset_version": "3.7.0"}
    expired = iter([False, True])
    monkeypatch.setattr(retry, "workspace_expired", lambda _: next(expired))
    second = await retry.enqueue_failed_release_media(session, 4)
    assert second["parent_ids"] != first["parent_ids"]
    assert second["retry_of"] == [images.id]
    assert len(session.added) == 4
    assert images.status == "enqueue_failed" and prepare.status == "completed"
    assert second["queued"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"kinds": {"voices"}}, {"kinds": set()},
                                   {"statuses": {"cancelled"}}, {"statuses": set()}])
async def test_unsupported_scope_fails_before_database_work(kwargs):
    session = AsyncMock()
    with pytest.raises(ValueError):
        await retry.enqueue_failed_release_media(session, 4, **kwargs)
    session.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_release_fails_without_publication(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(retry, "publish_media_job", publish)
    with pytest.raises(ValueError, match="not found"):
        await retry.enqueue_failed_release_media(Session(), 5)
    publish.assert_not_awaited()

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from wuwa_story.api.routes import character_models as routes
from wuwa_story.auth.constants import UserRole
from wuwa_story.auth.dependencies import get_auth_context
from wuwa_story.auth.security import csrf_token
from wuwa_story.config.settings import get_settings
from wuwa_story.db.repositories import character_models as repository
from wuwa_story.db.session import get_session
from wuwa_story.ingestion import character_models as service


@pytest.fixture(autouse=True)
def no_previous_models_by_default(monkeypatch):
    monkeypatch.setattr(service, "previous_model_releases", AsyncMock(return_value=[]))


async def test_previous_model_releases_never_return_future_patches():
    db = AsyncMock()
    db.scalars.return_value = [
        SimpleNamespace(id=10, game_version="3.7.0", sequence=10),
        SimpleNamespace(id=8, game_version="3.5.0", sequence=8),
        SimpleNamespace(id=3, game_version="3.3.0", sequence=3),
    ]
    releases = await repository.previous_model_releases(
        db, 15, SimpleNamespace(id=9, game_version="3.6.0")
    )
    assert [release.game_version for release in releases] == ["3.5.0", "3.3.0"]


async def test_missing_snapshot_reuses_only_a_complete_earlier_bundle(monkeypatch):
    current = SimpleNamespace(id=9, game_version="3.6.0")
    newer_incomplete = SimpleNamespace(id=8, game_version="3.5.0")
    older_complete = SimpleNamespace(id=7, game_version="3.4.0")
    monkeypatch.setattr(service, "previous_model_releases", AsyncMock(
        return_value=[newer_incomplete, older_complete]))
    async def rows(session, owner, release_id, bucket):
        if release_id == 8:
            return [("character_model_preview", "new", "objects/new", 1, None, None, None, None)]
        if release_id == 7:
            return [("character_model_preview", "old", "objects/preview", 1, None, None, None, None),
                    ("character_model_download", "old", "objects/download", 2, None, None, None, None)]
        return []
    monkeypatch.setattr(service, "published_model_files", rows)
    monkeypatch.setattr(service, "S3Storage", lambda _: SimpleNamespace(public_url=lambda key: key))
    result = await service.public_character_model(AsyncMock(), 15, current)
    assert result["status"] == "ready"
    assert result["game_version"] == "3.6.0"
    assert result["source_game_version"] == "3.4.0"
    assert result["download"]["url"] == "objects/download"


def test_admin_auth_and_csrf_are_enforced(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_session] = lambda: AsyncMock()
    enqueue = AsyncMock(return_value=SimpleNamespace(id=7, status="queued"))
    monkeypatch.setattr(routes, "enqueue_character_model", enqueue)
    client = TestClient(app)
    body = {"character_key": "character:1505"}

    async def unauthenticated():
        raise HTTPException(401)

    app.dependency_overrides[get_auth_context] = unauthenticated
    assert client.post("/admin/character-models", json=body).status_code == 401
    assert client.get("/admin/character-models/7").status_code == 401
    app.dependency_overrides[get_auth_context] = lambda: SimpleNamespace(
        user=SimpleNamespace(role=UserRole.USER)
    )
    assert client.post("/admin/character-models", json=body).status_code == 403
    assert client.get("/admin/character-models/7").status_code == 403
    app.dependency_overrides[get_auth_context] = lambda: SimpleNamespace(
        user=SimpleNamespace(role=UserRole.ADMIN)
    )
    assert client.post("/admin/character-models", json=body).status_code == 403
    settings = get_settings()
    token = csrf_token(settings.auth_csrf_secret.get_secret_value())
    client.cookies.set(settings.auth_csrf_cookie_name, token)
    assert (
        client.post(
            "/admin/character-models", json=body, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    enqueue.assert_not_awaited()
    response = client.post("/admin/character-models", json=body, headers={"X-CSRF-Token": token})
    assert response.status_code == 202
    assert response.json() == {"id": 7, "status": "queued"}


@pytest.mark.parametrize(
    "patch",
    [
        {"character_key": "../1505"},
        {"character_key": "item:1505"},
        {"force": "yes"},
        {"include_animations": "yes"},
        {"source_path": "/private/model"},
        {"game_version": ""},
    ],
)
def test_request_validation(patch):
    with pytest.raises(ValidationError):
        service.CharacterModelRequest.model_validate({"character_key": "character:1505", **patch})


@pytest.fixture
def enqueue_context(monkeypatch):
    db = AsyncMock()
    db.scalar.side_effect = [3, None]
    db.add = Mock(side_effect=lambda run: setattr(run, "id", 42))
    monkeypatch.setattr(
        service,
        "get_node",
        AsyncMock(return_value=SimpleNamespace(id=15, canonical_key="character:1505")),
    )
    monkeypatch.setattr(
        service,
        "model_release",
        AsyncMock(return_value=SimpleNamespace(id=8, game_version="1.4.0")),
    )
    monkeypatch.setattr(
        service,
        "inspect_installed_clients",
        Mock(
            return_value=[
                {"state": "downloading", "version": "3.8.0"},
                {
                    "state": "downloaded",
                    "version": "3.7.0",
                    "download_id": "a" * 64,
                    "tier": "hd",
                    "path": "/private/assets",
                },
            ]
        ),
    )
    publish = AsyncMock()
    monkeypatch.setattr(service, "publish_media_job", publish)
    return db, publish


async def test_exact_durable_payload_and_confirmation(enqueue_context):
    db, publish = enqueue_context

    async def confirmed(payload, job_id, queue):
        db.commit.assert_awaited_once()
        assert job_id == "character-model:42" and queue == service.QUEUE

    publish.side_effect = confirmed
    run = await service.enqueue_character_model(
        db, service.CharacterModelRequest(character_key="character:1505")
    )
    expected = {
        "schema_version": 1,
        "job_type": "media.character_model",
        "run_id": 42,
        "character_key": "character:1505",
        "owner_node_id": 15,
        "release_id": 8,
        "game_version": "1.4.0",
        "asset_version": "3.7.0",
        "download_id": "a" * 64,
        "tier": "hd",
        "include_animations": True,
    }
    assert run.metadata_json == {"payload": expected, "queue": service.QUEUE}
    assert publish.await_args.args[0] == expected
    assert service.CharacterModelJob.model_validate(expected).model_dump() == expected
    for patch in (
        {"download_id": "../client"},
        {"tier": "arbitrary"},
        {"run_id": 0},
        {"job_type": "media.entities"},
        {"schema_version": 2},
        {"path": "/secret"},
    ):
        with pytest.raises(ValidationError):
            service.CharacterModelJob.model_validate({**expected, **patch})


@pytest.mark.parametrize(
    "status,force",
    [
        ("queued", False),
        ("running", True),
        ("waiting_dependency", True),
        ("pending", True),
        ("completed", False),
    ],
)
async def test_idempotency_never_duplicates_active_or_unforced_completed(
    enqueue_context,
    status,
    force,
):
    db, publish = enqueue_context
    existing = SimpleNamespace(id=9, status=status)
    db.scalar.side_effect = [3, existing]
    run = await service.enqueue_character_model(
        db, service.CharacterModelRequest(character_key="character:1505", force=force)
    )
    assert run is existing
    publish.assert_not_awaited()
    db.add.assert_not_called()


async def test_animation_import_is_explicit_in_durable_identity(enqueue_context):
    db, publish = enqueue_context
    request = service.CharacterModelRequest(
        character_key="character:1505", include_animations=False
    )
    run = await service.enqueue_character_model(db, request)
    assert publish.await_args.args[0]["include_animations"] is False
    legacy = {
        key: value
        for key, value in run.metadata_json["payload"].items()
        if key != "include_animations"
    }
    assert service.CharacterModelJob.model_validate(legacy).include_animations is False
    assert service.CharacterModelRequest(character_key="character:1505").include_animations is True


@pytest.mark.parametrize(
    "status,force", [("completed", True), ("failed", False), ("enqueue_failed", False)]
)
async def test_force_and_retry_preserve_previous_run(enqueue_context, status, force):
    db, publish = enqueue_context
    existing = SimpleNamespace(id=9, status=status)
    db.scalar.side_effect = [3, existing]
    run = await service.enqueue_character_model(
        db, service.CharacterModelRequest(character_key="character:1505", force=force)
    )
    assert run.id == 42 and existing.status == status
    publish.assert_awaited_once()


async def test_unconfirmed_enqueue_is_failure(enqueue_context, monkeypatch):
    db, publish = enqueue_context
    publish.side_effect = OSError("private broker detail")
    monkeypatch.setattr(routes, "enqueue_character_model", service.enqueue_character_model)
    with pytest.raises(HTTPException) as error:
        await routes.create_character_model(
            service.CharacterModelRequest(character_key="character:1505"), db
        )
    assert error.value.status_code == 503
    assert "private" not in error.value.detail
    assert db.commit.await_count == 2
    update = db.execute.await_args.args[0]
    assert update.compile().params["status"] == "enqueue_failed"


@pytest.mark.parametrize("missing", ["character", "release", "client"])
async def test_missing_prerequisites_do_not_enqueue(enqueue_context, monkeypatch, missing):
    db, publish = enqueue_context
    if missing == "character":
        monkeypatch.setattr(service, "get_node", AsyncMock(return_value=None))
    elif missing == "release":
        monkeypatch.setattr(service, "model_release", AsyncMock(return_value=None))
    else:
        monkeypatch.setattr(service, "inspect_installed_clients", Mock(return_value=[]))
    with pytest.raises(ValueError):
        await service.enqueue_character_model(
            db, service.CharacterModelRequest(character_key="character:1505")
        )
    publish.assert_not_awaited()
    db.add.assert_not_called()


async def test_status_is_processor_scoped():
    db = AsyncMock()
    run = SimpleNamespace(
        id=42, processor_id=3, status="completed", raw_output={"files": 2}, error=None
    )
    db.get.side_effect = [run, SimpleNamespace(key=service.PROCESSOR)]
    assert await routes.character_model_status(42, db) == {
        "id": 42,
        "status": "completed",
        "result": {"files": 2},
        "error": None,
    }
    db.get.side_effect = [run, SimpleNamespace(key="entity_media")]
    with pytest.raises(HTTPException) as error:
        await routes.character_model_status(42, db)
    assert error.value.status_code == 404


async def test_public_bundle_does_not_mix_generations_or_expose_metadata(monkeypatch):
    rows = [
        ("character_model_preview", "new", "objects/new-preview", 100, None, None, None, None),
        (
            "character_model_animations",
            "new",
            "objects/new-animations",
            500,
            None,
            None,
            None,
            None,
        ),
        ("character_model_download", "old", "objects/old-download", 200, None, None, None, None),
        ("character_model_physics", "old", "objects/old-physics", 30, None, None, None, None),
    ]
    monkeypatch.setattr(service, "published_model_files", AsyncMock(return_value=rows))
    monkeypatch.setattr(
        service,
        "S3Storage",
        lambda _: SimpleNamespace(public_url=lambda key: "https://media.example/" + key),
    )
    release = SimpleNamespace(id=8, game_version="1.4.0")
    result = await service.public_character_model(AsyncMock(), 15, release)
    assert result == {
        "status": "missing",
        "game_version": "1.4.0",
        "preview": None,
        "download": None,
        "physics": None,
        "animations": None,
        "viewer_url": None,
        "forms": [],
    }
    rows.append(
        ("character_model_preview", "old", "objects/old-preview", 99, None, None, None, None)
    )
    result = await service.public_character_model(AsyncMock(), 15, release)
    assert result["status"] == "ready"
    assert result["preview"] == {
        "url": "https://media.example/objects/old-preview",
        "size_bytes": 99,
    }
    rows.insert(
        1, ("character_model_download", "new", "objects/new-download", 250, None, None, None, None)
    )
    result = await service.public_character_model(AsyncMock(), 15, release)
    assert result["preview"]["size_bytes"] == 100
    assert result["animations"]["size_bytes"] == 500
    assert result["download"]["size_bytes"] == 250
    assert result["physics"] is None
    assert result["viewer_url"] is None
    assert result["forms"] == [
        {
            "id": "default",
            "label": "Default",
            **{kind: result[kind] for kind in (*repository.REFERENCE_TYPES.values(), "viewer_url")},
        }
    ]


@pytest.mark.parametrize(
    "viewer_key",
    [
        None,
        "private/index.html",
        "https://other.example/index.html",
        "model-viewer/" + "a" * 63 + "/index.html",
        "model-viewer/" + "A" * 64 + "/index.html",
        "model-viewer/" + "a" * 64 + "/../index.html",
        "model-viewer/" + "a" * 64 + "/index.html\n",
        "model-viewer/" + "a" * 64 + "/index.html",
    ],
)
async def test_viewer_url_is_validated_and_scoped_to_complete_bundle(monkeypatch, viewer_key):
    other_key = "model-viewer/" + "b" * 64 + "/index.html"
    rows = [
        (
            "character_model_preview",
            "incomplete",
            "objects/new-preview",
            100,
            other_key,
            None,
            None,
            None,
        ),
        (
            "character_model_preview",
            "complete",
            "objects/preview",
            99,
            viewer_key,
            None,
            None,
            None,
        ),
        (
            "character_model_download",
            "complete",
            "objects/download",
            200,
            viewer_key,
            None,
            None,
            None,
        ),
    ]
    monkeypatch.setattr(service, "published_model_files", AsyncMock(return_value=rows))
    monkeypatch.setattr(
        service,
        "S3Storage",
        lambda _: SimpleNamespace(public_url=lambda key: "https://media.example/" + key),
    )
    result = await service.public_character_model(
        AsyncMock(), 15, SimpleNamespace(id=8, game_version="1.4.0")
    )
    assert result["status"] == "ready"
    valid_key = "model-viewer/" + "a" * 64 + "/index.html"
    assert result["viewer_url"] == (
        "https://media.example/" + valid_key if viewer_key == valid_key else None
    )


async def test_repository_filters_available_bucket_and_release():
    db = AsyncMock()
    db.execute.return_value = []
    await repository.published_model_files(db, 15, 8, "configured")
    sql = str(
        db.execute.await_args.args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    for clause in (
        "owner_node_id = 15",
        "release_id = 8",
        "bucket = 'configured'",
        "available IS true",
        "is_primary IS true",
        "backend = 's3'",
    ):
        assert clause in sql
    assert "source_path" not in sql and "source_name" not in sql
    assert "model_bundle_id" in sql
    assert "viewer_object_key" in sql
    for field in ("form_id", "form_label", "form_order"):
        assert field in sql
    assert [column.name for column in db.execute.await_args.args[0].selected_columns][-3:] == [
        "form_id",
        "form_label",
        "form_order",
    ]


@pytest.fixture
def public_forms(monkeypatch):
    rows = []
    monkeypatch.setattr(service, "published_model_files", AsyncMock(return_value=rows))
    monkeypatch.setattr(
        service,
        "S3Storage",
        lambda _: SimpleNamespace(public_url=lambda key: "https://media.example/" + key),
    )
    return rows


def form_rows(
    bundle, form_id="mesh-100038", label="Normal", order=0, kinds=("preview", "download")
):
    return [
        (
            f"character_model_{kind}",
            bundle,
            f"objects/{bundle}/{form_id}/{kind}",
            100,
            None,
            form_id,
            label,
            order,
        )
        for kind in kinds
    ]


async def test_public_forms_keep_files_and_viewers_in_their_form(public_forms):
    viewer = "model-viewer/" + "a" * 64 + "/index.html"
    public_forms.extend(
        form_rows("new", "mesh-100039", "Transformed", 1, ("preview", "download", "animations"))
    )
    public_forms.extend(form_rows("new", kinds=("preview", "download", "physics")))
    # Content-addressed files may be shared by multiple forms.
    public_forms[0] = (
        *public_forms[0][:2],
        "objects/shared-preview",
        100,
        viewer,
        *public_forms[0][5:],
    )
    public_forms[3] = (
        *public_forms[3][:2],
        "objects/shared-preview",
        100,
        None,
        *public_forms[3][5:],
    )
    result = await service.public_character_model(
        AsyncMock(), 15, SimpleNamespace(id=8, game_version="1.4.0")
    )
    default, transformed = result["forms"]
    assert result["status"] == "ready"
    assert [form["id"] for form in result["forms"]] == ["mesh-100038", "mesh-100039"]
    assert [form["label"] for form in result["forms"]] == ["Normal", "Transformed"]
    assert default["preview"] == transformed["preview"]
    assert default["physics"] is not None and default["animations"] is None
    assert transformed["physics"] is None and transformed["animations"] is not None
    assert default["viewer_url"] is None
    assert transformed["viewer_url"] == "https://media.example/" + viewer
    for kind in (*repository.REFERENCE_TYPES.values(), "viewer_url"):
        assert result[kind] == default[kind]
    for form in result["forms"]:
        assert set(form) == {
            "id",
            "label",
            "preview",
            "download",
            "physics",
            "animations",
            "viewer_url",
        }
        for kind in repository.REFERENCE_TYPES.values():
            if form[kind]:
                assert set(form[kind]) == {"url", "size_bytes"}
    assert "model_bundle_id" not in str(result) and "form_order" not in str(result)


@pytest.mark.parametrize("missing_kind", ["preview", "download"])
async def test_public_forms_skip_whole_incomplete_publication(public_forms, missing_kind):
    public_forms.extend(form_rows("new"))
    public_forms.extend(
        form_rows(
            "new",
            "mesh-100039",
            "Other",
            1,
            tuple(kind for kind in ("preview", "download", "animations") if kind != missing_kind),
        )
    )
    # A matching form from another run must never complete the new run.
    public_forms.extend(form_rows("partial", "mesh-100039", "Other", 1, (missing_kind,)))
    public_forms.extend(form_rows("old"))
    result = await service.public_character_model(
        AsyncMock(), 15, SimpleNamespace(id=8, game_version="1.4.0")
    )
    assert result["status"] == "ready" and len(result["forms"]) == 1
    assert "/old/" in result["preview"]["url"]
    assert result["animations"] is None


@pytest.mark.parametrize(
    "column,value",
    [
        (5, "../private"),
        (5, "mesh-0"),
        (5, 100038),
        (5, {}),
        (6, ""),
        (6, "  "),
        (6, 42),
        (6, None),
        (7, -1),
        (7, True),
        (7, "0"),
        (7, 1.5),
        (6, "Inconsistent label"),
        (7, 3),
    ],
)
async def test_public_forms_skip_malformed_bundle(public_forms, column, value):
    public_forms.extend(form_rows("bad"))
    public_forms.extend(form_rows("bad", "mesh-100039", "Other", 1))
    corrupted = list(public_forms[0])
    corrupted[column] = value
    public_forms[0] = tuple(corrupted)
    result = await service.public_character_model(
        AsyncMock(), 15, SimpleNamespace(id=8, game_version="1.4.0")
    )
    assert result["status"] == "missing" and result["forms"] == []
    public_forms.extend(form_rows("good"))
    result = await service.public_character_model(
        AsyncMock(), 15, SimpleNamespace(id=8, game_version="1.4.0")
    )
    assert result["status"] == "ready"
    assert "/good/" in result["preview"]["url"]


async def test_public_forms_choose_newest_complete_and_sort_default_orders(public_forms):
    public_forms.extend(form_rows("incomplete", kinds=("animations",)))
    public_forms.extend(form_rows("new", "mesh-100039", "Other", None))
    public_forms.extend(form_rows("old", kinds=("preview", "download", "physics")))
    public_forms.extend(form_rows("new"))
    result = await service.public_character_model(
        AsyncMock(), 15, SimpleNamespace(id=8, game_version="1.4.0")
    )
    assert [form["id"] for form in result["forms"]] == ["mesh-100038", "mesh-100039"]
    assert "/new/" in result["preview"]["url"]
    assert result["physics"] is None and result["animations"] is None


async def test_public_missing_release_has_empty_forms(public_forms):
    result = await service.public_character_model(AsyncMock(), 15, None)
    assert result["status"] == "missing" and result["forms"] == []


def test_public_get_serializes_multiple_forms(public_forms, monkeypatch):
    public_forms.extend(form_rows("new"))
    public_forms.extend(form_rows("new", "mesh-100039", "Other", 1))
    monkeypatch.setattr(routes, "get_node", AsyncMock(return_value=SimpleNamespace(id=15)))
    monkeypatch.setattr(
        routes,
        "model_release",
        AsyncMock(return_value=SimpleNamespace(id=8, game_version="1.4.0")),
    )
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_session] = lambda: AsyncMock()
    response = TestClient(app).get("/characters/1505/model?game_version=1.4.0")
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ready" and result["game_version"] == "1.4.0"
    assert [form["id"] for form in result["forms"]] == ["mesh-100038", "mesh-100039"]
    assert result["preview"] == result["forms"][0]["preview"]


async def test_public_route_uses_existing_character_resolver(monkeypatch):
    resolve = AsyncMock(return_value=SimpleNamespace(id=15))
    monkeypatch.setattr(routes, "get_node", resolve)
    monkeypatch.setattr(routes, "model_release", AsyncMock(return_value=None))
    db = AsyncMock()
    result = await routes.character_model("1505-shorekeeper", "unknown", db)
    resolve.assert_awaited_once_with(db, "1505-shorekeeper", ("character",))
    assert result["status"] == "missing" and result["game_version"] == "unknown"

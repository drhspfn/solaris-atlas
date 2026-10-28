import json
import re
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wuwa_story_worker import model_publication


@pytest.mark.asyncio
async def test_activation_publishes_tree_before_single_atomic_pointer_write(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "viewer.js").write_text("export {};")
    events = []

    async def upload(path, key, mime):
        events.append(key)

    client = SimpleNamespace(put_object=Mock(side_effect=lambda **kw: events.append(kw["Key"])))
    storage = SimpleNamespace(
        bucket="public",
        client=client,
        stat=AsyncMock(return_value=None),
        put_file=AsyncMock(side_effect=upload),
    )
    key = await model_publication.activate_viewer(storage, tmp_path)
    assert key == "model-viewer/current/index.html"
    assert len(events) == 3
    assert events[-1] == key
    assert events[-2].endswith("/index.html")
    client.put_object.assert_called_once()
    pointer = client.put_object.call_args.kwargs
    assert pointer["Bucket"] == "public"
    assert pointer["Key"] == key
    assert pointer["CacheControl"] == "no-cache"
    assert pointer["ContentType"] == "text/html"
    assert f'"/{events[-2]}"' in pointer["Body"].decode()

    # Reusing a complete immutable tree still refreshes just the current pointer.
    storage.stat.return_value = object()
    storage.put_file.reset_mock()
    assert await model_publication.activate_viewer(storage, tmp_path) == key
    storage.put_file.assert_not_called()
    assert client.put_object.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "stat", "asset", "index"])
async def test_publication_errors_leave_previous_pointer_untouched(tmp_path, failure):
    if failure != "missing":
        (tmp_path / "index.html").write_text("<html></html>")
        (tmp_path / "viewer.js").write_text("export {};")

    async def upload(path, key, mime):
        if (path.name == "index.html") == (failure == "index"):
            raise RuntimeError("upload failed")

    client = SimpleNamespace(put_object=Mock())
    storage = SimpleNamespace(
        client=client,
        bucket="public",
        stat=AsyncMock(side_effect=RuntimeError("stat failed") if failure == "stat" else None),
        put_file=AsyncMock(side_effect=upload),
    )
    storage.stat.return_value = None
    with pytest.raises((ValueError, RuntimeError)):
        await model_publication.activate_viewer(storage, tmp_path)
    client.put_object.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "https://evil.example/index.html",
        "//evil.example/index.html",
        "model-viewer/../index.html",
        'model-viewer/"</script>/index.html',
        "model-viewer/" + "a" * 64 + "/index.html?redirect=evil",
    ],
)
async def test_activation_rejects_non_hash_paths_before_writing_pointer(monkeypatch, key):
    monkeypatch.setattr(model_publication, "publish_viewer", AsyncMock(return_value=key))
    storage = SimpleNamespace(client=SimpleNamespace(put_object=Mock()))
    with pytest.raises(ValueError, match="Invalid immutable"):
        await model_publication.activate_viewer(storage)
    storage.client.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_pointer_upload_failure_is_propagated_without_deleting_previous_pointer(monkeypatch):
    monkeypatch.setattr(
        model_publication,
        "publish_viewer",
        AsyncMock(
            return_value="model-viewer/" + "a" * 64 + "/index.html",
        ),
    )
    client = SimpleNamespace(put_object=Mock(side_effect=RuntimeError("pointer upload failed")))
    with pytest.raises(RuntimeError, match="pointer upload failed"):
        await model_publication.activate_viewer(SimpleNamespace(client=client, bucket="public"))
    client.put_object.assert_called_once()


@pytest.mark.asyncio
async def test_redirect_preserves_query_flags_and_fragment_on_same_origin(monkeypatch):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the redirect JavaScript")
    index = "model-viewer/" + "a" * 64 + "/index.html"
    monkeypatch.setattr(model_publication, "publish_viewer", AsyncMock(return_value=index))
    client = SimpleNamespace(put_object=Mock())
    await model_publication.activate_viewer(SimpleNamespace(client=client, bucket="public"))
    script = re.search(
        r"<script>(.*?)</script>", client.put_object.call_args.kwargs["Body"].decode()
    )[1]
    query = "?model=%2Fobjects%2Fhero.glb&physics=0&animations=1&debug=true"
    fragment = "#camera=front"
    result = subprocess.run(
        [
            node,
            "-e",
            f"const location = {{search: {json.dumps(query)}, hash: {json.dumps(fragment)}, "
            "replace: value => console.log(new URL(value, 'https://cdn.example').href)};" + script,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "https://cdn.example/" + index + query + fragment


def test_cli_uses_server_storage_settings_and_prints_only_key(monkeypatch, capsys):
    from wuwa_story.config import settings
    from wuwa_story.storage import s3

    config, storage = object(), object()
    monkeypatch.setattr("sys.argv", ["model_publication", "--activate"])
    monkeypatch.setattr(settings, "get_settings", Mock(return_value=config))
    monkeypatch.setattr(s3, "S3Storage", Mock(return_value=storage))
    activate = AsyncMock(return_value="model-viewer/current/index.html")
    monkeypatch.setattr(model_publication, "activate_viewer", activate)
    model_publication.main()
    s3.S3Storage.assert_called_once_with(config)
    activate.assert_awaited_once_with(storage)
    assert capsys.readouterr().out == "model-viewer/current/index.html\n"

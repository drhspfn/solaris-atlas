import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from wuwa_story_worker import notifications


def failed_run(status="failed"):
    return SimpleNamespace(id=81, status=status, error="Voice media not extracted: zh/770729442",
                           metadata_json={"payload": {"request": {"game_version": "1.0.0"}}},
                           raw_output={"media_report": {"found": 12, "missing": 2, "entries": [
                               {"expected": "/Game/Aki/WwiseAudio/Events/test.test", "language": "zh", "status": "not_resolved"}]}})


def test_alert_contains_task_version_and_missing_file_but_no_credentials(monkeypatch):
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "credential-value")
    run = failed_run("partial")
    run.error = "Failed https://example.test/private?token=abc credential-value [SQL: private params]"
    text = notifications.alert_text(run, "entity_media")
    assert "Task #81" in text and "1.0.0" in text and "Missing [zh]" in text
    assert "credential-value" not in text and "private params" not in text
    assert "example.test" not in text
    assert "https://solarisatlas.fun/admin/data-operations" in text
    assert notifications.alert_text(failed_run("completed"), "entity_media") is None


def test_telegram_delivery_uses_the_configured_forum_topic(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value = io.BytesIO(b'{"ok":true}')
    request = MagicMock(return_value=response)
    monkeypatch.setattr(notifications, "urlopen", request)
    notifications.send_alert("fake-token", "-1004484145903", "2", "Import failed")
    args, kwargs = request.call_args
    body = json.loads(args[0].data)
    assert body["chat_id"] == "-1004484145903"
    assert body["message_thread_id"] == 2
    assert kwargs["timeout"] == 5


@pytest.mark.asyncio
async def test_disabled_notifications_do_not_access_database(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    session = MagicMock()
    monkeypatch.setattr(notifications, "SessionFactory", session)
    await notifications.notify_job_result(81, "entity_media")
    session.assert_not_called()


@pytest.mark.asyncio
async def test_failed_delivery_is_isolated_and_does_not_log_token(monkeypatch, caplog):
    token = "123:secret-fake-bot-token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1004484145903")
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.get.return_value = failed_run()
    monkeypatch.setattr(notifications, "SessionFactory", lambda: session)
    def fail(*args):
        raise OSError(f"https://api.telegram.org/bot{token}/sendMessage")
    monkeypatch.setattr(notifications, "send_alert", fail)
    await notifications.notify_job_result(81, "entity_media")
    assert "Telegram alert failed" in caplog.text
    assert token not in caplog.text

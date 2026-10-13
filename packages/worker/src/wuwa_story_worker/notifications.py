"""Optional Telegram forum alerts for failed imports and unresolved media."""

import asyncio
import json
import logging
import os
import re
from urllib.request import Request, urlopen

from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.session import SessionFactory

logger = logging.getLogger(__name__)
IMPORT_QUEUES = {"release_media", "entity_media", "event_media", "snapshot_build", "asset_download", "asset_extract"}


def alert_text(run, processor, error=None):
    output = run.raw_output or {}
    report = output.get("media_report") or {}
    status = "failed" if error else run.status
    if status not in {"failed", "blocked", "enqueue_failed", "partial"}:
        return None
    request = (run.metadata_json or {}).get("request") or (run.metadata_json or {}).get("payload", {}).get("request", {})
    # Entity jobs report their own missing files; do not repeat the same report
    # in the parent release job that only waited for those children.
    if processor == "release_media" and status == "partial" and request.get("kind") in {"images", "character_voices"}:
        return None
    lines = [f"Solaris Atlas · {processor}", f"Task #{run.id} · {status}"]
    if request.get("game_version"):
        lines.append(f"Game version: {request['game_version']}")
    if report.get("found") is not None:
        lines.append(f"Files found: {report['found']} · missing: {report.get('missing', 0)}")
    detail = str(error or run.error or "").split("[SQL:")[0].split("[parameters:")[0].strip()
    if detail:
        lines.append("Error: " + detail[:1400])
    missing = [entry for entry in report.get("entries", []) if entry.get("status") != "found"]
    for entry in missing[:8]:
        lines.append(f"Missing [{entry.get('language') or 'asset'}]: {entry.get('expected', '')[:180]}")
    if len(missing) > 8:
        lines.append(f"… and {len(missing) - 8} more; full report in the admin panel.")
    text = "\n".join(lines)
    # Errors can contain connection URLs or credential values. Never send them.
    text = re.sub(r"\b(?:https?|amqps?|postgresql[^:]*)://\S+", "[URL redacted]", text)
    for key, value in os.environ.items():
        if len(value) >= 6 and any(word in key for word in ("TOKEN", "PASSWORD", "SECRET", "API_KEY")):
            text = text.replace(value, "[secret redacted]")
    return text[:3500] + "\nAdmin: https://solarisatlas.fun/admin/data-operations"


def send_alert(token, chat, thread, text):
    body = {"chat_id": chat, "text": text, "link_preview_options": {"is_disabled": True}}
    if thread:
        body["message_thread_id"] = int(thread)
    request = Request(f"https://api.telegram.org/bot{token}/sendMessage",
                      data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    # urllib does not log the URL containing the bot token, unlike HTTP client
    # request logging. Telegram failure must not affect the import outcome.
    with urlopen(request, timeout=5) as response:
        if not json.load(response).get("ok"):
            raise RuntimeError("Telegram rejected the alert")


async def notify_job_result(run_id, processor, error=None):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat or type(run_id) is not int or processor not in IMPORT_QUEUES:
        return
    try:
        async with SessionFactory() as session:
            run = await session.get(ProcessingRun, run_id)
            if run is None or run.status == "cancelled":
                return
            text = alert_text(run, processor, error)
        if text:
            await asyncio.to_thread(send_alert, token, chat, os.getenv("TELEGRAM_ERROR_THREAD_ID"), text)
    except Exception as failure:
        # Exception URLs can contain the token. Log only safe type/status fields.
        logger.warning("Telegram alert failed run=%s type=%s status=%s", run_id,
                       type(failure).__name__, getattr(failure, "code", None))

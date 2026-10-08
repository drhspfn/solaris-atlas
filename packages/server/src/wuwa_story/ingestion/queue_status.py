"""Read-only RabbitMQ operations, without exposing credentials or message bodies."""

import asyncio
from urllib.parse import quote, unquote, urlsplit

import httpx

from wuwa_story.config.settings import get_settings


async def queue_status() -> dict:
    url = urlsplit(get_settings().rabbitmq_url.get_secret_value())
    host = url.hostname or "localhost"
    host = f"[{host}]" if ":" in host else host
    secure = url.scheme == "amqps"
    base = f"{'https' if secure else 'http'}://{host}:{15671 if secure else 15672}"
    vhost = unquote(url.path[1:]) or "/"
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            auth = (unquote(url.username or "guest"), unquote(url.password or "guest"))
            responses = await asyncio.gather(*(client.get(f"{base}/api/{resource}/{quote(vhost, safe='')}", auth=auth)
                                              for resource in ("queues", "consumers")))
            for response in responses:
                response.raise_for_status()
            rows, consumers = (response.json() for response in responses)
        return {"available": True, "queues": [
            {"name": row["name"], "ready": row.get("messages_ready", 0),
             "active": row.get("messages_unacknowledged", 0),
             "consumers": row.get("consumers", 0), "state": row.get("state", "unknown"),
             "workers": [{"name": consumer.get("channel_details", {}).get("connection_name", "Worker"),
                          "prefetch": consumer.get("prefetch_count", 0)}
                         for consumer in consumers if consumer.get("queue", {}).get("name") == row["name"]]}
            for row in rows if row.get("name", "").startswith("wuwa.")], "error": None}
    except (httpx.HTTPError, ValueError, TypeError):
        return {"available": False, "queues": [],
                "error": "RabbitMQ monitoring is unavailable; queue counts are unknown. Check the management listener and broker user permissions."}

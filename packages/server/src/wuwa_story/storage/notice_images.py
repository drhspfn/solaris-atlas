"""Public URL repair for archived official announcement images."""


def public_notice_image(url: str | None) -> str | None:
    """Repair archived URLs written by workers without a public CDN setting."""
    from wuwa_story.config.settings import get_settings

    settings = get_settings()
    prefix = f"{settings.s3_endpoint_url.rstrip('/')}/{settings.s3_bucket}"
    if url and settings.media_public_base_url and url.startswith(prefix + "/objects/"):
        return settings.media_public_base_url.rstrip("/") + url[len(prefix) :]
    return url

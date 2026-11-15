"""Discover and validate character/NPC reference images via DuckDuckGo image search."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from PIL import Image, UnidentifiedImageError

from wuwa_story.visual_memory.images import MAX_IMAGE_BYTES

logger = logging.getLogger(__name__)

EntityKind = Literal["playable", "npc", "custom"]

DEFAULT_SUFFIXES: dict[EntityKind, tuple[str, ...]] = {
    "playable": ("render", "ingame", "model", "screenshot", "official art"),
    "npc": ("npc", "screenshot", "model", "ingame"),
    "custom": ("npc", "screenshot", "model", "ingame", "render"),
}

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass(slots=True)
class EnrichmentTarget:
    """Entity selected for web visual-reference enrichment."""

    entity_id: int | None
    name: str
    kind: EntityKind
    role_type: str | None = None
    existing_image_count: int = 0


@dataclass(slots=True)
class WebImageCandidate:
    """Validated image downloaded from a web search result."""

    url: str
    page_url: str | None
    title: str | None
    query: str
    suffix: str
    width: int
    height: int
    sha256: str
    content: bytes

    def metadata(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "page_url": self.page_url,
            "title": self.title,
            "query": self.query,
            "suffix": self.suffix,
            "width": self.width,
            "height": self.height,
            "sha256": self.sha256,
            "size_bytes": len(self.content),
        }


def slugify_name(name: str) -> str:
    """Convert an entity name into a safe filesystem directory slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "entity"


_BLOCKED_DOMAINS = (
    "pixai.art",
    "civitai.com",
    "tensor.art",
    "tensorartassets.com",
    "seaart.ai",
    "prompthero.com",
    "lookaside.fbsbx.com",
)

_BLOCKED_URL_SUBSTRINGS = (
    "model-share-card",
    "/favicon",
    "sprite-icon",
)


def build_search_queries(
    name: str,
    kind: EntityKind,
    suffixes: list[str] | tuple[str, ...] | None = None,
) -> list[tuple[str, str]]:
    """Build `(suffix, query)` pairs for DuckDuckGo image search."""
    chosen = tuple(suffixes) if suffixes else DEFAULT_SUFFIXES[kind]
    clean_name = name.strip().replace('"', "")
    return [
        (suffix, f'"wuthering waves" "{clean_name}" {suffix}'.strip())
        for suffix in chosen
        if suffix.strip()
    ]


def _referer_for_url(url: str) -> str | None:
    lower = url.lower()
    if "wikia.nocookie.net" in lower or "fandom.com" in lower:
        return "https://wutheringwaves.fandom.com/"
    if "zerochan.net" in lower:
        return "https://www.zerochan.net/"
    if "pixiv.net" in lower or "pximg.net" in lower:
        return "https://www.pixiv.net/"
    return None


def _is_relevant_candidate(
    target_name: str,
    url: str,
    title: str | None,
    page_url: str | None = None,
    *,
    allow_ai_sites: bool = False,
) -> bool:
    """Require target name tokens in metadata and filter AI-generation / wrong-character hits."""
    lower_url = url.lower()
    lower_page = (page_url or "").lower()
    lower_title = (title or "").lower()

    if not allow_ai_sites and any(
        dom in lower_url or dom in lower_page for dom in _BLOCKED_DOMAINS
    ):
        return False
    if any(sub in lower_url for sub in _BLOCKED_URL_SUBSTRINGS):
        return False

    tokens = [t for t in re.split(r"[^a-z0-9]+", target_name.lower()) if len(t) >= 3]
    if not tokens:
        return True

    # Reject explicit other-character folders like /characters/encore/...
    match = re.search(r"/characters/([^/]+)/", lower_url)
    if match:
        folder = match.group(1)
        if not any(tok in folder for tok in tokens):
            return False

    # Require every meaningful name token to appear as a word/segment in title, image URL, or page URL.
    haystack = f"{lower_title} {lower_url} {lower_page}"
    return all(re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", haystack) for tok in tokens)


def _search_ddg_sync(query: str, max_results: int) -> list[dict[str, Any]]:
    from ddgs import DDGS

    with DDGS() as ddgs:
        return list(ddgs.images(query, max_results=max_results))


async def search_ddg_images(query: str, *, max_results: int = 5, retries: int = 2) -> list[dict[str, Any]]:
    """Run DuckDuckGo image search in a worker thread with brief retry on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await asyncio.to_thread(_search_ddg_sync, query, max_results)
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(0.9 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def validate_image_bytes(data: bytes, *, min_width: int = 200, min_height: int = 200) -> tuple[int, int, str] | None:
    """Verify image readability, dimensions, and return `(width, height, sha256)`."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    if width < min_width or height < min_height:
        return None
    return width, height, hashlib.sha256(data).hexdigest()


async def fetch_and_validate_image(
    client: httpx.AsyncClient,
    url: str,
    *,
    min_width: int = 200,
    min_height: int = 200,
) -> tuple[int, int, str, bytes] | None:
    """Download an image URL and validate its content type, size, and dimensions."""
    headers: dict[str, str] = {}
    referer = _referer_for_url(url)
    if referer:
        headers["Referer"] = referer
    try:
        resp = await client.get(url, headers=headers)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    ctype = (resp.headers.get("content-type") or "").lower()
    if ctype and not ctype.startswith("image/"):
        return None
    validated = validate_image_bytes(resp.content, min_width=min_width, min_height=min_height)
    if validated is None:
        return None
    width, height, digest = validated
    return width, height, digest, resp.content


async def collect_web_images_for_target(
    target: EnrichmentTarget,
    *,
    suffixes: list[str] | tuple[str, ...] | None = None,
    per_query: int = 5,
    max_images: int = 12,
    min_size: int = 200,
    delay_seconds: float = 0.4,
    search_fn: Any = search_ddg_images,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Search DuckDuckGo and download validated candidate images for one target."""
    queries = build_search_queries(target.name, target.kind, suffixes)
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    candidates: list[WebImageCandidate] = []
    skipped = 0
    errors: list[str] = []

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(
            timeout=12.0,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_USER_AGENT},
        )
    try:
        for idx, (suffix, query) in enumerate(queries):
            if len(candidates) >= max_images:
                break
            if idx > 0 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            try:
                rows = await search_fn(query, max_results=per_query)
            except Exception as exc:
                errors.append(f"{query}: {type(exc).__name__}")
                continue
            for row in rows:
                if len(candidates) >= max_images:
                    break
                url = (row.get("image") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                title = row.get("title")
                page_url = row.get("url")
                if not _is_relevant_candidate(target.name, url, title, page_url):
                    skipped += 1
                    continue
                fetched = await fetch_and_validate_image(
                    client,
                    url,
                    min_width=min_size,
                    min_height=min_size,
                )
                if fetched is None:
                    skipped += 1
                    continue
                width, height, digest, content = fetched
                if digest in seen_hashes:
                    skipped += 1
                    continue
                seen_hashes.add(digest)
                candidates.append(
                    WebImageCandidate(
                        url=url,
                        page_url=row.get("url"),
                        title=title,
                        query=query,
                        suffix=suffix,
                        width=width,
                        height=height,
                        sha256=digest,
                        content=content,
                    )
                )
    finally:
        if owns_client:
            await client.aclose()

    return {
        "target": target,
        "candidates": candidates,
        "skipped": skipped,
        "errors": errors,
    }


def export_candidates_to_dir(
    export_dir: Path,
    target: EnrichmentTarget,
    candidates: list[WebImageCandidate],
) -> Path:
    """Save downloaded candidate images and manifest.json into `<export_dir>/<kind>/<slug>/`."""
    target_dir = export_dir / target.kind / slugify_name(target.name)
    target_dir.mkdir(parents=True, exist_ok=True)
    saved_items = []
    for idx, cand in enumerate(candidates, start=1):
        ext = _guess_extension(cand.content)
        filename = f"{idx:02d}_{slugify_name(cand.suffix)}_{cand.sha256[:10]}.{ext}"
        file_path = target_dir / filename
        file_path.write_bytes(cand.content)
        saved_items.append({"file": filename, **cand.metadata()})

    manifest = {
        "target": asdict(target),
        "count": len(saved_items),
        "images": saved_items,
    }
    manifest_path = target_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return target_dir


def _guess_extension(data: bytes) -> str:
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "jpg").lower()
            return "jpg" if fmt == "jpeg" else fmt
    except Exception:
        return "jpg"


async def load_enrichment_targets(
    session,
    *,
    kind: EntityKind = "playable",
    names: list[str] | None = None,
    limit: int | None = None,
    role_types: list[str] | None = None,
) -> list[EnrichmentTarget]:
    """Load playable characters and/or NPCs from `core.character_read`."""
    from sqlalchemy import func, select

    from wuwa_story.db.models.browse import CharacterRead
    from wuwa_story.db.models.i18n import Locale
    from wuwa_story.db.models.storage import FileObject, FileReference

    locale_id = await session.scalar(select(Locale.id).where(Locale.code == "en"))
    if locale_id is None:
        return []

    role_expr = CharacterRead.card["role_type"].astext
    stmt = (
        select(
            CharacterRead.entity_id,
            CharacterRead.title,
            role_expr.label("role_type"),
        )
        .where(
            CharacterRead.locale_id == locale_id,
            CharacterRead.title.is_not(None),
            CharacterRead.title != "",
        )
        .distinct(CharacterRead.entity_id)
        .order_by(CharacterRead.entity_id, CharacterRead.id.desc())
    )
    if role_types:
        stmt = stmt.where(role_expr.in_(role_types))
    elif kind == "playable":
        stmt = stmt.where(role_expr == "1")
    elif kind == "npc":
        stmt = stmt.where(role_expr != "1")

    if names:
        lowered = [n.strip().lower() for n in names if n.strip()]
        stmt = stmt.where(func.lower(CharacterRead.title).in_(lowered))

    rows = (await session.execute(stmt)).all()
    if kind == "npc" and not role_types:
        # Prioritize named major NPCs (role_type 2, 4, 5, 3) before generic crowd NPCs (role_type 6).
        priority = {"2": 0, "4": 1, "5": 2, "3": 3, "6": 4}
        rows = sorted(rows, key=lambda r: (priority.get(r.role_type or "6", 5), r.entity_id))

    if limit is not None and limit > 0:
        rows = rows[:limit]

    targets: list[EnrichmentTarget] = []
    for row in rows:
        img_count = await session.scalar(
            select(func.count(FileReference.id))
            .join(FileObject, FileObject.id == FileReference.file_id)
            .where(
                FileReference.owner_node_id == row.entity_id,
                FileObject.mime_type.like("image/%"),
            )
        )
        target_kind: EntityKind = "playable" if row.role_type == "1" else "npc"
        targets.append(
            EnrichmentTarget(
                entity_id=row.entity_id,
                name=row.title.strip(),
                kind=target_kind,
                role_type=row.role_type,
                existing_image_count=int(img_count or 0),
            )
        )
    return targets


async def ensure_visual_identity_for_node(session, node_id: int, name: str) -> int:
    """Return the existing `VisualIdentity.id` for `node_id`, or create one if missing."""
    from sqlalchemy import select

    from wuwa_story.db.models.visual_memory import VisualIdentity, VisualIdentityNode

    existing = await session.scalar(
        select(VisualIdentityNode.identity_id)
        .where(VisualIdentityNode.node_id == node_id)
        .order_by(VisualIdentityNode.identity_id)
        .limit(1)
    )
    if existing is not None:
        return existing

    identity = VisualIdentity(name=name[:256], kind="character", description="")
    session.add(identity)
    await session.flush()
    session.add(VisualIdentityNode(identity_id=identity.id, node_id=node_id))
    await session.flush()
    return identity.id


async def import_existing_db_images_for_target(
    session,
    storage,
    service,
    target: EnrichmentTarget,
    identity_id: int,
) -> int:
    """Import all existing stored images for `target.entity_id` into `VisualReference`."""
    from sqlalchemy import select

    from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference

    if target.entity_id is None:
        return 0
    rows = (
        await session.execute(
            select(FileReference, FileObject, FileLocation)
            .join(FileObject, FileObject.id == FileReference.file_id)
            .join(FileLocation, FileLocation.file_id == FileObject.id)
            .where(
                FileReference.owner_node_id == target.entity_id,
                FileObject.mime_type.in_(
                    ("image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp")
                ),
                FileLocation.available.is_(True),
                FileLocation.backend == storage.backend,
                FileLocation.bucket == storage.bucket,
            )
            .order_by(FileReference.id.desc(), FileLocation.is_primary.desc(), FileLocation.id)
        )
    ).all()

    imported = 0
    seen_files: set[int] = set()
    for reference, file, location in rows:
        if file.id in seen_files:
            continue
        seen_files.add(file.id)
        if file.size_bytes is not None and file.size_bytes > MAX_IMAGE_BYTES:
            continue
        data = bytearray()
        async for chunk in storage.get(location.object_key):
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                break
        if len(data) > MAX_IMAGE_BYTES or not data:
            continue
        res = await service.add_reference(
            bytes(data),
            links=[{"identity_id": identity_id, "appearance_id": None}],
            source_type="game_asset",
            status="pending",
            source_group=f"db:{target.kind}:{target.entity_id}",
            rights=f"Game asset; node_id={target.entity_id}; file_reference_id={reference.id}; file_id={file.id}",
        )
        if not res.get("duplicate"):
            imported += 1
    return imported


async def persist_candidates_to_visual_memory(
    session,
    service,
    target: EnrichmentTarget,
    candidates: list[WebImageCandidate],
    *,
    storage=None,
    include_db_images: bool = False,
) -> dict[str, int]:
    """Ensure a `VisualIdentity` exists and save web (and optional DB) images as pending references."""
    if target.entity_id is None:
        raise ValueError("Cannot persist offline target without entity_id to database")

    identity_id = await ensure_visual_identity_for_node(session, target.entity_id, target.name)
    db_imported = 0
    if include_db_images and storage is not None:
        db_imported = await import_existing_db_images_for_target(
            session, storage, service, target, identity_id
        )

    added = 0
    duplicates = 0
    for cand in candidates:
        res = await service.add_reference(
            cand.content,
            links=[{"identity_id": identity_id, "appearance_id": None}],
            source_type="web_search",
            source_url=cand.url,
            source_page=cand.page_url,
            source_group=f"ddg:{target.kind}:{target.entity_id}",
            status="pending",
            rights=f"Web search candidate; query={cand.query}; title={(cand.title or '')[:200]}",
        )
        if res.get("duplicate"):
            duplicates += 1
        else:
            added += 1

    return {
        "identity_id": identity_id,
        "db_imported": db_imported,
        "web_added": added,
        "web_duplicates": duplicates,
    }

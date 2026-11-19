"""Typed async client and entity resolver for Wuthering Waves Fandom."""

from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.fandom.models import (
    FandomEntityBundle,
    FandomEntityKind,
    FandomImageAsset,
    FandomLicenseInfo,
    FandomParsedPage,
    FandomSearchResult,
    LicenseFilter,
)
from wuwa_story.fandom.parser import (
    extract_file_templates,
    extract_gallery_entries,
    extract_infobox,
    extract_relations,
    extract_summary,
    is_decorative_wiki_asset,
    strip_wikitext,
)

DEFAULT_API_URL = "https://wutheringwaves.fandom.com/api.php"
DEFAULT_WIKI_BASE = "https://wutheringwaves.fandom.com/wiki/"


def wiki_url(title: str) -> str:
    return DEFAULT_WIKI_BASE + quote(title.replace(" ", "_"), safe="/:()-'")


class FandomClient:
    """Async MediaWiki API client for `wutheringwaves.fandom.com`."""

    def __init__(
        self,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 20.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_url = api_url
        self.timeout = timeout
        self._external_client = http_client
        self._rights_cache: FandomLicenseInfo | None = None

    async def _request(self, params: dict[str, str | int]) -> dict:
        query = {"format": "json", "formatversion": 2, **params}
        headers = {
            "User-Agent": "SolarisAtlasBot/1.0 (+https://solarisatlas.fun)"}
        if self._external_client is not None:
            response = await self._external_client.get(self.api_url, params=query, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(self.api_url, params=query, headers=headers)
            response.raise_for_status()
            return response.json()

    async def get_site_rights(self) -> FandomLicenseInfo:
        if self._rights_cache is not None:
            return self._rights_cache
        data = await self._request({"action": "query", "meta": "siteinfo", "siprop": "rightsinfo"})
        rights = (data.get("query") or {}).get("rightsinfo") or {}
        text = rights.get("text")
        info = FandomLicenseInfo(
            text=text,
            url=rights.get("url"),
            is_cc_by_sa=bool(text and "CC-BY-SA" in text.upper()),
        )
        self._rights_cache = info
        return info

    async def search_pages(self, query: str, *, limit: int = 10) -> list[FandomSearchResult]:
        data = await self._request(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": max(1, min(limit, 50)),
                "srnamespace": 0,
            }
        )
        results: list[FandomSearchResult] = []
        for item in (data.get("query") or {}).get("search") or []:
            title = item.get("title")
            if not title:
                continue
            results.append(
                FandomSearchResult(
                    page_id=int(item["pageid"]),
                    title=title,
                    snippet=strip_wikitext(item.get("snippet") or ""),
                    url=wiki_url(title),
                )
            )
        return results

    async def parse_page(self, title: str) -> FandomParsedPage | None:
        data = await self._request(
            {
                "action": "parse",
                "page": title,
                "redirects": 1,
                "prop": "wikitext|images|categories|links|displaytitle",
            }
        )
        if "error" in data:
            return None
        parsed = data.get("parse") or {}
        resolved_title = parsed.get("title") or title
        categories = [
            c["category"] if isinstance(c, dict) else str(c)
            for c in parsed.get("categories") or []
        ]
        links = [
            lnk["title"] if isinstance(lnk, dict) else str(lnk)
            for lnk in parsed.get("links") or []
        ]
        return FandomParsedPage(
            title=resolved_title,
            display_title=strip_wikitext(
                parsed.get("displaytitle") or "") or None,
            url=wiki_url(resolved_title),
            wikitext=parsed.get("wikitext") or "",
            images=list(parsed.get("images") or []),
            categories=categories,
            links=links,
        )

    async def get_image_assets(
        self,
        filenames: list[str],
        *,
        source_page: str,
        captions: dict[str, str | None] | None = None,
        license_filter: LicenseFilter = LicenseFilter.IGNORE,
        check_file_wikitext: bool = False,
    ) -> list[FandomImageAsset]:
        filtered_names = [
            name.removeprefix("File:")
            for name in dict.fromkeys(filenames)
            if not is_decorative_wiki_asset(name)
        ]
        if not filtered_names:
            return []
        site_rights = await self.get_site_rights()
        assets: list[FandomImageAsset] = []
        captions = captions or {}
        for offset in range(0, len(filtered_names), 50):
            chunk = filtered_names[offset:offset + 50]
            titles_param = "|".join(f"File:{name}" for name in chunk)
            props = "imageinfo|revisions" if check_file_wikitext else "imageinfo"
            params: dict[str, str | int] = {
                "action": "query",
                "titles": titles_param,
                "prop": props,
                "iiprop": "url|size|mime|extmetadata",
            }
            if check_file_wikitext:
                params["rvprop"] = "content"
                params["rvslots"] = "main"
            data = await self._request(params)
            for page in (data.get("query") or {}).get("pages") or []:
                if page.get("missing"):
                    continue
                infos = page.get("imageinfo") or []
                if not infos:
                    continue
                info = infos[0]
                url = info.get("url")
                if not url:
                    continue
                templates: list[str] = []
                non_free = False
                if check_file_wikitext:
                    revs = page.get("revisions") or []
                    if revs:
                        content = ((revs[0].get("slots") or {}).get(
                            "main") or {}).get("content") or ""
                        templates, non_free = extract_file_templates(content)
                ext = info.get("extmetadata") or {}
                lic_short = (ext.get("LicenseShortName") or {}).get("value")
                file_license = FandomLicenseInfo(
                    text=lic_short or site_rights.text,
                    url=site_rights.url,
                    is_cc_by_sa=site_rights.is_cc_by_sa and not non_free,
                    file_templates=templates,
                    non_free_detected=non_free,
                )
                if license_filter == LicenseFilter.CC_BY_SA_ONLY and not file_license.is_cc_by_sa:
                    continue
                file_title = page.get("title") or ""
                short_name = file_title.removeprefix("File:")
                assets.append(
                    FandomImageAsset(
                        title=file_title,
                        filename=short_name,
                        url=url,
                        description_url=info.get(
                            "descriptionurl") or wiki_url(file_title),
                        width=info.get("width"),
                        height=info.get("height"),
                        size=info.get("size"),
                        mime=info.get("mime"),
                        source_page=source_page,
                        caption=captions.get(short_name) or captions.get(
                            short_name.replace("_", " ")),
                        license=file_license,
                    )
                )
        return assets

    async def fetch_entity_bundle(
        self,
        entity_kind: FandomEntityKind,
        name: str,
        *,
        include_gallery: bool = True,
        license_filter: LicenseFilter = LicenseFilter.IGNORE,
        check_file_wikitext: bool = False,
    ) -> FandomEntityBundle | None:
        site_rights = await self.get_site_rights()
        if license_filter == LicenseFilter.CC_BY_SA_ONLY and not site_rights.is_cc_by_sa:
            return None
        page = await self.parse_page(name)
        if page is None:
            hits = await self.search_pages(name, limit=3)
            if not hits:
                return None
            page = await self.parse_page(hits[0].title)
            if page is None:
                return None

        infobox = extract_infobox(page.wikitext)
        relations = extract_relations(infobox)
        gallery_entries = extract_gallery_entries(page.wikitext)
        captions = {fname: cap for fname, cap in gallery_entries}
        main_images = await self.get_image_assets(
            page.images,
            source_page=page.title,
            captions=captions,
            license_filter=license_filter,
            check_file_wikitext=check_file_wikitext,
        )

        gallery_url: str | None = None
        all_images = list(main_images)
        if include_gallery:
            gallery_page = await self.parse_page(f"{page.title}/Gallery")
            if gallery_page is not None:
                gallery_url = gallery_page.url
                gal_captions = {fname: cap for fname, cap in extract_gallery_entries(
                    gallery_page.wikitext)}
                gal_images = await self.get_image_assets(
                    gallery_page.images,
                    source_page=gallery_page.title,
                    captions=gal_captions,
                    license_filter=license_filter,
                    check_file_wikitext=check_file_wikitext,
                )
                seen_urls = {img.url for img in all_images}
                for img in gal_images:
                    if img.url not in seen_urls:
                        all_images.append(img)
                        seen_urls.add(img.url)

        return FandomEntityBundle(
            entity_kind=entity_kind,
            query_name=name,
            page_title=page.title,
            page_url=page.url,
            gallery_url=gallery_url,
            license=site_rights,
            infobox=infobox,
            summary=extract_summary(page.wikitext),
            categories=page.categories,
            relations=relations,
            images=all_images,
        )

    async def resolve_for_db_entity(
        self,
        session: AsyncSession,
        node_or_entity_id: int,
        *,
        entity_kind: FandomEntityKind | None = None,
        locale: str = "en",
        include_gallery: bool = True,
        license_filter: LicenseFilter = LicenseFilter.IGNORE,
    ) -> FandomEntityBundle | None:
        """Resolve a database node (character/npc/speaker/quest/item/location) or event to Fandom."""
        from wuwa_story.db.models.core import Character
        from wuwa_story.db.models.game_events import GameEvent
        from wuwa_story.db.models.graph import Node, NodeType
        from wuwa_story.story.service import _node_label

        if entity_kind == FandomEntityKind.EVENT:
            event = await session.get(GameEvent, node_or_entity_id)
            if event is None or not event.title:
                return None
            return await self.fetch_entity_bundle(
                FandomEntityKind.EVENT,
                event.title,
                include_gallery=include_gallery,
                license_filter=license_filter,
            )

        row = (
            await session.execute(
                select(Node, NodeType.key)
                .join(NodeType, NodeType.id == Node.type_id)
                .where(Node.id == node_or_entity_id)
            )
        ).first()
        if row is None:
            return None
        node, node_type_key = row
        inferred_kind = entity_kind
        if inferred_kind is None:
            if node_type_key == "character":
                inferred_kind = (
                    FandomEntityKind.NPC
                    if node.canonical_key.startswith("character:npc:")
                    else FandomEntityKind.CHARACTER
                )
            elif node_type_key in {"speaker", "quest", "item", "location"}:
                inferred_kind = FandomEntityKind(node_type_key)
            elif node_type_key == "area":
                inferred_kind = FandomEntityKind.LOCATION
            else:
                return None

        label = await _node_label(session, node.id, locale)
        name = label.get("label")
        if not name and node_type_key == "character":
            char = await session.get(Character, node.id)
            name = char.canonical_name if char else None
        if not name:
            return None
        return await self.fetch_entity_bundle(
            inferred_kind,
            name,
            include_gallery=include_gallery,
            license_filter=license_filter,
        )

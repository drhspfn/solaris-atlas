from html import escape
from urllib.parse import unquote, urljoin

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.session import get_session
from wuwa_story.db.repositories.nodes import get_node, get_quest
from wuwa_story.api.routes.story.shared import _node_label

router = APIRouter(tags=["seo"])

@router.get("/robots.txt")
async def robots_txt(request: Request) -> Response:
    settings = get_settings()
    sitemap_url = urljoin(settings.frontend_url, "/sitemap.xml")
    content = f"User-agent: *\nAllow: /\n\nSitemap: {sitemap_url}\n"
    return Response(content=content, media_type="text/plain")

@router.get("/sitemap.xml")
async def sitemap_xml(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    settings = get_settings()
    base_url = settings.frontend_url
    
    nodes = await session.execute(
        select(Node.slug, NodeType.key)
        .join(NodeType, Node.type_id == NodeType.id)
        .where(
            NodeType.key.in_(["character", "quest", "item", "area", "location"]),
            Node.slug.is_not(None),
            Node.status == "active"
        )
        .limit(50000)
    )
    
    urls = []
    urls.append(f"<url><loc>{base_url}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>")
    urls.append(f"<url><loc>{base_url}/map</loc><changefreq>weekly</changefreq><priority>0.9</priority></url>")
    urls.append(f"<url><loc>{base_url}/characters</loc><changefreq>weekly</changefreq><priority>0.9</priority></url>")
    urls.append(f"<url><loc>{base_url}/quests</loc><changefreq>daily</changefreq><priority>0.9</priority></url>")
    
    for row in nodes:
        slug, ntype = row.slug, row.key
        if ntype == "character":
            loc = f"{base_url}/characters/{slug}"
        elif ntype == "quest":
            loc = f"{base_url}/quests/{slug}"
        elif ntype == "item":
            loc = f"{base_url}/items/{slug}"
        else:
            loc = f"{base_url}/locations/{slug}"
        
        urls.append(f"<url><loc>{loc}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>")
        
    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_content += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml_content += "\n".join(urls) + "\n</urlset>"
    
    return Response(content=xml_content, media_type="application/xml")

@router.get("/api/seo/prerender")
async def seo_prerender(path: str, request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    settings = get_settings()
    base_url = settings.frontend_url
    
    title = settings.app_name or "Solaris Atlas"
    description = "Wuthering Waves story platform foundation"
    image = urljoin(base_url, "/preview.png")
    
    parts = path.strip("/").split("/")
    
    groups = {"characters": ("character",), "items": ("item",),
              "locations": ("area", "location"), "quests": ("quest",)}
    if len(parts) >= 2 and parts[0] in groups:
        key = unquote(parts[1])
        if parts[0] == "quests":
            quest = await get_quest(session, key)
            node = await session.get(Node, quest.node_id) if quest else None
        else:
            node = await get_node(session, key, groups[parts[0]])
        if node:
            name = (await _node_label(session, node.id, "en"))["label"]
            title = f"{name} | Solaris Atlas"
            description = f"Details and story connections for {name}."
    title, description, image = escape(title), escape(description), escape(image, quote=True)
    public_url = escape(urljoin(base_url, path), quote=True)

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <title>{title}</title>
    <meta name="description" content="{description}" />
    <meta property="og:title" content="{title}" />
    <meta property="og:description" content="{description}" />
    <meta property="og:image" content="{image}" />
    <meta property="og:url" content="{public_url}" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="{title}" />
    <meta name="twitter:description" content="{description}" />
    <meta name="twitter:image" content="{image}" />
</head>
<body>
    <h1>{title}</h1>
    <p>{description}</p>
</body>
</html>'''
    return HTMLResponse(content=html)

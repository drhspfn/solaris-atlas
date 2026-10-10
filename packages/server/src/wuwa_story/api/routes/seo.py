from urllib.parse import urljoin
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.session import get_session
from wuwa_story.config.settings import get_settings

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
            NodeType.key.in_(["character", "quest"]),
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
        else:
            loc = f"{base_url}/story/{slug}"
        
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
    
    if len(parts) >= 2 and parts[0] == "characters":
        slug = parts[1]
        node = await session.scalar(
            select(Node).join(NodeType).where(NodeType.key == "character", Node.slug == slug)
        )
        if node:
            title = f"{slug.replace('-', ' ').title()} - Characters | Solaris Atlas"
            description = f"Lore, stats, and voice lines for {slug.title()}."
            
    elif len(parts) >= 2 and parts[0] == "quests":
        slug = parts[1]
        node = await session.scalar(
            select(Node).join(NodeType).where(NodeType.key == "quest", Node.slug == slug)
        )
        if node:
            title = f"Quest: {slug.replace('-', ' ').title()} | Solaris Atlas"
            description = f"Story transcript and details for {slug.title()}."

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <title>{title}</title>
    <meta name="description" content="{description}" />
    <meta property="og:title" content="{title}" />
    <meta property="og:description" content="{description}" />
    <meta property="og:image" content="{image}" />
    <meta property="og:url" content="{urljoin(base_url, path)}" />
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

import httpx
import pytest

from wuwa_story.fandom import (
    FandomClient,
    FandomEntityKind,
    LicenseFilter,
    extract_gallery_entries,
    extract_infobox,
    extract_relations,
    is_decorative_wiki_asset,
)


def test_extract_infobox_and_family_relations_from_cassandra_wikitext():
    wikitext = """{{Stub|Profile}}
{{NPC Infobox
|image = NPC Cassandra.png
|nation = New Federation
|affiliation = Federated Cities
|affiliationNote = [[Resonator War]]
|title = Federated Commander
|child = Liam
|childNote = son
}}
'''Cassandra''' was [[Liam]]'s mother.<ref>memorial</ref>"""
    infobox = extract_infobox(wikitext)
    assert infobox is not None
    assert infobox.template == "NPC Infobox"
    assert infobox.fields["title"] == "Federated Commander"
    relations = extract_relations(infobox)
    assert any(r.relation_type == "parent_of" and r.target_name ==
               "Liam" and r.note == "son" for r in relations)
    assert any(r.relation_type == "affiliated_with" and r.target_name ==
               "Federated Cities" for r in relations)
    assert any(r.relation_type == "located_in" and r.target_name ==
               "New Federation" for r in relations)


def test_decorative_filter_and_gallery_extraction():
    assert is_decorative_wiki_asset("Emoji_EN_Flag.png") is True
    assert is_decorative_wiki_asset(
        "Mortefi_Sticker_Slice_of_Life_03_03.png") is True
    assert is_decorative_wiki_asset("NPC_Cassandra.png") is False
    entries = extract_gallery_entries(
        "<gallery>\nJinzhou.png|In-Game\nJinzhou Map.png|Map\n</gallery>")
    assert entries == [("Jinzhou.png", "In-Game"), ("Jinzhou Map.png", "Map")]


@pytest.mark.asyncio
async def test_fandom_client_fetches_bundle_with_gallery_and_license_filter():
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("meta") == "siteinfo":
            return httpx.Response(200, json={"query": {"rightsinfo": {"text": "CC-BY-SA", "url": "https://www.fandom.com/licensing"}}})
        if params.get("action") == "parse" and params.get("page") == "Lily":
            return httpx.Response(200, json={"parse": {
                "title": "Lily",
                "displaytitle": "Lily",
                "wikitext": "{{Resonator Infobox\n|name = Lily\n|attribute = Spectro\n}}\n'''Lily''' is a Resonator.",
                "images": ["Resonator_Lily.png", "Emoji_EN_Flag.png"],
                "categories": [{"category": "Playable_Resonators"}],
                "links": [],
            }})
        if params.get("action") == "parse" and params.get("page") == "Lily/Gallery":
            return httpx.Response(200, json={"parse": {
                "title": "Lily/Gallery",
                "wikitext": "<gallery>\nLily_Full_Sprite.png|Full Sprite\n</gallery>",
                "images": ["Lily_Full_Sprite.png"],
                "categories": [],
                "links": [],
            }})
        if params.get("action") == "query" and params.get("prop") == "imageinfo":
            titles = params.get("titles", "").split("|")
            pages = []
            for t in titles:
                fname = t.removeprefix("File:")
                pages.append({
                    "title": t,
                    "imageinfo": [{"url": f"https://static.wikia.nocookie.net/wutheringwaves/images/{fname}", "width": 800, "height": 900, "size": 10000, "mime": "image/png"}],
                })
            return httpx.Response(200, json={"query": {"pages": pages}})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = FandomClient(http_client=http)
        bundle = await client.fetch_entity_bundle(
            FandomEntityKind.CHARACTER,
            "Lily",
            include_gallery=True,
            license_filter=LicenseFilter.CC_BY_SA_ONLY,
        )
    assert bundle is not None
    assert bundle.page_url == "https://wutheringwaves.fandom.com/wiki/Lily"
    assert bundle.gallery_url == "https://wutheringwaves.fandom.com/wiki/Lily/Gallery"
    assert bundle.license.is_cc_by_sa is True
    assert [img.filename for img in bundle.images] == [
        "Resonator_Lily.png", "Lily_Full_Sprite.png"]
    assert bundle.images[1].caption == "Full Sprite"

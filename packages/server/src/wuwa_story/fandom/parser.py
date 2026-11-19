"""Wikitext parsers for Fandom infoboxes, galleries, relations, and license templates."""

import re

from wuwa_story.fandom.models import FandomInfobox, FandomRelationHint

_DECORATIVE_PREFIXES = ("Emoji_", "Icon_Emoji_", "Stub", "Element_", "Weapon_")
_DECORATIVE_CONTAINS = (
    "_Sticker_", "Sticker_Slice_of_Life", "Site-logo", "Wiki-wordmark")
_NON_FREE_TEMPLATES = {"fairuse", "fair use",
                       "copyrighted", "non-free", "official"}


def strip_wikitext(value: str) -> str:
    """Convert basic MediaWiki markup into plain readable text."""
    text = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^/]*/>", "", text)
    text = re.sub(r"<br\s*/?>", "; ", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\{\{!\}\}", "|", text)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"'{2,3}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip(" ;,")


def is_decorative_wiki_asset(filename: str) -> bool:
    """Filter out UI stickers, language flags, and wiki chrome from entity images."""
    normalized = filename.removeprefix("File:").replace(" ", "_")
    if normalized.startswith(_DECORATIVE_PREFIXES):
        return True
    return any(marker in normalized for marker in _DECORATIVE_CONTAINS)


def _extract_balanced_template(wikitext: str, start: int) -> str:
    depth = 0
    idx = start
    length = len(wikitext)
    while idx < length - 1:
        pair = wikitext[idx:idx + 2]
        if pair == "{{":
            depth += 1
            idx += 2
            continue
        if pair == "}}":
            depth -= 1
            idx += 2
            if depth <= 0:
                return wikitext[start:idx]
            continue
        idx += 1
    return wikitext[start:]


def _split_top_level_params(block: str) -> list[str]:
    inner = block[2:-
                  2] if block.startswith("{{") and block.endswith("}}") else block
    parts: list[str] = []
    current: list[str] = []
    brace_depth = 0
    bracket_depth = 0
    idx = 0
    while idx < len(inner):
        two = inner[idx:idx + 2]
        if two == "{{":
            brace_depth += 1
            current.append(two)
            idx += 2
            continue
        if two == "}}":
            brace_depth = max(0, brace_depth - 1)
            current.append(two)
            idx += 2
            continue
        if two == "[[":
            bracket_depth += 1
            current.append(two)
            idx += 2
            continue
        if two == "]]":
            bracket_depth = max(0, bracket_depth - 1)
            current.append(two)
            idx += 2
            continue
        ch = inner[idx]
        if ch == "|" and brace_depth == 0 and bracket_depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        idx += 1
    parts.append("".join(current))
    return parts


def extract_infobox(wikitext: str) -> FandomInfobox | None:
    """Extract the primary `{{... Infobox ...}}` template from MediaWiki wikitext."""
    match = re.search(r"\{\{\s*([A-Za-z0-9_ ]*Infobox)\b", wikitext)
    if not match:
        return None
    block = _extract_balanced_template(wikitext, match.start())
    parts = _split_top_level_params(block)
    if not parts:
        return None
    template_name = parts[0].strip()
    raw_fields: dict[str, str] = {}
    fields: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, raw_val = part.split("=", 1)
        key = key.strip()
        raw_val = raw_val.strip()
        if not key or not raw_val:
            continue
        raw_fields[key] = raw_val
        cleaned = strip_wikitext(raw_val)
        if cleaned:
            fields[key] = cleaned
    return FandomInfobox(template=template_name, fields=fields, raw_fields=raw_fields)


def extract_gallery_entries(wikitext: str) -> list[tuple[str, str | None]]:
    """Extract `(filename, caption)` pairs from `<gallery>` blocks in wikitext."""
    results: list[tuple[str, str | None]] = []
    for body in re.findall(r"<gallery[^>]*>(.*?)</gallery>", wikitext, flags=re.DOTALL | re.IGNORECASE):
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                name, caption = line.split("|", 1)
                results.append((name.strip().removeprefix(
                    "File:"), strip_wikitext(caption) or None))
            else:
                results.append((line.removeprefix("File:"), None))
    return results


def extract_summary(wikitext: str) -> str | None:
    """Extract the lead paragraph after infoboxes/templates."""
    cleaned = re.sub(r"<!--.*?-->", "", wikitext, flags=re.DOTALL)
    # Strip top-level templates sequentially from the lead.
    pos = 0
    while pos < len(cleaned):
        stripped = cleaned[pos:].lstrip()
        offset = len(cleaned[pos:]) - len(stripped)
        pos += offset
        if cleaned[pos:pos + 2] == "{{":
            block = _extract_balanced_template(cleaned, pos)
            pos += len(block)
            continue
        break
    lead = cleaned[pos:].split("\n==", 1)[0].strip()
    paragraphs = [strip_wikitext(p)
                  for p in lead.split("\n\n") if strip_wikitext(p)]
    return paragraphs[0] if paragraphs else None


def extract_file_templates(wikitext: str) -> tuple[list[str], bool]:
    """Return MediaWiki template names on a File page and whether a non-free marker exists."""
    templates = [m.strip() for m in re.findall(r"\{\{\s*([^|}\n]+)", wikitext)]
    non_free = any(tpl.lower() in _NON_FREE_TEMPLATES for tpl in templates)
    return templates, non_free


def extract_relations(infobox: FandomInfobox | None) -> list[FandomRelationHint]:
    """Derive typed relationship hints from infobox fields (family, affiliation, location, quests)."""
    if infobox is None:
        return []
    hints: list[FandomRelationHint] = []
    fields = infobox.fields

    def add_multi(field: str, relation_type: str, note: str | None = None) -> None:
        raw = fields.get(field)
        if not raw:
            return
        for target in re.split(r"[;\n,]+", raw):
            target = target.strip()
            if target:
                hints.append(
                    FandomRelationHint(
                        relation_type=relation_type,
                        target_name=target,
                        note=note,
                        source_field=field,
                    )
                )

    add_multi("child", "parent_of", fields.get("childNote"))
    add_multi("child2", "parent_of", fields.get("child2Note"))
    add_multi("parent", "child_of", fields.get("parentNote"))
    add_multi("mother", "child_of", "mother")
    add_multi("father", "child_of", "father")
    add_multi("sibling", "sibling_of", fields.get("siblingNote"))
    add_multi("affiliation", "affiliated_with", fields.get("affiliationNote"))
    add_multi("affiliation2", "affiliated_with",
              fields.get("affiliation2Note"))
    add_multi("affiliation3", "affiliated_with",
              fields.get("affiliation3Note"))
    add_multi("nation", "located_in")
    add_multi("region", "located_in")
    add_multi("characters", "appears_in_quest")
    return hints

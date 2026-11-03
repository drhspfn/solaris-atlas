"""Pure authored-identity matching shared by feed ingestion and event browsing."""

import re
from collections.abc import Sequence
from typing import Any, Protocol


class MatchingEvent(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def title(self) -> str: ...
    @property
    def event_kind(self) -> str: ...
    @property
    def source_data(self) -> dict[str, Any]: ...


def notice_candidates[T: MatchingEvent](
    content: dict[str, Any],
    events: Sequence[T],
    *,
    match_roster: bool = True,
) -> list[T]:
    def normalized(value: str) -> str:
        return re.sub(r"[^\w]+", "", value.casefold())

    title = re.search(r"\[([^]]+)\]", content["title"])
    name = normalized(title[1]) if title else None
    featured = re.search(
        r"5-Star (Resonator|Weapon):\s*([^,\n]+)", content["text"])
    rate_up = re.search(
        r"4-Star (?:Resonators|Weapons):\s*(.*?)\s+receive boosted drop rates",
        content["text"],
        re.S,
    )
    rate_up_names = (
        {normalized(n)
         for n in re.split(r",|\band\b", rate_up[1]) if n.strip()}
        if rate_up
        else None
    )
    matches = []
    for event in events:
        english = (event.source_data or {}).get(
            "client", {}).get("locales", {}).get("en", {})
        exact_title = name and normalized(
            english.get("title", event.title)) == name
        exact_featured = (
            featured
            and event.event_kind == "banner"
            and any(
                normalized(n) == normalized(featured[2]) for n in english.get("featured_names", [])
            )
            and ((featured[1] == "Weapon") == ("Weapon Convene" in english.get("title", "")))
        )
        if exact_title or exact_featured:
            matches.append(event)
    if match_roster and len(matches) > 1 and rate_up_names:
        matches = [
            event
            for event in matches
            if rate_up_names
            == {
                normalized(n)
                for n in (event.source_data or {})
                .get("client", {})
                .get("locales", {})
                .get("en", {})
                .get("up_names", [])
            }
        ]
    return matches


def notice_matches[T: MatchingEvent](content: dict[str, Any], events: Sequence[T]) -> list[T]:
    # Repeated titles/featured characters are not proof of which client row is used.
    matches = notice_candidates(content, events)
    return matches if len(matches) == 1 else []

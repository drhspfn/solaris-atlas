"""Pure story localization and authored choice resolution policies."""

import re
from collections import defaultdict
from typing import Any


def version_key(value):
    return tuple(int(part) for part in re.findall(r"\d+", value or ""))


def localization_payload(key, locale, requested, values, versions, *, available=True):
    if key is None or not available or requested is None:
        return {"key": key, "locale": locale, "content": None, "resolution": "unavailable"}
    exact = next(
        (value for value in values if value.release_id == requested.id), None)
    chosen = exact
    if exact is None or exact.status != "resolved_nonempty" or not exact.content:
        previous = [
            value
            for value in values
            if value.status == "resolved_nonempty"
            and value.content
            and version_key(versions[value.release_id].game_version)
            <= version_key(requested.game_version)
        ]
        if previous:
            chosen = max(
                previous,
                key=lambda value: (
                    version_key(versions[value.release_id].game_version),
                    versions[value.release_id].sequence,
                ),
            )
    source = versions[chosen.release_id] if chosen else requested
    return {
        "key": key,
        "locale": locale,
        "content": chosen.content if chosen else None,
        "resolution": chosen.status if chosen else "missing_key",
        "requested_resolution": exact.status if exact else "missing_key",
        "fallback": bool(chosen and chosen.release_id != requested.id),
        "source_game_version": source.game_version,
        "source_record_id": chosen.source_record_id if chosen else None,
    }


def choice_branch(
    params: dict[str, Any], state_key: str, action_index: int, target_line_id: str | None
) -> dict[str, Any] | None:
    """Read one source-authored talk sequence without inferring runtime paths."""
    prefix = f"talk_item:{state_key}:{action_index}:"
    if not target_line_id or not target_line_id.startswith(prefix):
        return None
    items = params.get("TalkItems")
    sequences = params.get("TalkSequence")
    if not isinstance(items, list) or not isinstance(sequences, list):
        return None
    try:
        target_item = items[int(target_line_id.removeprefix(prefix))]
        target_talk_id = target_item["Id"]
    except (ValueError, IndexError, TypeError, KeyError):
        return None
    matches = [
        (index, sequence.index(target_talk_id))
        for index, sequence in enumerate(sequences)
        if isinstance(sequence, list) and target_talk_id in sequence
    ]
    if len(matches) != 1:
        return None
    sequence_index, start = matches[0]
    local_ids: dict[Any, list[str]] = {}
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("Id") is not None:
            local_ids.setdefault(item["Id"], []).append(f"{prefix}{index}")

    def unique_line_id(talk_id: Any) -> str | None:
        candidates = local_ids.get(talk_id, [])
        return candidates[0] if len(candidates) == 1 else None

    line_ids = [unique_line_id(talk_id)
                for talk_id in sequences[sequence_index][start:]]
    if not line_ids or any(line_id is None for line_id in line_ids):
        return None
    continuation_line_id = None
    transitions = params.get("SequenceTransitions") or {}
    if isinstance(transitions, dict):
        outgoing = transitions.get(str(sequence_index), [])
        if isinstance(outgoing, list) and len(outgoing) == 1 and isinstance(outgoing[0], dict):
            transition = outgoing[0]
            next_index = transition.get("NextSequenceIndex")
            if (
                not transition.get("OptionTextKey")
                and isinstance(next_index, int)
                and 0 <= next_index < len(sequences)
            ):
                next_sequence = sequences[next_index]
                if isinstance(next_sequence, list) and next_sequence:
                    continuation_line_id = unique_line_id(next_sequence[0])
    return {"line_ids": line_ids, "continuation_line_id": continuation_line_id}


class StoryBrowsingError(ValueError):
    """A browsing request cannot be fulfilled with the selected filters."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def authored_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Topologically order explicit links, using the source Id as a stable tie-breaker."""
    by_id = {row["Id"]: row for row in rows}
    followers: dict[int, set[int]] = defaultdict(set)
    incoming = {identifier: 0 for identifier in by_id}
    for row in rows:
        identifier = row["Id"]
        previous = row.get("PreNode") or []
        if not isinstance(previous, list):
            previous = [previous]
        for predecessor in previous:
            if predecessor in by_id and identifier not in followers[predecessor]:
                followers[predecessor].add(identifier)
                incoming[identifier] += 1
        successor = row.get("NextNode")
        if successor in by_id and successor not in followers[identifier]:
            followers[identifier].add(successor)
            incoming[successor] += 1
    ready = sorted(identifier for identifier,
                   count in incoming.items() if count == 0)
    ordered: list[int] = []
    while ready:
        identifier = ready.pop(0)
        ordered.append(identifier)
        for successor in sorted(followers[identifier]):
            incoming[successor] -= 1
            if incoming[successor] == 0:
                ready.append(successor)
                ready.sort()
    # Cycles or malformed links are retained, without inventing an order for them.
    ordered.extend(sorted(set(by_id) - set(ordered)))
    return [by_id[identifier] for identifier in ordered]

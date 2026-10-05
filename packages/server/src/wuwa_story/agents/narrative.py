"""Bounded internal Markdown references, resolved only from published analysis data."""

import re

from wuwa_story.agents.contracts import AnalysisResult

REFERENCE = re.compile(r"\[[^\]\n]+\]\(([^)\s]+)\)")


def validate_narrative_links(result: AnalysisResult) -> None:
    for block in result.blocks:
        records = set(block.related_node_ids) | {item.node_id for item in block.related_records}
        records.update(citation.node_id for citation in block.citations)
        for target in REFERENCE.findall(block.text):
            match = re.fullmatch(r"(connection|record|event):([0-9]+)", target)
            if match is None:
                raise ValueError(
                    "Narrative links must use connection:index, event:index or record:node_id"
                )
            kind, value = match[1], int(match[2])
            if (
                (kind == "connection" and value >= len(result.links))
                or (kind == "event" and value >= len(result.events))
                or (kind == "record" and value not in records)
            ):
                raise ValueError(f"Narrative link {target} has no corresponding sourced record")

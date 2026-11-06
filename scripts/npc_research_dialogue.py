"""Read-only, bounded dialogue evidence for NPC source discovery.

Run in the API environment; output can be joined to world interaction flow triples.
A speaker participating in a flow is not automatically the world entity's identity.
"""
import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select, text
from wuwa_story.db.models.core import DialogueLine, QuestAction, QuestState
from wuwa_story.db.models.graph import Node
from wuwa_story.db.repositories.quest_scope import quest_state_links
from wuwa_story.db.session import SessionFactory

CONTROL_SPEAKERS = {
    "iris": [100204, 701169], "rosemary": [50104],
    "cristoforo": [50080, 1603, 1574, 800329],
    "avidius": [1580, 300041, 1600], "nyarla": [1485], "zahira": [100206, 850455],
}


async def collect(release_id=21):
    result = {"format": "solaris-npc-dialogue-evidence-v1", "release_id": release_id, "subjects": []}
    async with SessionFactory() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.execute(text("SET LOCAL statement_timeout='15s'"))
        for label, ids in CONTROL_SPEAKERS.items():
            speakers = (await session.execute(select(Node.id, Node.canonical_key).where(
                Node.canonical_key.in_([f"speaker:{value}" for value in ids])))).all()
            entries = []
            for node_id, key in speakers:
                rows = (await session.execute(select(
                    DialogueLine.node_id, QuestState.node_id.label("state_node_id"), QuestState.state_key, QuestState.flow_list_name,
                    QuestState.flow_id, QuestState.state_id,
                ).join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
                  .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
                  .where(DialogueLine.speaker_node_id == node_id)
                  .order_by(DialogueLine.node_id))).all()
                state_ids = {row.state_node_id for row in rows}
                links = quest_state_links(release_id, state_ids=state_ids)
                quests = [dict(row._mapping) for row in (await session.execute(select(links))).all()] if state_ids else []
                entries.append({"speaker_key": key, "speaker_node_id": node_id,
                    "dialogue_count": len(rows), "quest_state_links": quests, "lines": [dict(row._mapping) for row in rows]})
            result["subjects"].append({"label": label, "speaker_candidates": entries,
                "identity_status": "separate source speakers; no name-based merge"})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--release-id", type=int, default=21)
    args = parser.parse_args()
    value = json.dumps(asyncio.run(collect(args.release_id)), ensure_ascii=True, indent=2)
    if args.out:
        args.out.write_text(value, encoding="utf-8")
    else:
        print(value)

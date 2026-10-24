"""Explain authored quest roles without treating missing dialogue as a test signal."""

import re

from sqlalchemy import select

from wuwa_story.db.models.raw import SourceFile, SourceRecord

QUEST_DATA = "BinData/QuestData/questdata.json"
QUEST_TYPE = "BinData/questtype/questtype.json"
QUEST_MAIN_TYPE = "BinData/questtype/questmaintype.json"
QUEST_NODES = "BinData/QuestNodeData/questnodedata.json"


def classify_quest(data: dict, subtype: dict, main_type: dict) -> dict:
    key = data.get("Key", "")
    key = key if isinstance(key, str) else ""
    evidence = []
    if "剧情图鉴专用" in key:
        kind, label = "story_gallery", "Story gallery record"
        explanation = "The source marks this quest as a record for the story gallery."
        evidence.append({"field": "Data.Key", "value": key})
    elif re.search(r"^(?:\[|【)?(?:废弃|弃用|作废)(?:\]|】|[_/:： -]|$)", key):
        kind, label = "deprecated", "Deprecated quest record"
        explanation = "The internal source name marks this record as deprecated."
        evidence.append({"field": "Data.Key", "value": key})
    elif main_type.get("QuestTabIcon", "").rsplit("/", 1)[-1].split(".")[0] == "SP_QuestPagingTest":
        kind, label = "test", "Test quest"
        explanation = "The game's quest type assigns this record to its test category."
        evidence.append({"field": "QuestMainType.QuestTabIcon", "value": main_type["QuestTabIcon"]})
    elif subtype.get("IsShowInQuestPanel") is False and (
        "测试" in key or "调试" in key or re.search(r"^(test|debug)[/_ -]", key, re.I)
    ):
        kind, label = "test", "Test quest"
        explanation = "The internal name identifies a test and the quest type hides it from the quest journal."
        evidence.append({"field": "Data.Key", "value": key})
    elif subtype.get("IsShowInQuestPanel") is False:
        kind, label = "internal", "Internal quest record"
        explanation = "The game's quest type hides this record from the quest journal."
    else:
        kind, label = "unclassified", "Quest record"
        explanation = "No explicit test, gallery or internal marker was found in this snapshot."
    if "IsShowInQuestPanel" in subtype:
        evidence.append(
            {"field": "QuestType.IsShowInQuestPanel", "value": subtype["IsShowInQuestPanel"]}
        )
    return {
        "kind": kind,
        "label": label,
        "explanation": explanation,
        "internal_name": key or None,
        "source_type": data.get("Type"),
        "visible_in_quest_panel": subtype.get("IsShowInQuestPanel"),
        "evidence": evidence,
    }


def item_requirement(data: dict, item_id: int) -> dict:
    """Only exact authored condition IDs establish a requirement; never a reward."""
    condition = data.get("Condition") or {}
    if condition.get("Type") == "GetItem":
        for item in condition.get("Items", []):
            if type(item.get("ItemId")) is int and item["ItemId"] == item_id:
                return {
                    "kind": "obtain_requirement",
                    "label": "Requires obtaining this item",
                    "count": item.get("Count"),
                    "field": "Data.Condition.Items",
                    "is_reward": False,
                }
    if condition.get("Type") == "HandInItems":
        hand_in = condition.get("HandInItems") or {}
        for item in hand_in.get("Items", []) + [hand_in.get("GroupConfig") or {}]:
            if item_id in [value for value in item.get("ItemIds", []) if type(value) is int]:
                return {
                    "kind": "hand_in_requirement",
                    "label": "Requires handing in this item",
                    "count": item.get("Count"),
                    "field": "Data.Condition.HandInItems",
                    "alternatives": len(item.get("ItemIds", [])) > 1,
                    "is_reward": False,
                }
    return {"kind": "reference", "label": "References this item", "is_reward": None}


def quest_item_requirements(data: dict) -> list[dict]:
    condition = data.get("Condition") or {}
    items = condition.get("Items", []) if condition.get("Type") == "GetItem" else []
    ids = {item["ItemId"] for item in items if type(item.get("ItemId")) is int}
    if condition.get("Type") == "HandInItems":
        hand_in = condition.get("HandInItems") or {}
        ids.update(
            value
            for item in hand_in.get("Items", []) + [hand_in.get("GroupConfig") or {}]
            for value in item.get("ItemIds", [])
            if type(value) is int
        )
    return [{"item_id": identity, **item_requirement(data, identity)} for identity in sorted(ids)]


async def quest_classification(session, quest_id: int, release_id: int | None) -> dict:
    if release_id is None:
        return classify_quest({}, {}, {})
    rows = list(
        await session.execute(
            select(SourceRecord, SourceFile.logical_source_path)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(
                SourceRecord.release_id == release_id,
                SourceFile.logical_source_path == QUEST_DATA,
                SourceRecord.data["QuestId"].as_integer() == quest_id,
            )
            .limit(2)
        )
    )
    if len(rows) != 1:
        return classify_quest({}, {}, {})
    record, path = rows[0]
    data = record.data.get("Data") or {}
    # Type dictionaries are small and shared by every quest in one API request.
    cache_key = ("quest_type_records", release_id)
    if cache_key not in session.info:
        type_rows = await session.execute(
            select(SourceRecord, SourceFile.logical_source_path)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(
                SourceRecord.release_id == release_id,
                SourceFile.logical_source_path.in_([QUEST_TYPE, QUEST_MAIN_TYPE]),
            )
        )
        session.info[cache_key] = {(path, row.data.get("Id")): row for row, path in type_rows}
    types = session.info[cache_key]
    subtype = types.get((QUEST_TYPE, data.get("Type")))
    main_type = types.get((QUEST_MAIN_TYPE, subtype.data.get("MainId"))) if subtype else None
    result = classify_quest(
        data, subtype.data if subtype else {}, main_type.data if main_type else {}
    )
    result["source"] = {"file": path, "row": record.row_index, "release_id": release_id}
    result["type_sources"] = [
        {"file": path, "row": row.row_index}
        for path, row in ((QUEST_TYPE, subtype), (QUEST_MAIN_TYPE, main_type))
        if row
    ]
    return result

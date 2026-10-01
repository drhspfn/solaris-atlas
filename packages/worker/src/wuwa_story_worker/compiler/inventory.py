"""Schema inventory across every checked-out BinData and Textmaps JSON table."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .core import canonical_json, is_ignored_fs_entry, read_json

SUPPORTED = {
    "PlotHandBook/plothandbookconfig.json", "QuestData/questdata.json",
    "QuestNodeData/questnodedata.json", "flow/flow.json", "flowState/flowstate.json",
    "speaker/speaker.json", "PhoneMsg/shortmessage.json", "PhoneMsg/chatpartner.json",
    "RandomPlot/plotreference.json", "RandomPlot/randomplot.json",
    "RandomPlot/randomplottrigger.json", "PlotGuest/plotguest.json",
    "cgVedio/videodata.json", "cgVedio/videosound.json", "cgVedio/videocaption.json",
    "plot_audio/plotaudio.json", "audio/audio.json", "item/iteminfo.json",
    "area/area.json", "role/roleinfo.json", "subtitle_text/subtitletext.json",
    "questtype/questtype.json", "quest_chapter/questchapter.json",
    "QuestTree/questtreenode.json", "QuestTree/questtreechapter.json",
}
PARTIAL = {
    "QuestRefVideo/questrefvideoconfig.json", "QuestReview/questreviewnode.json",
    "QuestReview/questreviewline.json", "QuestMultiLine/questtimepointconfig.json",
    "QuestMultiLine/questbranchpageconfig.json", "FragmentMemory/photomemorycollect.json",
    "MusicSubTitle/musicsubtitle.json", "custom_sequence/customsequence.json",
    "BubbleData/bubbledata.json", "InteractData/interactdata.json",
    "LevelPlayData/levelplaydata.json", "LevelPlayNodeData/levelplaynodedata.json",
    "GuessJokerCard/guessjokerplotconfig.json", "PhantomBattle/phantombattlewinseq.json",
    "PhantomBattle/phantombattledialog.json",
    "QuestRefMapBlock/questrefmapblockconfig.json",
    "QuestTreeCustomJumpConfig/questtreecustomjumpconfig.json",
    "RefResourceQuestList/refresourcequestlist.json",
    "cgVedio/videoqte.json", "DownLoad/mapblockinfo.json",
    "instance_dungeon/instancedungeon.json",
}
NAME_HINTS = ("quest", "flow", "plot", "talk", "phone", "chat", "speaker", "subtitle",
              "caption", "cinematic", "video", "audio", "wwise", "story", "memory",
              "fragment", "sequence", "npc", "role", "item", "area", "map", "book", "lore")
TIER1_FIELDS = frozenset(("QuestId", "FlowId", "StateId", "StateKey", "ActionId", "ActionGuid",
                          "TidTalk", "TalkItems", "TalkSequence", "SequenceTransitions", "CgName",
                          "PlotLineKey", "PreQuest", "FlowGuid", "TalkId", "TalkItemId",
                          "TalkerId", "ChildQuestId", "RelatedQuestId", "ListenQuestId",
                          "ReChallengeQuestId", "EndQuestId", "PreQuestId", "AfterTalkStateId",
                          "RoleQuestId", "ShowQuestId"))
TIER2_FIELDS = frozenset(("WhoId", "SpeakerId", "SpeakerID", "TextId", "TidName", "TidDesc",
                          "TidContent", "CaptionText", "Subtitle", "SubtitleText", "EventPath",
                          "Wwise", "LocalizationId", "TextKey", "PlotId", "PlotAudioId",
                          "RoleId", "CharacterId", "GuestCharacterId", "TargetNpcId", "NpcId",
                          "MonsterNpcEntityId", "QuestBranchPageId", "BlackFlowerHelpId",
                          "CaptionId", "CardRoleId", "NpcChallengeSkillId", "NpcGroupId",
                          "CharacterLookAtPointId", "BgRoleId", "SelectRoleCameraId", "TrialRoleId",
                          "NpcAbpMontageId", "RoleTrialId"))
FIELD_HINTS = tuple(sorted(TIER1_FIELDS | TIER2_FIELDS))
KEY_CANDIDATES = ("Id", "Key", "QuestId", "StateKey", "AreaId", "CgId", "CaptionId", "GuestID")
REQUIRED_NARRATIVE_TABLES = frozenset({
    "BinData/flow/flow.json", "BinData/flowState/flowstate.json",
    "BinData/PlotHandBook/plothandbookconfig.json", "BinData/QuestData/questdata.json",
    "BinData/QuestNodeData/questnodedata.json", "BinData/InteractData/interactdata.json",
    "BinData/BubbleData/bubbledata.json", "BinData/LevelPlayNodeData/levelplaynodedata.json",
    "BinData/PhantomBattle/phantombattledialog.json", "BinData/RandomPlot/plotreference.json",
    "BinData/PhoneMsg/shortmessage.json", "BinData/plot_audio/plotaudio.json",
    "BinData/cgVedio/videodata.json", "BinData/cgVedio/videocaption.json",
})


def _shape(value: Any) -> str:
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _paths(value: Any, prefix: str = "", depth: int = 0) -> set[str]:
    if depth > 3:
        return set()
    if isinstance(value, dict):
        out: set[str] = set()
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            out.add(path)
            out |= _paths(child, path, depth + 1)
        return out
    if isinstance(value, list) and value:
        return _paths(value[0], prefix + "[]", depth + 1)
    if isinstance(value, str) and value[:1] in "[{" and len(value) < 100_000:
        try:
            return _paths(json.loads(value), prefix + "<json>", depth + 1)
        except (json.JSONDecodeError, RecursionError):
            return set()
    return set()


def _all_keys(value: Any, names: set[str], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            names.add(key)
            _all_keys(child, names, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _all_keys(child, names, depth + 1)
    elif isinstance(value, str) and value[:1] in "[{" and len(value) < 1_000_000:
        try:
            _all_keys(json.loads(value), names, depth + 1)
        except (json.JSONDecodeError, RecursionError):
            pass


def scan(data_root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for base in (data_root / "BinData", data_root / "Textmaps"):
        for path in sorted(p for p in base.rglob("*.json") if not is_ignored_fs_entry(p)):
            rel = path.relative_to(data_root).as_posix()
            family = path.relative_to(data_root / "BinData").as_posix() if base.name == "BinData" else None
            try:
                data = read_json(path)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                entries.append({"file": rel, "family": family, "status": "unknown",
                                "error": str(exc), "narrative_relevance": "unknown",
                                "narrative_tier": "unknown", "tier1_fields": [], "tier2_fields": []})
                continue
            records = data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
            sample = records[:3]
            fields = set().union(*(_paths(row) for row in sample)) if sample else set()
            all_names: set[str] = set()
            if base.name == "BinData":
                for record in records:
                    _all_keys(record, all_names)
            keys = [key for key in KEY_CANDIDATES if sample and all(isinstance(row, dict) and key in row for row in sample)]
            name_hit = any(hint in rel.lower() for hint in NAME_HINTS)
            field_hit = [hint for hint in FIELD_HINTS if hint in all_names or
                         any(p.rsplit(".", 1)[-1] == hint for p in fields)]
            tier1_hit = sorted(TIER1_FIELDS & all_names)
            tier2_hit = sorted(TIER2_FIELDS & all_names)
            tier = ("tier1" if tier1_hit else "tier2" if tier2_hit else
                    "tier3" if name_hit else "out_of_scope")
            narrative = "candidate" if tier != "out_of_scope" else "unlikely"
            textmap_supported = (base.name == "Textmaps" and
                (rel.endswith("/multi_text/MultiText.json") or
                 rel.endswith("/multi_text_1sthalf/MultiText.json") or
                 rel.endswith("/multi_text_2ndhalf/MultiText.json") or
                 rel.endswith("/speaker/Speaker.json") or
                 rel.endswith("/subtitle_text/SubtitleText.json")))
            if family in SUPPORTED or textmap_supported:
                status = "supported"
            elif family in PARTIAL:
                status = "partially_supported"
            elif narrative == "candidate":
                status = "unknown"
            else:
                status = "out_of_scope"
            classification = ("normalized" if status == "supported" else
                              "partially_normalized" if status == "partially_supported" else
                              "raw_evidence_only" if tier != "out_of_scope" else "out_of_scope")
            known_reference_fields = TIER1_FIELDS | TIER2_FIELDS
            unknown_reference_fields = sorted(name for name in all_names
                if name not in known_reference_fields and
                re.search(r"(Quest|Flow|Talk|Action|Speaker|Npc|Role|Character|Cutscene|Caption|Sequence).*(Id|ID|Key|Guid|GUID)$", name))
            entries.append({"file": rel, "family": family, "shape": _shape(data),
                            "record_count": len(data) if hasattr(data, "__len__") else None,
                            "key_fields": keys, "field_paths": sorted(fields),
                            "all_field_names": sorted(all_names),
                            "reference_field_candidates": sorted(field_hit),
                            "narrative_tier": tier,
                            "tier1_fields": tier1_hit,
                            "tier2_fields": tier2_hit,
                            "narrative_relevance": narrative,
                            "narrative_classification": classification,
                            "unknown_strong_reference_fields": unknown_reference_fields,
                            "status": status, "normalizer": (family or "localization")
                            if status in ("supported", "partially_supported") else None})
    counts = Counter(row["status"] for row in entries)
    tier_unknown = Counter(row.get("narrative_tier") for row in entries
                           if row.get("status") in ("unknown", "partially_supported"))
    tier_summary = {"tier1_unknown": tier_unknown["tier1"],
                    "tier2_unknown": tier_unknown["tier2"],
                    "tier3_unknown": tier_unknown["tier3"],
                    "out_of_scope": sum(row.get("narrative_tier") == "out_of_scope" for row in entries)}
    tier_summary["unclassified_tier1"] = sum(
        row.get("narrative_tier") == "tier1" and
        row.get("narrative_classification") not in ("normalized", "partially_normalized", "raw_evidence_only")
        for row in entries)
    tier_summary["unknown_strong_reference_fields"] = sum(
        len(row.get("unknown_strong_reference_fields", [])) for row in entries)
    schema = [{"file": row["file"], "shape": row.get("shape"),
               "fields": row.get("field_paths", []), "all_field_names": row.get("all_field_names", [])}
              for row in entries]
    return {"tables": entries, "summary": {"files": len(entries), "statuses": dict(sorted(counts.items())),
                                             **tier_summary},
            "schema_inventory_hash": hashlib.sha256(canonical_json(schema).encode()).hexdigest()}


def strict_coverage_findings(tables: list[dict[str, Any]], baseline_paths: set[str],
                             unsupported_talk_count: int = 0,
                             required_tables: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    """Return actionable completeness failures independently from schema drift."""
    return {
        "new_tier1_tables": [row["file"] for row in tables
                             if row.get("narrative_tier") == "tier1" and row["file"] not in baseline_paths],
        "unclassified_tier1_tables": [row["file"] for row in tables
                                      if row.get("narrative_tier") == "tier1" and
                                      row.get("narrative_classification") not in
                                      ("normalized", "partially_normalized", "raw_evidence_only")],
        "unknown_strong_reference_fields": {
            row["file"]: row["unknown_strong_reference_fields"] for row in tables
            if row.get("narrative_tier") in ("tier1", "tier2") and
            row.get("unknown_strong_reference_fields")},
        "unclassified_talk_diagnostics": unsupported_talk_count,
        "missing_required_tables": sorted(set(required_tables) - {row.get("file") for row in tables}),
    }

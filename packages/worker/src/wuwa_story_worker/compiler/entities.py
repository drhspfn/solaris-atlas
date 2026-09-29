"""Compile explicit speaker, phone, random plot, entity and story-reference tables."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import Writer, read_json


def _visit(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            yield child_path, key, child
            yield from _visit(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _visit(child, f"{path}[{index}]")


def compile_entities(root: Path, writer: Writer, english: dict[str, dict[str, Any]]) -> dict[str, int]:
    base = root / "BinData"
    counts: dict[str, int] = {}

    def rows(rel: str):
        path = base / rel
        if not path.exists():
            writer.diagnostic("missing_entity_table", "warning", f"BinData/{rel}", "$", None)
            return []
        return read_json(path)

    def loc(key: Any):
        if not isinstance(key, str) or not key:
            return None
        result = english.get(key)
        return {"key": key, "en": result.get("content") if result else None,
                "source": result.get("source") if result else None,
                "resolution": result.get("resolution", "resolved_nonempty") if result else "missing_key"}

    def simple(rel: str, kind: str, id_field: str, prefix: str, fields: tuple[str, ...] = ()):
        data = rows(rel)
        source = f"BinData/{rel}"
        for index, raw in enumerate(data):
            value = raw.get(id_field)
            if value is None:
                writer.diagnostic("missing_entity_id", "error", source, f"$[{index}].{id_field}", raw)
                continue
            identifier = f"{prefix}:{value}"
            record = {"id": identifier, "game_id": value,
                      "source": {"file": source, "row": index, "raw_path": f"$[{index}]"},
                      "raw": raw}
            for field in fields:
                record[field.lower()] = loc(raw.get(field))
            if kind == "speaker":
                record["display_name"] = loc(f"Speaker_{value}_Name")
                record["resolution_method"] = "Speaker.Id + exact MultiText key"
            elif kind == "npc":
                record["resolution_method"] = "NpcHeadInfo.Id only; speaker crosswalk unresolved"
            writer.emit(kind, record)
        counts[kind] = len(data)
        return data

    speakers = simple("speaker/speaker.json", "speaker", "Id", "speaker")
    speaker_ids = {r.get("Id") for r in speakers}
    for row in speakers:
        speaker_id = row.get("Id")
        if speaker_id is not None:
            # This table's Name is a numeric index; MultiText's Speaker_<Id>_Name
            # is the observed display text source.
            key = f"Speaker_{speaker_id}_Name"
            if key not in english:
                writer.diagnostic("unresolved_speaker_name", "warning", "BinData/speaker/speaker.json",
                                  str(speaker_id), key)
    role_rows = simple("role/roleinfo.json", "character", "Id", "character", ("Name", "NickName"))
    # A display-name match is not identity evidence. Speaker configs do, however,
    # carry the same dedicated portrait asset paths as RoleInfo. Accept a crosswalk
    # only when at least two distinct speaker asset fields exactly match assets
    # owned by the same single RoleInfo.Id.
    role_asset_fields = (
        "RoleHeadIcon", "RoleHeadIconBig", "RoleHeadIconCircle", "RoleHeadIconLarge",
        "FormationRoleCard", "RolePortrait",
    )
    speaker_asset_fields = (
        "HeadIconAsset", "HeadRoundIconAsset", "HeadRoundIconAssetMaleVariant",
        "RolePileIconAsset",
    )
    asset_role_ids: dict[str, set[int]] = {}
    for role in role_rows:
        role_id = role.get("Id")
        if role_id is None:
            continue
        for field in role_asset_fields:
            asset = role.get(field)
            if isinstance(asset, str) and asset:
                asset_role_ids.setdefault(asset, set()).add(role_id)
    resource_crosswalk_count = 0
    for speaker_index, speaker in enumerate(speakers):
        speaker_id = speaker.get("Id")
        if speaker_id is None:
            continue
        matches: list[tuple[str, int, str]] = []
        for field in speaker_asset_fields:
            asset = speaker.get(field)
            owners = asset_role_ids.get(asset, set()) if isinstance(asset, str) else set()
            if len(owners) == 1:
                matches.append((field, next(iter(owners)), asset))
        target_ids = {role_id for _, role_id, _ in matches}
        if len(matches) < 2 or len(target_ids) != 1:
            continue
        role_id = next(iter(target_ids))
        match_detail = "; ".join(f"{field}={asset}" for field, _, asset in matches)
        writer.edge(
            f"speaker:{speaker_id}", f"character:{role_id}", "references_character",
            f"two or more exact speaker/RoleInfo asset matches: {match_detail}",
            "BinData/speaker/speaker.json", f"$[{speaker_index}]",
            {"relation": "exact_join"},
        )
        resource_crosswalk_count += 1
    role_names: dict[str, list[int]] = {}
    for role in role_rows:
        for field in ("Name", "NickName"):
            key = role.get(field)
            localized = english.get(key) if isinstance(key, str) else None
            display = localized.get("content") if localized else None
            if isinstance(display, str) and display:
                role_names.setdefault(display, []).append(role.get("Id"))
    name_only_candidates = []
    for speaker in speakers:
        speaker_id = speaker.get("Id")
        localized = english.get(f"Speaker_{speaker_id}_Name")
        display = localized.get("content") if localized else None
        if isinstance(display, str) and display and display in role_names:
            name_only_candidates.append({"speaker_id": speaker_id, "display_name": display,
                                         "candidate_role_ids": sorted(set(role_names[display])),
                                         "resolution": "name_only_candidate"})
    writer.diagnostic("unresolved_entity_crosswalk", "info", "BinData/speaker/speaker.json", "$",
                      {"speaker_records": len(speakers), "explicit_crosswalks": 0,
                       "exact_asset_crosswalks": resource_crosswalk_count,
                       "name_only_candidate_count": len(name_only_candidates),
                       "name_only_candidates": name_only_candidates,
                       "role_or_npc_join": "no explicit SpeakerId/SpeakerID crosswalk field found"})
    npc_heads = simple("npc_headinfo/npcheadinfo.json", "npc", "Id", "npc")
    if npc_heads:
        writer.diagnostic("unresolved_npc_identity", "info", "BinData/npc_headinfo/npcheadinfo.json", "$",
                          "NpcHeadInfo.Id is retained in its own namespace; no proven SpeakerId crosswalk")
    items = simple("item/iteminfo.json", "item", "Id", "item", ("Name", "BgDescription"))
    areas = simple("area/area.json", "area", "AreaId", "area", ("Title",))
    item_ids = {r.get("Id") for r in items}
    area_ids = {r.get("AreaId") for r in areas}
    for index, raw in enumerate(items):
        components = raw.get("DecomposeInfo") or []
        if not isinstance(components, list):
            writer.diagnostic("unsupported_item_decompose_info", "warning",
                              "BinData/item/iteminfo.json", f"$[{index}].DecomposeInfo", components)
            continue
        for component_index, component in enumerate(components):
            if not isinstance(component, dict):
                writer.diagnostic("unsupported_item_decompose_entry", "warning",
                                  "BinData/item/iteminfo.json",
                                  f"$[{index}].DecomposeInfo[{component_index}]", component)
                continue
            target_id = component.get("Key")
            if target_id in item_ids:
                writer.edge(f"item:{raw['Id']}", f"item:{target_id}", "decomposes_into",
                            "DecomposeInfo[].Key exactly matches ItemInfo.Id",
                            "BinData/item/iteminfo.json",
                            f"$[{index}].DecomposeInfo[{component_index}].Key",
                            {"quantity": component.get("Value")})
            else:
                writer.diagnostic("unresolved_decomposed_item", "warning",
                                  "BinData/item/iteminfo.json",
                                  f"$[{index}].DecomposeInfo[{component_index}].Key", target_id)
    for index, raw in enumerate(areas):
        parent = raw.get("Father")
        if parent in area_ids and parent != raw.get("AreaId"):
            writer.edge(f"area:{raw['AreaId']}", f"area:{parent}", "within_area", "Father = Area.AreaId",
                        "BinData/area/area.json", f"$[{index}].Father")
        elif parent and parent not in area_ids:
            writer.diagnostic("unresolved_parent_area", "warning", "BinData/area/area.json",
                              f"$[{index}].Father", parent)

    partners = simple("PhoneMsg/chatpartner.json", "chat_partner", "Id", "chat_partner", ("Name",))
    partner_ids = {r.get("Id") for r in partners}
    flow_ids = {r.get("Id") for r in rows("flow/flow.json")}
    state_keys = {r.get("StateKey") for r in rows("flowState/flowstate.json")}
    for index, raw in enumerate(rows("PhoneMsg/shortmessage.json")):
        source = "BinData/PhoneMsg/shortmessage.json"
        identity = f"short_message:{raw.get('Id', index)}"
        writer.emit("short_message", {"id": identity,
                                      "source": {"file": source, "row": index, "raw_path": f"$[{index}]"},
                                      "raw": raw})
        partner = raw.get("WhichChat")
        if partner in partner_ids:
            writer.edge(identity, f"chat_partner:{partner}", "has_chat_partner", "WhichChat = ChatPartner.Id",
                        source, f"$[{index}].WhichChat")
        else:
            writer.diagnostic("unresolved_chat_partner", "warning", source, f"$[{index}].WhichChat", partner)
        param = raw.get("FlowParam")
        if isinstance(param, list) and len(param) == 3:
            flow_id = f"{param[0]}_{param[1]}"
            state_key = f"{flow_id}_{param[2]}"
            if flow_id in flow_ids and state_key in state_keys:
                writer.edge(identity, f"flow_state:{state_key}", "references_flow_state",
                            "FlowParam[0:3]", source, f"$[{index}].FlowParam")
            else:
                writer.diagnostic("unresolved_short_message_flow", "warning", source,
                                  f"$[{index}].FlowParam", param)
        else:
            writer.diagnostic("unsupported_short_message_flow", "warning", source,
                              f"$[{index}].FlowParam", param)
        quest_id = raw.get("QuestId")
        if isinstance(quest_id, int) and quest_id:
            writer.edge(identity, f"quest:{quest_id}", "references_quest", "QuestId", source,
                        f"$[{index}].QuestId")
        listen_quest = raw.get("ListenQuestId")
        if isinstance(listen_quest, int) and listen_quest:
            writer.edge(identity, f"quest:{listen_quest}", "listens_for_quest", "ListenQuestId", source,
                        f"$[{index}].ListenQuestId")
    counts["short_message"] = len(rows("PhoneMsg/shortmessage.json"))

    references = simple("RandomPlot/plotreference.json", "random_plot_reference", "Id", "random_plot_reference")
    reference_ids = {r.get("Id") for r in references}
    for index, raw in enumerate(references):
        plot = raw.get("Plot")
        if not isinstance(plot, str):
            continue
        pieces = plot.rsplit(",", 2)
        source = "BinData/RandomPlot/plotreference.json"
        if len(pieces) == 3:
            state_key = f"{pieces[0]}_{pieces[1]}_{pieces[2]}"
            if state_key in state_keys:
                writer.edge(f"random_plot_reference:{raw['Id']}", f"flow_state:{state_key}",
                            "references_flow_state", "Plot triple", source, f"$[{index}].Plot")
            else:
                writer.diagnostic("unresolved_random_plot_flow", "warning", source,
                                  f"$[{index}].Plot", plot)
    random_plots = simple("RandomPlot/randomplot.json", "random_plot", "Id", "random_plot")
    for index, raw in enumerate(random_plots):
        for position, ref in enumerate(raw.get("ClientPlotReferenceList") or []):
            source = "BinData/RandomPlot/randomplot.json"
            if ref in reference_ids:
                writer.edge(f"random_plot:{raw['Id']}", f"random_plot_reference:{ref}",
                            "uses_plot_reference", "ClientPlotReferenceList", source,
                            f"$[{index}].ClientPlotReferenceList[{position}]")
            else:
                writer.diagnostic("unresolved_random_plot_reference", "warning", source,
                                  f"$[{index}].ClientPlotReferenceList[{position}]", ref)
    triggers = simple("RandomPlot/randomplottrigger.json", "random_plot_trigger", "Id", "random_plot_trigger")
    random_ids = {r.get("Id") for r in random_plots}
    for index, raw in enumerate(triggers):
        target = raw.get("RandomPlotId")
        if target in random_ids:
            writer.edge(f"random_plot_trigger:{raw['Id']}", f"random_plot:{target}",
                        "triggers_random_plot", "RandomPlotId", "BinData/RandomPlot/randomplottrigger.json",
                        f"$[{index}].RandomPlotId")
        else:
            writer.diagnostic("unresolved_random_plot", "warning", "BinData/RandomPlot/randomplottrigger.json",
                              f"$[{index}].RandomPlotId", target)

    guests = simple("PlotGuest/plotguest.json", "plot_guest", "GuestID", "plot_guest", ("Name",))
    for index, raw in enumerate(guests):
        for position, speaker_id in enumerate(raw.get("SpeakerID") or []):
            if speaker_id in speaker_ids:
                writer.edge(f"plot_guest:{raw['GuestID']}", f"speaker:{speaker_id}", "has_speaker",
                            "SpeakerID array", "BinData/PlotGuest/plotguest.json",
                            f"$[{index}].SpeakerID[{position}]")
            else:
                writer.diagnostic("unresolved_plot_guest_speaker", "warning", "BinData/PlotGuest/plotguest.json",
                                  f"$[{index}].SpeakerID[{position}]", speaker_id)

    subtitles = rows("subtitle_text/subtitletext.json")
    subtitle_text = {r.get("Id"): r.get("Content") for r in read_json(
        root / "Textmaps/en/subtitle_text/SubtitleText.json")}
    for index, raw in enumerate(subtitles):
        source = "BinData/subtitle_text/subtitletext.json"
        identity = f"subtitle:{raw.get('Id', index)}"
        values = []
        for field in ("CharacterName", *(f"Subtitles{i}" for i in range(1, 6)),
                      *(f"Option{i}" for i in range(1, 6))):
            text_id = raw.get(field)
            if isinstance(text_id, int) and text_id >= 0:
                values.append({"field": field, "text_id": text_id, "en": subtitle_text.get(text_id)})
                if text_id not in subtitle_text:
                    writer.diagnostic("unresolved_subtitle_text", "warning", source,
                                      f"$[{index}].{field}", text_id)
        writer.emit("subtitle", {"id": identity,
                                 "source": {"file": source, "row": index, "raw_path": f"$[{index}]"},
                                 "raw": raw, "texts": values})
        role_id = raw.get("RoleId")
        if role_id and f"character:{role_id}" in writer.ids:
            writer.edge(identity, f"character:{role_id}", "references_character", "RoleId",
                        source, f"$[{index}].RoleId")
    counts["subtitle"] = len(subtitles)

    review_nodes = simple("QuestReview/questreviewnode.json", "quest_review_node", "Id",
                          "quest_review_node", ("Title", "Desc", "Brief"))
    review_ids = {r.get("Id") for r in review_nodes}
    review_lines = simple("QuestReview/questreviewline.json", "quest_review_line", "Id", "quest_review_line")
    for index, raw in enumerate(review_nodes):
        next_id = raw.get("SuccessorNodeId")
        if next_id in review_ids:
            writer.edge(f"quest_review_node:{raw['Id']}", f"quest_review_node:{next_id}",
                        "review_successor", "SuccessorNodeId", "BinData/QuestReview/questreviewnode.json",
                        f"$[{index}].SuccessorNodeId")
        elif next_id:
            writer.diagnostic("unresolved_review_successor", "warning", "BinData/QuestReview/questreviewnode.json",
                              f"$[{index}].SuccessorNodeId", next_id)
        line_id = raw.get("QuestLine")
        if any(line.get("Id") == line_id for line in review_lines):
            writer.edge(f"quest_review_line:{line_id}", f"quest_review_node:{raw['Id']}",
                        "has_review_node", "QuestLine", "BinData/QuestReview/questreviewnode.json",
                        f"$[{index}].QuestLine")
    for index, raw in enumerate(review_lines):
        first = raw.get("StartNodeId")
        if first in review_ids:
            writer.edge(f"quest_review_line:{raw['Id']}", f"quest_review_node:{first}",
                        "starts_at_review_node", "StartNodeId", "BinData/QuestReview/questreviewline.json",
                        f"$[{index}].StartNodeId")
    time_points = simple("QuestMultiLine/questtimepointconfig.json", "quest_time_point", "Id",
                         "quest_time_point", ("Name", "Title", "Desc"))
    for index, raw in enumerate(time_points):
        quest_id = raw.get("QuestId")
        if quest_id and f"quest:{quest_id}" in writer.ids:
            writer.edge(f"quest_time_point:{raw['Id']}", f"quest:{quest_id}", "references_quest",
                        "QuestId", "BinData/QuestMultiLine/questtimepointconfig.json",
                        f"$[{index}].QuestId")
        elif quest_id:
            writer.diagnostic("unresolved_time_point_quest", "warning",
                              "BinData/QuestMultiLine/questtimepointconfig.json", f"$[{index}].QuestId", quest_id)
    branch_pages = simple("QuestMultiLine/questbranchpageconfig.json", "quest_branch_page", "Id",
                          "quest_branch_page")
    for index, raw in enumerate(branch_pages):
        for position, pair in enumerate(raw.get("QuestNodes") or []):
            if not isinstance(pair, dict):
                continue
            quest_node_id = f"quest_node:{pair.get('X')}:{pair.get('Y')}"
            if quest_node_id in writer.ids:
                writer.edge(f"quest_branch_page:{raw['Id']}", quest_node_id,
                            "references_quest_node", "QuestNodes[X,Y]",
                            "BinData/QuestMultiLine/questbranchpageconfig.json",
                            f"$[{index}].QuestNodes[{position}]")
            else:
                writer.diagnostic("unresolved_branch_page_node", "warning",
                                  "BinData/QuestMultiLine/questbranchpageconfig.json",
                                  f"$[{index}].QuestNodes[{position}]", pair)
    memories = simple("FragmentMemory/photomemorycollect.json", "fragment_memory", "Id",
                      "fragment_memory", ("Title", "Desc", "TipsDesc"))
    for index, raw in enumerate(memories):
        targets = ([('QuestId', raw.get("QuestId"))] if raw.get("QuestId") else [])
        targets += [(f"QuestIdList[{position}]", quest_id)
                    for position, quest_id in enumerate(raw.get("QuestIdList") or [])]
        for field, quest_id in targets:
            if f"quest:{quest_id}" in writer.ids:
                writer.edge(f"fragment_memory:{raw['Id']}", f"quest:{quest_id}",
                            "references_quest", "QuestId/QuestIdList",
                            "BinData/FragmentMemory/photomemorycollect.json",
                            f"$[{index}].{field}")
            else:
                writer.diagnostic("unresolved_fragment_quest", "warning",
                                  "BinData/FragmentMemory/photomemorycollect.json",
                                  f"$[{index}].{field}", quest_id)
    simple("MusicSubTitle/musicsubtitle.json", "music_subtitle", "Id", "music_subtitle")

    # Explicit item/area IDs in quest structures only; RegionId is intentionally
    # excluded because its identity relationship to AreaId is not established.
    for rel, owner_kind in (("QuestData/questdata.json", "quest"),
                            ("QuestNodeData/questnodedata.json", "quest_node")):
        source = f"BinData/{rel}"
        for row_index, row in enumerate(rows(rel)):
            if owner_kind == "quest":
                owner = f"quest:{row.get('QuestId')}"
            else:
                parts = str(row.get("Key", "")).split("_", 1)
                owner = f"quest_node:{parts[0]}:{parts[1]}" if len(parts) == 2 else None
            if owner not in writer.ids:
                continue
            value = row.get("Data")
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    continue
            for raw_path, field, target in _visit(value, "Data"):
                if not isinstance(target, int) or target <= 0:
                    continue
                if field == "ItemId" and target in item_ids:
                    writer.edge(owner, f"item:{target}", "references_item", "explicit ItemId",
                                source, f"$[{row_index}].{raw_path}")
                elif field == "ItemId":
                    writer.diagnostic("unresolved_item", "warning", source, f"$[{row_index}].{raw_path}", target)
                elif field == "AreaId" and target in area_ids:
                    writer.edge(owner, f"area:{target}", "references_area", "explicit AreaId",
                                source, f"$[{row_index}].{raw_path}")
                elif field == "AreaId":
                    writer.diagnostic("unresolved_area", "warning", source, f"$[{row_index}].{raw_path}", target)
    return counts

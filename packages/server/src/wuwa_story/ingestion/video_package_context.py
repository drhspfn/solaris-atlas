"""Link quest video packages to uniquely identified cutscenes, without timing claims."""

import json
import re
from collections import defaultdict

QUEST_VIDEO = "BinData/QuestRefVideo/questrefvideoconfig.json"
VIDEO_DATA = "BinData/cgVedio/videodata.json"
RELATION = "references_packaged_cutscene"


def package_cutscene_links(packages, videos):
    names = defaultdict(set)
    evidence = defaultdict(list)
    for index, row in videos:
        identity, name = row.get("CgId"), row.get("CgName")
        if type(identity) is int and isinstance(name, str) and name:
            names[identity].add(name)
            evidence[identity].append(index)
    for index, row in packages:
        quest, package = row.get("QuestId"), row.get("PakName")
        if type(quest) is not int or not isinstance(package, str):
            continue
        match = re.fullmatch(r"(\d+)_(\d+)", package)
        if not match:
            continue
        identity = int(match.group(1))
        if len(names[identity]) != 1:
            continue
        yield {"from": f"quest:{quest}", "to": f"cutscene:{next(iter(names[identity]))}",
               "type": RELATION, "relation": "inference", "source": QUEST_VIDEO,
               "raw_path": f"$[{index}].PakName", "basis": json.dumps({
                   "meaning": "Quest references a video package; exact playback and order are unconfirmed",
                   "package": package, "cg_id": identity, "video_data_file": VIDEO_DATA,
                   "video_data_rows": evidence[identity],
               }, ensure_ascii=False, sort_keys=True)}

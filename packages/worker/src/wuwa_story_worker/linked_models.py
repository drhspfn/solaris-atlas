"""Discover linked models from cooked JSON without extracting or inferring assets.

``sources`` supplies component_blueprint, model, animation_resources, parent_form_id,
optional parent_skeleton and baked_components. Package references are returned as
Aki/... paths. Missing metadata is reported in every recipe's required_resources;
re-run after extraction. Cues use sequence-local times and retain montage segment
coordinates. Dynamic choices and unprovided defaults are never resolved by name.
"""

import json
from pathlib import Path

from wuwa_story_worker.model_sources import authored_package


def _package(value):
    if isinstance(value, dict):
        value = value.get("ObjectPath") or value.get("AssetPathName")
    if not isinstance(value, str) or not value:
        return None
    if value.endswith(".json"):
        value = value[:-5]
    if value.startswith("raw/Client/Content/"):
        value = value.removeprefix("raw/")
    if value.startswith("Client/Content/"):
        value = "/Game/" + value.removeprefix("Client/Content/")
    if value.startswith("Aki/"):
        value = "/Game/" + value
    try:
        return authored_package(value)
    except ValueError:
        return None


def _props(row):
    return row.get("Properties", {}) if isinstance(row, dict) else {}


def _transform(p):
    return {"location": p.get("RelativeLocation"), "rotation": p.get("RelativeRotation"),
            "scale": p.get("RelativeScale3D")}


def discover(cooked: Path, sources: dict) -> list[dict]:
    """Return SCS and effect recipes, or a $discovery placeholder for missing input.

    ``required_resources`` contains package paths, not filesystem paths. Source clips
    must be supplied by the caller or appear in a supplied montage. A follow-map key
    resolves to a parent clip only when exactly one candidate has that object name.
    """
    cooked = Path(cooked)
    required, diagnostics, cache = set(), [], {}

    def load(ref):
        package = _package(ref)
        if not package:
            return None
        if package in cache:
            return cache[package]
        candidates = [cooked / "raw/Client/Content" / (package + ".json"),
                      cooked / "Client/Content" / (package + ".json"),
                      cooked / (package + ".json")]
        for path in candidates:
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8-sig"))
                    if isinstance(data, list):
                        cache[package] = data
                        return data
                    diagnostics.append(f"Invalid export array: {package}")
                except (OSError, ValueError) as exc:
                    diagnostics.append(f"Invalid export {package}: {exc}")
                cache[package] = None
                return None
        required.add(package)
        cache[package] = None
        return None

    bp = _package(sources.get("component_blueprint") or sources.get("blueprint"))
    primary = _package(sources.get("model"))
    skeleton = _package(sources.get("parent_skeleton"))
    baked = set(sources.get("baked_components") or [])
    form = sources.get("parent_form_id")
    recipes = []

    def recipe(id_, kind, model, provenance):
        return {"id": id_, "kind": kind, "model": model, "blueprint": bp,
                "anim_blueprint": None, "parent_form_id": form, "parent_component": None,
                "socket": None, "transform": None, "socket_transform": None,
                "default_visible": None, "baked": id_ in baked, "source": provenance,
                "bindings": [], "cues": [], "required_resources": [], "diagnostics": []}

    if not bp:
        diagnostics.append("No component_blueprint supplied")
    chain, current, visited = [], bp, set()
    while current:
        if current in visited:
            diagnostics.append(f"Blueprint inheritance cycle: {current}")
            break
        if len(chain) >= 32:
            diagnostics.append("Blueprint inheritance limit reached (32 packages)")
            break
        visited.add(current)
        rows = load(current)
        if rows is None:
            break
        chain.append((current, rows))
        parents = set()
        for row in rows:
            if row.get("Type") != "BlueprintGeneratedClass":
                continue
            parent = row.get("SuperStruct") or {}
            if str(parent.get("ObjectName", "")).startswith("BlueprintGeneratedClass'"):
                path = _package(parent)
                if path:
                    parents.add(path)
        if len(parents) > 1:
            diagnostics.append(f"Ambiguous Blueprint parents: {current}")
            break
        current = next(iter(parents), None)

    # Override records are keyed by their original owner and SCS variable. A more
    # derived template wins per field; missing properties inherit from the parent.
    overrides = {}
    components = {}
    for owner, rows in reversed(chain):
        by_name = {r.get("Name"): r for r in rows if isinstance(r, dict)}
        for row in rows:
            if row.get("Type") != "SCS_Node":
                continue
            node = _props(row)
            template = node.get("ComponentTemplate") or {}
            template_name = str(template.get("ObjectName", "")).rsplit(":", 1)[-1].rstrip("'")
            component = by_name.get(template_name, {})
            if component.get("Type") not in ("SkeletalMeshComponent", "StaticMeshComponent"):
                continue
            name = node.get("InternalVariableName") or template_name
            if name in components:
                diagnostics.append(f"Duplicate SCS component name in inheritance: {name}")
            components[name] = {"owner": owner, "node": node, "template": template_name,
                                "type": component["Type"], "properties": dict(_props(component))}
        for row in rows:
            if row.get("Type") != "InheritableComponentHandler":
                continue
            for record in _props(row).get("Records", []):
                key = record.get("ComponentKey") or {}
                owner_class = _package(key.get("OwnerClass"))
                name = key.get("SCSVariableName")
                template = record.get("ComponentTemplate") or {}
                template_name = str(template.get("ObjectName", "")).rsplit(":", 1)[-1].rstrip("'")
                component = by_name.get(template_name)
                if name and owner_class and component:
                    overrides.setdefault((owner_class, name), []).append((owner, component))
                elif name and owner_class:
                    diagnostics.append(f"Unresolved inherited component override: {owner}:{name}")

    for name, info in components.items():
        p = info["properties"]
        applied = []
        for override_owner, component in overrides.get((info["owner"], name), []):
            p.update(_props(component))
            applied.append(override_owner)
        model = _package(p.get("SkeletalMesh") or p.get("StaticMesh"))
        if not model or model == primary:
            continue
        node = info["node"]
        result = recipe(name, "component", model, {"blueprint": bp, "component": name,
            "template": info["template"], "owner_blueprint": info["owner"],
            "override_blueprints": applied, "model_type": info["type"]})
        result.update(anim_blueprint=_package(p.get("AnimClass")),
                      parent_component=node.get("ParentComponentOrVariableName"),
                      socket=node.get("AttachToName"), transform=_transform(p),
                      default_visible=p.get("bVisible", True) and not p.get("bHiddenInGame", False))
        if info["type"] == "StaticMeshComponent":
            result["diagnostics"].append("StaticMesh component requires static-mesh export support")
        if p.get("bHideLogicallyOnBeginPlay"):
            result["diagnostics"].append("Logical begin-play hide is authored separately from visibility")
        result["source"]["animation_data"] = p.get("AnimationData")
        recipes.append(result)

    # Mesh skeletons and ABPs are discovered in stages as metadata becomes available.
    if not skeleton and primary:
        main_rows = load(primary) or []
        main_mesh = next((x for x in main_rows if x.get("Type") == "SkeletalMesh"), {})
        skeleton = _package(_props(main_mesh).get("Skeleton"))
    skeleton_rows = load(skeleton) if skeleton else None
    sockets = {}
    if primary and not skeleton:
        diagnostics.append("Parent skeleton not yet known")
    if skeleton_rows:
        sockets = {_props(r).get("SocketName"): _props(r) for r in skeleton_rows
                   if r.get("Type") == "SkeletalMeshSocket"}
        bones = {bone.get("Name") for row in skeleton_rows if row.get("Type") == "Skeleton"
                 for bone in (row.get("ReferenceSkeleton") or {}).get("FinalRefBoneInfo", [])}
        for name in bones - sockets.keys():
            if name:
                # The socket is the bone itself; its pose is already present in the rig.
                sockets[name] = {"BoneName": name}
        for r in recipes:
            socket = sockets.get(r["socket"])
            if socket:
                r["socket_transform"] = {"bone": socket.get("BoneName"), **_transform(socket),
                                         "source": skeleton}
            elif r["socket"]:
                r["diagnostics"].append("Socket transform not found in exported parent skeleton")

    # Resolve sequences from the explicit input set and montage segments only.
    queue = list(sources.get("animation_resources") or sources.get("animations") or [])
    clips, montages, contexts, seen = set(), set(), {}, set()
    while queue:
        package = _package(queue.pop(0))
        if not package or package in seen:
            continue
        seen.add(package)
        data = load(package)
        if not data:
            continue
        for row in data:
            p = _props(row)
            if row.get("Type") == "AnimSequence":
                clips.add(package)
            if row.get("Type") != "AnimMontage":
                continue
            montages.add(package)
            for track in p.get("SlotAnimTracks", []):
                for seg in track.get("AnimTrack", {}).get("AnimSegments", []):
                    clip = _package(seg.get("AnimReference"))
                    if not clip:
                        continue
                    contexts.setdefault(clip, []).append({"montage": package,
                        "slot": track.get("SlotName"), "segment_start": seg.get("StartPos"),
                        "source_start": seg.get("AnimStartTime"), "source_end": seg.get("AnimEndTime"),
                        "play_rate": seg.get("AnimPlayRate"), "loop_count": seg.get("LoopingCount")})
                    queue.append(clip)

    for r in recipes:
        model_rows = load(r["model"]) or []
        mesh = next((x for x in model_rows if x.get("Type") == "SkeletalMesh"), {})
        child_skeleton = _package(_props(mesh).get("Skeleton"))
        if child_skeleton:
            r["source"]["skeleton"] = child_skeleton
            load(child_skeleton)
        abp = r["anim_blueprint"]
        abp_rows = load(abp) if abp else None
        if not abp_rows:
            continue
        for row in abp_rows:
            if not str(row.get("Name", "")).startswith("Default__"):
                continue
            p = _props(row)
            nodes = [{"name": k, **v} for k, v in p.items()
                     if "AnimGraphNode_ExtraFollowAnims" in k and isinstance(v, dict)]
            tables = [(None, p.get("ExtraRibbonAnims") or [])]
            tables += [(x.get("Key"), (x.get("Value") or {}).get("DataTable", []))
                       for x in p.get("SecondaryExtraFollowAnims", [])]
            for table, entries in tables:
                selected = [n for n in nodes if (n.get("SecondaryExtraFollowTableName") or "None")
                            == (table or "None")]
                for entry in entries:
                    key, child = entry.get("Key"), _package(entry.get("Value"))
                    if not key or not child:
                        continue
                    matching = sorted(x for x in clips if x.rsplit("/", 1)[-1] == key)
                    r["bindings"].append({"parent_key": key,
                        "parent_clip": matching[0] if len(matching) == 1 else None,
                        "child_clip": child, "kind": "follow", "secondary_table": table,
                        "node": selected, "source": {"abp": abp, "field":
                        "SecondaryExtraFollowAnims" if table else "ExtraRibbonAnims"}})
                    if len(matching) > 1:
                        r["diagnostics"].append(f"Ambiguous parent sequence key: {key}")
                    if matching:
                        load(child)

    def cue_for(event, obj, clip, context):
        p = _props(obj)
        typ = obj.get("Type", "")
        start, duration = event.get("LinkValue"), event.get("Duration")
        return {"kind": typ, "source_clip": clip, **context, "start": start,
                "duration": duration, "end": (event.get("EndLink") or {}).get("LinkValue"),
                "properties": p, "event": event, "source": {"package": clip, "notify": obj.get("Name")}}

    effect_seen = set()
    effect_occurrences = {}

    def effect(ref, clip, context, event, notify, event_index, path=(), group_entries=()):
        package = _package(ref)
        if not package:
            return
        if package not in effect_seen and len(effect_seen) >= 256:
            raise ValueError("Effect discovery limit reached (256 packages); scene is incomplete")
        effect_seen.add(package)
        if package in path:
            diagnostics.append(f"Effect resource cycle: {package}")
            return
        if len(path) >= 32:
            diagnostics.append(f"Effect resource depth exceeded: {package}")
            return
        data = load(package) or []
        for item in data:
            p = _props(item)
            if item.get("Type") == "EffectModelGroup_C":
                for index, e in enumerate(p.get("EffectData", [])):
                    raw = e.get("Key", "")
                    if not isinstance(raw, str) or "'" not in raw:
                        diagnostics.append(f"Unsupported untyped effect group entry: {package}[{index}]")
                        continue
                    effect_type, nested = raw.split("'", 1)
                    nested = nested.split("'", 1)[0]
                    if effect_type not in ("EffectModelGroup_C", "EffectModelSkeletalMesh_C"):
                        message = f"Unsupported effect group entry type: {effect_type}"
                        if message not in diagnostics:
                            diagnostics.append(message)
                        continue
                    effect(nested, clip, context, event, notify, event_index, (*path, package),
                           (*group_entries, {"group": package, "index": index, "value": e.get("Value")}))
            if item.get("Type") != "EffectModelSkeletalMesh_C":
                continue
            model, child = _package(p.get("SkeletalMeshRef")), _package(p.get("AnimationRef"))
            if not model:
                diagnostics.append(f"Effect missing explicit mesh: {package}")
                continue
            if p.get("AnimationRef") and not child:
                diagnostics.append(f"Effect has invalid animation reference: {package}")
                continue
            group_id = ":".join(f"{entry['group']}[{entry['index']}]" for entry in group_entries)
            occurrence = (clip, event_index, notify.get("Name"), group_id, package)
            id_ = f"effect:{clip}:{event_index}:{notify.get('Name')}:{group_id}:{package}"
            notify_props = _props(notify)
            parent = None if notify_props.get("Attached") is False else "CharacterMesh0"
            transform = {"location": notify_props.get("Location"),
                         "rotation": notify_props.get("Rotation"),
                         "scale": notify_props.get("Scale")}
            # Only share an instance across distinct clips. Repeated uses inside
            # the same clip can overlap and must remain independently visible.
            r = effect_occurrences.get(occurrence)
            if r is None:
                r = next((item for item in recipes if item["kind"] == "effect"
                          and item["model"] == model and item["parent_component"] == parent
                          and item["socket"] == notify_props.get("SocketName")
                          and item["transform"] == transform
                          and item["source"].get("effect_slot") == notify_props.get("EffectSlotName")
                          and all(b.get("parent_clip") != clip for b in item["bindings"])), None)
                if r is None:
                    r = recipe(id_, "effect", model, {"blueprint": bp, "component": id_,
                        "effect": package, "effect_chain": list(path), "properties": p,
                        "model_type": "effect_spawned_skeletal_mesh", "group_entries": list(group_entries),
                        "effect_slot": notify_props.get("EffectSlotName"), "effect_instances": []})
                    r["default_visible"] = False
                    r["parent_component"] = parent
                    if parent is None:
                        r["diagnostics"].append("Effect is explicitly detached; world placement requires runtime semantics")
                    r["socket"] = notify_props.get("SocketName")
                    socket_data = sockets.get(r["socket"])
                    if socket_data:
                        r["socket_transform"] = {"bone": socket_data.get("BoneName"),
                                                 **_transform(socket_data), "source": skeleton}
                    r["transform"] = transform
                    recipes.append(r)
                    model_rows = load(model) or []
                    mesh = next((x for x in model_rows if x.get("Type") == "SkeletalMesh"), {})
                    child_skeleton = _package(_props(mesh).get("Skeleton"))
                    if child_skeleton:
                        r["source"]["skeleton"] = child_skeleton
                        load(child_skeleton)
                instance = {"effect": package, "effect_chain": list(path),
                            "group_entries": list(group_entries), "notify": notify.get("Name"),
                            "parent_clip": clip, "event_index": event_index, "properties": p}
                r["source"]["effect_instances"].append(instance)
                r["bindings"].append({"parent_key": None, "parent_clip": clip, "child_clip": child,
                    "kind": "effect", "secondary_table": None, "node": None,
                    "source": {"effect": package, "looping": p.get("Looping"),
                               "sync_event_time": notify_props.get("SyncEventTimeToEffectTime"),
                               "sync_event_time_continuous": notify_props.get("SyncEventTimeContinuous"),
                               "group_entries": list(group_entries), "notify": notify.get("Name"),
                               "event_index": event_index}})
                effect_occurrences[occurrence] = r
                if child:
                    load(child)
            r["cues"].append(cue_for(event, notify, clip, context))

    root_cues = []
    for clip in sorted(clips | montages):
        data = load(clip) or []
        for row in data:
            if row.get("Type") not in ("AnimSequence", "AnimMontage"):
                continue
            for event_index, event in enumerate(_props(row).get("Notifies", [])):
                ref = event.get("NotifyStateClass") or event.get("Notify") or {}
                try:
                    obj = data[int(ref.get("ObjectPath", "").rsplit(".", 1)[1])]
                except (IndexError, ValueError):
                    diagnostics.append(f"Unresolved notify in {clip}: {event.get('NotifyName')}")
                    continue
                typ, p = obj.get("Type", ""), _props(obj)
                for context in contexts.get(clip, [{}]):
                    if "Effect" in typ and p.get("EffectDataAssetRef"):
                        effect(p["EffectDataAssetRef"], clip, context, event, obj, event_index)
                    target = p.get("MeshName") or p.get("ComponentName") or p.get("ChildMeshName")
                    is_hide = "HideMesh" in typ
                    if not any(x in typ for x in ("SubMeshControl", "ChangeSlot", "HideMesh")):
                        continue
                    cue = cue_for(event, obj, clip, context)
                    if "SubMeshControl" in typ:
                        cue["defaults"] = {"begin_visible": p.get("开始是否可见", True),
                                           "end_visible": p.get("结束是否可见", True),
                                           "begin_delay": p.get("开始延迟时间", 0),
                                           "end_delay": p.get("结束延迟时间", 0)}
                    elif "ChangeSlot" in typ:
                        socket_data = sockets.get(p.get("SwitchToSlotName"))
                        cue["socket_transform"] = ({"bone": socket_data.get("BoneName"),
                            **_transform(socket_data), "source": skeleton} if socket_data else None)
                    elif is_hide:
                        cue["defaults"] = {"hide": p.get("Hide", True),
                                           "hide_children": p.get("HideChildren", True),
                                           "target": target or "notifying_mesh"}
                    if is_hide and not target:
                        root_cues.append(cue)
                    else:
                        for r in recipes:
                            if r["id"] == target:
                                r["cues"].append(cue)

    if not recipes and (required or diagnostics or root_cues):
        recipes.append(recipe("$discovery", "discovery", None, {"blueprint": bp, "component": None}))
    for r in recipes:
        r["cues"].extend(root_cues)
        r["required_resources"] = sorted(required)
        r["diagnostics"].extend(diagnostics)
    return recipes

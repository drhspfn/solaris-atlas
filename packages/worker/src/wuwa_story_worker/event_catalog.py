"""Event catalog read from cooked ConfigDB, with client-authored resource provenance.

Activity/GachaViewInfo offsets are verified against the 3.7 generated JS readers.
Supplementary images follow explicit ActivityId foreign keys, never filenames.
"""

import re
import struct
from pathlib import Path

from flatbuffers.table import Table

from wuwa_story_worker.map_catalog import int_list
from wuwa_story_worker.map_sources import flag, integer, open_db, strings, table, text


def texture_path(value: str) -> str | None:
    if not value.startswith("/Game/") or ".." in value.split("/"):
        return None
    return value.split(".", 1)[0]


def plain_text(value: str | None) -> str | None:
    return re.sub(r"</?(?:color|size|b|i)(?:=[^>]*)?>", "", value) if value else value


def _artwork(values, table_name, identifier):
    return [{"path": path, "role": role, "table": table_name, "record_id": str(identifier)}
            for role, value in values if (path := texture_path(value))]


def _activity_resources(config: Path, readers: Path, records: dict):
    """Read only string fields and ActivityId from the client's generated readers.

    Parse declarations as data; never execute game JavaScript. Restrict joins to
    explicit ActivityId fields so a coincidentally equal row ID cannot attach art.
    """
    databases = {}
    for path in (readers.parent / "ConfigQuery").glob("*.js"):
        source = path.read_text(encoding="utf-8-sig")
        match = re.search(r'DB="([A-Za-z0-9_]+\.db)".*?TABLE="([A-Za-z0-9_]+)"', source)
        if match:
            databases[match[2].lower()] = match[1].lower()
    schemas = {}
    for path in readers.glob("*.js"):
        source = path.read_text(encoding="utf-8-sig")
        if "get ActivityId()" not in source:
            continue
        getters = dict(re.findall(r"get (\w+)\(\)\{return this\.(\w+)\(\)\}", source))
        getters.update(re.findall(r"get (\w+)\(\)\{return [^}]*?this\.(\w+)Length\(\)", source))
        fields = {}
        for name, method in getters.items():
            match = re.search(re.escape(method) + r"\([^)]*\)\{([^}]+)\}", source)
            if not match:
                continue
            body = match[1]
            offset = re.search(r"__offset\(this\.\w+,(\d+)\)", body)
            if not offset:
                continue
            slot = (int(offset[1]) - 4) // 2
            if name == "ActivityId" and "readInt32" in body:
                fields[name] = (slot, "integer")
            elif "__string" in body:
                fields[name] = (slot, "strings" if "__vector(" in body else "text")
        if "ActivityId" in fields:
            schemas[path.stem.lower()] = fields
    seen = set()
    for database in sorted(config.glob("*.db")):
        with open_db(database) as db:
            names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for name in names:
                schema = schemas.get(name.lower())
                if databases.get(name.lower()) != database.name.lower():
                    continue
                if not schema or not re.fullmatch(r"[A-Za-z0-9_]+", name):
                    continue
                columns = {r[1].lower() for r in db.execute(f'PRAGMA table_info("{name}")')}
                if "bindata" not in columns:
                    continue
                for (blob,) in db.execute(f'SELECT BinData FROM "{name}"'):
                    row = table(blob)
                    identifier = integer(row, schema["ActivityId"][0])
                    record = records.get(str(identifier))
                    if record is None:
                        continue
                    for field, (slot, kind) in schema.items():
                        if kind == "integer":
                            continue
                        try:
                            values = strings(row, slot) if kind == "strings" else [text(row, slot)]
                        except (ValueError, IndexError, struct.error) as error:
                            raise ValueError(f"Unsupported event resource layout: {database.name}/{name}/{field}") from error
                        for value in values:
                            path = texture_path(value)
                            # Keep prefab references too, but do not try to decode
                            # a widget/3D model as a Texture2D.
                            if not path:
                                continue
                            identity = (identifier, name, field, path)
                            if identity in seen:
                                continue
                            seen.add(identity)
                            record["resources"].append({"path": path, "role": field, "table": name,
                                                        "config_file": database.name})
                            if any(part in path for part in ("/Image/", "/Atlas/")):
                                record["artwork"].append({"path": path, "role": field, "table": name})


def read_event_catalog(config: Path, readers: Path | None = None) -> dict[str, dict]:
    if readers is not None:
        for filename, fields in {"Activity": {"title": 46, "desc": 50, "bgresource": 52},
                                 "GachaViewInfo": {"showidlist": 36, "underbgtexturepath": 24}}.items():
            source = (readers / f"{filename}.js").read_text(encoding="utf-8-sig")
            for method, offset in fields.items():
                match = re.search(re.escape(method) + r"\([^)]*\)\{[^}]*?__offset\(this\.\w+,(\d+)\)", source)
                if not match or int(match[1]) != offset:
                    raise ValueError(f"Unsupported {filename}.{method} client schema; update the event reader")
    records = {}
    with open_db(config / "db_activity.db") as db:
        for identifier, blob in db.execute("SELECT Id, BinData FROM activity"):
            row = table(blob)
            artwork = _artwork([
                ("background", text(row, 24)),
                *[("tab", value) for value in strings(row, 19)],
                ("theme_icon", text(row, 28)), ("tag_icon", text(row, 29)),
                ("type_icon", text(row, 33)),
            ], "Activity", identifier)
            open_type = integer(row, 2)
            activity_type = integer(row, 1)
            records[str(identifier)] = {
                "table": "Activity", "config_file": "db_activity.db",
                "title_key": text(row, 21) or text(row, 20), "name_key": text(row, 20),
                "description_key": text(row, 23), "theme_key": text(row, 22),
                "banner_path": texture_path(text(row, 24)),
                "activity_type": activity_type, "open_type": open_type,
                "event_kind": "recurring" if activity_type in {21, 39, 77} else
                              "permanent" if open_type in {1, 2} else "limited",
                "hidden": flag(row, 42), "artwork": artwork,
                "resources": _artwork([("tab_prefab", text(row, 17)),
                                        ("permanent_tab_prefab", text(row, 18))], "Activity", identifier),
                "preview_drop_id": integer(row, 25), "permanent_drop_id": integer(row, 26),
            }
    with open_db(config / "db_gacha.db") as db:
        for identifier, blob in db.execute("SELECT Id, BinData FROM gachaviewinfo"):
            row = table(blob)
            featured = int_list(row, 16)
            # Both candidates are resolved against real localized entity IDs.
            # Weapon banners also use types 5/8/10/12/14, not just type 3.
            title_keys = ([f"RoleInfo_{featured[0]}_Name", f"WeaponConf_{featured[0]}_WeaponName"]
                          if featured else [])
            artwork = _artwork([(role, text(row, slot)) for role, slot in [
                ("content", 8), ("content_background", 9), ("background", 10),
                ("title_texture", 7), ("tab", 11), ("tab_selected", 12),
            ]], "GachaViewInfo", identifier)
            records[f"gacha:{identifier}"] = {
                "table": "GachaViewInfo", "config_file": "db_gacha.db",
                "title_key": "", "title_keys": title_keys, "summary_key": text(row, 2),
                "description_key": text(row, 3), "gacha_type": integer(row, 1),
                "banner_path": texture_path(text(row, 10)), "event_kind": "banner",
                "featured_ids": featured, "up_ids": int_list(row, 15),
                "artwork": artwork, "resources": _artwork([
                    ("weapon_prefab", text(row, 13))], "GachaViewInfo", identifier),
                "spine_prefab_key": text(row, 14),
            }
    featured_ids = {identifier for record in records.values() for identifier in record.get("featured_ids", [])}
    portraits = {}
    for filename, name, key_column, fields in [
        ("db_role.db", "roleinfo", "Id", [("featured_portrait", 21), ("quest_illustration", 20), ("featured_card", 17)]),
        ("db_weapon.db", "weaponconf", "ItemId", [("featured_weapon", 23), ("featured_weapon_small", 24)]),
    ]:
        if (config / filename).is_file():
            with open_db(config / filename) as db:
                for identifier, blob in db.execute(f"SELECT {key_column}, BinData FROM {name}"):
                    if identifier in featured_ids:
                        row = table(blob)
                        portraits[identifier] = _artwork([(role, text(row, slot)) for role, slot in fields],
                                                        name, identifier)
    for record in records.values():
        for identifier in record.get("featured_ids", []):
            record["artwork"].extend(portraits.get(identifier, []))
    # UI resources are explicitly tagged with the activity module names. This
    # includes images/prefabs absent from the generic Activity background field.
    resource_db = config / "db_ui_resource.db"
    if resource_db.is_file():
        with open_db(config / "db_activity.db") as db:
            has_types = db.execute("SELECT 1 FROM sqlite_master WHERE name='activitysplitpak'").fetchone()
            types = {integer(table(blob), 0): text(table(blob), 1)
                     for (blob,) in db.execute("SELECT BinData FROM activitysplitpak")} if has_types else {}
        activities = {}
        for record in records.values():
            module = types.get(record.get("activity_type"))
            if module:
                record["activity_module"] = module
                activities.setdefault(module, []).append(record)
        with open_db(resource_db) as db:
            for identifier, blob in db.execute("SELECT Id, BinData FROM uiresource"):
                row = table(blob)
                modules = strings(row, 4)
                values = _artwork([("ui_resource", text(row, 1)), ("pc_resource", text(row, 2))],
                                  "UiResource", identifier)
                targets = [record for module in modules for record in activities.get(module, [])]
                # Spine prefab keys are exact UiResource IDs, not guessed filenames.
                targets.extend(record for record in records.values()
                               if record.get("spine_prefab_key") == identifier)
                for record in targets:
                    for resource in values:
                        record["resources"].append(resource)
                        if any(part in resource["path"] for part in ("/Image/", "/Atlas/")):
                            record["artwork"].append(resource)
    if readers is not None:
        _activity_resources(config, readers, records)
    drops = {}
    if (config / "db_drop.db").is_file():
        with open_db(config / "db_drop.db") as db:
            for identifier, blob in db.execute("SELECT Id, BinData FROM droppackage"):
                row = table(blob)
                offset = row.Offset(12)  # DropPackage.DropPreview -> DicIntInt.
                items = []
                for index in range(row.VectorLen(offset)) if offset else ():
                    item = Table(blob, row.Indirect(row.Vector(offset) + index * 4))
                    items.append({"id": integer(item, 0), "value": integer(item, 1)})
                drops[identifier] = items
    for record in records.values():
        record["rewards"] = drops.get(record.get("preview_drop_id"), [])
    keys = {record[field] for record in records.values()
            for field in ("title_key", "description_key", "name_key", "theme_key", "summary_key")
            if record.get(field)}
    keys.update(key for record in records.values() for key in record.get("title_keys", []))
    entity_ids = {identifier for record in records.values()
                  for identifier in record.get("featured_ids", []) + record.get("up_ids", [])}
    entity_ids.update(item["id"] for record in records.values() for item in record["rewards"])
    entity_keys = {identifier: [f"ItemInfo_{identifier}_Name", f"RoleInfo_{identifier}_Name",
                               f"WeaponConf_{identifier}_WeaponName"] for identifier in entity_ids}
    if (config / "db_item.db").is_file():
        with open_db(config / "db_item.db") as db:
            for identifier, blob in db.execute("SELECT Id, BinData FROM iteminfo"):
                if identifier in entity_keys:
                    key = text(table(blob), 2)  # ItemInfo.Name may reference another row's text.
                    if key:
                        entity_keys[identifier].insert(0, key)
    keys.update(key for values in entity_keys.values() for key in values)
    translations = {}
    for directory in sorted(config.iterdir()):
        if not directory.is_dir():
            continue
        labels = {}
        for filename in ("lang_multi_text_1sthalf.db", "lang_multi_text.db"):
            path = directory / filename
            if not path.is_file():
                continue
            with open_db(path) as db:
                ordered = sorted(keys)
                for start in range(0, len(ordered), 500):
                    batch = ordered[start:start + 500]
                    if batch:
                        labels.update(db.execute(
                            f"SELECT Id, Content FROM MultiText WHERE Id IN ({','.join('?' for _ in batch)})",
                            batch,
                        ))
        if labels:
            translations[directory.name] = labels
    if "en" not in translations:
        raise ValueError("Client event catalog has no English MultiText localization")
    for record in records.values():
        candidates = record.get("title_keys") or [record["title_key"]]
        record["title_key"] = next((key for key in candidates if translations["en"].get(key)),
                                   candidates[0] if candidates else "")
        record["locales"] = {}
        for locale, labels in translations.items():
            title = plain_text(labels.get(record["title_key"]))
            if not title:
                continue
            if record["table"] == "GachaViewInfo":
                weapon = record["title_key"].startswith("WeaponConf_")
                record["convene_type"] = "Weapon Convene" if weapon else "Resonator Convene"
                title = f"{title} · {record['convene_type']}"
            description = plain_text(labels.get(record["description_key"])) or None
            record["locales"][locale] = {"title": title, "description": description}
            def entity_name(identifier, labels=labels):
                return next((plain_text(labels[key]) for key in entity_keys[identifier] if labels.get(key)), None)
            localized = record["locales"][locale]
            if record["rewards"]:
                localized["rewards"] = [{**item, "name": entity_name(item["id"])}
                                        for item in record["rewards"]]
            if record.get("featured_ids"):
                localized["featured_names"] = [value for identifier in record["featured_ids"]
                                                if (value := entity_name(identifier))]
            if record.get("up_ids"):
                localized["up_names"] = [value for identifier in record["up_ids"]
                                          if (value := entity_name(identifier))]
    return records


def client_event_record(records: dict, source_id: str) -> dict | None:
    group, _, identifier = source_id.partition(":")
    return records.get(f"gacha:{identifier}" if group in {"1", "gacha"} else identifier)

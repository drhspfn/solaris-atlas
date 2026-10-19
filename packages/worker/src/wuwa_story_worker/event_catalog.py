"""Event catalog read from cooked ConfigDB, with client-authored resource provenance.

Activity/GachaViewInfo offsets are verified against the 3.7 generated JS readers.
Supplementary images follow explicit ActivityId foreign keys, never filenames.
"""

import re
import struct
from pathlib import Path

from wuwa_story_worker.map_catalog import int_list
from wuwa_story_worker.map_sources import flag, integer, open_db, strings, table, text


def texture_path(value: str) -> str | None:
    if not value.startswith("/Game/") or ".." in value.split("/"):
        return None
    return value.split(".", 1)[0]


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
    keys = {record[field] for record in records.values()
            for field in ("title_key", "description_key", "name_key", "theme_key", "summary_key")
            if record.get(field)}
    keys.update(key for record in records.values() for key in record.get("title_keys", []))
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
            title = labels.get(record["title_key"])
            if not title:
                continue
            if record["table"] == "GachaViewInfo":
                weapon = record["title_key"].startswith("WeaponConf_")
                record["convene_type"] = "Weapon Convene" if weapon else "Resonator Convene"
                title = f"{title} · {record['convene_type']}"
            description = labels.get(record["description_key"]) or None
            if description:
                description = re.sub(r"</?(?:color|size|b|i)(?:=[^>]*)?>", "", description)
            record["locales"][locale] = {"title": title, "description": description}
    return records


def client_event_record(records: dict, source_id: str) -> dict | None:
    group, _, identifier = source_id.partition(":")
    return records.get(f"gacha:{identifier}" if group in {"1", "gacha"} else identifier)

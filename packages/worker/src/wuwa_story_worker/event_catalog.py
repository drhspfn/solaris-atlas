"""Read event presentation from the client's Activity and GachaViewInfo tables.

Field offsets verified against the exported 3.7 ConfigDB. Archive IDs are
matched exactly; filenames are never used to guess a localized title.
"""

import re
from pathlib import Path

from wuwa_story_worker.map_catalog import int_list
from wuwa_story_worker.map_sources import integer, open_db, table, text


def texture_path(value: str) -> str | None:
    if not value.startswith("/Game/") or ".." in value.split("/"):
        return None
    return value.split(".", 1)[0]


def read_event_catalog(config: Path) -> dict[str, dict]:
    records = {}
    with open_db(config / "db_activity.db") as db:
        for identifier, blob in db.execute("SELECT Id, BinData FROM activity"):
            row = table(blob)
            records[str(identifier)] = {
                "table": "Activity", "title_key": text(row, 20),
                "description_key": text(row, 23), "banner_path": texture_path(text(row, 24)),
            }
    with open_db(config / "db_gacha.db") as db:
        for identifier, blob in db.execute("SELECT Id, BinData FROM gachaviewinfo"):
            row = table(blob)
            featured = int_list(row, 16)
            weapon = integer(row, 1) == 3
            records[f"gacha:{identifier}"] = {
                "table": "GachaViewInfo", "title_key": (
                    f"WeaponConf_{featured[0]}_WeaponName" if weapon else
                    f"RoleInfo_{featured[0]}_Name"
                ) if featured else "",
                "description_key": text(row, 3),
                "banner_path": texture_path(text(row, 10)),
                "convene_type": "Weapon Convene" if weapon else "Resonator Convene",
                "featured_ids": featured,
            }
    keys = {record[field] for record in records.values()
            for field in ("title_key", "description_key") if record[field]}
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
        record["locales"] = {}
        for locale, labels in translations.items():
            title = labels.get(record["title_key"])
            if not title:
                continue
            if record.get("convene_type"):
                title = f"{title} · {record['convene_type']}"
            description = labels.get(record["description_key"]) or None
            # Game text formatting is not HTML and should not appear literally.
            if description:
                description = re.sub(r"</?(?:color|size|b|i)(?:=[^>]*)?>", "", description)
            record["locales"][locale] = {"title": title, "description": description}
    return records


def client_event_record(records: dict, source_id: str) -> dict | None:
    group, _, identifier = source_id.partition(":")
    return records.get(f"gacha:{identifier}" if group == "1" else identifier)

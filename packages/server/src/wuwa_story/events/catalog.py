"""Public event identities and independent periods from persisted source bindings."""


def is_catalog_capture(source_id, starts_at, ends_at, data):
    data = data or {}
    return bool((source_id or "").startswith("client:") and starts_at is None and ends_at is None
                and data.get("schedule_source") != "manual" and not data.get("manual_schedule"))


def catalog_rows(rows):
    families = {}
    for row in rows:
        identity = row.get("canonical_event_id") or row["id"]
        families.setdefault(identity, []).append(row)
    result = []
    for identity, sources in families.items():
        # A client catalog capture describes an event; it is not a scheduled run.
        periods = [row for row in sources if not row.get("_catalog_placeholder")
                   and row.get("occurrence_id") is not None]
        def capture_version(row, identity=identity):
            value = row.get("catalog_version") or ""
            version = tuple(int(part) for part in value.split(".")) if value and all(
                part.isdecimal() for part in value.split(".")) else ()
            return version, row.get("source_event_id", row["id"]) == identity

        captures = [row for row in sources if row.get("_catalog_placeholder") or row.get("occurrence_id") is None]
        selected = [*periods, *([max(captures, key=capture_version)] if captures else [])]
        for row in selected:
            public = {**row, "id": identity, "source_event_id": row.get("source_event_id", row["id"])}
            if row in captures:
                public["occurrence_id"] = None
                if row.get("selectable_pool_id"):
                    options = [option for option in captures
                               if option.get("selectable_pool_id") == row["selectable_pool_id"]
                               and option.get("catalog_version") == row.get("catalog_version")]
                    expected = {f"gacha:{member}" for member in row.get("selectable_pool_members", [])}
                    if expected and expected == {option.get("source_id") for option in options}:
                        public["featured_names"] = list(dict.fromkeys(
                            name for option in options for name in option.get("featured_names", [])))
                        public["title"] = "Selectable " + (row.get("convene_type") or "Convene")
            public.pop("_catalog_placeholder", None)
            result.append(public)
    return result

"""Public event identities and independent periods from persisted source bindings."""


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
        def capture_version(row):
            value = row.get("catalog_version") or ""
            return tuple(int(part) for part in value.split(".")) if value and all(
                part.isdecimal() for part in value.split(".")) else ()

        selected = periods or [max(sources, key=capture_version)]
        for row in selected:
            public = {**row, "id": identity, "source_event_id": row.get("source_event_id", row["id"])}
            if not periods:
                public["occurrence_id"] = None
            public.pop("_catalog_placeholder", None)
            result.append(public)
    return result

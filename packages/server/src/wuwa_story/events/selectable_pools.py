"""Group presentations only when an imported notice proves the client pool join."""


def proven_member_ids(notice_revision, related_ids, proof_revision, event_id=None):
    if (notice_revision != proof_revision or not isinstance(related_ids, list)
        or not 2 <= len(related_ids) <= 64
        or any(type(identity) is not int or identity <= 0 for identity in related_ids)
        or len(set(related_ids)) != len(related_ids)
        or (event_id is not None and event_id not in related_ids)):
        return []
    return related_ids


def pool_key(event):
    proof = event.get("_pool_match")
    if not isinstance(proof, dict) or proof.get("basis") != "gacha_pool":
        return None
    release, pool = proof.get("release_id"), proof.get("gacha_id")
    notice, revision = proof.get("notice_id"), proof.get("notice_revision")
    if (type(release) is not int or type(pool) is not int or type(notice) is not int
        or not isinstance(revision, str) or not revision
        or event.get("kind") != "banner" or not event.get("source_id", "").startswith("gacha:")
        or not event.get("occurrence_id") or not event.get("ends_at")
        or len(event.get("featured_names") or []) != 1):
        return None
    return release, pool, notice, revision, event.get("starts_at"), event["ends_at"]


def group_selectable_pools(events, server, locale):
    groups = {}
    for event in events:
        if key := pool_key(event):
            groups.setdefault(key, []).append(event)
    replacements, removed = {}, set()
    for group in groups.values():
        if (len(group) < 2 or len({row["id"] for row in group}) != len(group)
            or len({row["featured_names"][0] for row in group}) != len(group)):
            continue
        group.sort(key=lambda row: (row["source_id"], row["id"]))
        leader = group[0]
        members = [{"id": row["id"], "source_id": row["source_id"],
                    "occurrence_id": row["occurrence_id"], "featured_names": row["featured_names"],
                    "detail_url": f"/events/{row['id']}/detail?server={server}&locale={locale}&occurrence_id={row['occurrence_id']}"}
                   for row in group]
        replacements[id(leader)] = {**leader, "featured_names": [row["featured_names"][0] for row in group],
                                    "selectable_members": members}
        removed.update(id(row) for row in group[1:])
    return [replacements.get(id(row), row) for row in events if id(row) not in removed]


def pool_match(data):
    data = data or {}
    if data.get("schedule_source") == "manual" or data.get("manual_schedule"):
        return None
    match = data.get("notice_match")
    if not isinstance(match, dict):
        return None
    return {**match, "notice_id": data.get("notice_id"),
            "notice_revision": data.get("notice_revision")}

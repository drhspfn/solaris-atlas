"""Guard explicitly bounded migrations that can run with clients connected."""


def validate_online_search_index(target, current):
    if target != "0023_search_body_trgm" or current != ("0022_tree_index_schema_version",):
        raise RuntimeError("Online search index requires exactly revision 0022 -> 0023")


def validate_online_storage_verification(target, current):
    if target != "0029_storage_verification_noop" or current != ("0028_agent_reader_role",):
        raise RuntimeError("Online storage verification requires exactly revision 0028 -> 0029")

"""Guard the single migration allowed to run with database clients connected."""


def validate_online_search_index(target, current):
    if target != "0023_search_body_trgm" or current != ("0022_tree_index_schema_version",):
        raise RuntimeError("Online search index requires exactly revision 0022 -> 0023")

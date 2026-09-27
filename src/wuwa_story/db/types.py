from enum import StrEnum


class LocalizationStatus(StrEnum):
    RESOLVED_NONEMPTY = "resolved_nonempty"
    RESOLVED_EMPTY = "resolved_empty"
    BROKEN_REDIRECT = "broken_redirect"


class ResolutionType(StrEnum):
    EXPLICIT_CROSSWALK = "explicit_crosswalk"
    UNIQUE_EXTERNAL_REFERENCE = "unique_external_reference"
    MANUAL_VERIFIED = "manual_verified"


class GraphLayer(StrEnum):
    SOURCE = "source"
    CANONICAL = "canonical"
    SEMANTIC = "semantic"
    MANUAL = "manual"


class EdgeBasis(StrEnum):
    EXPLICIT_REFERENCE = "explicit_reference"
    EXACT_JOIN = "exact_join"
    AUTHORED_ORDER = "authored_order"
    SOURCE_ARRAY_ADJACENCY = "source_array_adjacency"
    SEMANTIC_EXTRACTION = "semantic_extraction"
    INFERENCE = "inference"
    MANUAL = "manual"

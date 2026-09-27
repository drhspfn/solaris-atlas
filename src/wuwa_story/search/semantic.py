from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SemanticSearchStatus:
    available: bool
    reason: str


def semantic_search_status(active_embedding_model: bool) -> SemanticSearchStatus:
    if active_embedding_model:
        return SemanticSearchStatus(
            False, "semantic query execution is not implemented in the foundation phase"
        )
    return SemanticSearchStatus(False, "no active embedding model is configured")

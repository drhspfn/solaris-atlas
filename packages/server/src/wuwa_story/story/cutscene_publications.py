"""Publication generation fence for cutscene media; derived reports keep their types."""

from sqlalchemy import SQLColumnExpression, case
from sqlalchemy.sql.elements import UnaryExpression

_KINDS = ('cutscene_video', 'cutscene_segment', 'cutscene_audio_bundle', 'cutscene_flow')


def publication_type(kind: str) -> str:
    return kind + '_v2' if kind in _KINDS else kind


def publication_types(kind: str) -> tuple[str, ...]:
    current = publication_type(kind)
    return (kind, current) if current != kind else (kind,)


def publication_order(column: SQLColumnExpression[str]) -> UnaryExpression[int]:
    """Sort before row recency, so a late legacy writer cannot supersede v2."""
    return case((column.in_(tuple(publication_type(kind) for kind in _KINDS)), 1), else_=0).desc()

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.db.repositories.search import lexical_search


def rows(*values):
    return [SimpleNamespace(_mapping=value) for value in values]


def match(identity=1, title="I Won't Wait Anymore", score=0.8):
    return dict(id=identity, canonical_key=f"quest:{identity}", slug=f"quest-{identity}",
                node_type="quest", alias=title, score=score)


@pytest.mark.parametrize('query', ["I Won't Wait Anymore", 'core', '昨日的星光'])
async def test_word_matches_return_without_expensive_fuzzy_search(query):
    expected = match(title=query)
    session = SimpleNamespace(execute=AsyncMock(side_effect=[[], [], rows(expected)]))
    result = await lexical_search(session, query, locale_id=1, categories=['quest'])
    assert result == [expected]
    assert session.execute.await_count == 3
    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert '@@' in str(compiled) and 'similarity(' not in str(compiled)
    assert 'search.document.locale_id =' in str(compiled)
    assert 'graph.node_type.key IN' in str(compiled)
    assert query in compiled.params.values()


async def test_typo_keeps_fuzzy_fallback_when_no_word_matches():
    expected = match(title='Denia')
    session = SimpleNamespace(execute=AsyncMock(side_effect=[[], [], [], rows(expected)]))
    assert await lexical_search(session, 'deniaa') == [expected]
    assert session.execute.await_count == 4
    assert 'similarity(' in str(session.execute.await_args.args[0])


async def test_word_results_deduplicate_before_offset_and_keep_name_sort():
    first, second = match(title='Alpha'), match(2, title='Beta')
    session = SimpleNamespace(execute=AsyncMock(side_effect=[[], [], rows(second, first, first)]))
    assert await lexical_search(session, 'word', sort_by='name', sort_order='asc',
                                offset=1, limit=1) == [second]
    assert session.execute.await_count == 3


@pytest.mark.parametrize('exact_stage', ['canonical', 'alias'])
async def test_exact_identity_and_alias_still_short_circuit(exact_stage):
    expected = match()
    answers = [rows(expected)] if exact_stage == 'canonical' else [[], rows(expected)]
    session = SimpleNamespace(execute=AsyncMock(side_effect=answers))
    assert await lexical_search(session, 'quest:1') == [expected]
    assert session.execute.await_count == len(answers)

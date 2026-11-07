import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.result import IteratorResult, SimpleResultMetaData

from wuwa_story.story.review_order import quest_review_order, review_positions


def item(state):
    return {'Flow': {'FlowListName': 'Story', 'FlowId': 2, 'StateId': state}}


def test_review_preserves_slots_not_numeric_state_order_and_omits_reused_states():
    data = [item(8), {'TidTip': 'Explore'}, item(2), item(8), item(9)]
    assert review_positions(json.dumps(data)) == {
        'Story_2_2': (2,), 'Story_2_9': (4,)}
    assert review_positions({'Flow': item(1)}) == {}
    assert review_positions(
        [{'Flow': {'FlowListName': 'Story', 'FlowId': True, 'StateId': 2}}]) == {}


@pytest.mark.asyncio
async def test_review_is_snapshot_scoped_and_excludes_unimported_states():
    def result(names, rows):
        return IteratorResult(SimpleResultMetaData(names), iter(rows))
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        result(['id', 'row', 'data'], [(41, 9, {'Data': [item(8), item(2)]})]),
        result(['key', 'id'], [('Story_2_2', 22)]),
    ]))
    review = await quest_review_order(session, 100, 21)
    assert review['positions'] == {'Story_2_2': (1,)}
    assert review['state_ids'] == [22]
    assert review['source']['release_id'] == 21
    queries = [str(call.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
               for call in session.execute.call_args_list]
    assert 'source_record.release_id = 21' in queries[0] and 'source_file.release_id = 21' in queries[0]
    assert 'node_revision.release_id = 21' in queries[1]


@pytest.mark.asyncio
async def test_conflicting_review_records_are_not_chosen_arbitrarily():
    session = SimpleNamespace(execute=AsyncMock(
        return_value=[(1, 0, {}), (2, 1, {})]))
    assert await quest_review_order(session, 100, 21) is None


@pytest.mark.asyncio
async def test_review_fallback_keeps_runtime_positions_unconfirmed(monkeypatch):
    from wuwa_story.story import tree_order
    monkeypatch.setattr(tree_order, '_authored_tree_order',
                        AsyncMock(return_value=None))
    monkeypatch.setattr(tree_order, 'quest_review_order', AsyncMock(return_value={
        'positions': {'Story_2_8': (3,), 'Story_2_2': (9,)}, 'state_ids': [8, 2],
        'source': {'release_id': 21, 'record_id': 41}}))
    monkeypatch.setattr(tree_order, 'source_parents',
                        AsyncMock(return_value=set()))
    monkeypatch.setattr(tree_order, 'media_links', AsyncMock(return_value={}))
    session = SimpleNamespace(execute=AsyncMock(side_effect=[[],
                                                             IteratorResult(SimpleResultMetaData(['id', 'key']), iter([(8, 'Story_2_8'), (2, 'Story_2_2')]))]))
    order = await tree_order.authored_order(session, 100, 21)
    assert order['ordering'] == 'review'
    assert order['state_ids'] == [8, 2]
    assert order['positions'] == {} and order['mapped_flow_states'] == []
    assert order['review_positions']['Story_2_8'] == (3,)
    assert order['review_source']['release_id'] == 21

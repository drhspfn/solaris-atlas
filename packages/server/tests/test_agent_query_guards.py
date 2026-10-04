import json

import pytest
from redis.exceptions import ConnectionError

from wuwa_story.agents.retrieval import query_vector
from wuwa_story.agents.settings import AgentSettings


class Cache:
    def __init__(self, cached=None, count=1, locked=True, fail=False):
        self.cached, self.count, self.locked, self.fail = cached, count, locked, fail
        self.reads = 0

    async def get(self, _key):
        self.reads += 1
        if self.fail:
            raise ConnectionError("test outage")
        return self.cached

    async def eval(self, *_args):
        return self.count

    async def set(self, *_args, **_kwargs):
        return self.locked


async def test_query_vectors_disabled_by_default():
    cache = Cache()
    settings = AgentSettings(_env_file=None, embedding_model="test")
    assert await query_vector(None, "why?", settings, cache, "actor") is None
    assert cache.reads == 0


@pytest.mark.parametrize("cache", [None, Cache(fail=True), Cache(count=31), Cache(locked=False)])
async def test_paid_queries_fail_closed_without_cache_or_allowance(cache):
    settings = AgentSettings(_env_file=None, embedding_model="test", public_query_embeddings=True)
    # No database/provider is available: attempting any paid work would fail this test.
    assert await query_vector(None, "why?", settings, cache, "actor") is None


async def test_cached_query_vector_needs_no_paid_request():
    settings = AgentSettings(
        _env_file=None,
        embedding_model="test",
        embedding_dimensions=3,
        public_query_embeddings=True,
    )
    assert await query_vector(None, "why?", settings, Cache(json.dumps([1, 0, 0])), "actor") == [
        1,
        0,
        0,
    ]

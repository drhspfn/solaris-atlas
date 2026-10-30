"""Bounded synthetic search probe on the disposable loopback verification PostgreSQL.

No source tables are read. TEMP tables/indexes disappear on connection close.
Run with the same interpreter as the server, then compare the printed JSON plans.
"""

import asyncio
import json
import statistics
import time

import asyncpg

TERMS = (
    "notfound-zqxv918273",
    "archive",
    "こんにちは",
    "にちは",
    "人物",
    "123456789",
    "d41d8cd98f00b204e9800998ecf8427e",
    "café",
)
MATCH = """(search_vector @@ plainto_tsquery('simple', $1)
             OR title ILIKE '%' || $1 || '%'
             OR searchable_text ILIKE '%' || $1 || '%'
             OR canonical_key = $1)"""
QUERY = f"""SELECT entity_id, title, card FROM projection_search_probe
            WHERE locale_id=1 AND snapshot_key='current' AND {MATCH}
            ORDER BY ts_rank(search_vector, plainto_tsquery('simple', $1)) DESC, entity_id LIMIT 24"""


async def measure(connection):
    measurements = {}
    for term in TERMS:
        times = []
        for _ in range(5):
            start = time.perf_counter()
            rows = await connection.fetch(QUERY, term)
            times.append((time.perf_counter() - start) * 1000)
        plan = json.loads(
            await connection.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + QUERY, term)
        )[0]
        nodes = []

        def visit(node, found=nodes):
            found.append(node["Node Type"])
            for child in node.get("Plans", []):
                visit(child, found)

        visit(plan["Plan"])
        measurements[term] = {
            "median_ms": round(statistics.median(times), 3),
            "page_rows": len(rows),
            "count": await connection.fetchval(
                "SELECT count(*) FROM projection_search_probe WHERE " + MATCH, term
            ),
            "plan_nodes": nodes,
            "execution_ms": plan["Execution Time"],
        }
    return measurements


async def main():
    # Intentionally fixed to the existing disposable local verification instance.
    connection = await asyncpg.connect(
        host="127.0.0.1",
        port=55439,
        user="postgres",
        database="public_read_models_verification",
        timeout=5,
    )
    try:
        await connection.execute("SET statement_timeout='15s'")
        await connection.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        await connection.execute("""CREATE TEMP TABLE projection_search_probe(
            entity_id bigint PRIMARY KEY, locale_id smallint NOT NULL, snapshot_key text NOT NULL,
            canonical_key text NOT NULL, title text NOT NULL, card jsonb NOT NULL,
            searchable_text text NOT NULL,
            search_vector tsvector GENERATED ALWAYS AS (
                to_tsvector('simple'::regconfig, title || ' ' || searchable_text)) STORED)""")
        await connection.execute("""INSERT INTO projection_search_probe
            SELECT i,1,'current','item:'||i,'Archive entry '||i,
                   jsonb_build_object('aliases',jsonb_build_array(CASE WHEN i=77 THEN '123456789' ELSE 'Alias '||i END)),
                   'Authored localized archive text '||i||' '||repeat('Shared public lore dialogue and item description. ',20)||
                   CASE WHEN i%100=0 THEN ' 人物の経歴 こんにちは '
                        WHEN i%100=1 THEN ' 漂泊者追寻星光的故事 '
                        WHEN i%100=2 THEN ' café étoile '
                        ELSE '' END ||
                   CASE WHEN i=77 THEN ' 123456789 ' WHEN i=88 THEN ' d41d8cd98f00b204e9800998ecf8427e ' ELSE '' END
            FROM generate_series(1,50000) i""")
        await connection.execute("CREATE INDEX ON projection_search_probe USING gin(search_vector)")
        await connection.execute(
            "CREATE INDEX ON projection_search_probe(locale_id,snapshot_key,canonical_key)"
        )
        await connection.execute("ANALYZE projection_search_probe")
        output = {
            "rows": 50000,
            "host": "loopback verification PostgreSQL only",
            "body_bytes": await connection.fetchval(
                "SELECT avg(octet_length(searchable_text)) FROM projection_search_probe"
            ),
            "vector_only": await measure(connection),
        }
        await connection.execute(
            "CREATE INDEX ON projection_search_probe USING gin(title gin_trgm_ops)"
        )
        await connection.execute(
            "CREATE INDEX ON projection_search_probe USING gin(searchable_text gin_trgm_ops)"
        )
        await connection.execute("ANALYZE projection_search_probe")
        output["vector_and_trigram"] = await measure(connection)
        assert {term: row["count"] for term, row in output["vector_only"].items()} == {
            term: row["count"] for term, row in output["vector_and_trigram"].items()
        }
        print(json.dumps(output, ensure_ascii=True, indent=2, default=float))
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())

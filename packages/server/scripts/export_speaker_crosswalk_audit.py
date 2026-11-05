"""Read retained identity sources/evidence through SSH into an offline audit file.

Uses the existing solaris-atlas host and a READ ONLY database transaction. No
game extraction, imports, production files, graph changes or refreshes occur.
"""

import argparse
import json
import subprocess
from pathlib import Path

SQL = """
BEGIN READ ONLY;
SET LOCAL statement_timeout='90s';
SELECT json_build_object('release_id',g.id,'game_version',g.game_version,'source_rows',
 (SELECT json_agg(x) FROM (SELECT r.release_id,r.row_index,f.logical_source_path AS file,r.data
 FROM raw.source_record r JOIN raw.source_file f ON f.id=r.source_file_id
 WHERE r.release_id=g.id AND f.release_id=g.id AND f.logical_source_path IN
 ('BinData/role/roleinfo.json','BinData/speaker/speaker.json','BinData/main_role_change/mainroleconfig.json')
 ORDER BY f.logical_source_path,r.row_index LIMIT 20001) x),
 'existing_crosswalks',(SELECT coalesce(json_agg(y),'[]'::json) FROM (
 SELECT s.game_speaker_id AS speaker,n.canonical_key AS character,
 (SELECT json_agg(ee.explanation) FROM graph.edge_evidence ee WHERE ee.edge_id=e.id AND ee.release_id=g.id) AS explanations
 FROM graph.edge e JOIN ontology.relation_type t ON t.id=e.relation_type_id
 JOIN core.speaker s ON s.node_id=e.from_node_id JOIN graph.node n ON n.id=e.to_node_id
 WHERE t.key='references_character' AND e.layer='source' AND e.basis='exact_join'
 AND EXISTS(SELECT 1 FROM graph.edge_evidence ee WHERE ee.edge_id=e.id AND ee.release_id=g.id)) y))::jsonb::text
FROM ops.game_release g WHERE g.id IN ({releases}) ORDER BY g.sequence;
COMMIT;
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-id', type=int, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    releases = set(args.release_id)
    if not 1 <= len(releases) <= 21 or any(identity <= 0 for identity in releases):
        parser.error('Select 1..21 positive imported release IDs')
    sql = SQL.format(releases=','.join(map(str, sorted(releases))))
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
               '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3', 'solaris-atlas',
               'docker exec -i solaris-atlas-postgres-1 psql -U wuwa -d wuwa_story -v ON_ERROR_STOP=1 -X -q -A -t']
    seen = set()
    with args.output.open('x', encoding='utf-8') as output, subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8') as process:
        try:
            process.stdin.write(sql)
            process.stdin.close()
            while line := process.stdout.readline(16 * 1024 * 1024 + 1):
                if not line.startswith('{'):
                    continue
                if len(line) > 16 * 1024 * 1024:
                    raise ValueError('One snapshot exceeds the 16 MiB audit budget')
                snapshot = json.loads(line)
                identity = snapshot['release_id']
                if identity not in releases or identity in seen:
                    raise ValueError('Unexpected or duplicate snapshot')
                rows = snapshot.get('source_rows') or []
                if not 1 <= len(rows) <= 20000:
                    raise ValueError(f'Release {identity} is empty or exceeds the row budget')
                seen.add(identity)
                output.write(line)
                output.flush()
                print(json.dumps({'release_id': identity, 'source_rows': len(rows)}), flush=True)
            if process.wait(timeout=120):
                raise RuntimeError(process.stderr.read())
            if seen != releases:
                raise ValueError(f'Imported release IDs absent: {sorted(releases - seen)}')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


if __name__ == '__main__':
    main()

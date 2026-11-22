"""Run a reviewed online migration using an installed API image and direct DB URL."""
import argparse
import os
import re
import subprocess
from pathlib import Path

CONTAINER_CODE = """import os
import sys
url = os.environ.get('MIGRATION_DATABASE_URL')
if not url:
    raise SystemExit('MIGRATION_DATABASE_URL is required')
os.environ['DATABASE_URL'] = url
os.environ.pop('WUWA_DB_TRANSACTION_POOLING', None)
os.execvp('alembic', ['alembic', 'upgrade', sys.argv[1]])
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True, help='Reviewed Alembic revision; head is not accepted')
    parser.add_argument('--sha', required=True, help='Full immutable API image SHA already present on the host')
    parser.add_argument('--owner', default='drhspfn')
    parser.add_argument('--app-dir', type=Path, default=Path.home() / 'solaris-atlas')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9]{4}_[a-z0-9_]+', args.revision):
        parser.error('Specify a concrete revision, for example 0043_quest_media_indexes')
    if not re.fullmatch(r'[a-f0-9]{40}', args.sha) or not re.fullmatch(r'[a-z0-9_-]+', args.owner):
        parser.error('Specify a full immutable image SHA')
    if not args.apply:
        print('Plan only: apply reviewed online revision', args.revision)
        return
    app = args.app_dir
    import fcntl
    command = ['docker', 'compose', '--project-name', 'solaris-atlas', '--env-file',
               str(app / 'env/compose.runtime.env'), '-f', str(app / 'compose.yml'),
               'run', '--rm', '-T', '--no-deps', '--pull', 'never', '--entrypoint', 'python',
               '-w', '/app/packages/server', 'api', '-c', CONTAINER_CODE, args.revision]
    with (app / '.migration-deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Migration errors can include connection URLs. Never forward raw output.
        result = subprocess.run(command, env={**os.environ, 'IMAGE_TAG': args.sha, 'GHCR_OWNER': args.owner},
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True, pass_fds=(lock.fileno(),), check=False)
    if result.returncode:
        raise SystemExit('Migration failed; inspect database state before retrying')
    print('Migration completed:', args.revision)


if __name__ == '__main__':
    main()

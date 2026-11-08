"""Host-only, journaled API + web release preserving workers and infrastructure.

Promotes the guarded API-only rollout: immutable images, captured identities,
durable command intent and children retaining the deployment lock. Uses the
installed compose/env; configuration, migrations and infrastructure are separate
maintenance operations. Default is a read-only plan; --apply performs changes.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

WORKERS = ('snapshot-worker', 'asset-worker', 'media-worker', 'projection-worker',
           'image-worker', 'audio-worker', 'video-worker', 'model-worker',
           'story-agent', 'cutscene-vision', 'cutscene-assembly-worker', 'visual-memory-worker')
SCHEMA_CHECK = """
import asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from wuwa_story.config.settings import get_settings
import os
async def check():
    engine = create_async_engine(os.environ.get("MIGRATION_DATABASE_URL") or get_settings().database_url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text('SET TRANSACTION READ ONLY'))
            await connection.execute(text("SET LOCAL statement_timeout='10s'"))
            actual = await connection.run_sync(lambda db: MigrationContext.configure(db).get_current_heads())
            expected = ScriptDirectory.from_config(Config('alembic.ini')).get_heads()
            if not expected or set(actual) != set(expected):
                raise SystemExit('Schema migration required; automatic app release refused. Use explicit maintenance before retrying.')
    finally:
        await engine.dispose()
try:
    asyncio.run(asyncio.wait_for(check(), 30))
except Exception as error:
    raise SystemExit('Read-only schema compatibility check failed: '+type(error).__name__) from None
print('schema_current')
"""


def save(path, state, phase):
    state['phase'] = phase
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(state, stream, indent=2)
        stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


class Docker:
    def __init__(self, app, owner, sha):
        self.owner, self.sha = owner, sha
        self.env = dict(os.environ, GHCR_OWNER=owner, IMAGE_TAG=sha,
            WORKER_IMAGE=f'ghcr.io/{owner}/solaris-atlas-worker:{sha}',
            MODEL_WORKER_IMAGE=f'ghcr.io/{owner}/solaris-atlas-models:{sha}')
        self.compose = ['docker', 'compose', '--project-name', 'solaris-atlas', '--env-file',
            str(app/'env/compose.runtime.env'), '-f', str(app/'compose.yml'), '--profile', 'workers']
        self.base_compose = list(self.compose)

    def read(self, command):
        return subprocess.check_output(command, text=True, env=self.env, timeout=30).strip()

    def configuration_hash(self):
        # Resolve env files too, but never persist/log their secret contents.
        resolved = json.loads(self.read(self.base_compose+['config', '--format', 'json']))
        return hashlib.sha256(json.dumps(resolved, sort_keys=True).encode()).hexdigest()

    def rows(self):
        ids = self.read(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=solaris-atlas']).split()
        if not ids:raise RuntimeError('Installed production project is missing; bootstrap separately')
        fmt = '{"Id":{{json .Id}},"Image":{{json .Image}},"State":{{json .State}},"RestartCount":{{.RestartCount}},"Service":{{json (index .Config.Labels "com.docker.compose.service")}},"Oneoff":{{json (index .Config.Labels "com.docker.compose.oneoff")}}}'
        return [r for r in (json.loads(line) for line in self.read(['docker', 'inspect', '--format', fmt, *ids]).splitlines())
                if str(r['Oneoff']).lower() != 'true']

    def image(self, kind):
        ref = f'ghcr.io/{self.owner}/solaris-atlas-{kind}:{self.sha}'
        for attempt in range(3):
            try:
                subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', ref], env=self.env,
                    stdout=sys.stderr, check=True, timeout=180)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if attempt == 2:raise
                time.sleep(2**attempt)
        image = json.loads(self.read(['docker', 'image', 'inspect', ref]))[0]
        if (image['Os'] != 'linux' or image['Architecture'] != 'amd64'
                or not re.fullmatch(r'sha256:[a-f0-9]{64}', image['Id'])):
            raise RuntimeError('Wrong image platform/immutable ID')
        return image['Id']


@contextmanager
def authentication(docker, token):
    if not token:
        yield
        return
    prior = docker.env.get('DOCKER_CONFIG')
    with tempfile.TemporaryDirectory(prefix='solaris-deploy-auth-') as directory:
        docker.env['DOCKER_CONFIG'] = directory
        try:
            subprocess.run(['docker', 'login', 'ghcr.io', '--username', docker.owner, '--password-stdin'],
                input=token, text=True, env=docker.env, stdout=subprocess.DEVNULL, check=True)
            yield
        finally:
            if prior is None:docker.env.pop('DOCKER_CONFIG', None)
            else:docker.env['DOCKER_CONFIG'] = prior


def preserved(rows, captured, excluded):
    actual = {r['Id']: r for r in rows if r['Service'] not in excluded}
    expected = {r['Id']: r for r in captured if r['Service'] not in excluded}
    if actual.keys() != expected.keys():raise RuntimeError('Untargeted container inventory changed')
    for identity, prior in expected.items():
        row = actual[identity]
        if (row['Image'] != prior['Image'] or row['RestartCount'] != prior['RestartCount']
                or any(row['State'][k] != prior['State'][k] for k in ('Pid', 'Running', 'OOMKilled'))):
            raise RuntimeError('Untargeted container image/process changed')


def command(docker, args, path, state, phase, lock_fd):
    if docker.configuration_hash() != state['config_hash']:
        raise RuntimeError('Installed configuration changed; release refused')
    state['command'] = args
    save(path, state, phase)  # Never retry a mutation without reconciling this intent.
    with path.with_suffix('.log').open('a', encoding='utf-8') as log:
        child = subprocess.Popen(args, env=docker.env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, pass_fds=(lock_fd,))
        state['command_pid'] = child.pid;save(path, state, phase)
        status = child.wait()  # No forced timeout, including native-job drain.
    if status:raise RuntimeError(f'{phase} exited{status}; inspect journal, do not blindly replay')


def verified(docker, service, image, count=1):
    rows = [r for r in docker.rows() if r['Service'] == service]
    if len(rows) != count or any(r['Image'] != image or not r['State']['Running']
            or r['State']['OOMKilled'] or r['State']['Restarting'] for r in rows):
        raise RuntimeError(f'{service} replacement identity/state mismatch')
    if service == 'api' and rows[0]['State'].get('Health', {}).get('Status') != 'healthy':
        raise RuntimeError('API not healthy')
    if service == 'web':
        docker.read(['docker', 'exec', rows[0]['Id'], 'node', '-e',
            "fetch('http://127.0.0.1:3000/',{redirect:'manual',signal:AbortSignal.timeout(5000)}).then(r=>{if(r.status<200||r.status>=400)process.exit(1)}).catch(()=>process.exit(1))"])
    return rows


def ready(docker, service, image):
    deadline = time.monotonic()+120
    while True:
        try:return verified(docker, service, image)
        except (RuntimeError, subprocess.CalledProcessError):
            if time.monotonic() >= deadline:raise
            time.sleep(3)


def app_release(docker, path, state, lock_fd):
    if docker.configuration_hash() != state['config_hash']:
        raise RuntimeError('Installed configuration changed; release refused')
    preserved(docker.rows(), state['before'], {'api', 'web'})
    if state['phase'] == 'prepared':
        # New image's Alembic heads must already match DB. No DDL/client stops.
        command(docker, docker.compose+['run', '--rm', '-T', '--no-deps', '--entrypoint', 'python',
            '-w', '/app/packages/server', 'api', '-c', SCHEMA_CHECK], path, state, 'schema_check', lock_fd)
        save(path, state, 'schema_ready')
    if state['phase'] == 'schema_check':
        raise RuntimeError('Interrupted schema check; inspect receipt before retry')
    for service, previous in [('api', 'schema_ready'), ('web', 'api_ready')]:
        if state['phase'] == previous:
            command(docker, docker.compose+['up', '-d', '--no-deps', '--pull', 'never', service],
                    path, state, service+'_started', lock_fd)
        if state['phase'] == service+'_started':
            ready(docker, service, state['images'][service])
            preserved(docker.rows(), state['before'], {'api', 'web'})
            save(path, state, service+'_ready')
    if state['phase'] not in {'web_ready', 'done'}:raise RuntimeError('Unknown app checkpoint')
    for service in ('api', 'web'):verified(docker, service, state['images'][service])
    preserved(docker.rows(), state['before'], {'api', 'web'})
    state['workers_deferred'] = [{'service': r['Service'], 'id': r['Id'], 'image': r['Image']}
        for r in state['before'] if r['Service'] in WORKERS]
    save(path, state, 'done')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--owner', default=os.environ.get('GHCR_OWNER', 'drhspfn'))
    parser.add_argument('--app-dir', type=Path, default=Path.home()/'solaris-atlas')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-f0-9]{40}', args.sha) or not re.fullmatch(r'[a-z0-9_-]+', args.owner):
        parser.error('Require immutable full40character SHA and valid lowercase owner')
    if not args.apply:
        print(json.dumps({'writes': False, 'component': 'app', 'sha': args.sha, 'workers_deferred': True}));return
    import fcntl  # Only the mutating host path requires Linux.
    docker = Docker(args.app_dir, args.owner, args.sha)
    with (args.app_dir/'.migration-deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        token = sys.stdin.readline().strip() if not sys.stdin.isatty() else ''
        with authentication(docker, token):
            directory = args.app_dir/'.deployments'/f'app-{args.sha}'
            directory.mkdir(parents=True, exist_ok=True);path = directory/'journal.json'
            if path.exists():
                state = json.loads(path.read_text())
                if state['sha'] != args.sha or state['component'] != 'app':raise RuntimeError('Journal identity changed')
            else:
                before = docker.rows()
                config_hash = docker.configuration_hash()
                images = {kind: docker.image(kind) for kind in ('api', 'web')}
                state = {'component': 'app', 'sha': args.sha, 'before': before,
                    'images': images, 'config_hash': config_hash}
                save(path, state, 'prepared')
            override = directory/'images.compose.json'
            override.write_text(json.dumps({'services': {s: {'image': i} for s, i in state['images'].items()}}))
            docker.compose += ['-f', str(override)]
            app_release(docker, path, state, lock.fileno())
            print(json.dumps({'phase': state['phase'], 'sha': args.sha, 'journal': str(path),
                'workers_deferred': state.get('workers_deferred', []), 'worker_workflow': 'Deploy selected services (explicit maintenance)'}))


if __name__ == '__main__':
    main()

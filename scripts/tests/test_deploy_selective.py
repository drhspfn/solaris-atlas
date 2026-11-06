"""Fake Docker only: no production commands, network, or database writes."""
import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('selective', ROOT/'scripts/deploy-selective.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def row(service):
    return {'Id': service+'-old', 'Image': 'old', 'Service': service, 'RestartCount': 0,
            'State': {'Running': True, 'Pid': 10, 'OOMKilled': False, 'Restarting': False,
                      'Health': {'Status': 'healthy'}}}


class FakeDocker:
    def __init__(self):
        self.compose = ['docker', 'compose', '-f', 'installed-compose']
        self.containers = [row(s) for s in ('api', 'web', 'audio-worker', 'projection-worker', 'pgbouncer')]
        self.config = 'installed-config'
        self.actions = []
        self.fail_schema = self.fail_api = self.change_worker = False

    def configuration_hash(self):return self.config
    def rows(self):return copy.deepcopy(self.containers)
    def read(self, command):return 'web-ready'

    def mutate(self, docker, command, path, state, phase, lock):
        self.actions.append(command);state['phase'] = phase
        if 'run' in command:
            if self.fail_schema:raise RuntimeError('Schema migration required')
            return
        service = command[-1]
        obj = next(r for r in self.containers if r['Service'] == service)
        obj.update(Id=service+'-new', Image=state['images'][service])
        if service == 'api' and self.fail_api:obj['State']['Health']['Status'] = 'unhealthy'
        if self.change_worker:self.containers[2]['State']['Pid'] += 1


class AppReleaseTests(unittest.TestCase):
    def run_release(self, docker, phase='prepared'):
        state = {'phase': phase, 'config_hash': 'installed-config', 'before': docker.rows(),
                 'images': {'api': 'api-pinned', 'web': 'web-pinned'}}
        def save(path, state, phase):state['phase'] = phase
        with patch.object(m, 'command', docker.mutate), patch.object(m, 'save', save), \
                patch.object(m, 'ready', m.verified):
            m.app_release(docker, Path('unused-journal'), state, 1)
        return state

    def test_app_changes_only_api_then_web_and_defers_workers(self):
        docker = FakeDocker();before = docker.rows()
        result = self.run_release(docker)
        self.assertEqual(result['phase'], 'done')
        self.assertEqual([c[-1] for c in docker.actions[1:]], ['api', 'web'])
        self.assertIn('/app/packages/server', docker.actions[0])
        self.assertIn('--entrypoint', docker.actions[0])
        for command in docker.actions:
            self.assertIn('--no-deps', command)
            self.assertFalse({'stop', 'pgbouncer', 'audio-worker', 'projection-worker'} & set(command))
        m.preserved(docker.rows(), before, {'api', 'web'})
        self.assertEqual({r['service'] for r in result['workers_deferred']}, {'audio-worker', 'projection-worker'})
        self.assertFalse(hasattr(m, 'worker_release'))

    def test_schema_mismatch_prevents_api_and_web_mutations(self):
        docker = FakeDocker();docker.fail_schema = True
        with self.assertRaisesRegex(RuntimeError, 'Schema migration required'):self.run_release(docker)
        self.assertEqual(len(docker.actions), 1)
        self.assertTrue(all(r['Image'] == 'old' for r in docker.rows()))

    def test_unhealthy_api_prevents_web_release(self):
        docker = FakeDocker();docker.fail_api = True
        with self.assertRaisesRegex(RuntimeError, 'healthy'):self.run_release(docker)
        self.assertEqual(next(r for r in docker.rows() if r['Service'] == 'web')['Image'], 'old')

    def test_config_drift_fails_before_any_command(self):
        docker = FakeDocker();docker.config = 'changed'
        with self.assertRaisesRegex(RuntimeError, 'configuration changed'):self.run_release(docker)
        self.assertEqual(docker.actions, [])

    def test_untargeted_worker_restart_is_not_hidden(self):
        docker = FakeDocker();docker.change_worker = True
        with self.assertRaisesRegex(RuntimeError, 'Untargeted'):self.run_release(docker)
        self.assertFalse(any(c[-1] == 'web' for c in docker.actions))

    def test_uncertain_api_checkpoint_verifies_without_replaying_api_up(self):
        docker = FakeDocker();docker.containers[0]['Image'] = 'api-pinned'
        self.run_release(docker, 'api_started')
        self.assertEqual([c[-1] for c in docker.actions], ['web'])
        docker = FakeDocker()
        with self.assertRaisesRegex(RuntimeError, 'identity'):self.run_release(docker, 'api_started')
        self.assertEqual(docker.actions, [])


class SchemaTests(unittest.TestCase):
    def check(self, actual, expected):
        sql = [];dispose = AsyncMock()
        class Connection:
            async def __aenter__(self):return self
            async def __aexit__(self, *args):return False
            async def execute(self, statement):sql.append(statement)
            async def run_sync(self, function):return function(None)
        engine = SimpleNamespace(connect=Connection, dispose=dispose)
        modules = {
            'alembic.config': SimpleNamespace(Config=lambda path: self.assertEqual(path, 'alembic.ini')),
            'alembic.script': SimpleNamespace(ScriptDirectory=SimpleNamespace(from_config=lambda _: SimpleNamespace(get_heads=lambda: expected))),
            'alembic.migration': SimpleNamespace(MigrationContext=SimpleNamespace(configure=lambda _: SimpleNamespace(get_current_heads=lambda: actual))),
            'sqlalchemy': SimpleNamespace(text=lambda query: query),
            'sqlalchemy.ext.asyncio': SimpleNamespace(create_async_engine=lambda _: engine),
            'wuwa_story.config.settings': SimpleNamespace(get_settings=lambda: SimpleNamespace(database_url='unused')),
        }
        try:
            # Execute the exact checked-in schema script against fake DB modules.
            with patch.dict(sys.modules, modules), redirect_stdout(io.StringIO()):
                exec(m.SCHEMA_CHECK, {})  # noqa: S102
        finally:
            dispose.assert_awaited_once()
            self.assertEqual(sql, ['SET TRANSACTION READ ONLY', "SET LOCAL statement_timeout='10s'"])

    def test_current_schema_supported(self):self.check(['revision1'], ['revision1'])

    def test_pending_future_migration_and_unknown_db_head_refuse(self):
        for actual, expected in [(['old'], ['new']), ([], ['new']), (['future'], ['old']), ([], [])]:
            with self.subTest(actual=actual), self.assertRaisesRegex(SystemExit, 'Schema migration required'):
                self.check(actual, expected)


class AuthenticationTests(unittest.TestCase):
    def test_token_auth_is_temporary_and_never_logs_out_host(self):
        with tempfile.TemporaryDirectory() as root:
            original = Path(root)/'config.json';original.write_text('existing-host-auth')
            docker = SimpleNamespace(owner='drhspfn', env={'DOCKER_CONFIG': root})
            seen = []
            def login(command, **kwargs):
                config = kwargs['env']['DOCKER_CONFIG'];seen.append(config)
                self.assertNotEqual(config, root)
                self.assertEqual(command[:2], ['docker', 'login'])
                self.assertTrue(Path(config).is_dir())
            with patch.object(m.subprocess, 'run', side_effect=login) as run:
                with m.authentication(docker, 'test-token'):
                    self.assertEqual(docker.env['DOCKER_CONFIG'], seen[0])
                self.assertEqual(run.call_count, 1)
            self.assertEqual(original.read_text(), 'existing-host-auth')
            self.assertEqual(docker.env['DOCKER_CONFIG'], root)
            self.assertFalse(Path(seen[0]).exists())

    def test_failed_login_also_preserves_host_credentials(self):
        docker = SimpleNamespace(owner='drhspfn', env={'DOCKER_CONFIG': 'host-config'})
        directories = []
        def fail(command, **kwargs):
            directories.append(kwargs['env']['DOCKER_CONFIG'])
            raise m.subprocess.CalledProcessError(1, command)
        with patch.object(m.subprocess, 'run', side_effect=fail), \
                self.assertRaises(m.subprocess.CalledProcessError), m.authentication(docker, 'test-token'):
            self.fail('must not yield on auth failure')
        self.assertEqual(docker.env['DOCKER_CONFIG'], 'host-config')
        self.assertFalse(Path(directories[0]).exists())


class ImmutableTests(unittest.TestCase):
    def test_cli_refuses_mutable_tags_before_docker(self):
        with patch.object(sys, 'argv', ['deploy-selective.py', '--sha', 'main']), \
                patch.object(m, 'Docker') as docker, redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit):
            m.main()
        docker.assert_not_called()

    def test_resolves_full_sha_to_digest_with_isolated_docker_environment(self):
        docker = m.Docker(Path('installed'), 'drhspfn', 'a'*40)
        docker.env['DOCKER_CONFIG'] = 'isolated-auth'
        obj = {'Id': 'sha256:'+'b'*64, 'Os': 'linux', 'Architecture': 'amd64'}
        with patch.object(m.subprocess, 'run') as run, patch.object(docker, 'read', return_value=json.dumps([obj])):
            self.assertEqual(docker.image('api'), obj['Id'])
        self.assertTrue(run.call_args.args[0][-1].endswith(':'+'a'*40))
        self.assertEqual(run.call_args.kwargs['env']['DOCKER_CONFIG'], 'isolated-auth')

    def test_config_fingerprint_ignores_own_image_override_but_detects_installed_env(self):
        docker = m.Docker(Path('installed'), 'drhspfn', 'a'*40)
        docker.compose += ['-f', 'pinned-images.compose.json']
        with patch.object(docker, 'read', return_value='{"services":{"api":{"environment":{"VALUE":"one"}}}}') as read:
            first = docker.configuration_hash()
            self.assertNotIn('pinned-images.compose.json', read.call_args.args[0])
        with patch.object(docker, 'read', return_value='{"services":{"api":{"environment":{"VALUE":"two"}}}}'):
            self.assertNotEqual(first, docker.configuration_hash())


class WorkflowTests(unittest.TestCase):
    def test_automatic_release_pins_sha_preserves_models_build_and_legacy_all(self):
        images = (ROOT/'.github/workflows/images.yml').read_text()
        deploy = images.split('  deploy:\n', 1)[1]
        self.assertIn('production-selective.yml', deploy)
        self.assertIn('image_sha: ${{ github.sha }}', deploy)
        self.assertNotIn('image_tag: main', deploy)
        self.assertNotIn('component: all', deploy)
        self.assertIn('  model-tools:', images)
        legacy = (ROOT/'.github/workflows/deploy.yml').read_text()
        self.assertIn('cutscene-vision, cutscene-assembly-worker, all]', legacy)
        self.assertIn('production-deploy.yml', legacy)

    def test_app_workflow_never_copies_live_config_and_reports_deferred_updates(self):
        workflow = (ROOT/'.github/workflows/production-selective.yml').read_text()
        self.assertIn('scp scripts/deploy-selective.py', workflow)
        self.assertNotIn('scp infrastructure/', workflow)
        self.assertNotIn('/*.env', workflow)
        self.assertIn('Workers/models are intentionally deferred', workflow)
        self.assertIn('production-deploy', workflow)
        self.assertIn('cancel-in-progress: false', workflow)
        self.assertIn('set -euo pipefail', workflow)


if __name__ == '__main__':unittest.main()

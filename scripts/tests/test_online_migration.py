import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('online_migration', Path(__file__).resolve().parents[1] / 'run-online-migration.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class OnlineMigrationTests(unittest.TestCase):
    def test_requires_explicit_revision_and_sha(self):
        with patch.object(sys, 'argv', ['migration', '--revision', 'head', '--sha', 'a' * 40]), self.assertRaises(SystemExit):
            m.main()

    def test_apply_uses_oneoff_without_printing_environment(self):
        with tempfile.TemporaryDirectory() as root:
            args = ['migration', '--revision', '0043_quest_media_indexes', '--sha', 'a' * 40, '--app-dir', root, '--apply']
            fake_lock = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None)
            with patch.object(sys, 'argv', args), patch.dict(sys.modules, {'fcntl': fake_lock}), \
                    patch.object(m.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run, \
                    redirect_stdout(io.StringIO()):
                m.main()
            command = run.call_args.args[0]
            self.assertIn('--no-deps', command)
            self.assertIn('--pull', command)
            self.assertNotIn('sh', command)
            self.assertEqual(run.call_args.kwargs['stdout'], m.subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs['stderr'], m.subprocess.DEVNULL)
            self.assertTrue(run.call_args.kwargs['pass_fds'])
            self.assertEqual(run.call_args.kwargs['env']['IMAGE_TAG'], 'a' * 40)

    def test_container_code_never_prints_credentials(self):
        self.assertNotIn('print(', m.CONTAINER_CODE)
        self.assertNotIn('export', m.CONTAINER_CODE)
        self.assertIn("os.environ['DATABASE_URL'] = url", m.CONTAINER_CODE)


if __name__ == '__main__':
    unittest.main()

"""Exercise deployment orchestration with Docker commands recorded, not executed."""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GIT_BASH = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.is_file() else shutil.which("bash")


@unittest.skipUnless(BASH, "Bash is required to test deployment orchestration")
class DeploymentTests(unittest.TestCase):
    def deploy(self, component, fail_validation=False, worker_image="", fail_migration=False):
        source = (ROOT / "scripts/deploy-remote.sh").read_text()
        tail = 'case "$DEPLOY_COMPONENT" in' + source.split('case "$DEPLOY_COMPONENT" in', 1)[1]
        harness = r'''
set -euo pipefail
DEPLOY_COMPONENT=$1
FAIL_VALIDATION=$2
WORKER_IMAGE=$3
FAIL_MIGRATION=$4
USE_MINIO=false
docker() { printf '%s\n' "$*"; printf '%s\n' "$*" >&2; }
record_compose() {
  if [[ "$*" == "ps -q --status running "* ]]; then
    printf '%s\n' api-container media-container-1 media-container-2
    return
  fi
  printf '%s\n' "$*"
  if [[ "$*" == *"alembic upgrade head"* && "$FAIL_MIGRATION" == 1 ]]; then
    return 1
  fi
  if [[ "$*" == *"caddy validate"* && "$FAIL_VALIDATION" == 1 ]]; then
    return 1
  fi
}
compose=(record_compose)
'''
        return subprocess.run(
            [BASH, "-s", "--", component, "1" if fail_validation else "0", worker_image,
             "1" if fail_migration else "0"],
            input=harness + tail, text=True, capture_output=True, timeout=10, check=False,
        )

    def test_web_and_full_deployments_reload_proxy_after_updating_services(self):
        for component in ("web", "all"):
            with self.subTest(component=component):
                result = self.deploy(component)
                self.assertEqual(result.returncode, 0, result.stderr)
                commands = result.stdout.splitlines()
                validate = next(i for i, command in enumerate(commands) if "caddy validate" in command)
                reload = next(i for i, command in enumerate(commands) if "caddy reload" in command)
                up = next(i for i, command in enumerate(commands) if command.startswith("up -d"))
                self.assertLess(up, validate)
                self.assertLess(validate, reload)
                self.assertLess(reload, commands.index("ps"))

    def test_invalid_proxy_configuration_fails_deployment_without_reloading(self):
        result = self.deploy("web", fail_validation=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("caddy reload", result.stdout)
        self.assertNotIn("\nps\n", result.stdout)

    def test_worker_deployment_does_not_touch_the_proxy(self):
        result = self.deploy("snapshot-worker")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("caddy", result.stdout)
        self.assertIn("up -d snapshot-worker asset-worker media-worker image-worker audio-worker video-worker", result.stdout)
        commands = result.stdout.splitlines()
        policy = next(i for i, command in enumerate(commands) if "set_policy" in command)
        startup = next(i for i, command in enumerate(commands) if command.startswith("up -d"))
        self.assertLess(policy, startup)
        self.assertIn("pool-(coordinator|image|audio|video)", commands[policy])

    def test_infrastructure_waits_for_broker_before_setting_job_policy(self):
        result = self.deploy("infrastructure")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("up -d --wait postgres rabbitmq redis", result.stdout)

    def test_migrations_release_connections_and_restore_running_replicas(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                result = self.deploy("api", fail_migration=fail)
                self.assertEqual(result.returncode == 0, not fail, result.stderr)
                self.assertIn("stop --time 120 api-container media-container-1 media-container-2", result.stdout)
                # docker start output is suppressed by the EXIT trap; record to stderr too.
                self.assertIn("start api-container media-container-1 media-container-2", result.stderr)
                if fail:
                    self.assertNotIn("up -d", result.stdout)

    def test_local_worker_image_is_not_pulled_from_a_registry(self):
        result = self.deploy("snapshot-worker", worker_image="solaris-atlas-worker:workers-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("pull snapshot-worker", result.stdout)
        self.assertIn("up -d snapshot-worker asset-worker media-worker image-worker audio-worker video-worker", result.stdout)


if __name__ == "__main__":
    unittest.main()
